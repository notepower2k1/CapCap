const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup({ readyAt = 0, accept = true, hidden = false, disabled = false } = {}) {
  let now = 0;
  let users = 0;
  let clicks = 0;
  let intervalCallback = null;
  const button = {
    offsetWidth: hidden ? 0 : 20, offsetHeight: 0, disabled,
    getClientRects: () => [], getAttribute: () => null,
    click() { clicks++; if (accept) users++; }
  };
  const form = {
    querySelectorAll(selector) {
      // Exercise the ID selector, without relying on English aria labels.
      return now >= readyAt && selector === 'button#composer-submit-button' ? [button] : [];
    }
  };
  const composer = {
    closest: () => form, focus() {}, isContentEditable: true,
    innerHTML: '', innerText: '', dispatchEvent() {}
  };
  const operations = [];
  const document = {
    querySelectorAll(selector) {
      return selector.includes('role="user"') ? Array(users).fill({}) : [];
    },
    querySelector: () => null,
    execCommand(command, _, text) {
      operations.push(command);
      if (command === 'insertText') composer.innerText = text;
    }
  };
  const context = vm.createContext({
    window: { location: { href: 'https://chatgpt.com/' } }, document,
    chrome: { runtime: { onMessage: { addListener() {} } } },
    console: { log() {} }, Date: { now: () => now },
    setTimeout(fn, ms) { now += ms; fn(); },
    setInterval(fn) { intervalCallback = fn; return 1; },
    clearInterval() { intervalCallback = null; },
  });
  const source = fs.readFileSync(path.join(__dirname, '../browser_extension/content.js'), 'utf8');
  vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.bridgeTest = { ChatGPT, insertPromptText, waitForReplyComplete }; })();'), context);
  return {
    ...context.bridgeTest, composer, document, operations, clicks: () => clicks,
    tick(ms) { now += ms; if (intervalCallback) intervalCallback(); }
  };
}

test('waits for a delayed ChatGPT send button and confirms submission', async () => {
  const env = setup({ readyAt: 1200 });
  await env.ChatGPT.submitPrompt(env.composer);
  assert.equal(env.clicks(), 1);
});

test('does not claim success or click twice when the page ignores Send', async () => {
  const env = setup({ accept: false });
  await assert.rejects(env.ChatGPT.submitPrompt(env.composer), /did not accept/);
  assert.equal(env.clicks(), 1);
});

for (const state of [{ hidden: true }, { disabled: true }]) {
  test(`rejects an unavailable send button: ${JSON.stringify(state)}`, async () => {
    const env = setup(state);
    await assert.rejects(env.ChatGPT.submitPrompt(env.composer), /unavailable or disabled/);
    assert.equal(env.clicks(), 0);
  });
}

test('inserts ChatGPT text through the editor editing operation', async () => {
  const env = setup();
  await env.insertPromptText(env.composer, 'line one\nline two', true);
  assert.ok(env.operations.includes('insertText'));
  assert.equal(env.composer.innerText, 'line one\nline two');
});

test('reads a new reply when the message container count stays unchanged', async () => {
  const env = setup();
  const reply = env.waitForReplyComplete({
    getCount: () => 1, initialCount: 1, initialText: 'Old reply',
    getLatestText: () => 'Character A calls B anh.', isGenerating: () => false, isComplete: () => true
  });
  env.tick(1600);
  assert.equal(await reply, 'Character A calls B anh.');
});

test('does not mistake the previous reply for new character analysis', async () => {
  const env = setup();
  const reply = env.waitForReplyComplete({
    getCount: () => 1, initialCount: 1, initialText: 'Old reply',
    getLatestText: () => 'Old reply', isGenerating: () => false, isComplete: () => true
  });
  env.tick(1600);
  env.tick(600001);
  await assert.rejects(reply, /Timeout 600s/);
});

test('waits until generation stops before returning character analysis', async () => {
  const env = setup();
  let generating = true;
  let settled = false;
  const reply = env.waitForReplyComplete({
    getCount: () => 1, initialCount: 0,
    getLatestText: () => 'Character rules', isGenerating: () => generating, isComplete: () => true
  });
  reply.then(() => { settled = true; });
  env.tick(2000);
  await Promise.resolve();
  assert.equal(settled, false);
  generating = false;
  env.tick(500);
  assert.equal(await reply, 'Character rules');
});

test('finds assistant replies using the alternative turn selector', () => {
  const env = setup();
  const messages = [{}];
  env.document.querySelectorAll = selector => selector.includes('[data-turn="assistant"]') ? messages : [];
  assert.deepEqual(Array.from(env.ChatGPT.getAssistantMessages()), messages);
});

test('reports DOM extraction exceptions instead of waiting forever', async () => {
  const env = setup();
  await assert.rejects(env.waitForReplyComplete({
    getCount: () => 1, initialCount: 0,
    getLatestText() { throw new Error('detached response'); },
    isGenerating: () => false
  }), /Cannot read AI reply: detached response/);
});

test('a long streaming pause never accepts output without completion UI', async () => {
  const env = setup();
  let complete = false;
  let text = '1. Partial';
  let settled = false;
  const reply = env.waitForReplyComplete({
    getCount: () => 1, initialCount: 0, getLatestText: () => text,
    isGenerating: () => false, isComplete: () => complete
  });
  reply.then(() => { settled = true; });
  env.tick(200000);
  await Promise.resolve();
  assert.equal(settled, false);
  text = '1. Complete\n2. Second line';
  complete = true;
  env.tick(1);
  assert.equal(await reply, text);
});

test('missing completion UI times out instead of returning partial subtitles', async () => {
  const env = setup();
  const reply = env.waitForReplyComplete({
    getCount: () => 1, initialCount: 0, getLatestText: () => '1. Partial',
    isGenerating: () => false
  });
  env.tick(600001);
  await assert.rejects(reply, /Timeout 600s/);
});
