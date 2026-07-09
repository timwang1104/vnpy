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
    ffIndustryCode: null,// current fundflow industry code
    // echarts instances
    heatChart: null,
    icChart: null,
    quintChart: null,
    rankChart: null,
    ltChart: null,
    fundflowChart: null,
    // simulator state
    simStrategies: [],   // strategy list from API
    simChart: null,      // equity curve echarts
    simRunning: false,   // is currently running a strategy
    // anomaly state
    anomalyThreshold: 2.0,
    anomalyData: null,
    _anomalyDebounce: null,
  };

  // ==================== 路由 ====================
  const sections = document.querySelectorAll('.tab-content');
  const links = document.querySelectorAll('.tab-link');

  function activateTab(hash) {
    const id = hash.replace('#','') || 'industry';
    sections.forEach(el => el.classList.toggle('active', el.id === id));
    links.forEach(el => el.classList.toggle('active', el.getAttribute('href') === '#'+id));
    // refresh anomaly banner on fundflow tab switch
    if (id === 'fundflow') {
      // 无论 STATE.industry 是否加载完成都尝试刷新（fetchAnomalies 内部处理 error 展示）
      fetchAnomalies(STATE.anomalyThreshold);
    }
    // resize charts on tab switch
    setTimeout(() => {
      ['heatChart','icChart','quintChart','rankChart','ltChart','fundflowChart','simChart'].forEach(k => {
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
    renderDatePicker(calendar, selectedDate);
    if (data.by_date && data.by_date[selectedDate]) {
      renderLimitupDay(data.by_date[selectedDate]);
    } else {
      loadJson('data/limitup/' + selectedDate + '.json')
        .then(dayData => renderLimitupDay(dayData))
        .catch(() => {
          document.getElementById('limitup-day-content').innerHTML =
            '<p style="color:var(--text-secondary)">该日无涨停数据</p>';
        });
    }
    renderLimitupChart(data);
  }

  // ==================== 日期弹出日历选择器 ====================
  let _tradeDates = [];
  let _dpOpen = false;
  let _viewYear = 0, _viewMonth = 0; // 0-based month

  function renderDatePicker(calendar, selectedDate) {
    closeDatePicker();
    const trigger = document.getElementById('limitup-current-date');
    if (!trigger) return;

    const weekdays = ['周日','周一','周二','周三','周四','周五','周六'];
    const dateObj = new Date(+selectedDate.slice(0,4), +selectedDate.slice(4,6)-1, +selectedDate.slice(6,8));
    trigger.textContent = `${selectedDate.slice(0,4)}-${selectedDate.slice(4,6)}-${selectedDate.slice(6,8)} ${weekdays[dateObj.getDay()]}`;

    _tradeDates = (calendar.dates || []).filter(e => e.has_data).map(e => e.date);
    _viewYear = +selectedDate.slice(0,4);
    _viewMonth = +selectedDate.slice(4,6) - 1;

    trigger.onclick = (e) => {
      e.stopPropagation();
      toggleDatePicker(selectedDate);
    };

    document.getElementById('dp-prev-month').onclick = (e) => {
      e.stopPropagation();
      _viewMonth--; if (_viewMonth < 0) { _viewMonth = 11; _viewYear--; }
      renderCalendarPopup(selectedDate);
    };
    document.getElementById('dp-next-month').onclick = (e) => {
      e.stopPropagation();
      _viewMonth++; if (_viewMonth > 11) { _viewMonth = 0; _viewYear++; }
      renderCalendarPopup(selectedDate);
    };

    // prev/next buttons
    document.getElementById('btn-prev-day').onclick = () => {
      const idx = _tradeDates.indexOf(selectedDate);
      if (idx > 0) selectLimitupDate(_tradeDates[idx - 1]);
    };
    document.getElementById('btn-next-day').onclick = () => {
      const idx = _tradeDates.indexOf(selectedDate);
      if (idx < _tradeDates.length - 1) selectLimitupDate(_tradeDates[idx + 1]);
    };

    renderCalendarPopup(selectedDate);
  }

  function toggleDatePicker(selectedDate) {
    _dpOpen = !_dpOpen;
    document.getElementById('dp-popup').classList.toggle('hidden', !_dpOpen);
    if (_dpOpen) {
      // Reset view to selected date's month
      _viewYear = +selectedDate.slice(0,4);
      _viewMonth = +selectedDate.slice(4,6) - 1;
      renderCalendarPopup(selectedDate);
    }
  }

  function closeDatePicker() {
    const popup = document.getElementById('dp-popup');
    if (popup) popup.classList.add('hidden');
    _dpOpen = false;
  }

  function renderCalendarPopup(selectedDate) {
    document.getElementById('dp-month-year').textContent =
      `${_viewYear}年${String(_viewMonth + 1).padStart(2, '0')}月`;

    const tradeDateSet = new Set(_tradeDates);
    const daysContainer = document.getElementById('dp-days');

    const firstDay = new Date(_viewYear, _viewMonth, 1).getDay();
    const daysInMonth = new Date(_viewYear, _viewMonth + 1, 0).getDate();

    let html = '';
    for (let i = 0; i < firstDay; i++) html += '<div class="dp-day empty"></div>';

    for (let day = 1; day <= daysInMonth; day++) {
      const dateStr = `${_viewYear}${String(_viewMonth + 1).padStart(2, '0')}${String(day).padStart(2, '0')}`;
      const hasData = tradeDateSet.has(dateStr);
      let cls = 'dp-day';
      if (!hasData) cls += ' muted';
      if (dateStr === selectedDate) cls += ' selected';
      html += `<div class="${cls}" data-date="${dateStr}">${day}</div>`;
    }

    daysContainer.innerHTML = html;

    daysContainer.querySelectorAll('.dp-day:not(.empty):not(.muted)').forEach(el => {
      el.addEventListener('click', () => {
        selectLimitupDate(el.dataset.date);
        closeDatePicker();
      });
    });
  }

  // Close date picker on outside click (one-time setup)
  document.addEventListener('click', (e) => {
    const picker = document.getElementById('date-picker');
    if (_dpOpen && picker && !picker.contains(e.target)) closeDatePicker();
  });

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

  // ==================== Tab3: 行业时序 ====================

  function getDefaultIndustryCode(industryData) {
    // 从 Tab1 排名（最新日 md_share 最大）找默认行业
    if (!industryData || !industryData.series) return null;
    const series = industryData.series;
    const mat = series.share_heat ? series.share_heat['1'] : [];
    if (!mat || !mat.length) return null;
    const lastRow = mat[mat.length - 1];
    if (!lastRow) return null;

    let maxIdx = 0, maxVal = -Infinity;
    for (let i = 0; i < lastRow.length; i++) {
      if (lastRow[i] != null && lastRow[i] > maxVal) {
        maxVal = lastRow[i];
        maxIdx = i;
      }
    }
    const codes = series.industries_code;
    if (codes && codes[maxIdx]) return codes[maxIdx];
    return null;
  }

  function populateIndustrySelect(industryData) {
    const select = document.getElementById('ff-industry-select');
    if (!select || !industryData || !industryData.series) return;

    const names = industryData.series.industries || [];
    const codes = industryData.series.industries_code || [];
    if (!names.length || !codes.length || names.length !== codes.length) return;

    let html = '';
    for (let i = 0; i < names.length; i++) {
      const code = codes[i] || '';
      html += `<option value="${code}">${names[i]}</option>`;
    }
    select.innerHTML = html;
  }

  function renderFundflowTab(tsCode, mode) {
    const el = document.getElementById('chart-fundflow');
    if (!el) return;
    if (!STATE.fundflowChart) STATE.fundflowChart = echarts.init(el);

    // Set loading
    STATE.fundflowChart.showLoading('default', {
      text: '加载中…',
      textColor: C('--text-secondary'),
      maskColor: 'rgba(0,0,0,0.3)',
    });

    fetch('/api/industry/' + encodeURIComponent(tsCode) + '/timeseries?mode=' + encodeURIComponent(mode))
      .then(r => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(data => {
        STATE.fundflowChart.hideLoading();
        if (!data || !data.dates || !data.dates.length) {
          el.innerHTML = '<p style="color:var(--text-secondary);padding:40px;text-align:center">该行业无资金流数据</p>';
          return;
        }

        const dates = data.dates.map(fmt);
        const values = data.values;
        const close = data.close;
        const pct = data.pct_change;
        const modeLabel = data.meta.mode_label;

        // update chart title
        var titleEl = document.getElementById('ff-chart-title');
        if (titleEl) titleEl.textContent = data.name + ' — 行业时序';

        // Right axis scale formatter
        function fmtClose(v) {
          if (v == null) return '-';
          return v >= 10000 ? (v / 10000).toFixed(2) + '万' : v.toFixed(2);
        }

        STATE.fundflowChart.setOption({
          backgroundColor: 'transparent',
          grid: { left: 60, right: 80, top: 30, bottom: 60 },
          legend: {
            data: [
              { name: modeLabel, icon: 'roundRect' },
              { name: '行业指数', icon: 'roundRect' },
              { name: '涨跌幅（%）', icon: 'roundRect' },
            ],
            selected: {
              '涨跌幅（%）': false,  // 默认隐藏
            },
            top: 0,
            textStyle: { color: C('--text-secondary'), fontSize: 11 },
            icon: 'roundRect', itemWidth: 14, itemHeight: 8,
          },
          tooltip: {
            trigger: 'axis',
            formatter: function (ps) {
              let s = '<b>' + ps[0].axisValue + '</b>';
              ps.forEach(function (p) {
                var v = p.data;
                if (v == null) return;
                if (p.seriesIndex === 0) {
                  s += '<br/>' + p.marker + ' ' + modeLabel + ': <b>' + (v >= 0 ? '+' : '') + v.toFixed(4) + '</b>';
                } else if (p.seriesIndex === 1) {
                  s += '<br/>' + p.marker + ' 行业指数: <b>' + v.toFixed(2) + '</b>';
                } else {
                  s += '<br/>' + p.marker + ' 涨跌幅: <b>' + (v >= 0 ? '+' : '') + v.toFixed(2) + '%</b>';
                }
              });
              return s;
            },
            backgroundColor: C('--bg-card'), borderColor: C('--border-color'),
            textStyle: { color: C('--text-primary'), fontSize: 12 },
          },
          xAxis: {
            type: 'category', data: dates,
            axisLabel: {
              color: C('--text-secondary'), fontSize: 10,
              interval: Math.floor(dates.length / 10),
            },
            axisLine: { lineStyle: { color: C('--border-color') } },
            axisTick: { show: false },
          },
          yAxis: [
            {
              type: 'value', name: modeLabel,
              nameTextStyle: { color: C('--text-secondary'), fontSize: 11 },
              axisLabel: {
                color: C('--text-secondary'), fontSize: 10,
                formatter: function (v) {
                  if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1) + 'k';
                  if (Math.abs(v) >= 1) return v.toFixed(2);
                  return v.toFixed(4);
                },
              },
              splitLine: { lineStyle: { color: C('--border-color') } },
              axisLine: { lineStyle: { color: C('--border-color') } },
            },
            {
              type: 'value', name: '行业指数',
              nameTextStyle: { color: C('--text-secondary'), fontSize: 11 },
              axisLabel: {
                color: C('--text-secondary'), fontSize: 10,
                formatter: fmtClose,
              },
              splitLine: { show: false },
              axisLine: { lineStyle: { color: C('--border-color') } },
            },
            {
              type: 'value',
              name: '',
              axisLabel: { show: false },
              splitLine: { show: false },
              axisLine: { show: false },
              axisTick: { show: false },
              nameTextStyle: { color: C('--text-secondary'), fontSize: 11 },
            },
          ],
          series: [
            {
              name: modeLabel,
              type: 'line',
              yAxisIndex: 0,
              data: values,
              smooth: true,
              symbol: 'none',
              lineStyle: { color: C('--s1'), width: 2 },
              itemStyle: { color: C('--s1') },
              areaStyle: { color: C('--s1'), opacity: 0.06 },
              connectNulls: false,
            },
            {
              name: '行业指数',
              type: 'line',
              yAxisIndex: 1,
              data: close,
              smooth: true,
              symbol: 'none',
              lineStyle: { color: C('--s3'), width: 1.5 },
              itemStyle: { color: C('--s3') },
              connectNulls: false,
            },
            {
              name: '涨跌幅（%）',
              type: 'line',
              yAxisIndex: 2,
              data: pct,
              smooth: true,
              symbol: 'none',
              lineStyle: { color: C('--s2'), width: 1.5, type: 'dashed' },
              itemStyle: { color: C('--s2') },
              connectNulls: false,
            },
          ],
          dataZoom: [
            {
              type: 'slider',
              start: 0,
              end: 100,
              height: 20,
              bottom: 10,
              borderColor: C('--border-color'),
              backgroundColor: C('--bg-card'),
              dataBackground: {
                lineStyle: { color: C('--s1'), opacity: 0.3 },
                areaStyle: { color: C('--s1'), opacity: 0.05 },
              },
              selectedDataBackground: {
                lineStyle: { color: C('--s1'), opacity: 0.6 },
                areaStyle: { color: C('--s1'), opacity: 0.15 },
              },
              textStyle: { color: C('--text-secondary'), fontSize: 10 },
            },
          ],
        }, true);
      })
      .catch(function (err) {
        STATE.fundflowChart.hideLoading();
        el.innerHTML = '<p style="color:var(--accent-red);padding:40px;text-align:center">数据加载失败: ' + err.message + '</p>';
        console.error('fundflow fetch error:', err);
      });
  }

  // ==================== 异常行业提示区 ====================

  function fetchAnomalies(threshold) {
    STATE.anomalyThreshold = threshold;
    const banner = document.getElementById('ff-anomaly-banner');
    const itemsEl = document.getElementById('ff-anomaly-items');
    const metaEl = document.getElementById('ff-anomaly-meta');
    if (!banner || !itemsEl || !metaEl) return;

    // 显示加载状态
    metaEl.textContent = '加载中…';
    itemsEl.innerHTML = '<span class="anomaly-overflow">正在获取异常行业数据…</span>';

    fetch('/api/industry/anomalies?threshold=' + threshold + '&limit=10')
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        STATE.anomalyData = data;
        renderAnomalyBanner(data);
      })
      .catch(function (err) {
        console.error('anomaly fetch error:', err);
        STATE.anomalyData = null;
        metaEl.textContent = '';
        itemsEl.innerHTML = '<span class="anomaly-overflow">⚠️ 无法获取异常数据' +
          (err.message !== 'Failed to fetch' ? ': ' + err.message : '') + '</span>';
      });
  }

  function renderAnomalyBanner(data) {
    const itemsEl = document.getElementById('ff-anomaly-items');
    const metaEl = document.getElementById('ff-anomaly-meta');
    if (!itemsEl || !metaEl) return;

    // meta text
    metaEl.textContent = 'Top ' + Math.min(data.total_anomalies, data.limit) +
      ' / ' + data.total_anomalies + ' 异常行业（共 ' + data.total_industries + ' 行业）';

    if (!data.anomalies || !data.anomalies.length) {
      itemsEl.innerHTML = '<span class="anomaly-overflow">无行业超过阈值 |z|>' + data.threshold.toFixed(1) + '</span>';
      return;
    }

    var currentCode = STATE.ffIndustryCode;
    var html = '';
    for (var i = 0; i < data.anomalies.length; i++) {
      var a = data.anomalies[i];
      var isActive = a.ts_code === currentCode;
      var zCls = a.latest_zscore >= 0 ? 'zpos' : 'zneg';
      var zSign = a.latest_zscore >= 0 ? '+' : '';
      html += '<div class="anomaly-item' + (isActive ? ' active' : '') + '" data-code="' + a.ts_code + '">' +
        a.name + ' <span class="' + zCls + '">' + zSign + a.latest_zscore.toFixed(2) + '</span></div>';
    }
    itemsEl.innerHTML = html;

    // overflow text
    if (data.total_anomalies > data.anomalies.length) {
      var extra = data.total_anomalies - data.anomalies.length;
      itemsEl.insertAdjacentHTML('beforeend', '<span class="anomaly-overflow">… 还有 ' + extra + ' 个</span>');
    }

    // bind click: jump to industry
    itemsEl.querySelectorAll('.anomaly-item').forEach(function (el) {
      el.addEventListener('click', function () {
        var code = this.dataset.code;
        if (!code) return;
        var select = document.getElementById('ff-industry-select');
        if (select) {
          select.value = code;
          STATE.ffIndustryCode = code;
          var mode = document.getElementById('ff-mode-select').value;
          renderFundflowTab(code, mode);
          // re-highlight
          renderAnomalyBanner(STATE.anomalyData);
        }
      });
    });
  }

  function setupAnomalyBanner() {
    // preset buttons
    document.querySelectorAll('.anomaly-threshold-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var t = parseFloat(this.dataset.t);
        if (isNaN(t)) return;
        // update active state
        document.querySelectorAll('.anomaly-threshold-btn').forEach(function (b) {
          b.classList.remove('active');
        });
        this.classList.add('active');
        document.getElementById('ff-threshold-input').value = t;
        fetchAnomalies(t);
      });
    });

    // custom input
    var input = document.getElementById('ff-threshold-input');
    if (input) {
      input.addEventListener('input', function () {
        var t = parseFloat(this.value);
        if (isNaN(t) || t < 1 || t > 5) return;
        // clear preset active
        document.querySelectorAll('.anomaly-threshold-btn').forEach(function (b) {
          b.classList.remove('active');
        });
        // debounce
        if (STATE._anomalyDebounce) clearTimeout(STATE._anomalyDebounce);
        STATE._anomalyDebounce = setTimeout(function () {
          fetchAnomalies(t);
          STATE._anomalyDebounce = null;
        }, 300);
      });
    }
  }

  // ==================== Tab4: 模拟盘 ====================

  function renderSimulatorTab() {
    const listEl = document.getElementById('sim-strategy-list');
    if (!listEl) return;

    // 隐藏老的结果
    document.getElementById('sim-result-header').style.display = 'none';

    if (!STATE.simStrategies.length) {
      listEl.innerHTML = '<p class="text-muted">暂无策略，请点击"刷新策略"或放入 strategies/ 目录</p>';
      return;
    }

    let html = '';
    for (const s of STATE.simStrategies) {
      const params = s.parameters || [];
      const latest = s.latest_batch || null;

      html += `<div class="sim-card" data-id="${s.id}">`;
      html += `<div class="sim-card-header">`;
      html += `<span class="sim-card-name">${s.name}</span>`;
      html += `<span class="sim-card-author">${s.author || 'unknown'}</span>`;
      if (latest && latest.status === 'completed') {
        const ret = latest.total_return;
        html += `<span class="sim-card-return ${ret >= 0 ? 'up' : 'down'}">收益: ${(ret >= 0 ? '+' : '')}${ret.toFixed(2)}%</span>`;
      }
      html += `</div>`;

      html += `<div class="sim-card-params">`;
      for (const p of params) {
        const name = p.name;
        const label = p.label || name;
        const type = p.type || 'string';
        const def = p.default !== undefined ? p.default : '';
        html += `<label class="sim-param">${label}: `;
        if (type === 'int' || type === 'float') {
          html += `<input type="number" class="sim-param-input" data-pname="${name}" value="${def}" step="${type === 'float' ? '0.1' : '1'}">`;
        } else {
          html += `<input type="text" class="sim-param-input" data-pname="${name}" value="${def}">`;
        }
        html += `</label>`;
      }
      html += `<button class="btn sim-run-btn" data-id="${s.id}">运行</button>`;
      html += `</div>`;

      if (latest) {
        html += `<div class="sim-card-meta">`;
        html += `最近运行: ${latest.run_at ? latest.run_at.slice(0,10) : '-'} `;
        html += `| 状态: ${latest.status} `;
        html += `| 最终权益: ${latest.final_equity ? fmtMoney(latest.final_equity) : '-'}`;
        html += `</div>`;
      }

      html += `</div>`;
    }
    listEl.innerHTML = html;

    // 绑定运行按钮
    listEl.querySelectorAll('.sim-run-btn').forEach(btn => {
      btn.addEventListener('click', function () {
        const id = this.dataset.id;
        runSimStrategy(id);
      });
    });
  }

  async function runSimStrategy(strategyId) {
    if (STATE.simRunning) {
      alert('已有策略在运行，请等待完成');
      return;
    }
    STATE.simRunning = true;

    // Gather parameters from the card
    const card = document.querySelector(`.sim-card[data-id="${strategyId}"]`);
    const inputs = card ? card.querySelectorAll('.sim-param-input') : [];
    const setting = {};
    inputs.forEach(inp => {
      setting[inp.dataset.pname] = inp.value;
    });

    const runBtn = card ? card.querySelector('.sim-run-btn') : null;
    if (runBtn) {
      runBtn.textContent = '运行中…';
      runBtn.disabled = true;
    }

    try {
      const resp = await fetch(`/api/sim/strategies/${strategyId}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ setting }),
      });
      const result = await resp.json();

      if (result.status === 'error') {
        alert('运行失败: ' + (result.message || '未知错误'));
        return;
      }

      // Small delay then reload data
      await new Promise(r => setTimeout(r, 1000));

      // Reload strategy detail to get updated batch info
      const detailResp = await fetch(`/api/sim/strategies/${strategyId}`);
      if (detailResp.ok) {
        const detail = await detailResp.json();
        // Update strategy in list
        const idx = STATE.simStrategies.findIndex(s => s.id == strategyId);
        if (idx >= 0) {
          STATE.simStrategies[idx] = detail;
        }
        renderSimulatorTab();
      }

      // Load results
      await loadSimResults(strategyId);

    } catch (e) {
      console.error('run error:', e);
      alert('运行出错: ' + e.message);
    } finally {
      STATE.simRunning = false;
      if (runBtn) {
        runBtn.textContent = '运行';
        runBtn.disabled = false;
      }
    }
  }

  async function loadSimResults(strategyId) {
    // Header
    const headerEl = document.getElementById('sim-result-header');
    headerEl.style.display = 'block';
    document.getElementById('sim-result-summary').textContent = '加载结果中…';

    try {
      const [equityResp, positionsResp, tradesResp] = await Promise.all([
        fetch(`/api/sim/strategies/${strategyId}/equity`),
        fetch(`/api/sim/strategies/${strategyId}/positions`),
        fetch(`/api/sim/strategies/${strategyId}/trades`),
      ]);

      if (!equityResp.ok || !positionsResp.ok || !tradesResp.ok) {
        document.getElementById('sim-result-summary').textContent = '结果加载失败';
        return;
      }

      const equityData = await equityResp.json();
      const positionsData = await positionsResp.json();
      const tradesData = await tradesResp.json();

      // Summary
      const dates = equityData.dates || [];
      const equity = equityData.equity || [];
      if (dates.length > 0) {
        const first = equity[0] || 0;
        const last = equity[equity.length - 1] || 0;
        const ret = first > 0 ? ((last - first) / first * 100) : 0;
        document.getElementById('sim-result-summary').innerHTML =
          `运行期间: ${dates[0]} ~ ${dates[dates.length-1]} | ` +
          `初始权益: ${fmtMoney(first)} | 最终权益: ${fmtMoney(last)} | ` +
          `收益率: <span class="${ret >= 0 ? 'up' : 'down'}">${(ret >= 0 ? '+' : '')}${ret.toFixed(2)}%</span> | ` +
          `交易次数: ${(tradesData.trades || []).length}`;
      }

      // Equity chart
      renderSimEquityChart(dates, equity);

      // Positions table
      renderSimPositionsTable(positionsData.positions || []);

      // Trades table
      renderSimTradesTable(tradesData.trades || []);

    } catch (e) {
      console.error('load results error:', e);
      document.getElementById('sim-result-summary').textContent = '结果加载出错: ' + e.message;
    }
  }

  function renderSimEquityChart(dates, equity) {
    const el = document.getElementById('chart-sim-equity');
    if (!el) return;
    if (!STATE.simChart) STATE.simChart = echarts.init(el);

    if (!dates.length) {
      STATE.simChart.clear();
      el.innerHTML = '<p class="text-muted">暂无数据</p>';
      return;
    }

    const fmtDates = dates.map(fmt);
    const firstVal = equity[0] || 1;

    STATE.simChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 70, right: 30, top: 30, bottom: 40 },
      tooltip: {
        trigger: 'axis',
        formatter: (ps) => {
          const p = ps[0];
          return `<b>${p.axisValue}</b><br/>权益: <b>${fmtMoney(p.data)}</b>`;
        },
        backgroundColor: C('--bg-card'), borderColor: C('--border-color'),
        textStyle: { color: C('--text-primary'), fontSize: 12 }
      },
      xAxis: {
        type: 'category', data: fmtDates,
        axisLabel: { color: C('--text-secondary'), fontSize: 10, interval: Math.floor(fmtDates.length / 10) },
        axisLine: { lineStyle: { color: C('--border-color') } }, axisTick: { show: false }
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: C('--text-secondary'), fontSize: 10, formatter: v => fmtMoney(v) },
        splitLine: { lineStyle: { color: C('--border-color') } },
        axisLine: { lineStyle: { color: C('--border-color') } }
      },
      series: [{
        type: 'line', data: equity,
        smooth: true, symbol: 'none',
        lineStyle: { color: C('--s1'), width: 2 },
        itemStyle: { color: C('--s1') },
        areaStyle: { color: C('--s1'), opacity: 0.08 },
        markLine: {
          symbol: 'none', silent: true,
          data: [{ yAxis: firstVal, lineStyle: { color: C('--border-color'), type: 'dashed' } }]
        }
      }]
    }, true);
  }

  function renderSimPositionsTable(positions) {
    const el = document.getElementById('sim-positions-table');
    if (!el) return;

    if (!positions.length) {
      el.innerHTML = '<p class="text-muted">无持仓</p>';
      return;
    }

    let html = '<table class="data-table"><thead><tr>' +
      '<th>股票</th><th>数量</th><th>均价</th><th>市值</th><th>盈亏</th><th>盈亏%</th></tr></thead><tbody>';
    for (const p of positions) {
      const pnlCls = p.pnl >= 0 ? 'up' : 'down';
      html += `<tr><td>${p.ts_code}</td><td>${p.volume}</td><td>${p.avg_price.toFixed(2)}</td>` +
        `<td>${fmtMoney(p.market_value)}</td>` +
        `<td class="${pnlCls}">${fmtMoney(p.pnl)}</td>` +
        `<td class="${pnlCls}">${(p.pnl_pct >= 0 ? '+' : '')}${p.pnl_pct.toFixed(2)}%</td></tr>`;
    }
    html += '</tbody></table>';
    el.innerHTML = html;
  }

  function renderSimTradesTable(trades) {
    const el = document.getElementById('sim-trades-table');
    if (!el) return;

    if (!trades.length) {
      el.innerHTML = '<p class="text-muted">无交易记录</p>';
      return;
    }

    let html = '<table class="data-table"><thead><tr>' +
      '<th>日期</th><th>股票</th><th>方向</th><th>价格</th><th>数量</th><th>金额</th><th>盈亏</th></tr></thead><tbody>';
    // Show last 100 trades
    const showTrades = trades.slice(-100);
    for (const t of showTrades) {
      const dirCls = t.direction === 'buy' ? 'up' : 'down';
      const pnlCls = t.pnl >= 0 ? 'up' : 'down';
      const formattedDate = t.trade_date ? fmt(t.trade_date) : '-';
      html += `<tr><td>${formattedDate}</td><td>${t.ts_code}</td>` +
        `<td class="${dirCls}">${t.direction === 'buy' ? '买入' : '卖出'}</td>` +
        `<td>${t.price.toFixed(2)}</td><td>${t.volume}</td><td>${fmtMoney(t.amount)}</td>` +
        `<td class="${pnlCls}">${t.pnl != 0 ? fmtMoney(t.pnl) : '-'}</td></tr>`;
    }
    html += '</tbody></table>';
    el.innerHTML = html;
  }

  async function discoverStrategies() {
    try {
      // 先触发后端扫描 strategies/ 目录
      await fetch('/api/sim/discover', { method: 'POST' });
      // 再读取最新策略列表
      const resp = await fetch('/api/sim/strategies');
      if (resp.ok) {
        const data = await resp.json();
        STATE.simStrategies = data.strategies || [];
      }
    } catch (e) {
      console.error('discover strategies error:', e);
    }
  }

  // ==================== 窗口 resize ====================
  window.addEventListener('resize', () => {
    ['heatChart','icChart','quintChart','rankChart','ltChart','fundflowChart','simChart'].forEach(k => {
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

      // Tab3 行业时序
      populateIndustrySelect(industry);
      const defaultCode = getDefaultIndustryCode(industry);
      if (defaultCode) {
        STATE.ffIndustryCode = defaultCode;
        document.getElementById('ff-industry-select').value = defaultCode;
        // 默认模式 raw
        renderFundflowTab(defaultCode, 'raw');
      }

      // Tab3 事件绑定
      document.getElementById('ff-industry-select').onchange = function () {
        STATE.ffIndustryCode = this.value;
        const mode = document.getElementById('ff-mode-select').value;
        renderFundflowTab(this.value, mode);
        // re-highlight anomaly banner
        if (STATE.anomalyData) renderAnomalyBanner(STATE.anomalyData);
      };
      document.getElementById('ff-mode-select').onchange = function () {
        const code = STATE.ffIndustryCode;
        if (code) renderFundflowTab(code, this.value);
      };

      // 异常提示区初始化
      setupAnomalyBanner();
      fetchAnomalies(STATE.anomalyThreshold);

      // Tab4 模拟盘
      const discoverBtn = document.getElementById('btn-sim-discover');
      if (discoverBtn) {
        discoverBtn.onclick = async function () {
          await discoverStrategies();
          renderSimulatorTab();
        };
      }

      // 初始加载策略列表
      await discoverStrategies();
      renderSimulatorTab();

    } catch (e) {
      console.error('初始化失败:', e);
      document.getElementById('overview-bar').innerHTML =
        `<div style="color:var(--accent-red);padding:12px">加载数据失败: ${e.message}</div>`;
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
