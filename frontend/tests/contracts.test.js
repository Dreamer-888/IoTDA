import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiClient, ApiError, TelemetryPoller } from '../lib/api.js';
import { summarizeSteps, validateRange, chinaDate, movingAverage, rawSummary } from '../lib/data.js';
import { DemoBackend } from '../lib/demo.js';

const response = (data, status = 200, message = '成功') => new Response(JSON.stringify({ code: status, message, data }), { status, headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'request-test' } });
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const delay = (ms) => new Promise((r) => setTimeout(r, ms));

test('HTTP calls send Cookie and unwrap data; login data remains nested', async () => {
  let request;
  const api = new ApiClient(async (url, options) => { request = { url, options }; return response({ user: { id: 1 }, authenticated: true }); });
  const data = await api.request('/auth/login', { method: 'POST', body: { username: 'demo', password: 'test-only' }, protectedRequest: false });
  assert.equal(request.url, '/api/auth/login');
  assert.equal(request.options.credentials, 'include');
  assert.equal(request.options.cache, 'no-store');
  assert.equal(request.options.headers['Content-Type'], 'application/json');
  assert.equal(JSON.parse(request.options.body).username, 'demo');
  assert.equal(data.user.id, 1);
});
test('only protected-session 401 expires the account; credential errors preserve it', async () => {
  let expired = 0;
  const api = new ApiClient(async () => response(null, 401, '密码错误'));
  api.onExpired = () => expired++;
  await assert.rejects(api.request('/auth/login', { protectedRequest: false }), { status: 401 });
  await assert.rejects(api.request('/users/1', { method: 'PATCH', credentialCheck: true }), { status: 401 });
  assert.equal(expired, 0);
  await assert.rejects(api.request('/devices'), { status: 401 });
  assert.equal(expired, 1);
});
test('account switches abort all pending reads and discard late responses even if transport ignores abort', async () => {
  const gate = deferred(); let signal;
  const api = new ApiClient(async (_, options) => { signal = options.signal; return gate.promise; });
  const request = api.request('/devices');
  api.invalidate();
  assert.equal(signal.aborted, true);
  gate.resolve(response([{ id: 7, user_id: 1 }]));
  await assert.rejects(request, { name: 'AbortError' });
  assert.equal(api.pending.size, 0);
});
test('external cancellation discards slow JSON parsing', async () => {
  const body = deferred();
  const api = new ApiClient(async () => ({ ok: true, json: () => body.promise, headers: new Headers() }));
  const controller = new AbortController();
  const request = api.request('/devices', { signal: controller.signal });
  await Promise.resolve();
  controller.abort();
  body.resolve({ data: [{ id: 7 }] });
  await assert.rejects(request, { name: 'AbortError' });
});
test('null telemetry is preserved; malformed server errors include request ID', async () => {
  assert.equal(await new ApiClient(async () => response(null)).request('/devices/7/telemetry/latest'), null);
  const api = new ApiClient(async () => new Response('not-json', { status: 503, headers: { 'X-Request-ID': 'failure-id' } }));
  await assert.rejects(api.request('/devices'), (e) => e instanceof ApiError && e.status === 503 && e.requestId === 'failure-id');
});
test('network failure on a write describes uncertain outcome and is never retried', async () => {
  let calls = 0;
  const api = new ApiClient(async () => { calls++; throw new TypeError('network'); });
  await assert.rejects(api.request('/devices', { method: 'POST', body: {} }), /提交结果尚未确认/);
  assert.equal(calls, 1);
});
test('device-switch generations prevent old telemetry rendering', async () => {
  const old = deferred(); const values = []; let oldSignal;
  const poller = new TelemetryPoller({
    interval: 10000,
    read: (id, signal) => { if (id === 7) { oldSignal = signal; return old.promise; } return Promise.resolve(id); },
    render: (value) => values.push(value), error: (e) => { throw e; },
  });
  const first = poller.start(7);
  await poller.start(8);
  assert.equal(oldSignal.aborted, true);
  old.resolve(7); await first;
  poller.stop();
  assert.deepEqual(values, [8]);
});
test('polling never overlaps and stop cancels subsequent work', async () => {
  const gate = deferred(); let calls = 0;
  const poller = new TelemetryPoller({ interval: 5, read: () => { calls++; return gate.promise; }, render: () => {}, error: () => {} });
  const first = poller.start({});
  await delay(20);
  assert.equal(calls, 1);
  poller.stop(); gate.resolve(null); await first; await delay(15);
  assert.equal(calls, 1);
});
test('history gaps remain null while recorded zero counts as a recorded day', () => {
  const result = summarizeSteps([{ steps: 3200, has_data: true }, { steps: 0, has_data: true }, { steps: null, has_data: false }]);
  assert.deepEqual(result, { total: 3200, days: 2, values: [3200, 0, null] });
});
test('date windows are inclusive, validate leap days and enforce 366 days', () => {
  assert.equal(validateRange('2024-01-01', '2024-12-31'), 366);
  assert.equal(validateRange('2026-10-14', '2026-10-14'), 1);
  assert.throws(() => validateRange('2026-10-14', '2026-10-13'), /不能晚于/);
  assert.throws(() => validateRange('2024-01-01', '2025-01-01'), /366/);
  assert.throws(() => validateRange('2026-02-29', '2026-03-01'), /有效/);
  assert.equal(chinaDate('2026-10-13T16:01:00Z'), '2026-10-14');
});
test('moving average only changes raw axes; posture and event flags remain intact', () => {
  const base = { accel_x: 0, accel_y: 0, accel_z: 0, gyro_x: 0, gyro_y: 0, gyro_z: 0, roll: 62, pitch: 1, yaw: -48, step_increment: 2, is_fall: 1, is_sedentary: 0 };
  const filtered = movingAverage([base, { ...base, accel_x: 10, roll: 70 }], 2);
  assert.equal(filtered[1].accel_x, 5);
  assert.equal(filtered[1].roll, 70);
  assert.equal(filtered[1].is_fall, 1);
  assert.equal(filtered[1].step_increment, 2);
  assert.equal(rawSummary([]).accel_x.avg, null);
});
test('demo transport supports device CRUD and conflict status without any network', async () => {
  const backend = new DemoBackend(); const api = new ApiClient(backend.fetch);
  const device = await api.request('/devices', { method: 'POST', body: { cloud_device_id: 'test-unique', cloud_product_id: 'demo', display_name: '测试设备' } });
  await assert.rejects(api.request('/devices', { method: 'POST', body: { cloud_device_id: 'test-unique' } }), { status: 409 });
  await api.request('/devices/' + device.id, { method: 'PATCH', body: { display_name: '已改名' } });
  assert.equal((await api.request('/devices')).find((d) => d.id === device.id).display_name, '已改名');
  await api.request('/devices/' + device.id, { method: 'DELETE' });
  assert.equal((await api.request('/devices')).some((d) => d.id === device.id), false);
});
test('demo credential flow rejects wrong current password and invalidates the old session on success', async () => {
  const backend = new DemoBackend(); const api = new ApiClient(backend.fetch);
  await assert.rejects(api.request('/users/1', { method: 'PATCH', body: { current_password: 'wrong', password: 'updated-password' }, credentialCheck: true }), { status: 401 });
  assert.equal((await api.request('/auth/me')).id, 1);
  await api.request('/users/1', { method: 'PATCH', body: { current_password: 'demo123456', password: 'updated-password' }, credentialCheck: true });
  await assert.rejects(api.request('/auth/me', { protectedRequest: false }), { status: 401 });
  const login = await api.request('/auth/login', { method: 'POST', body: { username: '演示用户', password: 'updated-password' }, protectedRequest: false });
  assert.equal(login.authenticated, true);
});
