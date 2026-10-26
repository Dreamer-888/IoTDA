import test from 'node:test';
import assert from 'node:assert/strict';
import { RealtimeClient } from '../lib/live.js';

class Socket {
  constructor() { this.sent = []; }
  send(value) { this.sent.push(JSON.parse(value)); }
  close() { this.closed = true; }
  emit(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
}

test('real-time selection sends parameters and rejects the previous device generation', () => {
  const sockets = [], received = [];
  const live = new RealtimeClient({ url: 'ws://test/api/telemetry/live', createSocket: () => { const socket = new Socket(); sockets.push(socket); return socket; }, onSample: sample => received.push(sample.device_id) });
  live.subscribe({ id: 7, seconds: 60, filter: 'none', filterWindow: 5 });
  live.start();
  const socket = sockets[0];
  socket.emit({ type: 'ready', data: { user_id: 1 } });
  const first = socket.sent.at(-1).subscription_id;
  socket.emit({ type: 'sample', subscription_id: first, data: { device_id: 7 } });
  live.subscribe({ id: 8, seconds: 300, filter: 'moving_average', filterWindow: 10 });
  const next = socket.sent.at(-1);
  assert.equal(next.device_id, 8);
  assert.equal(next.filter_window, 10);
  socket.emit({ type: 'sample', subscription_id: first, data: { device_id: 7 } });
  socket.emit({ type: 'sample', subscription_id: next.subscription_id, data: { device_id: 8 } });
  live.stop();
  assert.deepEqual(received, [7, 8]);
});

test('alerts continue when telemetry subscription is empty and are independent of selection', () => {
  const socket = new Socket(), events = [];
  const live = new RealtimeClient({ url: 'ws://test', createSocket: () => socket, onAlert: event => events.push(event.event_id) });
  live.start(); socket.emit({ type: 'ready', data: {} });
  assert.equal(socket.sent[0].device_id, null);
  socket.emit({ type: 'alert', data: { event_id: 'first', device_id: 7 } });
  live.stop();
  assert.deepEqual(events, ['first']);
});

test('account invalidation discards late frames and disables reconnect', () => {
  const socket = new Socket(), events = [];
  const live = new RealtimeClient({ url: 'ws://test', createSocket: () => socket, onAlert: event => events.push(event) });
  live.start();
  const late = socket.onmessage;
  live.stop();
  late({ data: JSON.stringify({ type: 'alert', data: { event_id: 'old-account' } }) });
  assert.equal(live.running, false);
  assert.deepEqual(events, []);
});

test('session-revocation close expires login rather than scheduling retry', () => {
  const socket = new Socket(); let expired = 0;
  const live = new RealtimeClient({ url: 'ws://test', createSocket: () => socket, onExpired: () => expired++ });
  live.start(); socket.emit({ type: 'ready', data: {} });
  socket.onclose({ code: 4401 });
  assert.equal(expired, 1);
  assert.equal(live.running, false);
  assert.equal(live.retry, undefined);
});

test('failed connection schedules one retry and stop cancels it', () => {
  const socket = new Socket(), states = [];
  const live = new RealtimeClient({ url: 'ws://test', createSocket: () => socket, onState: state => states.push(state) });
  live.start();
  socket.onclose({ code: 1006 });
  assert.deepEqual(states, ['connecting', 'disconnected']);
  assert.ok(live.retry);
  live.stop();
});
