const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

function load(html) {
  const dom = new JSDOM(html, { url: 'https://chatgpt.com/', runScripts: 'outside-only' });
  dom.window.chrome = { runtime: { onMessage: { addListener() {} } } };
  const source = fs.readFileSync(path.join(__dirname, '../content.js'), 'utf8');
  dom.window.eval(source.replace(/\}\)\(\);\s*$/, 'window.bridgeTest = { ChatGPT, Gemini, waitForReplyComplete, startFreshChat }; })();'));
  return { dom, chat: dom.window.bridgeTest.ChatGPT, gemini: dom.window.bridgeTest.Gemini,
    waitForReplyComplete: dom.window.bridgeTest.waitForReplyComplete, startFreshChat: dom.window.bridgeTest.startFreshChat };
}

test('latest agent turn is read even when older messages have role attributes', () => {
  const { dom, chat } = load(`
    <article data-testid="conversation-turn-1"><div data-message-author-role="assistant"><div class="markdown">Old rules</div></div></article>
    <article data-turn="user">Translate these lines</article>
    <div class="agent-turn"><div class="markdown"></div><div class="markdown"><ol><li>Xin chào</li><li>Tạm biệt</li></ol></div><button>Copy</button></div>
  `);
  try {
    const messages = chat.getAssistantMessages();
    assert.equal(messages.length, 2);
    assert.equal(chat.getAssistantText(messages.at(-1)), '1. Xin chào\n2. Tạm biệt');
  } finally { dom.window.close(); }
});

test('all markdown blocks are retained without duplicating nested prose', () => {
  const { dom, chat } = load(`<article data-turn="assistant"><div class="prose"><div class="markdown"><p>Character A</p></div></div><div class="markdown"><p>A calls B anh.</p></div></article>`);
  try {
    const text = chat.getAssistantText(chat.getAssistantMessages()[0]);
    assert.equal(text, 'Character A\n\nA calls B anh.');
  } finally { dom.window.close(); }
});

test('reads the DIL renderer observed on the live ChatGPT tab', () => {
  const { dom, chat } = load(`
    <div data-markdown-text-style="user-message">Analyze the dialogue</div>
    <div data-markdown-text-style="assistant-message">
      <div data-dil-source-message-id="response-1" data-markdown-copy="contents">
        <div data-dil-response data-dil-message-id="response-1">
          <h3 data-d-component="title"><span data-d-stream-run>Hồ sơ nhân vật</span></h3>
          <p data-d-component="text"><span data-d-stream-run>A gọi B là anh.</span></p>
        </div>
      </div>
    </div>
    <div data-markdown-text-style="assistant-message"><div data-dil-response>
      <ol start="1"><li><p>Xin chào.</p></li><li><p>Tạm biệt.</p></li></ol>
    </div></div>
  `);
  try {
    const messages = chat.getAssistantMessages();
    assert.equal(messages.length, 2);
    assert.equal(chat.getAssistantText(messages[0]), 'Hồ sơ nhân vật\n\nA gọi B là anh.');
    assert.equal(chat.getAssistantText(messages[1]), '1. Xin chào.\n2. Tạm biệt.');
  } finally { dom.window.close(); }
});

test('ChatGPT finds completion controls outside the DIL message selection wrapper', () => {
  const { dom, chat } = load(`<main><div data-markdown-text-style="user-message">Translate</div>
    <section><div data-chatgpt-selection-message-id="reply-1"><div data-markdown-text-style="assistant-message">
    <div data-dil-response>1. Hello</div></div></div><div><button aria-label="Copy response"></button></div></section></main>`);
  try {
    visible(dom.window.document.querySelector('button'));
    assert.equal(chat.isReplyComplete(), true);
    const reply = dom.window.document.querySelector('[data-dil-response]');
    reply.setAttribute('data-is-streaming', 'true');
    assert.equal(chat.isReplyComplete(), false);
    reply.removeAttribute('data-is-streaming');
    const container = reply.closest('section');
    container.parentElement.setAttribute('data-talvt-turn-state', 'in_progress');
    assert.equal(chat.isReplyComplete(), false);
    container.parentElement.setAttribute('data-talvt-turn-state', 'completed');
    assert.equal(chat.isReplyComplete(), true);
  } finally { dom.window.close(); }
});

test('Gemini recognizes the stop-generation label observed in Vietnamese UI', () => {
  const { dom, gemini } = load('<button aria-label="Ngừng tạo câu trả lời"></button>');
  try {
    visible(dom.window.document.querySelector('button'));
    assert.equal(gemini.isGenerating(), true);
  } finally { dom.window.close(); }
});

test('Gemini enables the localized temporary chat control and verifies it', async () => {
  const { dom, gemini } = load('<button aria-label="Cuộc trò chuyện tạm thời" aria-pressed="false"></button><main></main>');
  try {
    const button = dom.window.document.querySelector('button');
    Object.defineProperty(button, 'offsetWidth', { value: 20 });
    let clicks = 0;
    button.addEventListener('click', () => { clicks++; button.setAttribute('aria-pressed', 'true'); });
    assert.equal(gemini.isTemporaryChat(), false);
    await gemini.ensureTemporaryChat();
    await gemini.ensureTemporaryChat();
    assert.equal(clicks, 1);
  } finally { dom.window.close(); }
});

test('Gemini does not treat a sidebar entry as an active temporary chat', () => {
  const { dom, gemini } = load('<aside><button aria-label="Temporary chat"><i data-mat-icon-name="gemini_chat_temp"></i></button></aside><main>Normal chat</main>');
  try { assert.equal(gemini.isTemporaryChat(), false); }
  finally { dom.window.close(); }
});

test('Gemini recognizes the active state observed in the live DOM', async () => {
  const { dom, gemini } = load('<main><chat-window class="is-temporary-chat"></chat-window><gem-icon-button class="temp-chat-button temp-chat-on"><button aria-label="Cuộc trò chuyện tạm thời"></button></gem-icon-button></main>');
  try { await gemini.ensureTemporaryChat(); }
  finally { dom.window.close(); }
});

test('Gemini entry icon inside main does not falsely confirm temporary mode', () => {
  const { dom, gemini } = load('<main><chat-window></chat-window><gem-icon-button class="temp-chat-button"><button aria-label="Cuộc trò chuyện tạm thời"><mat-icon data-mat-icon-name="gemini_chat_temp"></mat-icon></button></gem-icon-button></main>');
  try { assert.equal(gemini.isTemporaryChat(), false); }
  finally { dom.window.close(); }
});

test('Gemini switches real active-state classes on before the first prompt', async () => {
  const { dom, gemini } = load('<main><chat-window></chat-window><gem-icon-button class="temp-chat-button"><button aria-label="Cuộc trò chuyện tạm thời"><mat-icon data-mat-icon-name="gemini_chat_temp"></mat-icon></button></gem-icon-button></main>');
  try {
    const button = dom.window.document.querySelector('button');
    Object.defineProperty(button, 'offsetWidth', { value: 20 });
    button.addEventListener('click', () => {
      dom.window.document.querySelector('chat-window').classList.add('is-temporary-chat');
      button.parentElement.classList.add('temp-chat-on');
    });
    await gemini.ensureTemporaryChat();
    assert.equal(gemini.isTemporaryChat(), true);
  } finally { dom.window.close(); }
});

test('Gemini keeps the same temporary conversation through six batches', async () => {
  const { dom, gemini } = load('<main><chat-window class="is-temporary-chat"><h1 class="temporary-chat-card-container">Temporary chat</h1></chat-window><gem-icon-button class="temp-chat-button temp-chat-on"><button aria-label="Cuộc trò chuyện tạm thời"></button></gem-icon-button></main>');
  try {
    let clicks = 0;
    dom.window.document.querySelector('button').addEventListener('click', () => { clicks++; });
    for (let batch = 0; batch < 6; batch++) {
      await gemini.ensureTemporaryChat();
      const chat = dom.window.document.querySelector('chat-window');
      chat.innerHTML = '<model-response>Batch ' + (batch + 1) + ' finished</model-response>';
    }
    assert.equal(clicks, 0);
  } finally { dom.window.close(); }
});

test('Gemini refuses to send if temporary mode cannot be verified', async () => {
  const { dom, gemini } = load('<main><div contenteditable="true">Existing draft</div></main>');
  try {
    const original = gemini.ensureTemporaryChat.bind(gemini);
    gemini.ensureTemporaryChat = () => original(1);
    await assert.rejects(gemini.typeAndSend('Private prompt', 'gemini', true), /No prompt was sent/);
    assert.equal(dom.window.document.querySelector('[contenteditable]').textContent, 'Existing draft');
  } finally { dom.window.close(); }
});

function visible(button) {
  Object.defineProperty(button, 'offsetWidth', { value: 20 });
}

for (const platform of ['chatgpt', 'gemini']) {
  test(`${platform}: old answer actions cannot finish the current streaming answer`, () => {
    const old = platform === 'chatgpt'
      ? '<article data-turn="assistant"><div data-markdown-text-style="assistant-message">Old</div><button aria-label="Copy response"></button></article>'
      : '<model-response><message-content>Old</message-content><button aria-label="Copy response"></button></model-response>';
    const current = platform === 'chatgpt'
      ? '<article data-turn="assistant" id="current"><div data-markdown-text-style="assistant-message">Partial</div><button hidden aria-label="Copy response"></button></article>'
      : '<model-response id="current"><message-content>Partial</message-content><button hidden aria-label="Copy response"></button></model-response>';
    const { dom, chat, gemini } = load(old + current);
    const api = platform === 'chatgpt' ? chat : gemini;
    try {
      const turn = dom.window.document.querySelector('#current');
      dom.window.document.querySelectorAll('button').forEach(visible);
      assert.equal(api.isReplyComplete(), false);
      turn.querySelector('button').hidden = false;
      turn.setAttribute('aria-busy', 'true');
      assert.equal(api.isReplyComplete(), false);
      turn.setAttribute('aria-busy', 'false');
      assert.equal(api.isReplyComplete(), true);
    } finally { dom.window.close(); }
  });
}

test('ChatGPT confirms submission through the new user-message DOM', async () => {
  const { dom, chat } = load('<form><div id="prompt-textarea" contenteditable="true">Prompt</div><button id="composer-submit-button"></button></form>');
  try {
    const button = dom.window.document.querySelector('button');
    visible(button);
    button.addEventListener('click', event => {
      event.preventDefault();
      const user = dom.window.document.createElement('div');
      user.setAttribute('data-markdown-text-style', 'user-message');
      user.textContent = 'Prompt';
      dom.window.document.body.append(user);
    });
    await chat.submitPrompt(dom.window.document.querySelector('#prompt-textarea'));
  } finally { dom.window.close(); }
});

test('Gemini reads all content from the latest response turn, not a nested last block', () => {
  const { dom, gemini } = load('<model-response><message-content><p>Old</p></message-content></model-response><model-response><message-content><p>1. First</p></message-content><message-content><p>2. Second</p></message-content></model-response>');
  try {
    assert.equal(gemini.getResponses().length, 2);
    assert.equal(gemini.getResponseText(gemini.latestReply()), '1. First\n\n2. Second');
  } finally { dom.window.close(); }
});

for (const platform of ['chatgpt', 'gemini']) {
  test(`${platform}: DOM completion attributes finish the wait without a stability delay`, async () => {
    const html = platform === 'chatgpt'
      ? '<article data-turn="assistant" aria-busy="true"><div data-markdown-text-style="assistant-message"><p>1. Answer</p></div><button hidden aria-label="Copy response"></button></article>'
      : '<model-response aria-busy="true"><message-content><p>1. Answer</p></message-content><button hidden aria-label="Copy response"></button></model-response>';
    const { dom, chat, gemini, waitForReplyComplete } = load(html);
    const api = platform === 'chatgpt' ? chat : gemini;
    try {
      const turn = dom.window.document.querySelector('[aria-busy]');
      const button = turn.querySelector('button');
      visible(button);
      let settled = false;
      const result = waitForReplyComplete({
        getCount: () => 1, initialCount: 0,
        getLatestNode: () => api.latestReply(),
        getLatestText: () => platform === 'chatgpt'
          ? chat.getAssistantText(api.latestReply()) : gemini.getResponseText(api.latestReply()),
        isComplete: () => api.isReplyComplete(), isGenerating: () => api.isGenerating()
      });
      result.then(() => { settled = true; });
      button.hidden = false;
      await new Promise(resolve => dom.window.queueMicrotask(resolve));
      assert.equal(settled, false);
      turn.setAttribute('aria-busy', 'false');
      // Only flush DOM mutation observers/microtasks, without advancing a timer.
      await new Promise(resolve => dom.window.queueMicrotask(resolve));
      assert.equal(await result, '1. Answer');
    } finally { dom.window.close(); }
  });
}

for (const platform of ['chatgpt', 'gemini']) {
  test(`${platform}: fresh-chat setup clears the old conversation before accepting a new batch`, async () => {
    const old = platform === 'chatgpt'
      ? '<div data-markdown-text-style="user-message">Old prompt</div><div data-markdown-text-style="assistant-message">Old reply</div>'
      : '<user-query>Old prompt</user-query><model-response>Old reply</model-response>';
    const composer = platform === 'chatgpt' ? '<div id="prompt-textarea" contenteditable="true"></div>' : '<rich-textarea><div class="ql-editor" contenteditable="true"></div></rich-textarea>';
    const { dom, startFreshChat } = load(`<main>${old}</main><button aria-label="New chat"></button>${composer}`);
    try {
      if (platform === 'chatgpt') dom.window.history.replaceState({}, '', '/?temporary-chat=true');
      dom.window.document.querySelectorAll('button, [contenteditable]').forEach(visible);
      dom.window.document.querySelector('button').addEventListener('click', () => {
        dom.window.document.querySelector('main').innerHTML = platform === 'gemini' ? '<chat-window class="is-temporary-chat"></chat-window>' : '';
      });
      await startFreshChat(platform, true);
      assert.equal(dom.window.document.querySelector('main').textContent, '');
    } finally { dom.window.close(); }
  });
}

test('fresh-chat setup refuses an unchanged old conversation', async () => {
  const { dom, startFreshChat } = load('<main><div data-markdown-text-style="assistant-message">Old answer</div></main><button aria-label="New chat"></button><div id="prompt-textarea" contenteditable="true"></div>');
  try {
    dom.window.document.querySelectorAll('button, [contenteditable]').forEach(visible);
    let now = 0;
    dom.window.Date.now = () => (now += 1000);
    dom.window.setTimeout = fn => { queueMicrotask(fn); return 1; };
    await assert.rejects(startFreshChat('chatgpt', false), /Cannot verify empty chat/);
  } finally { dom.window.close(); }
});
