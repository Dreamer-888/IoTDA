'use strict';

// A session change invalidates both pending requests and their eventual results.
class ReportMonitor {
  constructor({ render, clear, status, sessionExpired, fetcher = (...args) => globalThis.fetch(...args) }) {
    Object.assign(this, { render, clear, status, sessionExpired, fetcher });
    this.generation = 0;
    this.userId = null;
    this.timer = null;
    this.controller = null;
  }

  stop() {
    this.generation += 1;
    this.userId = null;
    clearTimeout(this.timer);
    this.timer = null;
    this.controller?.abort();
    this.controller = null;
    this.clear();
  }

  start(userId) {
    this.stop();
    this.userId = userId;
    this.status('正在读取当前用户的设备报文…');
    return this.poll(this.generation);
  }

  async poll(generation) {
    if (generation !== this.generation || this.userId === null) return;
    const controller = new AbortController();
    this.controller = controller;
    try {
      const response = await this.fetcher('/api/telemetry/reports', {
        credentials: 'include', cache: 'no-store', signal: controller.signal,
      });
      if (generation !== this.generation) return;
      if (response.status === 401) {
        this.stop();
        this.sessionExpired('登录状态已失效，请重新登录。');
        return;
      }
      const result = await response.json();
      if (generation !== this.generation) return;
      if (!response.ok) throw new Error(result.message || `HTTP ${response.status}`);
      if (result.data.user_id !== this.userId) {
        this.stop();
        this.sessionExpired('登录用户已变化，请重新登录或刷新页面。');
        return;
      }
      this.render(result.data);
      this.status(`每秒自动更新 · 当前用户 ${this.userId} · ${result.data.devices.length} 台设备`);
    } catch (error) {
      if (generation === this.generation && error.name !== 'AbortError') {
        this.status(`暂时无法读取报文：${error.message}；稍后自动重试。`);
      }
    } finally {
      if (generation === this.generation && this.userId !== null) {
        this.controller = null;
        this.timer = setTimeout(() => this.poll(generation), 1000);
      }
    }
  }
}

if (typeof module !== 'undefined' && module.exports) module.exports = { ReportMonitor };
