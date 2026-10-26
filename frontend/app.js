import './assets/fonts/fonts.css';
import './workflow.css';
import './styles.css';
import '@phosphor-icons/web/regular';
import { ApiClient, TelemetryPoller } from './lib/api.js';
import { RealtimeClient } from './lib/live.js';
import { DemoBackend } from './lib/demo.js';
import { chinaDate, chinaTime, addDays, number, finite, validateRange, summarizeSteps, movingAverage, rawSummary } from './lib/data.js';
import { Charts } from './lib/charts.js';
import { SensorScene } from './lib/sensor-scene.js';

const $ = (id) => document.getElementById(id);
for (const icon of document.querySelectorAll('i.ph')) icon.setAttribute('aria-hidden', 'true');
const state = { user: null, devices: [], selected: null, page: 'monitor', axis: 'accel', seconds: 60, filter: 'none', filterWindow: 5, snapshot: null, points: [], demo: null };
const api = new ApiClient();
const charts = new Charts($('telemetry-chart'), $('history-chart'));
let sensor;
let toastTimer;
let historyController;
let historyVersion = 0;
let deviceVersion = 0;
let editingDevice = null;
let unbindingDevice = null;
let liveSamples = [];
let alertCursor = 0;
let alertStore = null;
let alertVersion = 0;
let alertEvents = [];
let allowHttpFallback = false;
const seenAlerts = new Set();
const setText = (id, text) => { $(id).textContent = text; };
const show = (id, visible) => { $(id).hidden = !visible; };
const escapeError = (error) => error.message + (error.requestId && error.requestId !== 'demo-session' ? '（请求编号：' + error.requestId + '）' : '');
const isCancellation = (error) => error.name === 'AbortError';

function toast(message, error = false) {
  clearTimeout(toastTimer);
  setText('toast', message);
  $('toast').classList.toggle('error', error);
  show('toast', true);
  toastTimer = setTimeout(() => show('toast', false), error ? 6500 : 3500);
}
function alert(message = '') {
  setText('workspace-alert', message);
  show('workspace-alert', !!message);
}
function syncDate() {
  setText('today', chinaDate().replaceAll('-', '.'));
  $('today').dateTime = chinaDate();
}
function syncModeUI() {
  setText('mode-label', state.demo ? '演示模式' : '真实采集模式');
  setText('mobile-mode-label', state.demo ? '演示模式' : '真实模式');
  setText('mode-switch', state.demo ? '返回真实' : '切至演示');
  $('demo-toggle').setAttribute('aria-checked', String(!!state.demo));
  show('demo-badge', !!state.demo);
}
function setConnection(status) {
  for (const name of ['online', 'offline', 'unknown']) $('connection').classList.toggle(name, name === status);
}
function receivedTime(value) {
  if (!value || Number.isNaN(new Date(value).getTime())) return '—';
  return chinaDate(value) + ' ' + chinaTime(value) + '.' + String(new Date(value).getUTCMilliseconds()).padStart(3, '0') + ' (UTC+8)';
}
syncDate();
setInterval(syncDate, 60000);

const poller = new TelemetryPoller({
  async read(config, signal) {
    const prefix = '/devices/' + config.id + '/telemetry/';
    const query = new URLSearchParams({ seconds: String(config.seconds), filter: config.filter });
    if (config.filter === 'moving_average') query.set('filter_window', String(config.filterWindow));
    const [latest, window, summary, status] = await Promise.all([
      api.request(prefix + 'latest', { signal }),
      api.request(prefix + 'window?' + query, { signal }),
      api.request(prefix + 'summary?seconds=' + config.seconds, { signal }),
      api.request(prefix + 'status', { signal }),
    ]);
    if (latest && latest.device_id !== config.id || window.device_id !== config.id || summary.device_id !== config.id || status.device_id !== config.id || window.samples.some((s) => s.device_id !== config.id)) throw new Error('设备数据不一致，请重新选择设备。');
    return { latest, window, summary, status };
  },
  render(snapshot) {
    alert();
    state.snapshot = snapshot;
    renderTelemetry();
  },
  error(error) {
    clearTelemetry();
    setText('status-text', '连接暂时中断');
    setConnection('offline');
    setText('data-source-health', '连接中断');
    alert(escapeError(error) + ' 正在尝试重新连接。');
  },
});
api.onExpired = () => showAuth('登录状态已失效，请重新登录。');

const live = new RealtimeClient({
  url: () => { const url = new URL('/api/telemetry/live', location.href); url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'; return url.href; },
  onReady(data) {
    if (data.user_id !== state.user?.id) { showAuth('登录用户发生变化，请重新登录。'); return; }
    if (alertStore !== data.store_id) { alertCursor = 0; seenAlerts.clear(); alertEvents = []; alertStore = data.store_id; renderAlerts(); }
    syncAlerts().catch(handleError);
  },
  onSnapshot(snapshot) {
    if (state.page !== 'monitor' || document.hidden || snapshot.user_id !== state.user?.id) return;
    liveSamples = snapshot.raw_samples;
    state.snapshot = snapshot;
    alert(); renderTelemetry();
  },
  onSample(sample) {
    if (state.page !== 'monitor' || document.hidden || !state.snapshot) return;
    liveSamples.push(sample);
    state.snapshot.latest = sample;
    state.snapshot.status = { ...state.snapshot.status, status: 'online', seconds_since_report: 0, last_received_at: sample.received_at };
    refreshLiveWindow(Date.parse(sample.received_at));
    renderTelemetry();
  },
  onStatus(status) {
    if (state.snapshot && state.page === 'monitor') {
      state.snapshot.status = status;
      if (!status.last_received_at) { state.snapshot.latest = null; liveSamples = []; }
      refreshLiveWindow(status.last_received_at ? Date.parse(status.last_received_at) + status.seconds_since_report * 1000 : Date.now());
      renderTelemetry();
    }
  },
  onAlert(event) { receiveAlert(event); },
  onState(status) {
    setText('transport-label', state.demo ? '演示数据' : status === 'connected' ? 'WebSocket' : 'HTTP 备用');
    setText('transport-value', status === 'connected' ? '实时推送' : '1.0s');
    setText('footer-transport', status === 'connected' ? 'WEBSOCKET: LIVE' : 'HTTP FALLBACK: 1.0s');
    if (status === 'connected') { allowHttpFallback = false; poller.stop(); }
    else if (status === 'disconnected' && state.user && !state.demo) {
      allowHttpFallback = true;
      startTelemetry();
      recoverRealtimeSession().catch(handleError);
    }
  },
  onExpired: () => showAuth('登录状态已失效，请重新登录。'),
  onRemoved() { if (state.user) { clearTelemetry(); loadDevices().catch(handleError); } },
});

function refreshLiveWindow(now) {
  liveSamples = liveSamples.filter((s) => Date.parse(s.received_at) > now - state.seconds * 1000).slice(-6000);
  state.snapshot.window = { device_id: state.selected, seconds: state.seconds, count: liveSamples.length,
    filter: state.filter, filter_window: state.filter === 'moving_average' ? state.filterWindow : null,
    samples: state.filter === 'moving_average' ? movingAverage(liveSamples, state.filterWindow) : liveSamples };
  state.snapshot.summary = { device_id: state.selected, seconds: state.seconds, basis: 'raw', count: liveSamples.length, metrics: rawSummary(liveSamples) };
}

function renderAlerts() {
  const owned = new Set(state.devices.map((device) => device.id));
  alertEvents = alertEvents.filter((event) => owned.has(event.device_id)).sort((a, b) => b.sequence - a.sequence).slice(0, 50);
  $('alert-list').replaceChildren();
  for (const event of alertEvents) {
    const item = document.createElement('li');
    item.className = event.type === 'fall' ? 'fall-alert' : 'sedentary-alert';
    const device = state.devices.find((d) => d.id === event.device_id);
    item.textContent = `${device?.display_name || '设备 ' + event.device_id} · ${event.type === 'fall' ? '摔倒告警' : '久坐提醒'} · ${receivedTime(event.occurred_at)}`;
    $('alert-list').append(item);
  }
  setText('alert-count', String(alertEvents.length));
  show('alert-center', !!alertEvents.length);
}
function receiveAlert(event, recovered = false) {
  if (!state.user || !state.devices.some((device) => device.id === event.device_id) || seenAlerts.has(event.event_id)) return;
  seenAlerts.add(event.event_id);
  alertEvents.unshift(event);
  renderAlerts();
  if (!recovered) {
    const name = state.devices.find((device) => device.id === event.device_id)?.display_name || '设备';
    toast(`${name}：${event.type === 'fall' ? '设备上报摔倒告警' : '设备上报久坐提醒'}`, event.type === 'fall');
    $('alert-center').open = true;
  }
}
async function syncAlerts() {
  if (!state.user || state.demo) return;
  const version = ++alertVersion;
  const userId = state.user.id;
  let more;
  do {
    const result = await api.request('/telemetry/alerts?after=' + alertCursor);
    if (version !== alertVersion || state.user?.id !== userId) return;
    if (result.user_id !== userId) { showAuth('登录用户发生变化，请重新登录。'); return; }
    if (alertStore && alertStore !== result.store_id) { alertCursor = 0; seenAlerts.clear(); alertEvents = []; alertStore = result.store_id; more = true; continue; }
    alertStore = result.store_id;
    for (const event of result.events) receiveAlert(event, true);
    alertCursor = result.cursor;
    more = result.has_more;
  } while (more);
}
async function recoverRealtimeSession() {
  const userId = state.user?.id;
  const user = await api.request('/auth/me');
  if (!state.user || state.user.id !== userId) return;
  if (user.id !== userId) { showAuth('登录用户发生变化，请重新登录。'); return; }
  await syncAlerts();
}

function ensureSensor() {
  if (sensor) return;
  try { sensor = new SensorScene($('sensor-scene')); }
  catch {
    $('pose-empty').querySelector('strong').textContent = '浏览器暂不支持三维视图';
    $('pose-empty').querySelector('span').textContent = '姿态角与六轴曲线仍可正常查看';
    show('pose-empty', true);
  }
}
function clearTelemetry() {
  liveSamples = [];
  state.snapshot = null;
  for (const id of ['roll', 'pitch', 'yaw', 'axis-x', 'axis-y', 'axis-z', 'step-increment', 'last-update']) setText(id, '—');
  setText('status-text', '等待设备上报');
  setConnection('unknown');
  setText('data-source-health', '等待数据');
  setText('pose-sync-status', '姿态数据: 等待上报');
  setText('waveform-integrity', '等待有效样本');
  for (const name of ['sedentary', 'fall']) setText('raw-' + name, 'VAL: —');
  for (const angle of ['roll', 'pitch', 'yaw']) $('meter-' + angle).style.left = '50%';
  for (const id of ['sedentary', 'fall']) { setText(id, '等待数据'); $(id).classList.remove('flag-active'); }
  show('pose-empty', true);
  $('reset-view').disabled = true;
  for (const id of ['toggle-grid', 'toggle-projection', 'zoom-in']) $(id).disabled = true;
  sensor?.update(null);
  charts.telemetry([], state.axis, state.seconds);
  renderStats();
}
function renderStats() {
  const unit = state.axis === 'accel' ? 'm/s²' : '°/s';
  const title = state.axis === 'accel' ? '加速度' : '角速度';
  setText('stats-description', `（基于当前 ${state.seconds}s 原始数据计算 · 空窗口显示“—”）`);
  for (const el of document.querySelectorAll('[data-stat-count]')) el.textContent = 'N = ' + (state.snapshot?.summary.count ?? '—') + ' SAMPLES';
  for (const axis of ['x', 'y', 'z']) {
    const metric = state.snapshot?.summary.metrics[state.axis + '_' + axis];
    document.querySelector(`[data-stat-label="${axis}"]`).textContent = axis.toUpperCase() + ' 轴统计（' + title + '）';
    for (const property of ['min', 'max', 'avg', 'range']) {
      const cell = document.querySelector(`[data-stat-value="${axis}-${property}"]`);
      const suffix = document.createElement('small'); suffix.textContent = unit;
      cell.replaceChildren(document.createTextNode(number(metric?.[property])), suffix);
    }
  }
}
function renderTelemetry() {
  const snapshot = state.snapshot;
  if (!snapshot) { clearTelemetry(); return; }
  const sample = snapshot.latest;
  for (const angle of ['roll', 'pitch', 'yaw']) {
    const value = sample?.[angle];
    setText(angle, finite(value) ? (angle === 'pitch' && value > 0 ? '+' : '') + number(value) : '—');
    $('meter-' + angle).style.left = (finite(value) ? Math.max(0, Math.min(94, 47 + value / 180 * 47)) : 47) + '%';
  }
  for (const [id, property] of [['sedentary', 'is_sedentary'], ['fall', 'is_fall']]) {
    setText(id, !sample ? '等待数据' : (sample[property] === 1 ? '检测到' : '未检测到') + (id === 'fall' ? '摔倒' : '久坐'));
    setText('raw-' + id, 'VAL: ' + (sample?.[property] ?? '—'));
    $(id).classList.toggle('flag-active', sample?.[property] === 1);
  }
  setText('step-increment', sample && finite(sample.step_increment) ? (sample.step_increment > 0 ? '+' : '') + String(sample.step_increment) : '—');
  setText('last-update', receivedTime(sample?.received_at || snapshot.status.last_received_at || snapshot.status.last_seen_at));
  const statuses = { online: '近期有上报 (Online)', offline: '近期无上报 (Offline)', unknown: '暂无有效上报' };
  setText('status-text', statuses[snapshot.status.status] || '状态未知');
  setConnection(snapshot.status.status);
  setText('data-source-health', snapshot.status.status === 'online' ? '数据源正常' : '等待上报');
  setText('pose-sync-status', sample ? (snapshot.status.status === 'online' ? '姿态数据: 已同步' : '姿态数据: 上次上报') : '姿态数据: 等待上报');
  $('connection').title = '根据最近设备上报推断状态';
  show('pose-empty', !sample || !sensor);
  $('reset-view').disabled = !sample || !sensor;
  for (const id of ['toggle-grid', 'toggle-projection', 'zoom-in']) $(id).disabled = !sample || !sensor;
  sensor?.update(sample);
  const unit = state.axis === 'accel' ? 'm/s²' : '°/s';
  const last = snapshot.window.samples.at(-1);
  for (const axis of ['x', 'y', 'z']) {
    setText('axis-' + axis, number(last?.[state.axis + '_' + axis]));
  }
  for (const el of document.querySelectorAll('[data-unit]')) el.textContent = unit;
  const complete = snapshot.window.samples.filter((s) => ['x', 'y', 'z'].every((axis) => finite(s[state.axis + '_' + axis]))).length;
  setText('waveform-integrity', snapshot.window.count ? `有效轴样本: ${complete} / ${snapshot.window.count} · ${snapshot.window.filter === 'moving_average' ? '滑动平均' : '原始数据'}` : '当前时间窗口暂无样本');
  charts.telemetry(snapshot.window.samples, state.axis, state.seconds);
  renderStats();
}
function startTelemetry() {
  poller.stop();
  clearTelemetry();
  const enabled = state.user && state.page === 'monitor' && state.selected !== null && !document.hidden;
  const config = enabled ? { id: state.selected, seconds: state.seconds, filter: state.filter, filterWindow: state.filterWindow } : null;
  live.subscribe(config);
  if (enabled && (state.demo || allowHttpFallback)) poller.start(config);
}
function setAccountMenu(open) {
  show('account-menu', open);
  $('account-toggle').setAttribute('aria-expanded', String(open));
}
function showAuth(message = '', tone = 'error') {
  document.title = '六轴监测 · 登录';
  api.invalidate();
  live.stop();
  allowHttpFallback = false;
  alertVersion++; alertCursor = 0; alertStore = null; alertEvents = []; seenAlerts.clear();
  $('alert-list').replaceChildren(); show('alert-center', false);
  poller.stop();
  historyController?.abort();
  historyVersion++;
  deviceVersion++;
  state.user = null; state.devices = []; state.selected = null; state.points = [];
  charts.clear();
  clearTelemetry();
  sensor?.setActive(false);
  show('loading-view', false); show('workspace', false); show('auth-view', true);
  show('main-nav', false); show('account', false);
  setAccountMenu(false);
  for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
  $('device-list').replaceChildren();
  $('device-select').replaceChildren();
  $('daily-rows').replaceChildren();
  setText('history-total', '—'); setText('history-days', '—'); setText('history-range', '');
  setText('account-name', ''); setText('profile-name', ''); setText('profile-created', '');
  for (const id of ['profile-form', 'password-form', 'login-form', 'register-form', 'device-form']) $(id).reset();
  alert();
  setText('auth-message', message);
  $('auth-message').dataset.tone = tone;
  syncModeUI();
}
function renderProfile() {
  if (!state.user) return;
  const user = state.user;
  setText('account-name', user.username);
  setText('profile-name', user.username);
  setText('profile-created', '加入于 ' + chinaDate(user.created_at).replaceAll('-', '.'));
  $('profile-form').elements.username.value = user.username;
  $('profile-form').elements.email.value = user.email || '';
}
async function enterWorkspace(user) {
  api.invalidate();
  state.user = user;
  show('loading-view', false); show('auth-view', false); show('workspace', true);
  show('main-nav', true); show('account', true);
  show('demo-badge', !!state.demo);
  syncModeUI();
  setText('logout-label', state.demo ? '退出演示' : '退出登录');
  renderProfile();
  try { await loadDevices(); }
  catch (error) { if (!isCancellation(error) && state.user) alert(escapeError(error)); }
  if (state.user) {
    if (!state.demo) live.start();
    else { setText('transport-label', '演示数据'); setText('transport-value', '1.0s'); setText('footer-transport', 'DEMO: 1.0s'); }
    route();
  }
}
function renderDevices() {
  renderAlerts();
  const select = $('device-select');
  select.replaceChildren();
  if (!state.devices.length) {
    const option = new Option('暂无设备', ''); select.append(option); select.disabled = true;
  } else {
    select.disabled = false;
    for (const device of state.devices) select.append(new Option(device.display_name, String(device.id)));
    select.value = String(state.selected);
  }
  setText('device-count', String(state.devices.length));
  const current = state.devices.find((device) => device.id === state.selected);
  setText('selected-device-name', current?.display_name || '暂无设备');
  setText('selected-device-code', current ? '(' + current.cloud_device_id + ')' : '');
  show('no-devices', !state.devices.length);
  show('monitor-content', !!state.devices.length);
  const list = $('device-list'); list.replaceChildren();
  if (!state.devices.length) {
    const empty = document.createElement('div'); empty.className = 'empty-state';
    const title = document.createElement('h2'); title.textContent = '还没有绑定设备';
    const caption = document.createElement('p'); caption.textContent = '添加一台获准使用的传感器，即可开始监测。';
    const button = document.createElement('button'); button.className = 'primary'; button.textContent = '绑定第一台设备'; button.addEventListener('click', () => openDevice());
    empty.append(title, caption, button); list.append(empty); return;
  }
  for (const device of state.devices) {
    const row = document.createElement('article'); row.className = 'device-row';
    const icon = document.createElement('div'); icon.className = 'device-icon';
    const symbol = document.createElement('i'); symbol.className = 'ph ph-cube-transparent'; symbol.setAttribute('aria-hidden', 'true'); icon.append(symbol);
    const copy = document.createElement('div');
    const name = document.createElement('h3'); name.textContent = device.display_name;
    const meta = document.createElement('p'); meta.className = 'device-meta';
    meta.textContent = '设备 ID：' + device.cloud_device_id + ' · ' + (device.hardware_model || '型号未填写');
    const lastSeen = document.createElement('p'); lastSeen.className = 'device-meta';
    lastSeen.textContent = '最近上报：' + (device.last_seen_at ? chinaDate(device.last_seen_at) + ' ' + chinaTime(device.last_seen_at) : '暂无记录');
    copy.append(name, meta, lastSeen);
    const actions = document.createElement('div'); actions.className = 'button-row';
    for (const [label, css, handler] of [
      ['查看监测', 'secondary', () => { state.selected = device.id; select.value = String(device.id); location.hash = 'monitor'; }],
      ['重命名', 'secondary', () => openDevice(device)],
      ['解绑', 'text-button coral', () => openUnbind(device)],
    ]) {
      const button = document.createElement('button'); button.className = css; button.textContent = label;
      button.setAttribute('aria-label', label + ' ' + device.display_name);
      button.addEventListener('click', handler); actions.append(button);
    }
    row.append(icon, copy, actions); list.append(row);
  }
}
async function loadDevices() {
  const version = ++deviceVersion;
  const userId = state.user?.id;
  const devices = await api.request('/devices');
  if (version !== deviceVersion) return;
  if (!state.user || userId !== state.user.id || devices.some((d) => d.user_id !== userId)) {
    showAuth('登录用户发生变化，请重新登录。'); return;
  }
  state.devices = devices;
  if (!devices.some((d) => d.id === state.selected)) state.selected = devices[0]?.id ?? null;
  renderDevices();
  if (state.page === 'monitor') startTelemetry();
}
function route() {
  if (!state.user) return;
  const requested = location.hash.slice(1);
  if (requested === 'main') return;
  window.scrollTo(0, 0);
  state.page = ['monitor', 'history', 'devices', 'profile'].includes(requested) ? requested : 'monitor';
  poller.stop();
  live.subscribe(null);
  historyController?.abort();
  historyVersion++;
  setAccountMenu(false);
  for (const view of document.querySelectorAll('[data-view]')) view.hidden = view.dataset.view !== state.page;
  for (const item of document.querySelectorAll('[data-page]')) {
    const active = item.dataset.page === state.page;
    item.classList.toggle('active', active);
    if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
  }
  const labels = { monitor: '实时监测', history: '历史步数', devices: '我的设备', profile: '个人资料' };
  document.title = '六轴监测 · ' + labels[state.page];
  alert();
  if (state.page === 'monitor') {
    if (state.devices.length) ensureSensor();
    sensor?.setActive(true);
    renderDevices();
    startTelemetry();
  } else sensor?.setActive(false);
  if (state.page === 'history') loadHistory().catch(handleError);
  if (state.page === 'devices') loadDevices().catch(handleError);
  if (state.page === 'profile') renderProfile();
}
window.addEventListener('hashchange', route);
document.addEventListener('visibilitychange', () => { if (state.user) startTelemetry(); });
function handleError(error) { if (!isCancellation(error)) toast(escapeError(error), true); }

function quickDates(days) {
  const end = chinaDate();
  $('start-date').value = addDays(end, 1 - days);
  $('end-date').value = end;
  for (const button of document.querySelectorAll('[data-days]')) {
    const selected = Number(button.dataset.days) === days;
    button.classList.toggle('selected', selected); button.setAttribute('aria-pressed', String(selected));
  }
}
quickDates(30);
async function loadHistory() {
  const start = $('start-date').value, end = $('end-date').value;
  validateRange(start, end);
  historyController?.abort();
  const controller = new AbortController(); historyController = controller;
  const version = ++historyVersion;
  const userId = state.user.id;
  state.points = [];
  charts.history([]);
  setText('history-total', '—'); setText('history-days', '—'); setText('history-range', '正在读取历史记录…'); $('daily-rows').replaceChildren();
  try {
    const result = await api.request('/telemetry/steps/daily?' + new URLSearchParams({ start_date: start, end_date: end }), { signal: controller.signal });
    if (version !== historyVersion || controller.signal.aborted) return;
    if (result.user_id !== userId) { showAuth('登录用户发生变化，请重新登录。'); return; }
    state.points = result.points;
    const summary = summarizeSteps(result.points);
    const setSummary = (id, value, unit) => {
      const small = document.createElement('small'); small.textContent = unit;
      $(id).replaceChildren(document.createTextNode(value), small);
    };
    setSummary('history-total', summary.days ? summary.total.toLocaleString('zh-CN') : '—', '步');
    setSummary('history-days', String(summary.days), '天');
    setText('history-range', start + ' — ' + end);
    charts.history(result.points);
    for (const point of [...result.points].reverse()) {
      const row = $('daily-rows').insertRow();
      row.insertCell().textContent = point.date;
      row.insertCell().textContent = point.has_data && finite(point.steps) ? point.steps.toLocaleString('zh-CN') : '—';
      const status = row.insertCell(); status.textContent = point.has_data ? '已记录' : '尚未记录'; status.className = point.has_data ? 'cyan' : 'muted';
    }
  } catch (error) {
    if (!isCancellation(error) && version === historyVersion) { setText('history-range', '读取失败，可重新查询'); alert(escapeError(error)); }
    throw error;
  }
}
$('date-form').addEventListener('submit', (event) => {
  event.preventDefault();
  for (const button of document.querySelectorAll('[data-days]')) { button.classList.remove('selected'); button.setAttribute('aria-pressed', 'false'); }
  loadHistory().catch(handleError);
});
for (const button of document.querySelectorAll('[data-days]')) button.addEventListener('click', () => { quickDates(Number(button.dataset.days)); loadHistory().catch(handleError); });
$('device-select').addEventListener('change', () => { state.selected = Number($('device-select').value); renderDevices(); startTelemetry(); });
for (const button of document.querySelectorAll('[data-axis]')) button.addEventListener('click', () => {
  state.axis = button.dataset.axis;
  for (const tab of document.querySelectorAll('[data-axis]')) tab.setAttribute('aria-selected', String(tab === button));
  renderTelemetry();
});
for (const button of document.querySelectorAll('[data-window]')) button.addEventListener('click', () => {
  state.seconds = Number(button.dataset.window);
  for (const option of document.querySelectorAll('[data-window]')) option.setAttribute('aria-pressed', String(option === button));
  startTelemetry();
});
for (const button of document.querySelectorAll('[data-filter]')) button.addEventListener('click', () => {
  state.filter = button.dataset.filter;
  for (const option of document.querySelectorAll('[data-filter]')) option.setAttribute('aria-pressed', String(option === button));
  show('filter-window-label', state.filter === 'moving_average'); startTelemetry();
});
$('filter-window').addEventListener('input', () => setText('filter-window-value', $('filter-window').value));
$('filter-window').addEventListener('change', () => {
  const value = Number($('filter-window').value);
  if (!Number.isInteger(value) || value < 1 || value > 50) { $('filter-window').value = String(state.filterWindow); toast('滤波窗口须为 1–50 的整数。', true); return; }
  state.filterWindow = value; setText('filter-window-value', String(value)); startTelemetry();
});
$('reset-view').addEventListener('click', () => sensor?.reset());
$('toggle-grid').addEventListener('click', () => { if (sensor) $('toggle-grid').setAttribute('aria-pressed', String(sensor.toggleGrid())); });
$('toggle-projection').addEventListener('click', () => { if (sensor) $('toggle-projection').setAttribute('aria-pressed', String(sensor.toggleProjection())); });
$('zoom-in').addEventListener('click', () => sensor?.zoom(1.15));
$('refresh-monitor').addEventListener('click', async () => {
  $('refresh-monitor').disabled = true;
  try { await loadDevices(); toast('设备与遥测状态已刷新。'); }
  catch (error) { handleError(error); }
  finally { $('refresh-monitor').disabled = false; }
});
$('mode-switch').addEventListener('click', switchDataMode);
$('demo-toggle').addEventListener('click', switchDataMode);
$('stats-toggle').addEventListener('click', () => { const open = $('stats-area').hidden; show('stats-area', open); $('stats-toggle').setAttribute('aria-expanded', String(open)); });
$('account-toggle').addEventListener('click', () => setAccountMenu($('account-menu').hidden));
document.addEventListener('click', (e) => { if (!$('account').contains(e.target)) setAccountMenu(false); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setAccountMenu(false); });
for (const tablist of document.querySelectorAll('[role="tablist"]')) tablist.addEventListener('keydown', (event) => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  const tabs = [...tablist.querySelectorAll('[role="tab"]')];
  const current = tabs.indexOf(document.activeElement);
  if (current < 0) return;
  event.preventDefault();
  const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  tabs[index].focus(); tabs[index].click();
});

function authTab(register) {
  document.title = '六轴监测 · ' + (register ? '注册' : '登录');
  show('register-form', register); show('login-form', !register);
  $('register-tab').setAttribute('aria-selected', String(register)); $('login-tab').setAttribute('aria-selected', String(!register));
  setText('auth-message', '');
}
$('login-tab').addEventListener('click', () => authTab(false));
$('register-tab').addEventListener('click', () => authTab(true));
function onForm(id, action, messageId) {
  const form = $(id);
  let busy = false;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    busy = true;
    const button = event.submitter || form.querySelector('[type=submit]');
    if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
    if (messageId) { setText(messageId, ''); $(messageId).dataset.tone = 'error'; }
    try { await action(Object.fromEntries(new FormData(form)), form); }
    catch (error) { if (!isCancellation(error)) { if (messageId) setText(messageId, escapeError(error)); else handleError(error); } }
    finally { busy = false; if (button) { button.disabled = false; button.removeAttribute('aria-busy'); } }
  });
}
onForm('login-form', async (body, form) => {
  body.username = body.username.trim();
  if (!body.username) throw new Error('请输入用户名。');
  try {
    const result = await api.request('/auth/login', { method: 'POST', body, protectedRequest: false });
    await enterWorkspace(result.user);
  } finally { form.elements.password.value = ''; }
}, 'auth-message');
onForm('register-form', async (body, form) => {
  body.username = body.username.trim(); body.email = body.email.trim() || null;
  if (!body.username) throw new Error('用户名不能为空。');
  try {
    const user = await api.request('/auth/register', { method: 'POST', body, protectedRequest: false });
    authTab(false);
    $('login-form').elements.username.value = user.username;
    toast('注册成功，请使用新账户登录。');
    $('login-form').elements.password.focus();
  } finally { form.elements.password.value = ''; }
}, 'auth-message');
onForm('profile-form', async (body) => {
  body.username = body.username.trim(); body.email = body.email.trim() || null;
  if (!body.username) throw new Error('用户名不能为空。');
  await api.request('/users/' + state.user.id, { method: 'PATCH', body });
  state.user = await api.request('/auth/me');
  renderProfile(); toast('资料已保存。');
});
onForm('password-form', async (body, form) => {
  if (body.password !== body.confirmation) throw new Error('两次输入的新密码不一致。');
  const request = { current_password: body.current_password, password: body.password };
  await api.request('/users/' + state.user.id, { method: 'PATCH', body: request, credentialCheck: true });
  form.reset();
  showAuth('密码修改成功，请使用新密码登录。', 'success');
});
$('logout').addEventListener('click', async () => {
  const button = $('logout'); if (button.disabled) return; button.disabled = true;
  // Stop background reads immediately; retain the current account if logout fails.
  poller.stop(); live.stop(); historyController?.abort();
  try {
    await api.request('/auth/logout', { method: 'POST', protectedRequest: false });
    if (state.demo) { state.demo = null; api.fetcher = (...args) => fetch(...args); const url = new URL(location.href); url.searchParams.delete('demo'); window.history.replaceState(null, '', url); show('demo-badge', false); }
    showAuth('已安全退出。', 'success');
  } catch (error) { handleError(error); if (state.user) { if (!state.demo) live.start(); startTelemetry(); } }
  finally { button.disabled = false; }
});
async function enterDemo() {
  showAuth();
  state.demo = new DemoBackend(); api.fetcher = state.demo.fetch;
  const url = new URL(location.href); url.searchParams.set('demo', '1'); url.hash = 'monitor';
  window.history.replaceState(null, '', url);
  await enterWorkspace(state.demo.user);
}
async function switchDataMode() {
  if ($('mode-switch').disabled) return;
  $('mode-switch').disabled = true; $('demo-toggle').disabled = true;
  try {
    if (!state.demo) { await enterDemo(); return; }
    showAuth();
    state.demo = null;
    api.fetcher = (...args) => fetch(...args);
    const url = new URL(location.href); url.searchParams.delete('demo'); window.history.replaceState(null, '', url);
    syncModeUI();
    await initialize();
  } finally { $('mode-switch').disabled = false; $('demo-toggle').disabled = false; }
}
$('enter-demo').addEventListener('click', enterDemo);

function openDevice(device = null) {
  editingDevice = device;
  const form = $('device-form'); form.reset();
  const editing = !!device;
  setText('device-dialog-title', editing ? '重命名设备' : '绑定设备');
  setText('device-save', editing ? '保存名称' : '绑定设备');
  setText('device-form-message', '');
  show('binding-fields', !editing); show('hardware-field', !editing); show('bind-hint', !editing);
  for (const name of ['cloud_device_id', 'cloud_product_id', 'hardware_model']) form.elements[name].disabled = editing;
  if (device) form.elements.display_name.value = device.display_name;
  $('device-dialog').showModal();
  if (editing) form.elements.display_name.focus();
}
for (const button of document.querySelectorAll('[data-bind]')) button.addEventListener('click', () => openDevice());
for (const button of document.querySelectorAll('[data-close]')) button.addEventListener('click', () => button.closest('dialog').close());
function openUnbind(device) {
  unbindingDevice = device; setText('unbind-description', '确认解绑“' + device.display_name + '”？解绑后将停止接收该设备的实时数据。');
  setText('unbind-message', ''); $('unbind-dialog').showModal();
}
onForm('device-form', async (body) => {
  body.display_name = body.display_name.trim();
  if (!body.display_name) throw new Error('设备名称不能为空。');
  if (editingDevice) await api.request('/devices/' + editingDevice.id, { method: 'PATCH', body: { display_name: body.display_name } });
  else {
    body.cloud_device_id = body.cloud_device_id.trim(); body.cloud_product_id = body.cloud_product_id.trim(); body.hardware_model = body.hardware_model.trim() || null;
    if (!body.cloud_device_id || !body.cloud_product_id) throw new Error('请填写设备编号和产品编号。');
    await api.request('/devices', { method: 'POST', body });
  }
  $('device-dialog').close();
  // A successful write is not retried if the subsequent list refresh fails.
  toast(editingDevice ? '设备名称已保存。' : '设备已绑定。');
  try { await loadDevices(); }
  catch (error) { if (!isCancellation(error)) alert('设备已保存，但列表刷新失败。请点击“我的设备”中的“刷新”重试。' + escapeError(error)); }
  if (state.page === 'monitor' && state.devices.length) { ensureSensor(); sensor?.setActive(true); }
}, 'device-form-message');
onForm('unbind-form', async () => {
  await api.request('/devices/' + unbindingDevice.id, { method: 'DELETE' });
  poller.stop(); clearTelemetry();
  state.devices = state.devices.filter((d) => d.id !== unbindingDevice.id);
  if (state.selected === unbindingDevice.id) state.selected = state.devices[0]?.id ?? null;
  renderDevices(); $('unbind-dialog').close(); toast('设备已解绑，历史步数已保留。');
  try { await loadDevices(); }
  catch (error) { if (!isCancellation(error)) alert('设备已解绑，但列表刷新失败。请点击“刷新”重试。' + escapeError(error)); }
}, 'unbind-message');
$('refresh-devices').addEventListener('click', async () => {
  const button = $('refresh-devices'); button.disabled = true;
  try { await loadDevices(); toast('设备列表已更新。'); }
  catch (error) { handleError(error); }
  finally { button.disabled = false; }
});

async function initialize() {
  if (new URLSearchParams(location.search).get('demo') === '1') { await enterDemo(); return; }
  try { const user = await api.request('/auth/me', { protectedRequest: false }); await enterWorkspace(user); }
  catch (error) { if (!isCancellation(error)) showAuth(error.status === 401 ? '' : escapeError(error)); }
}
initialize().catch(handleError);

