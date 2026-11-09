/**
 * CapCap Web AI Translator Bridge - Background Service Worker
 * Coordinates tasks between CapCap Desktop (127.0.0.1:18765) and Web AI platforms (ChatGPT & Gemini)
 */

const BRIDGE_BASE_URL = "http://127.0.0.1:18765";
const POLL_INTERVAL_MS = 1500;
const HEARTBEAT_INTERVAL_MS = 3000;
const TAB_LOAD_TIMEOUT_MS = 30000;

// Track tab IDs for each translation session: session_id -> tabId
let sessionTabs = {};
const sessionWindows = {};
const pinnedWindows = {};
// Analysis/review and translation use different desktop provider instances.
// Retain the successful analysis tab so the next stage can take ownership.
const contextSessions = {};

// Polling lock and state
let isPolling = false;
let isProcessingTask = false;
let pollTimerId = null;

// Heartbeat lock and state
let isHeartbeating = false;
let heartbeatTimerId = null;

// Current running task watchdog tracking: { id, tabId: null }
let currentTask = null;
let currentTaskCancel = null;

// Clean up stale session tabs when closed
chrome.tabs.onRemoved.addListener((closedTabId) => {
  for (const [sessionId, tabId] of Object.entries(sessionTabs)) {
    if (tabId === closedTabId) {
      delete sessionTabs[sessionId];
      delete sessionWindows[sessionId];
      console.log(`[CapCap Bridge] Session tab closed: ${sessionId} -> tabId ${closedTabId}`);
    }
  }
  for (const [platform, pinned] of Object.entries(pinnedWindows)) {
    if (pinned && pinned.tabId === closedTabId) {
      delete pinnedWindows[platform];
      console.log(`[CapCap Bridge] Pinned tab closed: ${platform} -> tabId ${closedTabId}`);
    }
  }
});

// Clean up stale session windows when closed
if (chrome.windows && chrome.windows.onRemoved) {
  chrome.windows.onRemoved.addListener((closedWinId) => {
    for (const [sessionId, winId] of Object.entries(sessionWindows)) {
      if (winId === closedWinId) {
        delete sessionWindows[sessionId];
        delete sessionTabs[sessionId];
        console.log(`[CapCap Bridge] Session window closed: ${sessionId} -> winId ${closedWinId}`);
      }
    }
    for (const [platform, pinned] of Object.entries(pinnedWindows)) {
      if (pinned && pinned.windowId === closedWinId) {
        delete pinnedWindows[platform];
        console.log(`[CapCap Bridge] Pinned window closed: ${platform} -> winId ${closedWinId}`);
      }
    }
  });
}

/**
 * Default to a separate window. Migrate the previous tab default once;
 * users can still explicitly choose a normal tab afterwards.
 */
async function getRunMode() {
  try {
    const res = await chrome.storage.local.get(["capcap_run_mode", "capcap_run_mode_version"]);
    if (res.capcap_run_mode_version !== 3) {
      await chrome.storage.local.set({ capcap_run_mode: "window", capcap_run_mode_version: 3 });
      return "window";
    }
    return res.capcap_run_mode === "foreground" ? "foreground" : "window";
  } catch (err) {
    return "window";
  }
}

/**
 * Update connection state and sync status in chrome.storage.local
 */
async function updateStoredStatus(connected, syncState = null) {
  try {
    const update = {
      connected,
      last_sync: Date.now()
    };
    if (syncState) {
      update.sync_state = syncState;
    }
    await chrome.storage.local.set(update);
  } catch (err) {
    console.warn("[CapCap Bridge] Error updating storage:", err);
  }
}

/**
 * Wait for a browser tab to complete loading
 */
function waitForTabLoad(tabId, timeoutMs = TAB_LOAD_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    let timer = null;

    const listener = (updatedTabId, changeInfo, tab) => {
      if (updatedTabId === tabId && changeInfo.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener);
        if (timer) clearTimeout(timer);
        resolve(tab);
      }
    };

    chrome.tabs.onUpdated.addListener(listener);

    timer = setTimeout(async () => {
      chrome.tabs.onUpdated.removeListener(listener);
      try {
        const tab = await chrome.tabs.get(tabId);
        if (tab && tab.status === "complete") {
          resolve(tab);
          return;
        }
      } catch (e) {
        // Tab may have closed
      }
      reject(new Error(`Timeout waiting for tab ${tabId} to load`));
    }, timeoutMs);
  });
}

/**
 * Send message to tab with automatic content.js injection fallback
 */
async function sendMessageWithInjection(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch (initialErr) {
    console.log(`[CapCap Bridge] Direct sendMessage failed, attempting injection for tab ${tabId}...`);
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["content.js"]
      });
      // Small pause to allow content script initialization
      await new Promise((r) => setTimeout(r, 300));
      return await chrome.tabs.sendMessage(tabId, message);
    } catch (injectErr) {
      console.error(`[CapCap Bridge] Script injection and send failed for tab ${tabId}:`, injectErr);
      throw injectErr;
    }
  }
}

/**
 * Find or create a matching tab for platform
 */
async function getOrCreateTabForPlatform(platform, sessionId, temporaryChat = true, foreground = false) {
  const isChatGPT = platform === "chatgpt";
  const targetDomainPattern = isChatGPT ? ["chatgpt.com", "chat.openai.com"] : ["gemini.google.com"];
  const chatgptTempUrl = "https://chatgpt.com/?temporary-chat=true";
  const chatgptNormalUrl = "https://chatgpt.com/";
  const geminiUrl = "https://gemini.google.com/app";

  const targetUrl = isChatGPT
    ? (temporaryChat ? chatgptTempUrl : chatgptNormalUrl)
    : geminiUrl;

  const runMode = await getRunMode();
  const isForeground = foreground === true || runMode === "foreground";

  if (isForeground) {
    // 1. Check if session already has a recorded tab
    const existingTabId = sessionTabs[sessionId];
    if (existingTabId) {
      try {
        const existingTab = await chrome.tabs.get(existingTabId);
        if (existingTab && existingTab.url) {
          const matches = targetDomainPattern.some((d) => existingTab.url.includes(d));
          if (matches) {
            // If ChatGPT and requires temporary chat, ensure url has parameter
            if (isChatGPT && temporaryChat && !existingTab.url.includes("temporary-chat=true")) {
              await chrome.tabs.update(existingTab.id, { url: targetUrl, active: true });
              await waitForTabLoad(existingTab.id);
            } else {
              await chrome.tabs.update(existingTab.id, { active: true });
            }
            return existingTab.id;
          }
        }
      } catch (e) {
        // Tab no longer exists, clear stale mapping
        delete sessionTabs[sessionId];
        delete sessionWindows[sessionId];
      }
    }

    // 2. Look for any existing tab matching the platform domains
    const allTabs = await chrome.tabs.query({});
    for (const tab of allTabs) {
      if (tab.url && targetDomainPattern.some((d) => tab.url.includes(d))) {
        sessionTabs[sessionId] = tab.id;
        if (isChatGPT && temporaryChat && !tab.url.includes("temporary-chat=true")) {
          await chrome.tabs.update(tab.id, { url: targetUrl, active: true });
          await waitForTabLoad(tab.id);
        } else {
          await chrome.tabs.update(tab.id, { active: true });
        }
        return tab.id;
      }
    }

    // 3. No existing tab found - create a new tab
    console.log(`[CapCap Bridge] Creating new tab for ${platform}: ${targetUrl}`);
    const newTab = await chrome.tabs.create({ url: targetUrl, active: true });
    sessionTabs[sessionId] = newTab.id;
    await waitForTabLoad(newTab.id);
    // Wait an extra 1.5s for initial Single-Page-App hydration
    await new Promise((r) => setTimeout(r, 1500));
    return newTab.id;
  }

  // --- Window Mode (default) ---
  // (a) If sessionTabs[sessionId] still exists and URL matches platform domain -> reuse it WITHOUT calling tabs.update active:true
  const existingTabId = sessionTabs[sessionId];
  if (existingTabId) {
    try {
      const existingTab = await chrome.tabs.get(existingTabId);
      if (existingTab && existingTab.url) {
        const matches = targetDomainPattern.some((d) => existingTab.url.includes(d));
        if (matches) {
          // If ChatGPT and requires temporary chat, ensure url has parameter
          if (isChatGPT && temporaryChat && !existingTab.url.includes("temporary-chat=true")) {
            await chrome.tabs.update(existingTab.id, { url: targetUrl });
            await waitForTabLoad(existingTab.id);
          }
          return existingTab.id;
        }
      }
    } catch (e) {
      // Tab no longer exists, clear stale mapping
      delete sessionTabs[sessionId];
      delete sessionWindows[sessionId];
    }
  }

  // (b) NEW: if pinnedWindows[platform] exists and its tab is still alive and matches the platform domain, reuse it WITHOUT focusing/activating anything, set sessionTabs[sessionId] = pinned tabId, and do NOT add it to sessionWindows (so it is not treated as extension-owned); apply the ChatGPT temp-chat navigation rule as today if the URL lacks temporary-chat=true (tabs.update url only, no active);
  const pinned = pinnedWindows[platform];
  if (pinned && pinned.tabId) {
    try {
      const pinnedTab = await chrome.tabs.get(pinned.tabId);
      if (pinnedTab && pinnedTab.url) {
        const matches = targetDomainPattern.some((d) => pinnedTab.url.includes(d));
        if (matches) {
          sessionTabs[sessionId] = pinnedTab.id;
          if (isChatGPT && temporaryChat && !pinnedTab.url.includes("temporary-chat=true")) {
            await chrome.tabs.update(pinnedTab.id, { url: targetUrl });
            await waitForTabLoad(pinnedTab.id);
          }
          return pinnedTab.id;
        } else {
          delete pinnedWindows[platform];
        }
      }
    } catch (e) {
      delete pinnedWindows[platform];
    }
  }

  // (c) Otherwise create a new dedicated window:
  // Do NOT search for/reuse the user's existing gemini/chatgpt tabs in window mode.
  // Do NOT minimize the window (minimized windows are treated as hidden by Chrome and get throttled).
  console.log(`[CapCap Bridge] Creating dedicated window for ${platform}: ${targetUrl}`);
  try {
    const win = await chrome.windows.create({
      url: targetUrl,
      type: "popup",
      focused: false,
      width: 960,
      height: 760
    });
    const tabId = (win.tabs && win.tabs[0] && win.tabs[0].id) ||
      (await chrome.tabs.query({ windowId: win.id }))[0]?.id;

    if (!tabId) {
      throw new Error("Created window did not return a valid tab");
    }

    sessionWindows[sessionId] = win.id;
    sessionTabs[sessionId] = tabId;
    await waitForTabLoad(tabId);
    // Wait an extra 1.5s for initial Single-Page-App hydration
    await new Promise((r) => setTimeout(r, 1500));
    return tabId;
  } catch (winErr) {
    console.warn("[CapCap Bridge] chrome.windows.create failed, falling back to background tab:", winErr);
    // Fall back to creating a normal background tab
    const newTab = await chrome.tabs.create({ url: targetUrl, active: false });
    sessionTabs[sessionId] = newTab.id;
    await waitForTabLoad(newTab.id);
    await new Promise((r) => setTimeout(r, 1500));
    return newTab.id;
  }
}

/**
 * Post task execution result back to CapCap server
 */
async function postResult(taskId, success, text = "", error = "") {
  try {
    const payload = {
      task_id: taskId,
      success: !!success,
      text: text || "",
      error: error || ""
    };
    const response = await fetch(`${BRIDGE_BASE_URL}/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return response.ok;
  } catch (err) {
    console.error("[CapCap Bridge] Failed to post task result:", err);
    return false;
  }
}

/**
 * Handle list of canceled task IDs from CapCap server
 */
function handleCanceledIds(ids) {
  if (!ids || !Array.isArray(ids)) return;
  if (currentTask && ids.some((id) => String(id) === String(currentTask.id))) {
    console.warn(`[CapCap Bridge] Task ${currentTask.id} was canceled by CapCap`);
    if (currentTask.tabId) {
      try {
        chrome.tabs.sendMessage(currentTask.tabId, { action: "CAPCAP_ABORT" }).catch(() => {});
      } catch (e) {
        // best effort
      }
    }
    if (typeof currentTaskCancel === "function") {
      const cancelFn = currentTaskCancel;
      currentTaskCancel = null;
      cancelFn(new Error("Task canceled by CapCap"));
    }
  }
}

/**
 * Independent heartbeat cycle to maintain connection liveness and receive cancellations
 */
async function heartbeatCycle() {
  if (isHeartbeating) return;
  isHeartbeating = true;

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2500);

    const response = await fetch(`${BRIDGE_BASE_URL}/heartbeat?platforms=gemini,chatgpt`, {
      method: "GET",
      signal: controller.signal
    });
    clearTimeout(timeoutId);

    if (response.ok) {
      const data = await response.json();
      await updateStoredStatus(true, data.sync_state || null);
      if (data && Array.isArray(data.canceled_task_ids)) {
        handleCanceledIds(data.canceled_task_ids);
      }
    } else {
      await updateStoredStatus(false);
    }
  } catch (err) {
    // Network errors occur naturally when CapCap is closed
    await updateStoredStatus(false);
  } finally {
    isHeartbeating = false;
  }
}

/**
 * Handle a received task from CapCap
 */
async function handleTask(task) {
  console.log(`[CapCap Bridge] Handling task: id=${task.id}, type=${task.type}, platform=${task.platform}`);

  try {
    if (task.type === "open_tab") {
      const platform = task.platform;
      const isChatGPT = platform === "chatgpt";
      const temporaryChat = task.temporary_chat !== false;
      const targetDomainPattern = isChatGPT ? ["chatgpt.com", "chat.openai.com"] : ["gemini.google.com"];
      const chatgptTempUrl = "https://chatgpt.com/?temporary-chat=true";
      const chatgptNormalUrl = "https://chatgpt.com/";
      const geminiUrl = "https://gemini.google.com/app";
      const targetUrl = isChatGPT
        ? (temporaryChat ? chatgptTempUrl : chatgptNormalUrl)
        : geminiUrl;

      const runMode = await getRunMode();
      let tabId;

      if (runMode === "foreground") {
        tabId = await getOrCreateTabForPlatform(platform, task.session_id || "default", temporaryChat, true);
      } else {
        // Window Mode (default)
        const pinned = pinnedWindows[platform];
        let reused = false;
        if (pinned && pinned.tabId) {
          try {
            const existingTab = await chrome.tabs.get(pinned.tabId);
            if (existingTab && existingTab.url) {
              const matches = targetDomainPattern.some((d) => existingTab.url.includes(d));
              if (matches) {
                if (isChatGPT && temporaryChat && !existingTab.url.includes("temporary-chat=true")) {
                  await chrome.tabs.update(existingTab.id, { url: targetUrl });
                  await waitForTabLoad(existingTab.id);
                }
                const winId = pinned.windowId || existingTab.windowId;
                if (winId && chrome.windows && chrome.windows.update) {
                  try {
                    await chrome.windows.update(winId, { focused: true });
                  } catch (e) {
                    // Window update might fail if window closed
                  }
                }
                tabId = existingTab.id;
                reused = true;
              }
            }
          } catch (e) {
            // Tab no longer exists
          }
          if (!reused) {
            delete pinnedWindows[platform];
          }
        }

        if (!reused) {
          console.log(`[CapCap Bridge] Creating dedicated pinned window for ${platform}: ${targetUrl}`);
          try {
            const win = await chrome.windows.create({
              url: targetUrl,
              type: "popup",
              focused: true,
              width: 960,
              height: 760
            });
            const createdTabId = (win.tabs && win.tabs[0] && win.tabs[0].id) ||
              (await chrome.tabs.query({ windowId: win.id }))[0]?.id;

            if (!createdTabId) {
              throw new Error("Created window did not return a valid tab");
            }

            tabId = createdTabId;
            pinnedWindows[platform] = { windowId: win.id, tabId };
            await waitForTabLoad(tabId);
            // Wait an extra 1.5s for initial Single-Page-App hydration
            await new Promise((r) => setTimeout(r, 1500));
          } catch (winErr) {
            console.warn("[CapCap Bridge] chrome.windows.create failed in open_tab, falling back to tab:", winErr);
            const newTab = await chrome.tabs.create({ url: targetUrl, active: true });
            tabId = newTab.id;
            pinnedWindows[platform] = { windowId: newTab.windowId || null, tabId };
            await waitForTabLoad(tabId);
            await new Promise((r) => setTimeout(r, 1500));
          }
        }
      }

      if (currentTask && currentTask.id === task.id) {
        currentTask.tabId = tabId;
      }
      await postResult(task.id, true, "opened");
      return;
    }

    if (task.type === "cleanup_session") {
      const sessionId = task.session_id;

      // If dedicated window exists for this session, remove window and skip old reset logic
      if (sessionId && sessionWindows[sessionId]) {
        const winId = sessionWindows[sessionId];
        const tabId = sessionTabs[sessionId];
        if (currentTask && currentTask.id === task.id) {
          currentTask.tabId = tabId || null;
        }
        delete sessionWindows[sessionId];
        delete sessionTabs[sessionId];
        try {
          await chrome.windows.remove(winId);
        } catch (e) {
          // Window may already be closed
        }
        await postResult(task.id, true, "cleaned");
        return;
      }

      const isChatGPT = task.platform === "chatgpt";
      const temporaryChat = task.temporary_chat !== false;
      const tabId = sessionId ? sessionTabs[sessionId] : null;
      if (currentTask && currentTask.id === task.id) {
        currentTask.tabId = tabId || null;
      }

      // Else if the session tab is a pinned tab -> do NOT close it; reset it to a clean chat
      const pinned = pinnedWindows[task.platform];
      const isPinnedTab = tabId && ((pinned && pinned.tabId === tabId) ||
        Object.values(pinnedWindows).some((p) => p && p.tabId === tabId));
      if (isPinnedTab) {
        if (sessionId) {
          delete sessionTabs[sessionId];
        }
        if (isChatGPT) {
          try {
            await chrome.tabs.update(tabId, { url: "https://chatgpt.com/?temporary-chat=true" });
          } catch (_) {}
        } else {
          try {
            await sendMessageWithInjection(tabId, { action: "CAPCAP_CLEANUP_CHAT" });
          } catch (e) {
            try {
              await chrome.tabs.update(tabId, { url: "https://gemini.google.com/app" });
            } catch (_) {}
          }
        }
        await postResult(task.id, true, "cleaned");
        return;
      }

      // Otherwise keep the old logic
      if (temporaryChat && tabId) {
        if (!isChatGPT) {
          // Gemini cleanup: send message to clean up or reset
          try {
            await sendMessageWithInjection(tabId, { action: "CAPCAP_CLEANUP_CHAT" });
          } catch (e) {
            // Reset url to clean view as fallback
            try {
              await chrome.tabs.update(tabId, { url: "https://gemini.google.com/app" });
            } catch (_) {}
          }
        } else {
          // ChatGPT: Reset to clean temporary chat
          try {
            await chrome.tabs.update(tabId, { url: "https://chatgpt.com/?temporary-chat=true" });
          } catch (_) {}
        }
      }

      await postResult(task.id, true, "cleaned");
      return;
    }

    if (task.type === "translate") {
      const temporaryChat = task.temporary_chat !== false;
      const sessionId = task.session_id || "default";
      const DEAD_TAB_REGEX = /No tab with id|Receiving end does not exist|tab was closed|Cannot access/i;

      const runTranslate = async () => {
        const context = contextSessions[task.platform];
        if (context && context.sessionId !== sessionId && context.temporaryChat === temporaryChat) {
          const contextTabId = sessionTabs[context.sessionId];
          if (contextTabId) {
            try {
              const tab = await chrome.tabs.get(contextTabId);
              const hostname = new URL(tab.url).hostname;
              const matches = task.platform === "chatgpt"
                ? ["chatgpt.com", "chat.openai.com"].includes(hostname)
                : hostname === "gemini.google.com";
              if (matches) {
                sessionTabs[sessionId] = contextTabId;
                if (sessionWindows[context.sessionId]) {
                  sessionWindows[sessionId] = sessionWindows[context.sessionId];
                }
                delete sessionTabs[context.sessionId];
                delete sessionWindows[context.sessionId];
              }
            } catch (_) {
              // Closed or navigated away: normal creation below will recover.
            }
          }
          delete contextSessions[task.platform];
        }
        const tabId = await getOrCreateTabForPlatform(task.platform, sessionId, temporaryChat);
        if (currentTask && currentTask.id === task.id) {
          currentTask.tabId = tabId;
        }

        const resp = await sendMessageWithInjection(tabId, {
          action: "CAPCAP_TRANSLATE",
          prompt: task.prompt,
          platform: task.platform,
          temporary_chat: temporaryChat,
          session_id: sessionId
        });

        if (!resp) {
          throw new Error("No response received from page content script");
        }
        return resp;
      };

      let response;
      try {
        response = await runTranslate();
      } catch (firstErr) {
        const errMsg = firstErr?.message || String(firstErr);
        if (DEAD_TAB_REGEX.test(errMsg)) {
          console.warn(`[CapCap Bridge] Tab communication failed ("${errMsg}"). Recreating tab and retrying translation once...`);
          const deadTabId = sessionTabs[sessionId] || (currentTask && currentTask.tabId);
          delete sessionTabs[sessionId];
          delete sessionWindows[sessionId];
          if (pinnedWindows[task.platform] && pinnedWindows[task.platform].tabId === deadTabId) {
            delete pinnedWindows[task.platform];
          }
          response = await runTranslate();
        } else {
          throw firstErr;
        }
      }

      if (response.success && task.operation === "context") {
        contextSessions[task.platform] = { sessionId, temporaryChat };
      } else if (task.operation !== "context") {
        delete contextSessions[task.platform];
      }
      await postResult(task.id, response.success, response.text, response.error);
      return;
    }

    // Unknown task type
    await postResult(task.id, false, "", `Unknown task type: ${task.type}`);
  } catch (err) {
    console.error(`[CapCap Bridge] Error executing task ${task.id}:`, err);
    await postResult(task.id, false, "", err.message || String(err));
  }
}

/**
 * Main polling cycle to fetch tasks and sync state from CapCap
 */
async function pollCycle() {
  if (isPolling || isProcessingTask) return;
  isPolling = true;

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2500);

    const response = await fetch(`${BRIDGE_BASE_URL}/poll`, {
      method: "GET",
      signal: controller.signal
    });
    clearTimeout(timeoutId);

    if (response.ok) {
      const data = await response.json();
      await updateStoredStatus(true, data.sync_state || null);

      if (data && Array.isArray(data.canceled_task_ids)) {
        handleCanceledIds(data.canceled_task_ids);
      }

      if (data.has_task && data.task) {
        isProcessingTask = true;
        let timeoutTimer = null;
        const cancelPromise = new Promise((_, reject) => {
          currentTaskCancel = reject;
        });

        currentTask = {
          id: data.task.id,
          tabId: null
        };

        const timeoutMs = (Number(data.task.timeout) || 240) * 1000 + 15000;
        const timeoutPromise = new Promise((_, reject) => {
          timeoutTimer = setTimeout(() => {
            reject(new Error(`Task timed out after ${timeoutMs}ms`));
          }, timeoutMs);
        });

        try {
          await Promise.race([
            handleTask(data.task),
            timeoutPromise,
            cancelPromise
          ]);
        } catch (raceErr) {
          const reason = raceErr?.message || String(raceErr);
          console.warn(`[CapCap Bridge] Task ${data.task.id} ended with error: ${reason}`);
          await postResult(data.task.id, false, "", reason);
        } finally {
          if (timeoutTimer) clearTimeout(timeoutTimer);
          currentTaskCancel = null;
          currentTask = null;
          isProcessingTask = false;
          try {
            await chrome.storage.local.set({
              task_progress: {
                phase: "idle",
                chars: 0,
                generating: false,
                platform: null,
                task_id: null,
                at: Date.now()
              }
            });
          } catch (_) {}
        }
      }
    } else {
      await updateStoredStatus(false);
    }
  } catch (err) {
    // Network errors occur naturally when CapCap is closed
    await updateStoredStatus(false);
  } finally {
    isPolling = false;
  }
}

/**
 * Start or resume polling loop
 */
function startPolling() {
  if (pollTimerId) clearInterval(pollTimerId);
  pollTimerId = setInterval(pollCycle, POLL_INTERVAL_MS);
  pollCycle();

  if (heartbeatTimerId) clearInterval(heartbeatTimerId);
  heartbeatTimerId = setInterval(heartbeatCycle, HEARTBEAT_INTERVAL_MS);
  heartbeatCycle();
}

// Alarm keep-alive for Manifest V3 Service Worker lifecycle
chrome.alarms.create("capcap_keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "capcap_keepalive") {
    startPolling();
  }
});

// Extension lifecycle hooks
chrome.runtime.onInstalled.addListener(() => {
  console.log("[CapCap Bridge] Extension installed/updated.");
  startPolling();
});

chrome.runtime.onStartup.addListener(() => {
  console.log("[CapCap Bridge] Extension starting up.");
  startPolling();
});

// Runtime message listener for popup.js interactions and content script progress
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request && request.type === "CAPCAP_PROGRESS") {
    const progressData = {
      phase: request.phase || "idle",
      chars: typeof request.chars === "number" ? request.chars : 0,
      generating: !!request.generating,
      platform: request.platform || null,
      message: request.message || null,
      task_id: currentTask?.id || null,
      at: Date.now()
    };
    chrome.storage.local.set({ task_progress: progressData }).catch(() => {});
    return false;
  }

  if (request.action === "CHECK_TABS") {
    (async () => {
      try {
        const tabs = await chrome.tabs.query({});
        const geminiTab = tabs.find((t) => t.url && t.url.includes("gemini.google.com"));
        const chatgptTab = tabs.find(
          (t) => t.url && (t.url.includes("chatgpt.com") || t.url.includes("chat.openai.com"))
        );
        sendResponse({
          geminiOpen: !!geminiTab,
          chatgptOpen: !!chatgptTab,
          geminiTabId: geminiTab ? geminiTab.id : null,
          chatgptTabId: chatgptTab ? chatgptTab.id : null
        });
      } catch (e) {
        sendResponse({ geminiOpen: false, chatgptOpen: false, error: e.message });
      }
    })();
    return true; // Keep channel open for async response
  }

  if (request.action === "OPEN_TAB") {
    (async () => {
      try {
        const platform = request.platform;
        const temporaryChat = request.temporary_chat !== false;
        const targetUrl =
          platform === "chatgpt"
            ? (temporaryChat ? "https://chatgpt.com/?temporary-chat=true" : "https://chatgpt.com/")
            : "https://gemini.google.com/app";

        const tab = await chrome.tabs.create({ url: targetUrl, active: true });
        sendResponse({ success: true, tabId: tab.id });
      } catch (e) {
        sendResponse({ success: false, error: e.message });
      }
    })();
    return true;
  }

  if (request.action === "TRIGGER_POLL") {
    pollCycle().then(() => sendResponse({ success: true }));
    return true;
  }
});

// Immediately kick off polling upon background script execution
startPolling();
