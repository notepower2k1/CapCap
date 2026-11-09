/**
 * CapCap Web AI Bridge - Popup Script
 * Real-time dashboard synchronizing connection, active engine, progress, and tabs.
 */

const STATUS_ENDPOINT = "http://127.0.0.1:18765/status";
let statusInterval = null;

// DOM Elements
const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const syncTime = document.getElementById("syncTime");
const platformVal = document.getElementById("platformVal");
const chatModeVal = document.getElementById("chatModeVal");
const progressState = document.getElementById("progressState");
const progressPercent = document.getElementById("progressPercent");
const progressBar = document.getElementById("progressBar");
const batchCount = document.getElementById("batchCount");
const subCount = document.getElementById("subCount");
const tabStatusText = document.getElementById("tabStatusText");
const btnOpenGemini = document.getElementById("btnOpenGemini");
const btnOpenChatGPT = document.getElementById("btnOpenChatGPT");
const runModeSelect = document.getElementById("runMode");
const taskProgressLabel = document.getElementById("taskProgressLabel");

/**
 * Format task progress phase into Vietnamese label
 */
function formatTaskProgress(progress) {
  if (!progress || !progress.phase || progress.phase === "idle") {
    return "Nhàn rỗi";
  }
  const now = Date.now();
  if (progress.at && now - progress.at > 20000) {
    return "Không có phản hồi từ trang (có thể đã đóng)";
  }
  const chars = typeof progress.chars === "number" ? progress.chars : 0;
  switch (progress.phase) {
    case "typing":
      return "Đang nhập prompt";
    case "sent":
      return "Đã gửi, chờ AI trả lời";
    case "waiting_reply":
      return `AI đang trả lời (${chars} ký tự)`;
    case "reply_received":
      return `Đã nhận câu trả lời (${chars} ký tự)`;
    case "error":
      return `Lỗi: ${progress.message || "Không xác định"}`;
    case "idle":
    default:
      return "Nhàn rỗi";
  }
}

function updateTaskProgressUI(progress) {
  if (taskProgressLabel) {
    taskProgressLabel.innerText = formatTaskProgress(progress);
  }
}

/**
 * Format relative time
 */
function formatTime(timestamp) {
  if (!timestamp) return "Chưa đồng bộ";
  const diffSec = Math.floor((Date.now() - timestamp) / 1000);
  if (diffSec < 3) return "Đồng bộ: Vừa xong";
  if (diffSec < 60) return `Đồng bộ: ${diffSec}s trước`;
  const d = new Date(timestamp);
  return `Đồng bộ: ${d.toLocaleTimeString("vi-VN")}`;
}

/**
 * Render sync state into popup UI
 */
function renderUI(connected, syncState = null, lastSyncTime = null) {
  // 1. Connection status
  if (connected) {
    statusDot.className = "dot connected";
    statusText.innerText = "🟢 Đã kết nối với CapCap";
    statusText.style.color = "#10b981";
  } else {
    statusDot.className = "dot disconnected";
    statusText.innerText = "⚪ Chưa kết nối (Hãy mở CapCap)";
    statusText.style.color = "#94a3b8";
  }
  syncTime.innerText = formatTime(lastSyncTime || (connected ? Date.now() : null));

  // 2. Synced Configuration
  if (syncState) {
    // Platform label
    let platLabel = syncState.platform_label;
    if (!platLabel) {
      if (syncState.platform === "chatgpt") platLabel = "ChatGPT Web";
      else if (syncState.platform === "gemini") platLabel = "Google Gemini Web";
      else platLabel = syncState.platform || "Chưa cấu hình";
    }
    platformVal.innerText = platLabel;

    // Chat Mode
    if (syncState.temporary_chat === false) {
      chatModeVal.innerText = "💬 Lưu lịch sử";
      chatModeVal.style.color = "#94a3b8";
    } else {
      chatModeVal.innerText = "🛡️ Không lưu lịch sử";
      chatModeVal.style.color = "#38bdf8";
    }

    // 3. Translation progress
    const isTranslating = !!syncState.is_translating;
    const progress = Math.min(100, Math.max(0, Math.round(syncState.progress_percent || 0)));

    if (syncState.status_text) {
      progressState.innerText = syncState.status_text;
    } else if (isTranslating) {
      progressState.innerText = `Đang dịch Batch ${syncState.current_batch || 1}/${syncState.total_batches || 1}...`;
    } else if (progress === 100) {
      progressState.innerText = "✅ Đã hoàn thành!";
    } else {
      progressState.innerText = "Sẵn sàng nhận tác vụ";
    }

    progressPercent.innerText = `${progress}%`;
    progressBar.style.width = `${progress}%`;

    const currBatch = syncState.current_batch || 0;
    const totalBatches = syncState.total_batches || 0;
    batchCount.innerText = `Batch: ${currBatch} / ${totalBatches}`;

    const currSubs = syncState.translated_subs || 0;
    const totalSubs = syncState.total_subs || 0;
    subCount.innerText = `Phụ đề: ${currSubs} / ${totalSubs} câu`;
  } else {
    if (!connected) {
      platformVal.innerText = "--";
      chatModeVal.innerText = "--";
      progressState.innerText = "Chờ mở phần mềm CapCap";
      progressPercent.innerText = "0%";
      progressBar.style.width = "0%";
      batchCount.innerText = "Batch: --";
      subCount.innerText = "Phụ đề: --";
    }
  }
}

/**
 * Check open browser tabs for Gemini or ChatGPT
 */
async function checkTabs() {
  try {
    const tabs = await chrome.tabs.query({});
    const hasGemini = tabs.some((t) => t.url && t.url.includes("gemini.google.com"));
    const hasChatGPT = tabs.some(
      (t) => t.url && (t.url.includes("chatgpt.com") || t.url.includes("chat.openai.com"))
    );

    if (hasGemini && hasChatGPT) {
      tabStatusText.innerHTML = '<span style="color:#10b981">🟢 Đã mở sẵn cả Gemini & ChatGPT</span>';
    } else if (hasGemini) {
      tabStatusText.innerHTML = '<span style="color:#10b981">🟢 Đã mở sẵn tab Gemini</span>';
    } else if (hasChatGPT) {
      tabStatusText.innerHTML = '<span style="color:#10b981">🟢 Đã mở sẵn tab ChatGPT (Temp)</span>';
    } else {
      tabStatusText.innerHTML = '<span style="color:#eab308">⚠️ Chưa mở tab (Sẽ tự mở khi dịch)</span>';
    }
  } catch (err) {
    tabStatusText.innerText = "Không thể kiểm tra tab";
  }
}

/**
 * Direct status query from CapCap bridge HTTP server
 */
async function queryCapCapStatus() {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 1200);

    const res = await fetch(STATUS_ENDPOINT, {
      method: "GET",
      signal: controller.signal
    });
    clearTimeout(timeoutId);

    if (res.ok) {
      const data = await res.json();
      renderUI(true, data.sync_state || data, Date.now());
      // Also update storage for background consistency
      chrome.storage.local.set({
        connected: true,
        last_sync: Date.now(),
        sync_state: data.sync_state || data
      });
      return;
    }
  } catch (e) {
    // If direct fetch fails, read from storage
  }

  // Fallback to storage
  chrome.storage.local.get(["connected", "last_sync", "sync_state", "task_progress"], (store) => {
    const isConn = store.connected && (Date.now() - (store.last_sync || 0) < 4000);
    renderUI(!!isConn, store.sync_state || null, store.last_sync || null);
    updateTaskProgressUI(store.task_progress);
  });
}

// Button actions
btnOpenGemini.addEventListener("click", () => {
  chrome.tabs.create({ url: "https://gemini.google.com/app", active: true });
});

btnOpenChatGPT.addEventListener("click", () => {
  chrome.tabs.create({ url: "https://chatgpt.com/?temporary-chat=true", active: true });
});

// Run mode configuration (window vs foreground)
chrome.storage.local.get(["capcap_run_mode", "capcap_run_mode_version"], (store) => {
  if (runModeSelect) {
    runModeSelect.value = store.capcap_run_mode_version === 3 && store.capcap_run_mode === "foreground" ? "foreground" : "window";
  }
});

if (runModeSelect) {
  runModeSelect.addEventListener("change", () => {
    chrome.storage.local.set({ capcap_run_mode: runModeSelect.value, capcap_run_mode_version: 3 });
  });
}

// Storage changes listener
chrome.storage.onChanged.addListener((changes, namespace) => {
  if (namespace === "local") {
    if (changes.capcap_run_mode && runModeSelect) {
      runModeSelect.value = changes.capcap_run_mode.newValue === "foreground" ? "foreground" : "window";
    }
    if (changes.task_progress) {
      updateTaskProgressUI(changes.task_progress.newValue);
    }
    chrome.storage.local.get(["connected", "last_sync", "sync_state", "task_progress"], (store) => {
      const isConn = store.connected && (Date.now() - (store.last_sync || 0) < 4000);
      renderUI(!!isConn, store.sync_state || null, store.last_sync || null);
      updateTaskProgressUI(store.task_progress);
    });
  }
});

// Initial boot
chrome.storage.local.get(["connected", "last_sync", "sync_state", "task_progress"], (store) => {
  renderUI(!!store.connected, store.sync_state || null, store.last_sync || null);
  updateTaskProgressUI(store.task_progress);
});

checkTabs();
queryCapCapStatus();

// Polling interval when popup is open
statusInterval = setInterval(() => {
  queryCapCapStatus();
  checkTabs();
  chrome.storage.local.get(["task_progress"], (store) => {
    updateTaskProgressUI(store.task_progress);
  });
}, 1000);

window.addEventListener("unload", () => {
  if (statusInterval) clearInterval(statusInterval);
});
