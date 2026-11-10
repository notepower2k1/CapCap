const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup({ mode = 'window', holdReplies = false, queue = [], disconnectedFirstTab = false, failNewChat = false } = {}) {
  const tabs = new Map();
  const sentTo = [];
  const closedWindows = [];
  const closedTabs = [];
  const replies = new Map();
  const results = [];
  const aborted = [];
  const freshChats = [];
  const stored = {};
  let messageListener;
  let created = 0;
  const event = () => ({ addListener() {}, removeListener() {} });
  const context = vm.createContext({
    URL, AbortController, console: { log() {}, warn() {}, error() {} },
    setTimeout(fn, ms) { if (ms <= 30000) queueMicrotask(fn); return 1; }, clearTimeout() {},
    fetch: async (url, options) => {
      if (url.endsWith('/result')) results.push(JSON.parse(options.body));
      return { ok: true, json: async () => {
        const task = queue.shift();
        return { has_task: !!task, task };
      } };
    },
    chrome: {
      storage: { local: {
        get: async () => ({ capcap_run_mode: mode, capcap_run_mode_version: 3 }),
        set: async data => Object.assign(stored, data)
      } },
      tabs: {
        onRemoved: event(),
        onUpdated: { addListener(fn) { queueMicrotask(() => fn(created, { status: 'complete' }, tabs.get(created))); }, removeListener() {} },
        get: async id => { if (!tabs.has(id)) throw Error('No tab'); return tabs.get(id); },
        query: async () => [...tabs.values()],
        update: async (id, changes) => { Object.assign(tabs.get(id), changes); return tabs.get(id); },
        create: async ({ url }) => {
          const id = ++created;
          const tab = { id, windowId: 100, url, status: 'complete' };
          tabs.set(id, tab);
          return tab;
        },
        remove: async id => { closedTabs.push(id); tabs.delete(id); },
        sendMessage: async (id, message) => {
          if (message.action === 'CAPCAP_NEW_CHAT') {
            freshChats.push(id);
            return { success: !failNewChat, error: failNewChat ? 'Cannot verify fresh chat' : '' };
          }
          if (message.action === 'CAPCAP_ABORT') aborted.push(id);
          if (message.action === 'CAPCAP_TRANSLATE') {
            if (disconnectedFirstTab && id === 1) throw Error('Receiving end does not exist');
            sentTo.push(id);
            if (holdReplies) return new Promise(resolve => replies.set(id, resolve));
          }
          return { success: true, text: 'AI response' };
        }
      },
      scripting: { executeScript: async options => {
        if (options.files) throw Error('Receiving end does not exist');
        return [{ result: { reply_chars: 30, alerts: [] } }];
      } },
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
      runtime: { onInstalled: event(), onStartup: event(), onMessage: { addListener(fn) { messageListener = fn; } } }
    }
  });
  const source = fs.readFileSync(path.join(__dirname, '../background.js'), 'utf8');
  vm.runInContext(source.replace(/startPolling\(\);\s*$/, 'globalThis.bridgeTest = { handleTask, executeTask, pollCycle, activeTasks, handleCanceledIds };'), context);
  const task = (id, operation, platform = 'chatgpt') => context.bridgeTest.handleTask({
    id, type: 'translate', session_id: id, operation, platform, temporary_chat: true, prompt: 'Test'
  });
  return {
    task, tabs, sentTo, closedWindows, closedTabs, replies, results, aborted, stored, freshChats,
    created: () => created, ...context.bridgeTest,
    progress: id => messageListener({ type: 'CAPCAP_PROGRESS', phase: 'generating', chars: 42 }, { tab: { id } }, () => {})
  };
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
  assert.deepEqual([...env.sentTo].sort(), [1, 2]);
});

test('analysis tabs are not transferred across platforms', async () => {
  const env = setup();
  await env.task('analysis', 'context', 'gemini');
  await env.task('translation', 'batch', 'chatgpt');
  assert.equal(env.created(), 2);
  assert.deepEqual([...env.sentTo].sort(), [1, 2]);
});

const flush = async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); };
const batchTask = (id, platform = 'chatgpt') => ({
  id, type: 'translate', session_id: `session-${id}`, operation: 'batch',
  isolated: true, platform, temporary_chat: true, prompt: 'Test'
});

for (const platform of ['chatgpt', 'gemini']) {
  for (const mode of ['window', 'foreground']) {
    test(`${platform}/${mode}: parallel replies remain isolated and both sessions close`, async () => {
      const env = setup({ mode, holdReplies: true });
      // An existing user tab must not be used or closed by isolated workers.
      env.tabs.set(99, { id: 99, windowId: 100, url: platform === 'chatgpt' ? 'https://chatgpt.com/' : 'https://gemini.google.com/app' });
      const first = env.executeTask(batchTask('first', platform));
      const second = env.executeTask(batchTask('second', platform));
      await flush();
      assert.deepEqual([...env.sentTo].sort(), [1, 2]);
      env.progress(1);
      assert.equal(env.stored.task_progress.task_id, 'first');
      env.progress(2);
      assert.equal(env.stored.task_progress.task_id, 'second');
      env.progress(99);
      assert.equal(env.stored.task_progress.task_id, 'second');
      env.replies.get(2)({ success: true, text: 'second answer' });
      await second;
      assert.equal(env.activeTasks.size, 1);
      env.replies.get(1)({ success: true, text: 'first answer' });
      await first;
      assert.deepEqual(env.results.map(r => [r.task_id, r.text]), [['second', 'second answer'], ['first', 'first answer']]);
      assert.equal(env.tabs.size, 1);
      assert.ok(env.tabs.has(99));
      assert.ok(env.results.filter(row => row.task_id.startsWith('cleanup-')).every(row => row.success));
      assert.deepEqual(mode === 'window' ? env.closedWindows : env.closedTabs, [2, 1]);
    });
  }
}

test('polling starts at most four tasks and fills a freed slot', async () => {
  const queue = ['a', 'b', 'c', 'd', 'e'].map(id => batchTask(id));
  const env = setup({ holdReplies: true, queue });
  for (let i = 0; i < 4; i++) await env.pollCycle();
  await flush();
  await env.pollCycle();
  assert.equal(env.activeTasks.size, 4);
  assert.equal(queue.length, 1);
  assert.equal(env.created(), 4);
  env.replies.get(2)({ success: true, text: 'b answer' });
  await flush();
  await env.pollCycle();
  await flush();
  assert.equal(env.activeTasks.size, 4);
  assert.equal(queue.length, 0);
  assert.deepEqual([...env.sentTo].sort(), [1, 2, 3, 4, 5]);
  env.replies.get(1)({ success: true, text: 'a answer' });
  env.replies.get(3)({ success: true, text: 'c answer' });
  env.replies.get(4)({ success: true, text: 'd answer' });
  env.replies.get(5)({ success: true, text: 'e answer' });
  await flush();
  assert.equal(env.activeTasks.size, 0);
});

test('canceling one worker aborts its own tab and ignores a late answer', async () => {
  const env = setup({ holdReplies: true });
  const first = env.executeTask(batchTask('first'));
  const second = env.executeTask(batchTask('second'));
  await flush();
  env.handleCanceledIds(['first']);
  await first;
  assert.deepEqual(env.aborted, [1]);
  assert.equal(env.activeTasks.size, 1);
  assert.ok(env.tabs.has(2));
  env.replies.get(1)({ success: true, text: 'late answer' });
  env.replies.get(2)({ success: true, text: 'second answer' });
  await second;
  await flush();
  assert.deepEqual(env.results.map(r => [r.task_id, r.success]), [['first', false], ['second', true]]);
  assert.equal(env.tabs.size, 0);
});

test('a canceled worker never submits even when tab creation finishes later', async () => {
  const env = setup({ holdReplies: true });
  const task = env.executeTask(batchTask('first'));
  env.handleCanceledIds(['first']);
  await task;
  await flush();
  assert.deepEqual(env.sentTo, []);
  assert.equal(env.tabs.size, 0);
  assert.equal(env.results.length, 1);
  assert.equal(env.results[0].success, false);
});

test('only one parallel worker can claim the dedicated analysis conversation', async () => {
  const env = setup();
  await env.task('analysis', 'context');
  await Promise.all([env.handleTask(batchTask('first')), env.handleTask(batchTask('second'))]);
  assert.equal(env.created(), 2);
  assert.deepEqual(env.sentTo, [1, 1, 2]);
  assert.equal(env.tabs.size, 0);
});

test('replacing a disconnected isolated tab also closes its old window', async () => {
  const env = setup({ disconnectedFirstTab: true });
  await env.executeTask(batchTask('first'));
  assert.equal(env.created(), 2);
  assert.deepEqual(env.closedWindows, [1, 2]);
  assert.equal(env.tabs.size, 0);
  assert.equal(env.results.length, 1);
  assert.equal(env.results[0].success, true);
});

test('diagnostics inspect assigned tabs and leave unrelated tabs alone', async () => {
  const env = setup({ holdReplies: true });
  env.tabs.set(99, { id: 99, url: 'https://chatgpt.com/' });
  const running = env.executeTask(batchTask('first'));
  await flush();
  await env.executeTask({ id: 'diagnostic', type: 'diagnose_tasks' });
  const snapshot = JSON.parse(env.results[0].text);
  assert.equal(snapshot.length, 1);
  assert.equal(snapshot[0].task_id, 'first');
  assert.equal(snapshot[0].tab_id, 1);
  assert.equal(snapshot[0].reply_chars, 30);
  assert.deepEqual(env.sentTo, [1]);
  assert.ok(env.tabs.has(99));
  env.replies.get(1)({ success: true, text: 'answer' });
  await running;
});

for (const platform of ['chatgpt', 'gemini']) {
  for (const mode of ['window', 'foreground']) {
    test(`${platform}/${mode}: four workers reuse windows over three waves with fresh chats`, async () => {
      const env = setup({ mode, holdReplies: true });
      env.tabs.set(99, { id: 99, url: 'https://chatgpt.com/' });
      for (let wave = 0; wave < 3; wave++) {
        const jobs = Array.from({length: 4}, (_, i) => env.executeTask({
          ...batchTask(`wave-${wave}-${i}`, platform), session_id: `worker-${i}`,
          keep_open: true, new_chat: true
        }));
        await flush();
        assert.equal(env.created(), 4);
        assert.equal(env.activeTasks.size, 4);
        // Finish out of order. Each subsequent wave must use these same tabs.
        for (const id of [4, 2, 1, 3]) env.replies.get(id)({ success: true, text: 'answer' });
        await Promise.all(jobs);
        assert.equal(env.tabs.size, 5);
      }
      assert.equal(env.freshChats.length, 12);
      assert.equal(env.sentTo.length, 12);
      assert.equal(env.closedWindows.length + env.closedTabs.length, 0);
      for (let i = 0; i < 4; i++) await env.executeTask({
        id: `cleanup-${i}`, type: 'cleanup_session', session_id: `worker-${i}`, close_owned: true
      });
      assert.equal(env.tabs.size, 1);
      assert.ok(env.tabs.has(99));
    });
  }
}

test('a failed fresh-chat verification never sends the batch into old history', async () => {
  const env = setup({ failNewChat: true });
  await env.executeTask({ ...batchTask('first'), keep_open: true, new_chat: true });
  assert.equal(env.created(), 1);
  assert.equal(env.freshChats.length, 2); // UI attempt and same-tab navigation fallback.
  assert.deepEqual(env.sentTo, []);
  assert.equal(env.results[0].success, false);
  assert.equal(env.tabs.size, 0);
});

test('session cleanup cancels a busy worker and prevents late reopening/submission', async () => {
  const env = setup({ holdReplies: true });
  const task = { ...batchTask('first'), keep_open: true, new_chat: true };
  const running = env.executeTask(task);
  await flush();
  await env.executeTask({ id: 'cleanup', type: 'cleanup_session', session_id: task.session_id, close_owned: true });
  await running;
  env.replies.get(1)({ success: true, text: 'late' });
  await flush();
  assert.equal(env.tabs.size, 0);
  assert.equal(env.results.filter(r => r.task_id === 'first' && r.success).length, 0);
  await env.executeTask({ ...task, id: 'late-job' });
  assert.equal(env.tabs.size, 0);
  assert.equal(env.sentTo.length, 1);
});
