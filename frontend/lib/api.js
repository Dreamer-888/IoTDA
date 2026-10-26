export class ApiError extends Error {
  constructor(message, status = 0, requestId = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.requestId = requestId;
  }
}

export class ApiClient {
  constructor(fetcher = (...args) => fetch(...args)) {
    this.fetcher = fetcher;
    this.epoch = 0;
    this.pending = new Set();
    this.onExpired = () => {};
  }
  invalidate() {
    this.epoch++;
    for (const controller of this.pending) controller.abort();
    this.pending.clear();
  }
  async request(path, { method = 'GET', body, signal, protectedRequest = true, credentialCheck = false } = {}) {
    const epoch = this.epoch;
    const controller = new AbortController();
    let timedOut = false;
    const abort = () => controller.abort();
    if (signal?.aborted) throw new DOMException('请求已取消', 'AbortError');
    signal?.addEventListener('abort', abort, { once: true });
    this.pending.add(controller);
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 12000);
    try {
      const response = await this.fetcher('/api' + path, {
        method, credentials: 'include', cache: 'no-store', signal: controller.signal,
        ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
      });
      let result;
      try { result = await response.json(); }
      catch { throw new ApiError('服务暂不可用，请检查后端连接。', response.status, response.headers.get('X-Request-ID')); }
      if (epoch !== this.epoch || controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError');
      if (!response.ok) {
        if (response.status === 401 && protectedRequest && !credentialCheck) this.onExpired();
        throw new ApiError(result.message || '请求失败，请稍后重试。', response.status, response.headers.get('X-Request-ID'));
      }
      return result.data;
    } catch (error) {
      if (timedOut && epoch === this.epoch) throw new ApiError(method === 'GET' ? '连接超时，请稍后重试。' : '请求超时，提交结果尚未确认。请先刷新查询，避免重复提交。');
      if (epoch !== this.epoch || controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError');
      if (error instanceof TypeError) throw new ApiError(method === 'GET' ? '无法连接服务，请确认后端已启动。' : '连接中断，提交结果尚未确认。请先刷新查询，避免重复提交。');
      throw error;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      this.pending.delete(controller);
    }
  }
}

// Requests finish before the next tick; both abort and generation guards prevent stale renders.
export class TelemetryPoller {
  constructor({ read, render, error, interval = 1000 }) {
    Object.assign(this, { read, render, error, interval });
    this.generation = 0;
  }
  stop() {
    this.generation++;
    clearTimeout(this.timer);
    this.controller?.abort();
    this.controller = null;
  }
  start(config) {
    this.stop();
    const generation = this.generation;
    const tick = async () => {
      if (generation !== this.generation) return;
      const controller = new AbortController();
      this.controller = controller;
      try {
        const value = await this.read(config, controller.signal);
        if (generation === this.generation) this.render(value);
      } catch (error) {
        controller.abort();
        if (generation === this.generation && error.name !== 'AbortError') this.error(error);
      } finally {
        if (generation === this.generation) this.timer = setTimeout(tick, this.interval);
      }
    };
    return tick();
  }
}
