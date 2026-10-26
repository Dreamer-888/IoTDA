// One account connection; telemetry selection changes independently of alerts.
export class RealtimeClient {
  constructor({ createSocket = (url) => new WebSocket(url), url, onReady = () => {},
    onSnapshot = () => {}, onSample = () => {}, onStatus = () => {}, onAlert = () => {},
    onState = () => {}, onExpired = () => {}, onRemoved = () => {} }) {
    Object.assign(this, { createSocket, url, onReady, onSnapshot, onSample, onStatus, onAlert, onState, onExpired, onRemoved });
    this.generation = 0;
    this.version = 0;
    this.config = null;
    this.connected = false;
    this.running = false;
  }
  start() {
    this.stop();
    this.running = true;
    this.delay = 1000;
    this.connect(this.generation);
  }
  stop() {
    this.generation++;
    this.running = false;
    this.connected = false;
    clearTimeout(this.retry);
    clearTimeout(this.deadline);
    if (this.socket) {
      this.socket.onopen = this.socket.onmessage = this.socket.onclose = this.socket.onerror = null;
      this.socket.close();
      this.socket = null;
    }
  }
  subscribe(config) {
    this.config = config;
    this.version = (this.version + 1) % 2147483647;
    if (this.connected) this.sendSubscription();
  }
  sendSubscription() {
    this.socket.send(JSON.stringify({ type: 'subscribe', subscription_id: this.version,
      device_id: this.config?.id ?? null, seconds: this.config?.seconds ?? 60,
      filter: this.config?.filter ?? 'none', filter_window: this.config?.filterWindow ?? 5 }));
  }
  connect(generation) {
    if (!this.running || generation !== this.generation) return;
    this.onState('connecting');
    let socket;
    try { socket = this.createSocket(typeof this.url === 'function' ? this.url() : this.url); }
    catch { this.schedule(generation); return; }
    this.socket = socket;
    this.deadline = setTimeout(() => socket.close(), 12000);
    socket.onmessage = (event) => {
      if (generation !== this.generation) return;
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === 'ready') {
        clearTimeout(this.deadline);
        this.connected = true;
        this.delay = 1000;
        this.onReady(message.data);
        if (generation !== this.generation || !this.running) return;
        this.sendSubscription();
        this.onState('connected');
      } else if (message.type === 'alert') {
        this.onAlert(message.data);
      } else if (message.type === 'error') {
        if (message.code === 401) { this.stop(); this.onExpired(); }
        else if (message.code === 404) this.onRemoved();
      } else if (message.subscription_id === this.version && message.data?.device_id === this.config?.id) {
        if (message.type === 'snapshot') this.onSnapshot(message.data);
        if (message.type === 'sample') this.onSample(message.data);
        if (message.type === 'status') this.onStatus(message.data);
      }
    };
    socket.onerror = () => {}; // close owns retry, so one failure creates one timer.
    socket.onclose = (event) => {
      if (generation !== this.generation || !this.running) return;
      clearTimeout(this.deadline);
      this.connected = false;
      if (event.code === 4401) { this.stop(); this.onExpired(); return; }
      if (event.code === 4404) this.onRemoved();
      this.schedule(generation);
    };
  }
  schedule(generation) {
    if (!this.running || generation !== this.generation) return;
    this.connected = false;
    this.onState('disconnected');
    clearTimeout(this.retry);
    this.retry = setTimeout(() => this.connect(generation), this.delay);
    this.delay = Math.min(this.delay * 2, 30000);
  }
}
