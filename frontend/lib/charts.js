import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, GraphicComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { chinaTime, number, summarizeSteps } from './data.js';
import { readTheme } from './theme.js';
echarts.use([LineChart, GridComponent, TooltipComponent, GraphicComponent, CanvasRenderer]);

export class Charts {
  constructor(liveElement, historyElement) {
    this.theme = readTheme();
    this.palette = ['chart-x', 'chart-y', 'chart-z'].map((token) => this.theme[token]);
    this.axisLabel = { color: this.theme.muted, fontSize: 10, fontFamily: 'JetBrains Mono, Microsoft YaHei, sans-serif' };
    this.lineStyle = { color: this.theme.line, width: 1 };
    this.tooltip = { trigger: 'axis', backgroundColor: '#ffffff', borderColor: '#eff4ff', borderWidth: 1, padding: 9, textStyle: { color: this.theme.text, fontSize: 10, fontFamily: 'JetBrains Mono, Microsoft YaHei, sans-serif' }, confine: true, extraCssText: 'border-radius:4px;box-shadow:0 4px 10px #0f172a18;' };
    this.liveElement = liveElement;
    this.historyElement = historyElement;
    this.observer = new ResizeObserver(() => {
      if (liveElement.clientWidth) this.live?.resize();
      if (historyElement.clientWidth) this.daily?.resize();
    });
    this.observer.observe(liveElement);
    this.observer.observe(historyElement);
  }
  telemetry(samples, axis, seconds) {
    if (!this.liveElement.clientWidth) return;
    const { theme, palette, axisLabel, lineStyle, tooltip } = this;
    this.live ||= echarts.init(this.liveElement, null, { renderer: 'canvas' });
    const unit = axis === 'accel' ? 'm/s²' : '°/s';
    const title = axis === 'accel' ? '加速度' : '角速度';
    const last = samples.at(-1);
    const end = last ? Date.parse(last.received_at) : Date.now();
    const values = samples.flatMap((s) => ['x', 'y', 'z'].map((a) => s[axis + '_' + a])).filter(Number.isFinite);
    const extent = Math.max(axis === 'accel' ? 12 : 3, ...values.map((n) => Math.abs(n)));
    const bound = axis === 'accel' ? Math.ceil(extent / 6) * 6 : Math.ceil(extent);
    const small = this.liveElement.clientWidth < 550;
    this.live.setOption({
      animation: false, color: palette, textStyle: { fontFamily: 'JetBrains Mono, Microsoft YaHei, sans-serif' },
      grid: { left: small ? 38 : 56, right: small ? 12 : 20, top: 20, bottom: 30 },
      tooltip: { ...tooltip, axisPointer: { type: 'line', lineStyle: { color: '#737686', type: 'dashed' } }, formatter: (params) => {
        const list = Array.isArray(params) ? params : [params];
        const time = list[0]?.value?.[0];
        return `<div style="color:#737686;padding-bottom:5px;margin-bottom:4px;border-bottom:1px solid #eff4ff">T: ${chinaTime(time)} (${((time - end) / 1000).toFixed(1)}s)</div>` + list.map((p, i) => `<div style="display:flex;gap:28px;justify-content:space-between;line-height:20px"><span style="color:${palette[i]}">${p.seriesName}:</span><strong>${number(p.value[1])} ${unit}</strong></div>`).join('');
      } },
      xAxis: { type: 'value', min: end - seconds * 1000, max: end, interval: seconds * 1000 / (small ? 2 : 4), splitNumber: small ? 2 : 4, axisLabel: { ...axisLabel, formatter: (v) => chinaTime(v) + (small ? '' : v === end ? ' (NOW)' : ` (${Math.round((v - end) / 1000)}s)`), hideOverlap: true }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: true, lineStyle: { color: '#eef2f6' } } },
      yAxis: { type: 'value', min: -bound, max: bound, interval: bound / 2, splitNumber: 4, axisLabel: { ...axisLabel, formatter: (v) => (v > 0 ? '+' : '') + Number(v).toFixed(1) }, splitLine: { lineStyle: { ...lineStyle, type: 'dashed' } }, axisLine: { show: false }, axisTick: { show: false } },
      graphic: samples.length ? [] : [{ type: 'text', left: 'center', top: 'middle', style: { text: '当前窗口暂无数据', fill: theme.muted, font: '14px Microsoft YaHei' } }],
      series: ['x', 'y', 'z'].map((a, i) => ({ name: a.toUpperCase(), type: 'line', showSymbol: samples.length <= 3, symbolSize: 5, connectNulls: false, smooth: .18, lineStyle: { width: i === 2 ? 2 : 1.5 }, itemStyle: { color: palette[i] }, data: samples.map((s) => [Date.parse(s.received_at), s[axis + '_' + a]]) })),
    }, { notMerge: true });
  }
  history(points) {
    if (!this.historyElement.clientWidth) return;
    const { theme, axisLabel, lineStyle, tooltip } = this;
    this.daily ||= echarts.init(this.historyElement, null, { renderer: 'canvas' });
    const summary = summarizeSteps(points);
    this.daily.setOption({
      animation: false, color: [theme.text], textStyle: { fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif' },
      grid: { left: 56, right: 25, top: 32, bottom: 42 },
      tooltip: { ...tooltip, formatter: (params) => {
        const p = points[params[0]?.dataIndex];
        if (!p) return '';
        // Only validated date strings and numeric API fields enter this tooltip.
        const date = /^\d{4}-\d{2}-\d{2}$/.test(p.date) ? p.date : '';
        return date + '<br>' + (p.has_data && Number.isFinite(p.steps) ? p.steps.toLocaleString('zh-CN') + ' 步' : '尚未记录');
      } },
      xAxis: { type: 'category', boundaryGap: false, data: points.map((p) => p.date), axisLabel: { ...axisLabel, hideOverlap: true, formatter: (v) => v.slice(5).replace('-', '.') }, axisLine: { lineStyle: { color: theme['border-strong'] } }, splitLine: { show: true, lineStyle } },
      yAxis: { type: 'value', min: 0, minInterval: 1, axisLabel, splitLine: { lineStyle }, name: '步', nameTextStyle: { color: theme.muted } },
      graphic: summary.days ? [] : [{ type: 'text', left: 'center', top: 'middle', style: { text: '所选日期尚未记录步数', fill: theme.muted, font: '14px Microsoft YaHei' } }],
      series: [{ type: 'line', name: '每日步数', data: summary.values, connectNulls: false, showSymbol: true, symbolSize: points.length > 60 ? 3 : 6, lineStyle: { width: 2.5 }, areaStyle: { color: theme.accent, opacity: .055 } }],
    }, { notMerge: true });
    return summary;
  }
  clear() { this.live?.clear(); this.daily?.clear(); }
}
