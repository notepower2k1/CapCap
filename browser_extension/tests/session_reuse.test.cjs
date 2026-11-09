const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup() {
  const tabs = new Map();
  const sentTo = [];
  const closedWindows = [];
  let created = 0;
  const event = () => ({ addListener() {}, removeListener() {} });
  const context = vm.createContext({
    URL, console: { log() {}, warn() {}, error() {} },
    setTimeout(fn) { queueMicrotask(fn); return 1; }, clearTimeout() {},
    fetch: async () => ({ ok: true }),
    chrome: {
      storage: { local: {
        get: async () => ({ capcap_run_mode: 'window', capcap_run_mode_version: 3 }),
        set: async () => {}
      } },
      tabs: {
        onRemoved: event(),
        onUpdated: { addListener(fn) { queueMicrotask(() => fn(created, { status: 'complete' }, tabs.get(created))); }, removeListener() {} },
        get: async id => { if (!tabs.has(id)) throw Error('No tab'); return tabs.get(id); },
        query: async () => [...tabs.values()],
        update: async (id, changes) => { Object.assign(tabs.get(id), changes); return tabs.get(id); },
        sendMessage: async (id, message) => {
          if (message.action === 'CAPCAP_TRANSLATE') sentTo.push(id);
          return { success: true, text: 'AI response' };
        }
      },
      windows: {
        onRemoved: event(),
        create: async ({ url }) => {
          const id = ++created;
          const tab = { id, windowId: id, url, status: 'complete' };
          tabs.set(id, tab);
          return { id, tabs: [tab] };
        },
        remove: async id => { closedWindows.push(id); tabs.delete(id); }
      },
      alarms: { create() {}, onAlarm: event() },
      runtime: { onInstalled: event(), onStartup: event(), onMessage: event() }
    }
  });
  const source = fs.readFileSync(path.join(__dirname, '../background.js'), 'utf8');
  vm.runInContext(source.replace(/startPolling\(\);\s*$/, 'globalThis.bridgeTest = { handleTask };'), context);
  const task = (id, operation, platform = 'chatgpt') => context.bridgeTest.handleTask({
    id, type: 'translate', session_id: id, operation, platform, temporary_chat: true, prompt: 'Test'
  });
  return { task, tabs, sentTo, closedWindows, created: () => created, handleTask: context.bridgeTest.handleTask };
}

for (const platform of ['chatgpt', 'gemini']) {
  test(`${platform}: analysis, re-analysis and translation share one popup`, async () => {
    const env = setup();
    await env.task('analysis', 'context', platform);
    await env.task('review-again', 'context', platform);
    await env.task('translation', 'batch', platform);
    assert.equal(env.created(), 1);
    assert.deepEqual(env.sentTo, [1, 1, 1]);
    await env.handleTask({ id: 'cleanup', type: 'cleanup_session', session_id: 'translation', platform });
    assert.deepEqual(env.closedWindows, [1]);
  });
}

test('a closed analysis tab is replaced', async () => {
  const env = setup();
  await env.task('analysis', 'context');
  env.tabs.clear();
  await env.task('translation', 'batch');
  assert.equal(env.created(), 2);
  assert.deepEqual(env.sentTo, [1, 2]);
});

test('analysis tabs are not transferred across platforms', async () => {
  const env = setup();
  await env.task('analysis', 'context', 'gemini');
  await env.task('translation', 'batch', 'chatgpt');
  assert.equal(env.created(), 2);
  assert.deepEqual(env.sentTo, [1, 2]);
});
