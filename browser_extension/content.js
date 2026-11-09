/**
 * CapCap Web AI Translator Bridge - Content Script
 * Automates DOM interaction for ChatGPT (chatgpt.com) and Google Gemini (gemini.google.com)
 */

(function () {
  if (window.__capcapBridgeInjected) {
    return;
  }
  window.__capcapBridgeInjected = true;
  console.log("[CapCap Content] Injected successfully into:", window.location.href);

  // Task abort state
  let abortRequested = false;

  // Helper: Report progress back to background service for popup/debugging visibility
  function reportProgress(phase, details = {}) {
    try {
      if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({
          type: "CAPCAP_PROGRESS",
          phase,
          chars: typeof details.chars === "number" ? details.chars : 0,
          generating: !!details.generating,
          platform: details.platform || null,
          message: details.message || null
        });
      }
    } catch (_) {
      // Never throw from progress reporting
    }
  }

  // Helper: Sleep
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // Helper: Escape HTML
  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Helper: Wait for any element matching selectors
  async function waitForElement(selectors, timeoutMs = 15000) {
    const startTime = Date.now();
    while (Date.now() - startTime < timeoutMs) {
      if (abortRequested) {
        return null;
      }
      for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el && (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0)) {
          return el;
        }
      }
      await sleep(200);
    }
    return null;
  }

  // Helper: Clean response text
  function cleanResponseText(rawText) {
    if (!rawText) return "";
    let text = rawText.trim();
    // Remove Gemini or ChatGPT disclaimer footers if included
    // Usually innerText of message-content / markdown only has the response
    return text;
  }

  // Helper: Extract text from response container preserving list numbering
  function extractTextWithListNumbers(el) {
    if (!el) return "";
    const clone = el.cloneNode(true);

    // Remove UI noise elements (icons, feedback buttons, action menus)
    const noise = clone.querySelectorAll(
      "button, mat-icon, .actions-container, .response-feedback, [aria-hidden='true']"
    );
    noise.forEach((n) => n.remove());

    // Process every <ol> in document order with running fallback counter
    let fallbackCounter = 1;
    const olList = clone.tagName === "OL" ? [clone] : Array.from(clone.querySelectorAll("ol"));
    for (const ol of olList) {
      if (!ol.parentNode && clone !== ol) continue;

      const startAttr = ol.getAttribute("start");
      const parsedStart = startAttr ? parseInt(startAttr, 10) : NaN;
      const startNum = Number.isInteger(parsedStart) ? parsedStart : fallbackCounter;

      const lis = Array.from(ol.children).filter((c) => c.tagName === "LI");
      const liLines = lis.map((li, idx) => {
        const n = startNum + idx;
        const text = (li.textContent || "").replace(/\s+/g, " ").trim();
        return `${n}. ${text}`;
      });

      if (lis.length > 0) {
        fallbackCounter = startNum + lis.length;
      }

      const replacement = document.createTextNode("\n" + liLines.join("\n") + "\n");
      if (ol.parentNode) {
        ol.parentNode.replaceChild(replacement, ol);
      } else {
        return liLines.join("\n").trim();
      }
    }

    // Manual block-aware text extraction
    function walk(node) {
      if (!node) return "";
      if (node.nodeType === 3 /* Node.TEXT_NODE */) {
        return node.nodeValue || "";
      }
      if (node.nodeType !== 1 /* Node.ELEMENT_NODE */) {
        return "";
      }
      const tag = node.tagName.toUpperCase();
      if (tag === "SCRIPT" || tag === "STYLE") {
        return "";
      }
      if (tag === "BR") {
        return "\n";
      }

      let text = "";
      for (let child = node.firstChild; child; child = child.nextSibling) {
        text += walk(child);
      }

      if (tag === "LI") {
        return text.trim() + "\n";
      }
      const isBlock = /^(P|DIV|H[1-6]|BLOCKQUOTE|PRE|TR|UL|TABLE|SECTION|ARTICLE|HEADER|FOOTER)$/.test(tag);
      if (isBlock) {
        return "\n" + text + "\n";
      }
      return text;
    }

    const raw = walk(clone);
    const lines = raw.split("\n").map((l) => l.trimEnd());
    const cleaned = [];
    let prevEmpty = false;
    for (const line of lines) {
      const isEmpty = line.trim().length === 0;
      if (isEmpty) {
        if (!prevEmpty && cleaned.length > 0) {
          cleaned.push("");
        }
        prevEmpty = true;
      } else {
        cleaned.push(line);
        prevEmpty = false;
      }
    }

    return cleaned.join("\n").trim();
  }

  // Helper: Insert prompt text into composer preserving newlines
  async function insertPromptText(composer, prompt, useEditorInput = false) {
    // Focus and clear composer
    composer.focus();
    await sleep(100);

    if (composer.tagName === "TEXTAREA") {
      composer.value = "";
      composer.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      try {
        document.execCommand("selectAll", false, null);
        document.execCommand("delete", false, null);
      } catch (e) {
        // ignore
      }
      composer.innerHTML = "";
    }

    // Let ChatGPT's editor process an actual editing operation before falling
    // back to DOM assignment, which can leave its internal document empty.
    if (useEditorInput && composer.isContentEditable) {
      try {
        document.execCommand("insertText", false, prompt);
        await sleep(300);
        if ((composer.innerText || "").trim()) return;
      } catch (_) {
        // Older editors may only support the paste fallback below.
      }
    }

    // Try synthetic paste first to preserve newlines in rich editors (ProseMirror / Quill)
    try {
      const dt = new DataTransfer();
      dt.setData("text/plain", prompt);
      composer.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    } catch (e) {
      // ignore
    }

    await sleep(300);

    const currentText = (composer.tagName === "TEXTAREA" ? composer.value : composer.innerText) || "";
    const promptLenNoNl = prompt.replace(/[\r\n]+/g, "").length;

    // Fallback if composer text is still empty or clearly shorter than 80% of prompt length without newlines
    if (!currentText || currentText.trim().length === 0 || currentText.length < promptLenNoNl * 0.8) {
      if (composer.tagName === "TEXTAREA") {
        composer.value = prompt;
      } else {
        const lines = prompt.split(/\r?\n/);
        const html = lines
          .map((line) => {
            if (line.length === 0 || line.trim().length === 0) {
              return "<p><br></p>";
            }
            return `<p>${escapeHtml(line)}</p>`;
          })
          .join("");
        composer.innerHTML = html;
      }
    }

    // Always dispatch input/change events for state synchronization
    composer.dispatchEvent(
      new InputEvent("input", { bubbles: true, cancelable: true, inputType: "insertText", data: prompt })
    );
    composer.dispatchEvent(new Event("input", { bubbles: true }));
    composer.dispatchEvent(new Event("change", { bubbles: true }));

    await sleep(350);
  }

  /**
   * Shared helper: Wait for AI response to finish generating and stabilize.
   * Uses time-based stability (unchanged text for >= 1500ms while !isGenerating)
   * and dual polling (MutationObserver + 500ms fallback interval) to remain robust
   * against browser timer throttling in background/unfocused windows.
   */
  async function waitForReplyComplete({
    getLatestText,
    isGenerating,
    getCount,
    initialCount,
    initialText = "",
    platformName = "AI",
    platform = "ai"
  }) {
    const startTime = Date.now();
    let lastChangeAt = Date.now();
    let previousText = "";
    let lastProgressReportAt = 0;
    const INACTIVITY_TIMEOUT_MS = 180000; // 3 minutes without change
    const MAX_TOTAL_TIMEOUT_MS = 600000;  // 10 minutes max total
    const STABILITY_DURATION_MS = 1500;

    let observer = null;
    let timerId = null;

    try {
      return await new Promise((resolve, reject) => {
        let isSettled = false;

        function done(fn, arg) {
          if (isSettled) return;
          isSettled = true;
          if (timerId) {
            clearInterval(timerId);
            timerId = null;
          }
          if (observer) {
            try { observer.disconnect(); } catch (_) {}
            observer = null;
          }
          fn(arg);
        }

        function check() {
          if (isSettled) return;

          if (abortRequested) {
            done(reject, new Error("Đã hủy bởi CapCap."));
            return;
          }

          const now = Date.now();

          // 1. Timeout checks: fail only if inactive for 180s or total time exceeds 600s
          if (now - startTime > MAX_TOTAL_TIMEOUT_MS) {
            done(reject, new Error(`Quá thời gian chờ phản hồi từ ${platformName} (Timeout 600s).`));
            return;
          }
          if (now - lastChangeAt > INACTIVITY_TIMEOUT_MS) {
            done(reject, new Error(`Quá thời gian chờ phản hồi từ ${platformName} (Timeout 180s).`));
            return;
          }

          // 2. Generation & stability checks
          let count, text, generating;
          try {
            count = getCount ? getCount() : 0;
            text = (getLatestText ? getLatestText() : "") || "";
            generating = isGenerating ? isGenerating() : false;
          } catch (error) {
            // Exceptions inside an observer/timer otherwise leave this promise
            // pending forever, with no error returned to the desktop.
            done(reject, new Error(`Cannot read ${platformName} reply: ${error.message || error}`));
            return;
          }

          // Progress report: phase "waiting_reply", throttled to at most once per second
          if (now - lastProgressReportAt >= 1000) {
            lastProgressReportAt = now;
            reportProgress("waiting_reply", {
              platform,
              chars: text.length,
              generating: !!generating
            });
          }

          // SPA renderers can replace/reuse a message container without
          // increasing its count. Compare with the response BEFORE sending.
          if (count > initialCount || (text.trim() && text !== initialText)) {
            if (text !== previousText) {
              previousText = text;
              lastChangeAt = now;
            }

            // Complete when not generating, text non-empty, and text unchanged for >= 1500ms
            if (!generating && text.trim().length > 0 && (now - lastChangeAt >= STABILITY_DURATION_MS)) {
              console.log(`[CapCap Content] ${platformName} response received successfully (length: ${text.length})`);
              reportProgress("reply_received", {
                platform,
                chars: text.length
              });
              done(resolve, cleanResponseText(text));
              return;
            }
          }
        }

        // Attach MutationObserver for immediate re-check (not throttled by Chrome in background tabs)
        if (typeof MutationObserver !== "undefined" && document.body) {
          try {
            observer = new MutationObserver(() => {
              check();
            });
            observer.observe(document.body, {
              childList: true,
              subtree: true,
              characterData: true
            });
          } catch (e) {
            console.warn("[CapCap Content] Failed to attach MutationObserver:", e);
          }
        }

        // Fallback timer polling at 500ms
        timerId = setInterval(check, 500);

        // Initial check
        check();
      });
    } finally {
      if (observer) {
        try { observer.disconnect(); } catch (_) {}
        observer = null;
      }
      if (timerId) {
        clearInterval(timerId);
        timerId = null;
      }
    }
  }

  // ==========================================
  // ChatGPT Automation
  // ==========================================
  const ChatGPT = {
    composerSelectors: [
      "#prompt-textarea",
      "div#prompt-textarea[contenteditable='true']",
      "div.ProseMirror[contenteditable='true']",
      "textarea[data-id='root']",
      "div[contenteditable='true'][tabindex='0']",
      "textarea"
    ],

    sendButtonSelectors: [
      'button#composer-submit-button',
      'button[data-testid="send-button"]',
      'button[data-testid="composer-send-button"]',
      'button[aria-label*="Send" i]',
      'button[aria-label*="Gửi" i]'
    ],

    stopButtonSelectors: [
      'button[data-testid="stop-button"]',
      'button[aria-label*="Stop" i]',
      'button[aria-label*="Dừng" i]'
    ],

    assistantSelectors: [
      '[data-markdown-text-style="assistant-message"]',
      '[data-dil-response]',
      '[data-message-author-role="assistant"]',
      '[data-turn="assistant"]',
      'article[data-testid^="conversation-turn-"]:has([data-message-author-role="assistant"])',
      'div.agent-turn'
    ],

    isGenerating() {
      for (const sel of this.stopButtonSelectors) {
        const btn = document.querySelector(sel);
        if (btn && (btn.offsetWidth > 0 || btn.offsetHeight > 0)) {
          return true;
        }
      }
      return false;
    },

    getAssistantMessages() {
      // Query all supported layouts together in document order. Older turns
      // can retain role attributes while the newest turn uses an agent wrapper.
      const messages = Array.from(document.querySelectorAll(this.assistantSelectors.join(", ")));
      // Keep one outer container per turn, rather than counting both an article
      // and its nested assistant message as separate replies.
      return messages.filter((message) => !messages.some((other) =>
        other !== message && other.contains(message)
      ));
    },

    getAssistantText(el) {
      if (!el) return "";
      const blocks = Array.from(el.querySelectorAll('[data-dil-response], .markdown, .prose, div.whitespace-pre-wrap'));
      const roots = blocks.filter((block) => !blocks.some((other) =>
        other !== block && other.contains(block)
      ));
      // ChatGPT may split an answer across several markdown containers. Reading
      // only querySelector's first match can return an empty streaming shell.
      const text = roots.map(extractTextWithListNumbers).filter(Boolean).join("\n\n");
      return text || extractTextWithListNumbers(el);
    },

    async submitPrompt(composer) {
      const userCount = () => document.querySelectorAll('[data-message-author-role="user"]').length;
      const initialUserCount = userCount();
      const initialAssistantCount = this.getAssistantMessages().length;
      const readyDeadline = Date.now() + 15000;
      let button = null;
      while (Date.now() < readyDeadline) {
        if (abortRequested) throw new Error("ChatGPT submission canceled by CapCap.");
        const scope = composer.closest("form") || document;
        const selectors = [...this.sendButtonSelectors, ...(scope !== document ? ['button[type="submit"]'] : [])];
        button = selectors.flatMap((sel) => Array.from(scope.querySelectorAll(sel))).find((btn) =>
          (btn.offsetWidth > 0 || btn.offsetHeight > 0 || btn.getClientRects().length > 0) &&
          !btn.disabled && btn.getAttribute("aria-disabled") !== "true"
        );
        if (button) break;
        await sleep(200);
      }
      if (!button) {
        throw new Error("ChatGPT send button is unavailable or disabled. Check the chat window and try again.");
      }

      button.click();
      // A click is not proof of submission. Wait for the page to accept the
      // message; never retry a click here, which could submit a duplicate.
      const acceptedDeadline = Date.now() + 10000;
      while (Date.now() < acceptedDeadline) {
        if (abortRequested) throw new Error("ChatGPT submission canceled by CapCap.");
        if (userCount() > initialUserCount || this.isGenerating() ||
            this.getAssistantMessages().length > initialAssistantCount) return;
        await sleep(200);
      }
      throw new Error("ChatGPT did not accept the prompt after clicking Send. Check the chat window and try again.");
    },

    async typeAndSend(prompt, platform = "chatgpt") {
      if (abortRequested) {
        throw new Error("Đã hủy bởi CapCap.");
      }
      const composer = await waitForElement(this.composerSelectors, 15000);
      if (abortRequested) {
        throw new Error("Đã hủy bởi CapCap.");
      }
      if (!composer) {
        const pageHint = ` (trang hiện tại: ${document.title || location.href}) - có thể cần đăng nhập hoặc xác minh Cloudflare trong cửa sổ này.`;
        throw new Error(`Không tìm thấy ô nhập câu lệnh ChatGPT (Composer textarea).${pageHint}`);
      }

      // Record count before sending
      const initialMessages = this.getAssistantMessages();
      const initialAssistantCount = initialMessages.length;
      const initialAssistantText = initialMessages.length
        ? this.getAssistantText(initialMessages[initialMessages.length - 1]) : "";

      // Check abort before inserting text
      if (abortRequested) {
        throw new Error("Đã hủy bởi CapCap.");
      }

      // Phase: "typing" before inserting prompt
      reportProgress("typing", { platform, chars: prompt.length });

      // Insert prompt text preserving newlines
      await insertPromptText(composer, prompt, true);

      // Check abort right before sending
      if (abortRequested) {
        throw new Error("Đã hủy bởi CapCap.");
      }

      await this.submitPrompt(composer);

      // Phase: "sent" after clicking send / Enter
      reportProgress("sent", { platform, chars: 0 });

      console.log("[CapCap Content] ChatGPT prompt submitted, waiting for response...");

      // Allow a brief moment for the request to register
      await sleep(1000);

      return await waitForReplyComplete({
        getCount: () => this.getAssistantMessages().length,
        initialCount: initialAssistantCount,
        initialText: initialAssistantText,
        getLatestText: () => {
          const msgs = this.getAssistantMessages();
          return msgs.length > 0 ? this.getAssistantText(msgs[msgs.length - 1]) : "";
        },
        isGenerating: () => this.isGenerating(),
        platformName: "ChatGPT",
        platform: platform
      });
    }
  };

  // ==========================================
  // Google Gemini Automation
  // ==========================================
  const Gemini = {
    composerSelectors: [
      "rich-textarea .ql-editor",
      "rich-textarea div[contenteditable='true']",
      "div.ql-editor[contenteditable='true']",
      "div[contenteditable='true'][role='textbox']",
      "rich-textarea",
      "div[aria-label*='prompt' i]",
      "div[aria-label*='nhập' i]"
    ],

    sendButtonSelectors: [
      "button.send-button",
      "button[data-test-id='send-button']",
      "button[aria-label*='Send' i]",
      "button[aria-label*='Gửi' i]",
      "button.send-button-container"
    ],

    stopButtonSelectors: [
      "button[aria-label*='Stop' i]",
      "button[aria-label*='Dừng' i]",
      "button[aria-label*='Stop response' i]",
      "button[data-test-id='stop-button']",
      ".stop-generating-button",
      ".is-generating"
    ],

    responseSelectors: [
      "message-content",
      "model-response",
      ".model-response-text",
      "div[data-test-id='model-response']",
      ".response-container"
    ],

    isGenerating() {
      for (const sel of this.stopButtonSelectors) {
        const btn = document.querySelector(sel);
        if (btn && (btn.offsetWidth > 0 || btn.offsetHeight > 0)) {
          return true;
        }
      }
      return false;
    },

    getResponses() {
      return document.querySelectorAll("message-content, model-response, .model-response-text");
    },

    getResponseText(el) {
      if (!el) return "";
      return extractTextWithListNumbers(el);
    },

    temporaryChatButtons() {
      return Array.from(document.querySelectorAll('button, [role="button"]')).filter((button) =>
        /temporary chat|cuộc trò chuyện tạm thời/i.test(button.getAttribute("aria-label") || "")
      );
    },

    isTemporaryChat() {
      if (this.temporaryChatButtons().some((button) =>
        button.getAttribute("aria-pressed") === "true" || button.getAttribute("aria-checked") === "true"
      )) return true;
      // The sidebar's entry button exists in normal chats too. Only accept
      // an active-chat indicator or the temporary-chat explanation in main.
      if (document.querySelector('main [data-mat-icon-name="gemini_chat_temp"], main .temporary-chat-banner, main [data-test-id="temporary-chat-banner"]')) return true;
      const main = document.querySelector('main, [role="main"]');
      const text = main ? (main.innerText || main.textContent || "") : "";
      return /temporary chats (?:don.t|do not|won.t) appear|cuộc trò chuyện tạm thời (?:sẽ )?không (?:xuất hiện|hiển thị)/i.test(text);
    },

    async ensureTemporaryChat(timeoutMs = 15000) {
      const deadline = Date.now() + timeoutMs;
      let clicked = false;
      while (Date.now() < deadline) {
        if (abortRequested) throw new Error("Gemini temporary chat setup canceled.");
        if (this.isTemporaryChat()) return;
        if (!clicked) {
          const button = this.temporaryChatButtons().find((button) =>
            !button.disabled && button.getAttribute("aria-disabled") !== "true" &&
            (button.offsetWidth > 0 || button.offsetHeight > 0 || button.getClientRects().length > 0)
          );
          if (button) {
            button.click();
            clicked = true;
          }
        }
        await sleep(200);
      }
      throw new Error("Cannot verify Gemini Temporary Chat. No prompt was sent. Enable Temporary Chat in Gemini or turn off Temporary Chat in CapCap to use saved chats.");
    },

    async cleanupChat() {
      try {
        // Try clicking "New chat" button
        const newChatSelectors = [
          'button[aria-label*="New chat" i]',
          'button[aria-label*="Cuộc trò chuyện mới" i]',
          'a[href="/app"]',
          '.new-conversation-button'
        ];
        for (const sel of newChatSelectors) {
          const btn = document.querySelector(sel);
          if (btn) {
            btn.click();
            await sleep(500);
            return true;
          }
        }
        // Fallback: reload app
        window.location.href = "https://gemini.google.com/app";
        return true;
      } catch (err) {
        console.warn("[CapCap Content] Gemini cleanup error:", err);
        return false;
      }
    },

    async typeAndSend(prompt, platform = "gemini", temporaryChat = false) {
      if (abortRequested) {
        throw new Error("Đã hủy bởi CapCap.");
      }
      if (temporaryChat) await this.ensureTemporaryChat();
      const composer = await waitForElement(this.composerSelectors, 15000);
      if (abortRequested) {
        throw new Error("Đã hủy bởi CapCap.");
      }
      if (!composer) {
        const pageHint = ` (trang hiện tại: ${document.title || location.href}) - có thể cần đăng nhập hoặc xác minh Cloudflare trong cửa sổ này.`;
        throw new Error(`Không tìm thấy ô nhập câu lệnh Google Gemini (Composer).${pageHint}`);
      }

      // Record count before sending
      const initialResponses = this.getResponses();
      const initialResponseCount = initialResponses.length;
      const initialResponseText = initialResponses.length
        ? this.getResponseText(initialResponses[initialResponses.length - 1]) : "";

      // Check abort before inserting text
      if (abortRequested) {
        throw new Error("Đã hủy bởi CapCap.");
      }

      // Phase: "typing" before inserting prompt
      reportProgress("typing", { platform, chars: prompt.length });

      // Insert prompt text preserving newlines
      await insertPromptText(composer, prompt);

      // Check abort right before sending
      if (abortRequested) {
        throw new Error("Đã hủy bởi CapCap.");
      }

      // Find and click send button
      let sent = false;
      for (const sel of this.sendButtonSelectors) {
        const btn = document.querySelector(sel);
        if (btn && !btn.disabled && btn.getAttribute("aria-disabled") !== "true") {
          btn.click();
          sent = true;
          break;
        }
      }

      // Fallback: send Enter keydown
      if (!sent) {
        const enterEvent = new KeyboardEvent("keydown", {
          key: "Enter",
          code: "Enter",
          keyCode: 13,
          which: 13,
          bubbles: true,
          cancelable: true
        });
        composer.dispatchEvent(enterEvent);
      }

      // Phase: "sent" after clicking send / Enter
      reportProgress("sent", { platform, chars: 0 });

      console.log("[CapCap Content] Gemini prompt submitted, waiting for response...");

      // Allow a brief moment for the request to register
      await sleep(1000);

      return await waitForReplyComplete({
        getCount: () => this.getResponses().length,
        initialCount: initialResponseCount,
        initialText: initialResponseText,
        getLatestText: () => {
          const resps = this.getResponses();
          return resps.length > 0 ? this.getResponseText(resps[resps.length - 1]) : "";
        },
        isGenerating: () => this.isGenerating(),
        platformName: "Google Gemini",
        platform: platform
      });
    }
  };

  // ==========================================
  // Message Listener from Background Service
  // ==========================================
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    console.log("[CapCap Content] Message received:", request.action);

    if (request.action === "CAPCAP_ABORT") {
      abortRequested = true;
      sendResponse({ ok: true });
      return false;
    }

    if (request.action === "PING") {
      sendResponse({ status: "ok", url: window.location.href });
      return false;
    }

    if (request.action === "CAPCAP_CLEANUP_CHAT") {
      (async () => {
        try {
          if (window.location.hostname.includes("gemini.google.com")) {
            await Gemini.cleanupChat();
          }
          sendResponse({ success: true });
        } catch (e) {
          sendResponse({ success: false, error: e.message });
        }
      })();
      return true;
    }

    if (request.action === "CAPCAP_TRANSLATE") {
      abortRequested = false;
      const isChatGPT =
        request.platform === "chatgpt" ||
        window.location.hostname.includes("chatgpt.com") ||
        window.location.hostname.includes("chat.openai.com");
      const platform = isChatGPT ? "chatgpt" : "gemini";

      (async () => {
        try {
          let resultText = "";
          if (isChatGPT) {
            resultText = await ChatGPT.typeAndSend(request.prompt, platform);
          } else {
            resultText = await Gemini.typeAndSend(request.prompt, platform, request.temporary_chat !== false);
          }

          sendResponse({ success: true, text: resultText });
        } catch (err) {
          console.error("[CapCap Content] Translation error:", err);
          reportProgress("error", {
            platform,
            message: err.message || String(err)
          });
          sendResponse({ success: false, error: err.message || String(err) });
        }
      })();
      return true; // Keep asynchronous sendResponse channel open
    }
  });
})();
