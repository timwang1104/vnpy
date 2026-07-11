/**
 * overview.js — 市场概览页面
 *
 * 从 market_research/report/static/app.js 提取的概览 tab 独立模块。
 * 展示市场 KPI 卡片（净流入、涨停数等）及迷你流入时序图。
 *
 * Usage:
 *   <div id="overview-container"></div>
 *   <script src="/static/shared.js"></script>
 *   <script src="/pages/overview.js"></script>
 *   <script>initOverview('overview-container');</script>
 */

(function () {
  'use strict';

  // ==================== 依赖检查 ====================

  if (!window.__shared) {
    console.error('overview.js 依赖 shared.js，请先加载 shared.js');
    return;
  }

  var _ = window.__shared;

  // ==================== 内部状态 ====================

  /** @type {Object<string, echarts.Chart|null>} */
  var _charts = {};

  /** 当前数据缓存 */
  var _data = {
    overview: null,
    miniSeries: [],
  };

  // ==================== 渲染函数 ====================

  /**
   * 渲染 KPI 卡片
   * @param {HTMLElement} container
   * @param {Object}      data — overview.kpi 对象
   */
  function renderKpiCards(container, kpi) {
    if (!kpi) return;

    var items = [
      { label: '沪深净流入', value: _.fmtMoney(kpi.net_inflow),      cls: _.signClass(kpi.net_inflow) },
      { label: '主力净流入', value: _.fmtMoney(kpi.large_inflow),     cls: _.signClass(kpi.large_inflow) },
      { label: '沪指',       value: kpi.close_sh != null ? kpi.close_sh.toFixed(1) : '-', cls: '' },
      { label: '涨停',       value: kpi.limit_up_cnt ?? '-',           cls: '' },
      { label: '炸板',       value: kpi.limit_break_cnt ?? '-',        cls: '' },
      { label: '跌停',       value: kpi.limit_down_cnt ?? '-',         cls: '' },
      { label: '最高板',     value: kpi.max_limit_times ?? '-',        cls: '' },
    ];

    var html = items.map(function (it) {
      var cls = it.cls ? ' class="ov-value ' + it.cls + '"' : ' class="ov-value"';
      return '<div class="ov-item">' +
        '<div class="ov-label">' + it.label + '</div>' +
        '<div' + cls + '>' + it.value + '</div>' +
        '</div>';
    }).join('');

    container.innerHTML = html;
  }

  /**
   * 渲染迷你流入时序图
   * @param {HTMLElement} container
   * @param {Array}       series — market_flow_mini 数组 [{date, net_amount, large_inflow}]
   */
  function renderMiniChart(container, series) {
    if (!series || !series.length) {
      container.innerHTML = '<p class="text-muted" style="padding:20px;text-align:center">暂无时序数据</p>';
      return;
    }

    var dates = series.map(function (d) { return _.fmtDate(d.date); });
    var netData = series.map(function (d) { return d.net_amount; });
    var largeData = series.map(function (d) { return d.large_inflow; });

    var chart = echarts.init(container);

    _charts.miniChart = chart;

    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 60, right: 20, top: 24, bottom: 30 },
      tooltip: {
        trigger: 'axis',
        formatter: function (ps) {
          var s = '<b>' + ps[0].axisValue + '</b>';
          ps.forEach(function (p) {
            if (p.seriesIndex === 0) {
              s += '<br/>' + p.marker + ' 净流入: <b>' + _.fmtMoney(p.data) + '</b>';
            } else {
              s += '<br/>' + p.marker + ' 主力净流入: <b>' + _.fmtMoney(p.data) + '</b>';
            }
          });
          return s;
        },
        backgroundColor: _.cssVar('--bg-card'),
        borderColor: _.cssVar('--border-color'),
        textStyle: { color: _.cssVar('--text-primary'), fontSize: 12 },
      },
      legend: {
        data: ['净流入', '主力净流入'],
        top: 0,
        textStyle: { color: _.cssVar('--text-secondary'), fontSize: 11 },
        icon: 'roundRect', itemWidth: 14, itemHeight: 8,
      },
      xAxis: {
        type: 'category', data: dates,
        axisLabel: {
          color: _.cssVar('--text-secondary'), fontSize: 10,
          interval: Math.max(0, Math.floor(dates.length / 6)),
        },
        axisLine: { lineStyle: { color: _.cssVar('--border-color') } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          color: _.cssVar('--text-secondary'), fontSize: 10,
          formatter: function (v) { return _.fmtMoney(v); },
        },
        splitLine: { lineStyle: { color: _.cssVar('--border-color') } },
        axisLine: { lineStyle: { color: _.cssVar('--border-color') } },
      },
      series: [
        {
          name: '净流入',
          type: 'line',
          data: netData,
          smooth: true,
          symbol: 'circle',
          symbolSize: 4,
          lineStyle: { color: _.cssVar('--s1'), width: 2 },
          itemStyle: { color: _.cssVar('--s1') },
          areaStyle: { color: _.cssVar('--s1'), opacity: 0.08 },
          connectNulls: false,
        },
        {
          name: '主力净流入',
          type: 'line',
          data: largeData,
          smooth: true,
          symbol: 'diamond',
          symbolSize: 4,
          lineStyle: { color: _.cssVar('--s2'), width: 1.5, type: 'dashed' },
          itemStyle: { color: _.cssVar('--s2') },
          connectNulls: false,
        },
      ],
    }, true);
  }

  // ==================== 数据加载 ====================

  /**
   * 加载概览数据并渲染
   * @param {HTMLElement} container
   */
  function loadAndRender(container) {
    Promise.all([
      _.loadJson('data/limitup_main.json'),
      _.loadJson('data/industry.json'),
    ]).then(function (results) {
      var limitupMain = results[0];
      var industry = results[1];

      // overview 数据优先来自 limitup_main.json 的 .overview 字段
      var overview = limitupMain && limitupMain.overview ? limitupMain.overview : industry;
      _data.overview = overview && overview.kpi ? overview.kpi : null;

      // 迷你时序
      _data.miniSeries = (overview && overview.series && overview.series.market_flow_mini)
        ? overview.series.market_flow_mini
        : [];

      // 渲染
      renderKpiCards(container, _data.overview);

      // 在 KPI 卡片下方创建迷你图表容器
      var chartWrap = document.getElementById('overview-mini-chart');
      if (!chartWrap) {
        chartWrap = document.createElement('div');
        chartWrap.id = 'overview-mini-chart';
        chartWrap.className = 'chart-box';
        chartWrap.innerHTML = '<h3>大盘净流入（近 20 日）</h3><div id="chart-overview-mini" class="chart-container" style="height:240px"></div>';
        container.parentNode.insertBefore(chartWrap, container.nextSibling);
      }

      var chartEl = document.getElementById('chart-overview-mini');
      if (chartEl && _data.miniSeries.length) {
        renderMiniChart(chartEl, _data.miniSeries);
      }

    }).catch(function (err) {
      container.innerHTML = '<div style="color:var(--accent-red);padding:24px;text-align:center">' +
        '加载概览数据失败: ' + err.message + '</div>';
      console.error('overview init error:', err);
    });
  }

  // ==================== Resize 处理 ====================

  function onResize() {
    for (var key in _charts) {
      if (_charts[key] && typeof _charts[key].resize === 'function') {
        _charts[key].resize();
      }
    }
  }

  // ==================== 公共 API ====================

  /**
   * 初始化概览页面
   * @param {string} containerId — KPI 卡片容器的 DOM id
   */
  window.initOverview = function (containerId) {
    var container = document.getElementById(containerId);
    if (!container) {
      console.warn('overview.js: container #' + containerId + ' 不存在');
      return;
    }

    // 加载数据并渲染
    loadAndRender(container);

    // 注册 resize
    window.addEventListener('resize', onResize);
  };

})();
