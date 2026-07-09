/* market_research app.js — 单页 + hash 路由 + ECharts 渲染
   移植自 examples/fundflow_viz/dashboard.html，适配统一 schema。 */

(function () {
  'use strict';

  // ==================== CSS 变量 ====================
  const CSS = getComputedStyle(document.documentElement);
  const C = (n) => CSS.getPropertyValue(n).trim();

  function parseHex(h) {
    h = h.replace('#','');
    if (h.length===3) h = h.split('').map(x=>x+x).join('');
    return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16)];
  }
  function interp(hexA, hexB, t) {
    const a = parseHex(hexA), b = parseHex(hexB);
    return `rgb(${Math.round(a[0]+(b[0]-a[0])*t)},${Math.round(a[1]+(b[1]-a[1])*t)},${Math.round(a[2]+(b[2]-a[2])*t)})`;
  }
  function divColor(v) {
    const a = Math.max(-1, Math.min(1, v));
    if (Math.abs(a) < 0.02) return C('--div-mid');
    if (a > 0) {
      return a < 0.5 ? interp(C('--div-mid'), C('--div-pos-soft'), a/0.5)
                     : interp(C('--div-pos-soft'), C('--div-pos'), (a-0.5)/0.5);
    }
    return -a < 0.5 ? interp(C('--div-mid'), C('--div-neg-soft'), -a/0.5)
                    : interp(C('--div-neg-soft'), C('--div-neg'), (-a-0.5)/0.5);
  }

  // ==================== 工具函数 ====================
  function fmt(d) { return d.slice(0,4)+'-'+d.slice(4,6)+'-'+d.slice(6,8); }
  function loadJson(path) {
    return fetch(path).then(r => {
      if (!r.ok) throw new Error('HTTP '+r.status+' '+path);
      return r.json();
    });
  }
  function fmtMoney(v) {
    if (v == null) return '-';
    const abs = Math.abs(v);
    if (abs >= 1e8) return (v / 1e8).toFixed(1) + '亿';
    if (abs >= 1e4) return (v / 1e4).toFixed(0) + '万';
    return v.toFixed(0);
  }
  function signClass(v) { return v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : ''; }

  // ==================== 全局状态 ====================
  const STATE = {
    industry: null,      // industry.json data
    limitup: null,       // limitup_main.json data
    calendar: null,      // calendar.json data
    limitupDate: null,   // current selected limitup date
    // echarts instances
    heatChart: null,
    icChart: null,
    quintChart: null,
    rankChart: null,
    ltChart: null,
  };

  // ==================== 路由 ====================
  const sections = document.querySelectorAll('.tab-content');
  const links = document.querySelectorAll('.tab-link');

  function activateTab(hash) {
    const id = hash.replace('#','') || 'industry';
    sections.forEach(el => el.classList.toggle('active', el.id === id));
    links.forEach(el => el.classList.toggle('active', el.getAttribute('href') === '#'+id));
    // resize charts on tab switch
    setTimeout(() => {
      ['heatChart','icChart','quintChart','rankChart','ltChart'].forEach(k => {
        if (STATE[k]) STATE[k].resize();
      });
    }, 50);
  }

  window.addEventListener('hashchange', () => activateTab(location.hash));
  if (!location.hash) location.hash = '#industry';
  activateTab(location.hash);

  // ==================== 概览条 ====================
  const bar = document.getElementById('overview-bar');
  function renderOverview(overview) {
    if (!overview) return;
    const kpi = overview.kpi || {};
    const items = [
      { label: '沪深净流入', value: fmtMoney(kpi.net_inflow), cls: signClass(kpi.net_inflow) },
      { label: '主力净流入', value: fmtMoney(kpi.large_inflow), cls: signClass(kpi.large_inflow) },
      { label: '沪指', value: kpi.close_sh != null ? kpi.close_sh.toFixed(1) : '-' },
      { label: '涨停', value: kpi.limit_up_cnt ?? '-' },
      { label: '炸板', value: kpi.limit_break_cnt ?? '-' },
      { label: '跌停', value: kpi.limit_down_cnt ?? '-' },
      { label: '最高板', value: kpi.max_limit_times ?? '-' },
    ];
    bar.innerHTML = items.map(it =>
      `<div class="ov-item"><div class="ov-label">${it.label}</div><div class="ov-value ${it.cls||''}">${it.value}</div></div>`
    ).join('');
  }

  // ==================== Tab1: 行业资金流 ====================
  function renderIndustryTab(data) {
    if (!data) return;
    const series = data.series;
    if (!series) return;

    renderHeatmap(series);
    renderRanking(series, data.kpi);
    renderIC(series);
    renderQuintile(series);
  }

  function renderHeatmap(series) {
    const el = document.getElementById('chart-heat');
    if (!el) return;
    if (!STATE.heatChart) STATE.heatChart = echarts.init(el);

    const dates = series.dates;
    const inds = series.industries;
    const mat = series.share_heat ? series.share_heat['1'] : [];
    if (!mat || !mat.length) return;

    const data = [];
    let vmax = 0;
    for (let di = 0; di < mat.length; di++) {
      const row = mat[di];
      if (!row) continue;
      for (let ii = 0; ii < row.length; ii++) {
        const v = row[ii];
        if (v == null) continue;
        data.push([di, ii, v]);
        if (Math.abs(v) > vmax) vmax = Math.abs(v);
      }
    }
    const vrange = Math.max(vmax, 0.02);

    STATE.heatChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 120, right: 60, top: 20, bottom: 60 },
      tooltip: {
        position: 'top',
        formatter: (p) => {
          const d = dates[p.data[0]]; const ind = inds[p.data[1]]; const v = p.data[2];
          return `<b>${fmt(d)}</b><br/>${ind}<br/><b>md_share = ${(v>=0?'+':'')+v.toFixed(4)}</b>`;
        },
        backgroundColor: C('--bg-card'), borderColor: C('--border-color'),
        textStyle: { color: C('--text-primary'), fontSize: 12 }
      },
      xAxis: {
        type: 'category', data: dates.map(fmt),
        axisLabel: { color: C('--text-secondary'), fontSize: 10, interval: Math.floor(dates.length/8) },
        axisLine: { lineStyle: { color: C('--border-color') } }, axisTick: { show: false }
      },
      yAxis: {
        type: 'category', data: inds,
        axisLabel: { color: C('--text-secondary'), fontSize: 10 },
        axisLine: { lineStyle: { color: C('--border-color') } }, axisTick: { show: false }
      },
      visualMap: {
        min: -vrange, max: vrange, calculable: true, orient: 'vertical', right: 0, top: 30,
        itemHeight: 180, itemWidth: 12,
        textStyle: { color: C('--text-secondary'), fontSize: 10 },
        inRange: { color: [C('--div-neg'), C('--div-neg-soft'), C('--div-mid'), C('--div-pos-soft'), C('--div-pos')] }
      },
      series: [{
        type: 'heatmap', data: data,
        progressive: 2000,
        emphasis: { itemStyle: { borderColor: C('--text-primary'), borderWidth: 1 } }
      }]
    }, true);
  }

  function renderRanking(series, kpi) {
    const el = document.getElementById('chart-rank');
    if (!el) return;
    if (!STATE.rankChart) STATE.rankChart = echarts.init(el);

    const mat = series.share_heat ? series.share_heat['1'] : [];
    if (!mat || !mat.length) return;
    const row = mat[mat.length - 1];
    if (!row) return;

    const inds = series.industries;
    const items = inds.map((nm, i) => ({ name: nm, v: row[i] })).filter(x => x.v != null);
    items.sort((a, b) => a.v - b.v);

    const cats = items.map(x => x.name);
    const vals = items.map(x => x.v);
    const barColors = vals.map(v => divColor(v));

    STATE.rankChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 130, right: 40, top: 16, bottom: 40 },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps) => {
          const p = ps[0];
          return `${p.name}<br/><b>md_share = ${(p.data>=0?'+':'')+p.data.toFixed(4)}</b>`;
        },
        backgroundColor: C('--bg-card'), borderColor: C('--border-color'),
        textStyle: { color: C('--text-primary'), fontSize: 12 }
      },
      xAxis: {
        type: 'value', min: Math.min(...vals, -0.01), max: Math.max(...vals, 0.01),
        axisLabel: { color: C('--text-secondary'), fontSize: 10, formatter: v => (v>=0?'+':'')+v.toFixed(2) },
        splitLine: { lineStyle: { color: C('--border-color') } },
        axisLine: { lineStyle: { color: C('--border-color') } }
      },
      yAxis: {
        type: 'category', data: cats,
        axisLabel: { color: C('--text-secondary'), fontSize: 10 },
        axisLine: { lineStyle: { color: C('--border-color') } }, axisTick: { show: false }
      },
      series: [{
        type: 'bar', data: vals.map((v,i) => ({ value: v, itemStyle: { color: barColors[i] } })),
        barWidth: '60%',
        label: {
          show: true, position: (p) => p.data.value >= 0 ? 'right' : 'left',
          formatter: p => (p.data.value>=0?'+':'')+p.data.value.toFixed(3),
          color: C('--text-secondary'), fontSize: 9
        }
      }]
    }, true);
  }

  function renderIC(series) {
    const el = document.getElementById('chart-ic');
    if (!el) return;
    if (!STATE.icChart) STATE.icChart = echarts.init(el);

    const icData = series.ic_series || {};
    const k1 = icData.k1 || [], k3 = icData.k3 || [];

    STATE.icChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 55, right: 30, top: 30, bottom: 40 },
      legend: {
        data: ['单日 (k=1)', '3日平滑 (k=3)'], top: 0,
        textStyle: { color: C('--text-secondary'), fontSize: 11 },
        icon: 'roundRect', itemWidth: 14, itemHeight: 8
      },
      tooltip: {
        trigger: 'axis',
        formatter: (ps) => {
          let s = `<b>${fmt(ps[0].axisValue)}</b>`;
          ps.forEach(p => { s += `<br/>${p.marker} ${p.seriesName}: <b>${(p.data[1]>=0?'+':'')+p.data[1].toFixed(3)}</b>`; });
          return s;
        },
        backgroundColor: C('--bg-card'), borderColor: C('--border-color'),
        textStyle: { color: C('--text-primary'), fontSize: 12 }
      },
      xAxis: {
        type: 'category', data: k1.map(x => fmt(x[0])),
        axisLabel: { color: C('--text-secondary'), fontSize: 10, interval: Math.floor(k1.length/8) },
        axisLine: { lineStyle: { color: C('--border-color') } }, axisTick: { show: false }
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: C('--text-secondary'), fontSize: 10, formatter: v => (v>=0?'+':'')+v.toFixed(2) },
        splitLine: { lineStyle: { color: C('--border-color') } },
        axisLine: { lineStyle: { color: C('--border-color') } }
      },
      series: [
        { name: '单日 (k=1)', type: 'line', data: k1.map(x => [fmt(x[0]), x[1]]),
          smooth: true, symbol: 'circle', symbolSize: 5,
          lineStyle: { color: C('--s1'), width: 2 }, itemStyle: { color: C('--s1') } },
        { name: '3日平滑 (k=3)', type: 'line', data: k3.map(x => [fmt(x[0]), x[1]]),
          smooth: true, symbol: 'circle', symbolSize: 5,
          lineStyle: { color: C('--s2'), width: 2 }, itemStyle: { color: C('--s2') } }
      ],
      markLine: {
        symbol: 'none', silent: true,
        data: [{ yAxis: 0, lineStyle: { color: C('--border-color'), type: 'dashed' } }]
      }
    }, true);
  }

  function renderQuintile(series) {
    const el = document.getElementById('chart-quint');
    if (!el) return;
    if (!STATE.quintChart) STATE.quintChart = echarts.init(el);

    const qp = series.quintile_perf || {};
    const q1 = qp.k1 || [], q3 = qp.k3 || [];
    const groups = ['q0\n最深净流出','q1','q2','q3','q4\n最深净流入'];

    function seriesFor(q, name, color) {
      return {
        name, type: 'bar', data: q.map(v => v==null ? 0 : v),
        barWidth: '28%',
        itemStyle: { color },
        label: {
          show: true, position: 'top',
          formatter: p => (p.value>=0?'+':'')+p.value.toFixed(2)+'%',
          color: C('--text-secondary'), fontSize: 10
        }
      };
    }

    STATE.quintChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 55, right: 30, top: 30, bottom: 50 },
      legend: {
        data: ['单日 (k=1)','3日平滑 (k=3)'], top: 0,
        textStyle: { color: C('--text-secondary'), fontSize: 11 },
        icon: 'roundRect', itemWidth: 14, itemHeight: 8
      },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps) => {
          let s = `<b>${ps[0].axisValue.replace('\n',' ')}</b>`;
          ps.forEach(p => { s += `<br/>${p.marker} ${p.seriesName}: <b>${(p.value>=0?'+':'')+p.value.toFixed(3)}%</b>`; });
          return s;
        },
        backgroundColor: C('--bg-card'), borderColor: C('--border-color'),
        textStyle: { color: C('--text-primary'), fontSize: 12 }
      },
      xAxis: {
        type: 'category', data: groups,
        axisLabel: { color: C('--text-secondary'), fontSize: 10 },
        axisLine: { lineStyle: { color: C('--border-color') } }, axisTick: { show: false }
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: C('--text-secondary'), fontSize: 10, formatter: v => v.toFixed(1)+'%' },
        splitLine: { lineStyle: { color: C('--border-color') } },
        axisLine: { lineStyle: { color: C('--border-color') } }
      },
      series: [
        seriesFor(q1, '单日 (k=1)', C('--s1')),
        seriesFor(q3, '3日平滑 (k=3)', C('--s2'))
      ],
      markLine: {
        symbol: 'none', silent: true,
        data: [{ yAxis: 0, lineStyle: { color: C('--border-color'), type: 'dashed' } }]
      }
    }, true);
  }

  // ==================== Tab2: 涨停池 ====================
  function renderLimitupTab(data, calendar, selectedDate) {
    if (!data || !calendar) return;
    renderCalendar(calendar, selectedDate);
    if (data.by_date && data.by_date[selectedDate]) {
      renderLimitupDay(data.by_date[selectedDate]);
    } else {
      // try loading historical slice
      loadJson('data/limitup/' + selectedDate + '.json')
        .then(dayData => renderLimitupDay(dayData))
        .catch(() => {
          document.getElementById('limitup-day-content').innerHTML =
            '<p style="color:var(--text-secondary)">该日无涨停数据</p>';
        });
    }
    renderLimitupChart(data);
    // update date display
    document.getElementById('limitup-current-date').textContent = fmt(selectedDate);
  }

  let calendarGrid = null;
  function renderCalendar(calendar, selectedDate) {
    const container = document.getElementById('calendar-grid');
    if (!container) return;
    calendarGrid = calendar;

    const dates = calendar.dates || [];
    const selected = selectedDate;

    // build month navigator
    let html = '';
    let currentMonth = '';
    for (const entry of dates) {
      const d = entry.date;
      const monthKey = d.slice(0, 6);
      if (monthKey !== currentMonth) {
        if (currentMonth) html += '</div>';
        currentMonth = monthKey;
        html += `<div class="cal-month-label">${d.slice(0,4)}-${d.slice(4,6)}</div><div class="calendar-grid-inner">`;
      }
      const dayOfWeek = new Date(parseInt(d.slice(0,4)), parseInt(d.slice(4,6))-1, parseInt(d.slice(6,8))).getDay();
      // pad start of month
      if (html.endsWith('<div class="calendar-grid-inner">')) {
        for (let p = 0; p < dayOfWeek; p++) html += '<div class="cal-day other-month"></div>';
      }
      const cls = entry.has_data ? 'cal-day' : 'cal-day no-data';
      const sel = d === selected ? ' selected' : '';
      html += `<div class="${cls}${sel}" data-date="${d}">${parseInt(d.slice(6,8))}</div>`;
    }
    if (dates.length) html += '</div>';
    container.innerHTML = html;

    // click handlers
    container.querySelectorAll('.cal-day:not(.no-data)').forEach(el => {
      el.addEventListener('click', () => {
        const d = el.dataset.date;
        if (d) selectLimitupDate(d);
      });
    });

    // prev/next day buttons
    document.getElementById('btn-prev-day').onclick = () => {
      const idx = dates.findIndex(e => e.date === selectedDate);
      if (idx > 0) selectLimitupDate(dates[idx - 1].date);
    };
    document.getElementById('btn-next-day').onclick = () => {
      const idx = dates.findIndex(e => e.date === selectedDate);
      if (idx < dates.length - 1) selectLimitupDate(dates[idx + 1].date);
    };
  }

  function selectLimitupDate(d) {
    STATE.limitupDate = d;
    renderLimitupTab(STATE.limitup, STATE.calendar, d);
  }

  function renderLimitupDay(dayData) {
    const container = document.getElementById('limitup-day-content');
    if (!dayData || !dayData.tables) {
      container.innerHTML = '<p style="color:var(--text-secondary)">无数据</p>';
      return;
    }

    const kpi = dayData.kpi || {};
    const tiers = (dayData.tables.tiers || []).slice(0, 10); // top 10 tiers
    const indConcentration = (dayData.series && dayData.series.industry_concentration) || [];

    let html = '<div class="kpi-row">';
    html += `<span>涨停 ${kpi.limit_up_cnt} 炸板 ${kpi.limit_break_cnt} 跌停 ${kpi.limit_down_cnt} 最高板 ${kpi.max_limit_times}</span>`;
    html += '</div>';

    // tier table
    if (tiers.length) {
      html += '<h3 style="margin:12px 0 8px">梯队</h3>';
      html += '<table class="data-table"><thead><tr><th>连板</th><th>数量</th><th>股票</th><th>行业</th><th>封板时间</th><th>炸板时间</th><th>封单(亿)</th></tr></thead><tbody>';
      for (const t of tiers) {
        for (const m of t.members) {
          html += `<tr><td>${t.limit_times}板</td><td>${t.count}</td><td>${m.name}</td><td>${m.industry}</td>`;
          html += `<td>${m.first_time}</td><td>${m.last_time !== m.first_time ? m.last_time : ''}</td>`;
          html += `<td>${(m.fd_amount / 1e8).toFixed(1)}</td></tr>`;
        }
      }
      html += '</tbody></table>';
    }

    // industry concentration
    if (indConcentration.length) {
      html += '<h3 style="margin:16px 0 8px">行业聚集</h3>';
      html += '<table class="data-table"><thead><tr><th>行业</th><th>涨停数</th></tr></thead><tbody>';
      for (const ind of indConcentration.slice(0, 10)) {
        html += `<tr><td>${ind.industry}</td><td>${ind.count}</td></tr>`;
      }
      html += '</tbody></table>';
    }

    container.innerHTML = html;
  }

  function renderLimitupChart(data) {
    const el = document.getElementById('chart-limitup');
    if (!el) return;
    if (!STATE.ltChart) STATE.ltChart = echarts.init(el);

    const dates = data.dates || [];
    const byDate = data.by_date || {};

    const upCounts = [];
    for (const d of dates) {
      const day = byDate[d];
      upCounts.push([d, day && day.kpi ? (day.kpi.limit_up_cnt || 0) : 0]);
    }

    STATE.ltChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 45, right: 20, top: 20, bottom: 30 },
      tooltip: {
        trigger: 'axis',
        formatter: (ps) => `<b>${fmt(ps[0].axisValue)}</b><br/>涨停: <b>${ps[0].data[1]}</b>`,
        backgroundColor: C('--bg-card'), borderColor: C('--border-color'),
        textStyle: { color: C('--text-primary'), fontSize: 12 }
      },
      xAxis: {
        type: 'category', data: upCounts.map(x => fmt(x[0])),
        axisLabel: { color: C('--text-secondary') || '#a0a5b5', fontSize: 10, interval: Math.floor(upCounts.length/8) },
        axisLine: { lineStyle: { color: C('--border-color') } }, axisTick: { show: false }
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: C('--text-secondary'), fontSize: 10 },
        splitLine: { lineStyle: { color: C('--border-color') } },
        axisLine: { lineStyle: { color: C('--border-color') } }
      },
      series: [{
        type: 'line', data: upCounts.map(x => [fmt(x[0]), x[1]]),
        lineStyle: { color: C('--accent-red'), width: 1.5 },
        itemStyle: { color: C('--accent-red') },
        symbol: 'none', areaStyle: { color: C('--accent-red'), opacity: 0.08 }
      }]
    }, true);
  }

  // ==================== 窗口 resize ====================
  window.addEventListener('resize', () => {
    ['heatChart','icChart','quintChart','rankChart','ltChart'].forEach(k => {
      if (STATE[k]) STATE[k].resize();
    });
  });

  // ==================== 主入口 ====================
  async function init() {
    try {
      const [industry, limitupMain, calendar] = await Promise.all([
        loadJson('data/industry.json'),
        loadJson('data/limitup_main.json'),
        loadJson('data/calendar.json'),
      ]);

      STATE.industry = industry;
      STATE.limitup = limitupMain;
      STATE.calendar = calendar;

      // 概览条
      renderOverview(limitupMain.overview || industry);

      // Tab1 行业
      renderIndustryTab(industry);

      // Tab2 涨停
      const calDates = calendar.dates || [];
      const lastTrade = calDates.filter(e => e.has_data).pop();
      STATE.limitupDate = lastTrade ? lastTrade.date : '';
      renderLimitupTab(limitupMain, calendar, STATE.limitupDate);

    } catch (e) {
      console.error('初始化失败:', e);
      document.getElementById('overview-bar').innerHTML =
        `<div style="color:var(--accent-red);padding:12px">加载数据失败: ${e.message}</div>`;
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
