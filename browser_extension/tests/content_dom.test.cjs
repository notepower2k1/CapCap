const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

function load(html) {
  const dom = new JSDOM(html, { url: 'https://chatgpt.com/', runScripts: 'outside-only' });
  dom.window.chrome = { runtime: { onMessage: { addListener() {} } } };
  const source = fs.readFileSync(path.join(__dirname, '../content.js'), 'utf8');
  dom.window.eval(source.replace(/\}\)\(\);\s*$/, 'window.bridgeTest = { ChatGPT, Gemini }; })();'));
  return { dom, chat: dom.window.bridgeTest.ChatGPT, gemini: dom.window.bridgeTest.Gemini };
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

test('Gemini recognizes an active-chat indicator', async () => {
  const { dom, gemini } = load('<main><i data-mat-icon-name="gemini_chat_temp"></i></main>');
  try { await gemini.ensureTemporaryChat(); }
  finally { dom.window.close(); }
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
