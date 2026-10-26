'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { ReportMonitor } = require('../frontend/reports.js');

function response(userId, devices = []) {
  return { ok: true, status: 200, json: async () => ({ data: { user_id: userId, devices } }) };
}

function monitorWith(fetcher) {
  const rendered = [], expired = [], statuses = [];
  let cleared = 0;
  const monitor = new ReportMonitor({
    fetcher, render: (data) => rendered.push(data), clear: () => { cleared += 1; },
    status: (message) => statuses.push(message), sessionExpired: (message) => expired.push(message),
  });
  return { monitor, rendered, expired, statuses, cleared: () => cleared };
}

test('a delayed response from user A cannot appear after switching to user B', async (context) => {
  let finishA, firstSignal;
  let calls = 0;
  const state = monitorWith((_path, options) => {
    calls += 1;
    assert.equal(options.credentials, 'include');
    assert.equal(options.cache, 'no-store');
    if (calls === 1) {
      firstSignal = options.signal;
      return new Promise((resolve) => { finishA = resolve; });
    }
    return Promise.resolve(response(2, [{ device_id: 9 }]));
  });
  context.after(() => state.monitor.stop());
  const pendingA = state.monitor.start(1);
  await state.monitor.start(2);
  assert.equal(firstSignal.aborted, true);
  finishA(response(1, [{ device_id: 7 }, { device_id: 8 }]));
  await pendingA;
  assert.deepEqual(state.rendered.map((data) => data.user_id), [2]);
  assert.deepEqual(state.rendered[0].devices, [{ device_id: 9 }]);
});

test('logout clears reports and a pending response is discarded', async () => {
  let finish;
  const state = monitorWith(() => new Promise((resolve) => { finish = resolve; }));
  const pending = state.monitor.start(1);
  state.monitor.stop();
  finish(response(1, [{ device_id: 7 }]));
  await pending;
  assert.equal(state.rendered.length, 0);
  assert.equal(state.monitor.timer, null);
  assert.equal(state.cleared(), 2);
});

test('a shared cookie changing users cannot render another user under the old account', async () => {
  const state = monitorWith(async () => response(2, [{ device_id: 9 }]));
  await state.monitor.start(1);
  assert.equal(state.rendered.length, 0);
  assert.equal(state.expired.length, 1);
  assert.equal(state.monitor.userId, null);
  assert.equal(state.monitor.timer, null);
});

test('an expired session clears the page and stops polling', async () => {
  const state = monitorWith(async () => ({ status: 401, ok: false }));
  await state.monitor.start(1);
  assert.equal(state.rendered.length, 0);
  assert.equal(state.expired.length, 1);
  assert.equal(state.monitor.timer, null);
  assert.equal(state.cleared(), 2);
});

test('a temporary server error schedules a retry without rendering an error payload', async (context) => {
  const state = monitorWith(async () => ({
    status: 503, ok: false, json: async () => ({ message: '数据库暂时不可用' }),
  }));
  context.after(() => state.monitor.stop());
  await state.monitor.start(1);
  assert.equal(state.rendered.length, 0);
  assert.equal(state.expired.length, 0);
  assert.ok(state.monitor.timer);
  assert.match(state.statuses.at(-1), /稍后自动重试/);
});
