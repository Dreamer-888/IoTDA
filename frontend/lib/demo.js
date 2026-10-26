import { addDays, chinaDate, movingAverage, rawSummary, validateRange } from './data.js';

// Explicit, in-memory demo transport. It never sends requests or writes real user data.
export class DemoBackend {
  constructor() {
    const now = new Date().toISOString();
    this.user = { id: 1, username: '演示用户', email: 'demo@example.com', created_at: now, updated_at: now };
    this.password = 'demo123456';
    this.loggedIn = true;
    this.startedAt = Math.floor(Date.now() / 1000) * 1000;
    this.devices = [
      { id: 7, user_id: 1, cloud_device_id: 'dev_imu_9842f', cloud_product_id: 'six-axis-demo', display_name: '六轴高精实验标定仪 A01', hardware_model: 'IMU · 六轴传感器', sample_interval_ms: null, last_seen_at: now, created_at: now, updated_at: now },
      { id: 8, user_id: 1, cloud_device_id: 'demo-sensor-02', cloud_product_id: 'six-axis-demo', display_name: '备用传感器', hardware_model: null, sample_interval_ms: null, last_seen_at: null, created_at: now, updated_at: now },
    ];
    this.nextId = 9;
    this.fetch = this.fetch.bind(this);
  }
  sample(time, id) {
    const t = (time - this.startedAt) / 1000;
    const accel_x = .24 + Math.sin(t * .32) * 1.1;
    const accel_y = -.12 + Math.sin(t * .37 + 1) * .95;
    const accel_z = 9.78 + Math.sin(t * .42) * .65;
    const gyro_x = -1.45 + Math.sin(t / 3) * .7;
    const gyro_y = .82 + Math.cos(t / 4) * .5;
    const gyro_z = .03 + Math.sin(t / 5) * .4;
    return { device_id: id, reported_at: new Date(time).toISOString(), cloud_event_at: new Date(time).toISOString(), received_at: new Date(time).toISOString(), accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z, roll: -3.82 + Math.sin(t / 9) * .12, pitch: 14.25 + Math.sin(t / 8) * .15, yaw: 128.4 + Math.sin(t / 10) * .2, step_increment: 2, is_sedentary: 0, is_fall: 0, accel_magnitude: Math.hypot(accel_x, accel_y, accel_z), gyro_magnitude: Math.hypot(gyro_x, gyro_y, gyro_z) };
  }
  async fetch(input, options = {}) {
    await new Promise((resolve) => setTimeout(resolve, 100));
    if (options.signal?.aborted) throw new DOMException('请求已取消', 'AbortError');
    const url = new URL(input, 'http://demo.local');
    const path = url.pathname.replace(/^\/api/, '');
    const body = options.body ? JSON.parse(options.body) : {};
    const method = options.method || 'GET';
    const reply = (data, code = 200, message = '操作成功') => new Response(JSON.stringify({ code, message, data }), { status: code, headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'demo-session' } });
    if (path === '/auth/logout') { this.loggedIn = false; return reply(null); }
    if (path === '/auth/login') {
      if (body.username !== this.user.username || body.password !== this.password) return reply(null, 401, '用户名或密码错误');
      this.loggedIn = true; return reply({ user: this.user, authenticated: true });
    }
    if (path === '/auth/register') {
      if (body.username === this.user.username) return reply(null, 409, '用户名已存在');
      this.user = { ...this.user, username: body.username, email: body.email };
      this.password = body.password;
      return reply(this.user, 201);
    }
    if (!this.loggedIn) return reply(null, 401, '尚未登录');
    if (path === '/auth/me') return reply(this.user);
    if (path === '/users/1') {
      if (method === 'PATCH') {
        if (body.password) {
          if (body.current_password !== this.password) return reply(null, 401, '当前密码错误');
          this.password = body.password; this.loggedIn = false; return reply('密码已修改，请重新登录');
        }
        Object.assign(this.user, body, { updated_at: new Date().toISOString() });
        return reply('资料已保存');
      }
      return reply(this.user);
    }
    if (path === '/devices') {
      if (method === 'POST') {
        if (this.devices.some((d) => d.cloud_device_id === body.cloud_device_id)) return reply(null, 409, '该设备已绑定');
        const device = { ...body, id: this.nextId++, user_id: 1, sample_interval_ms: null, last_seen_at: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
        this.devices.push(device); return reply(device, 201);
      }
      return reply(this.devices);
    }
    if (path === '/telemetry/steps/daily') {
      const today = chinaDate();
      const start = url.searchParams.get('start_date') || addDays(today, -29);
      const end = url.searchParams.get('end_date') || today;
      const count = validateRange(start, end);
      const points = Array.from({ length: count }, (_, i) => {
        const date = addDays(start, i);
        const day = Math.floor(Date.parse(date) / 86400000);
        const missing = date >= today || day % 11 === 0;
        return { date, steps: missing ? null : day % 9 === 0 ? 0 : 3100 + Math.abs((day * 137) % 5000), has_data: !missing };
      });
      return reply({ user_id: 1, timezone: 'Asia/Shanghai', start_date: start, end_date: end, points });
    }
    const match = path.match(/^\/devices\/(\d+)(?:\/telemetry\/(latest|window|summary|status))?$/);
    if (match) {
      const id = Number(match[1]);
      const device = this.devices.find((d) => d.id === id);
      if (!device) return reply(null, 404, '设备不存在');
      if (!match[2]) {
        if (method === 'PATCH') { device.display_name = body.display_name; return reply('设备名称已更新'); }
        if (method === 'DELETE') { this.devices = this.devices.filter((d) => d.id !== id); return reply('已解绑设备'); }
      }
      const now = Math.floor(Date.now() / 1000) * 1000;
      const seconds = Number(url.searchParams.get('seconds') || 60);
      const samples = id === 7 ? Array.from({ length: seconds }, (_, i) => this.sample(now - (seconds - 1 - i) * 1000, id)) : [];
      if (match[2] === 'latest') return reply(samples.at(-1) || null);
      if (match[2] === 'status') return reply({ device_id: id, status: samples.length ? 'online' : 'unknown', basis: 'recent_reports', online_timeout_seconds: 30, seconds_since_report: samples.length ? 0 : null, last_received_at: samples.length ? new Date(now).toISOString() : null, last_seen_at: device.last_seen_at });
      if (match[2] === 'summary') return reply({ device_id: id, seconds, basis: 'raw', count: samples.length, metrics: rawSummary(samples) });
      const filter = url.searchParams.get('filter') || 'none';
      const filterWindow = filter === 'moving_average' ? Number(url.searchParams.get('filter_window') || 5) : null;
      return reply({ device_id: id, seconds, count: samples.length, filter, filter_window: filterWindow, samples: filterWindow ? movingAverage(samples, filterWindow) : samples });
    }
    return reply(null, 404, '接口不存在');
  }
}
