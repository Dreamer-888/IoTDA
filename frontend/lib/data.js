export const AXES = ['accel_x', 'accel_y', 'accel_z', 'gyro_x', 'gyro_y', 'gyro_z'];
export const finite = (value) => typeof value === 'number' && Number.isFinite(value);
export const number = (value, digits = 2) => finite(value) ? value.toFixed(digits).replace('-', '−') : '—';
export function chinaDate(value = new Date()) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value));
}
export function chinaTime(value) {
  if (!value || Number.isNaN(new Date(value).getTime())) return '—';
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(value));
}
export function addDays(date, count) {
  const value = new Date(date + 'T12:00:00Z');
  value.setUTCDate(value.getUTCDate() + count);
  return value.toISOString().slice(0, 10);
}
export function validateRange(start, end) {
  const valid = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s) && s.slice(0, 4) >= '1000' && Number.isFinite(Date.parse(s + 'T00:00:00Z')) && new Date(s + 'T00:00:00Z').toISOString().slice(0, 10) === s;
  if (!valid(start) || !valid(end)) throw new Error('请选择有效的开始和结束日期。');
  const days = (Date.parse(end) - Date.parse(start)) / 86400000 + 1;
  if (days < 1) throw new Error('开始日期不能晚于结束日期。');
  if (days > 366) throw new Error('一次最多查询 366 天，请缩小日期范围。');
  return days;
}
export function summarizeSteps(points) {
  const recorded = points.filter((p) => p.has_data && finite(p.steps));
  return { total: recorded.reduce((sum, p) => sum + p.steps, 0), days: recorded.length, values: points.map((p) => p.has_data && finite(p.steps) ? p.steps : null) };
}
export function movingAverage(samples, size) {
  return samples.map((sample, i) => {
    const window = samples.slice(Math.max(0, i - size + 1), i + 1);
    return { ...sample, ...Object.fromEntries(AXES.map((key) => [key, window.reduce((sum, item) => sum + item[key], 0) / window.length])) };
  });
}
export function rawSummary(samples) {
  return Object.fromEntries(AXES.map((key) => {
    const values = samples.map((s) => s[key]).filter(finite);
    return [key, values.length ? { min: Math.min(...values), max: Math.max(...values), avg: values.reduce((a, b) => a + b, 0) / values.length, range: Math.max(...values) - Math.min(...values) } : { min: null, max: null, avg: null, range: null }];
  }));
}
