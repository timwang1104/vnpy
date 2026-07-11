/* industry.js — 行业资金流页面模块
   从 app.js 提取，封装行业热力图/排名/IC/五分组/时序图/异常检测
   依赖: ECharts 5+
   用法: initIndustry('container-id')
*/

(function () {
  'use strict';

  // ======================== CSS 变量工具 ========================
  const CSS = getComputedStyle(document.documentElement);
  function C(n) { return CSS.getPropertyValue(n).trim(); }

  function parseHex(h) {
    h = h.replace('#', '');
    if (h.length === 3) h = h.split('').map(function (x) { return x + x; }).join('');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  function interp(hexA, hexB, t) {
    var a = parseHex(hexA), b = parseHex(hexB);
    return 'rgb(' +
      Math.round(a[0] + (b[0] - a[0]) * t) + ',' +
      Math.round(a[1] + (b[1] - a[1]) * t) + ',' +
      Math.round(a[2] + (b[2] - a[2]) * t) + ')';
  }
  function divColor(v) {
    var a = Math.max(-1, Math.min(1, v));
    if (Math.abs(a) < 0.02) return C('--div-mid');
    if (a > 0) {
      return a < 0.5
        ? interp(C('--div-mid'), C('--div-pos-soft'), a / 0.5)
        : interp(C('--div-pos-soft'), C('--div-pos'), (a - 0.5) / 0.5);
    }
    return -a < 0.5
      ? interp(C('--div-mid'), C('--div-neg-soft'), -a / 0.5)
      : interp(C('--div-neg-soft'), C('--div-neg'), (-a - 0.5) / 0.5);
  }

  // ======================== 通用工具 ========================
  function fmt(d) { return d.slice(0, 4) + '-' + d.slice(4, 6) + '-' + d.slice(6, 8); }

  function loadJson(path) {
    return fetch(path + (path.indexOf('?') !== -1 ? '&' : '?') + 't=' + Date.now()).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + path);
      return r.json();
    });
  }

  function fmtMoney(v) {
    if (v == null) return '-';
    var abs = Math.abs(v);
    if (abs >= 1e8) return (v / 1e8).toFixed(1) + '亿';
    if (abs >= 1e4) return (v / 1e4).toFixed(0) + '万';
    return v.toFixed(0);
  }

  function signClass(v) { return v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : ''; }

  // ======================== 模块状态 ========================
  var S = {
    data: null,
    ffIndustryCode: null,
    anomalyThreshold: 2.0,
    anomalyData: null,
    _anomalyDebounce: null,
    // echarts 实例
    heatChart: null,
    rankChart: null,
    icChart: null,
    quintChart: null,
    fundflowChart: null,
  };

  // ======================== HTML 模板 ========================
  function buildHTML() {
    return '' +
      /* Tab1: 行业资金流 */
      '<section class="tab-content active">' +
        '<div class="chart-box">' +
          '<h3>行业 × 日期 的 md_share 热力图</h3>' +
          '<div id="chart-heat" class="chart-container"></div>' +
        '</div>' +
        '<div class="chart-box">' +
          '<h3>最新交易日截面排名</h3>' +
          '<div id="chart-rank" class="chart-container" style="height:500px"></div>' +
        '</div>' +
        '<div class="chart-box">' +
          '<h3>前瞻 IC 时序 (md_share vs 未来5日收益)</h3>' +
          '<div id="chart-ic" class="chart-container" style="height:280px"></div>' +
        '</div>' +
        '<div class="chart-box">' +
          '<h3>五分组前瞻超额收益</h3>' +
          '<div id="chart-quint" class="chart-container" style="height:300px"></div>' +
        '</div>' +
      '</section>' +

      /* 分隔 */
      '<hr style="margin:32px 0;border-color:var(--border-color)">' +

      /* Tab3 风格: 行业时序 + 异常检测 */
      '<div class="control-row">' +
        '<label for="ff-industry-select">行业:</label>' +
        '<select id="ff-industry-select" class="ff-select"></select>' +
        '<label for="ff-mode-select" style="margin-left:16px">指标:</label>' +
        '<select id="ff-mode-select" class="ff-select">' +
          '<option value="raw">中单净额（亿元）</option>' +
          '<option value="share">归一化 md_share</option>' +
          '<option value="zscore">Z-score（60日滚动）</option>' +
        '</select>' +
      '</div>' +

      /* 异常行业检测 */
      '<div id="ff-anomaly-banner" class="anomaly-banner">' +
        '<div class="anomaly-banner-header">' +
          '<span class="anomaly-title">🔴 异常行业检测</span>' +
          '<div class="anomaly-threshold-group">' +
            '<label>阈值 |z|&gt;</label>' +
            '<button class="anomaly-threshold-btn" data-t="1.5">1.5</button>' +
            '<button class="anomaly-threshold-btn active" data-t="2.0">2.0</button>' +
            '<button class="anomaly-threshold-btn" data-t="2.5">2.5</button>' +
            '<button class="anomaly-threshold-btn" data-t="3.0">3.0</button>' +
            '<input type="number" id="ff-threshold-input" class="ff-threshold-input" value="2.0" step="0.1" min="1" max="5">' +
          '</div>' +
          '<span class="anomaly-meta" id="ff-anomaly-meta"></span>' +
        '</div>' +
        '<div class="anomaly-items" id="ff-anomaly-items"></div>' +
      '</div>' +

      /* 时序图 */
      '<div class="chart-box">' +
        '<h3 id="ff-chart-title">行业时序</h3>' +
        '<div id="chart-fundflow" class="chart-container" style="height:450px"></div>' +
      '</div>';
  }

  // ======================== Tab1: 热力图 ========================
  function renderHeatmap(series) {
    var el = document.getElementById('chart-heat');
    if (!el) return;
    if (!S.heatChart) S.heatChart = echarts.init(el);

    var dates = series.dates;
    var inds = series.industries;
    var mat = series.share_heat ? series.share_heat['1'] : [];
    if (!mat || !mat.length) return;

    var data = [];
    var vmax = 0;
    for (var di = 0; di < mat.length; di++) {
      var row = mat[di];
      if (!row) continue;
      for (var ii = 0; ii < row.length; ii++) {
        var v = row[ii];
        if (v == null) continue;
        data.push([di, ii, v]);
        if (Math.abs(v) > vmax) vmax = Math.abs(v);
      }
    }
    var vrange = Math.max(vmax, 0.02);

    S.heatChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 120, right: 60, top: 20, bottom: 60 },
      tooltip: {
        position: 'top',
        formatter: function (p) {
          var d = dates[p.data[0]], ind = inds[p.data[1]], v = p.data[2];
          return '<b>' + fmt(d) + '</b><br/>' + ind + '<br/><b>md_share = ' + (v >= 0 ? '+' : '') + v.toFixed(4) + '</b>';
        },
        backgroundColor: C('--bg-card'), borderColor: C('--border-color'),
        textStyle: { color: C('--text-primary'), fontSize: 12 }
      },
      xAxis: {
        type: 'category', data: dates.map(fmt),
        axisLabel: {
          color: C('--text-secondary'), fontSize: 10,
          interval: Math.floor(dates.length / 8)
        },
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
        inRange: {
          color: [C('--div-neg'), C('--div-neg-soft'), C('--div-mid'), C('--div-pos-soft'), C('--div-pos')]
        }
      },
      series: [{
        type: 'heatmap', data: data,
        progressive: 2000,
        emphasis: { itemStyle: { borderColor: C('--text-primary'), borderWidth: 1 } }
      }]
    }, true);
  }

  // ======================== Tab1: 最新截面排名 ========================
  function renderRanking(series, kpi) {
    var el = document.getElementById('chart-rank');
    if (!el) return;
    if (!S.rankChart) S.rankChart = echarts.init(el);

    var mat = series.share_heat ? series.share_heat['1'] : [];
    if (!mat || !mat.length) return;
    var row = mat[mat.length - 1];
    if (!row) return;

    var inds = series.industries;
    var items = inds.map(function (nm, i) { return { name: nm, v: row[i] }; }).filter(function (x) { return x.v != null; });
    items.sort(function (a, b) { return a.v - b.v; });

    var cats = items.map(function (x) { return x.name; });
    var vals = items.map(function (x) { return x.v; });
    var barColors = vals.map(function (v) { return divColor(v); });

    S.rankChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 130, right: 40, top: 16, bottom: 40 },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: function (ps) {
          var p = ps[0];
          return p.name + '<br/><b>md_share = ' + (p.data >= 0 ? '+' : '') + p.data.toFixed(4) + '</b>';
        },
        backgroundColor: C('--bg-card'), borderColor: C('--border-color'),
        textStyle: { color: C('--text-primary'), fontSize: 12 }
      },
      xAxis: {
        type: 'value',
        min: Math.min.apply(null, vals.concat(-0.01)),
        max: Math.max.apply(null, vals.concat(0.01)),
        axisLabel: {
          color: C('--text-secondary'), fontSize: 10,
          formatter: function (v) { return (v >= 0 ? '+' : '') + v.toFixed(2); }
        },
        splitLine: { lineStyle: { color: C('--border-color') } },
        axisLine: { lineStyle: { color: C('--border-color') } }
      },
      yAxis: {
        type: 'category', data: cats,
        axisLabel: { color: C('--text-secondary'), fontSize: 10 },
        axisLine: { lineStyle: { color: C('--border-color') } }, axisTick: { show: false }
      },
      series: [{
        type: 'bar',
        data: vals.map(function (v, i) { return { value: v, itemStyle: { color: barColors[i] } }; }),
        barWidth: '60%',
        label: {
          show: true,
          position: function (p) { return p.data.value >= 0 ? 'right' : 'left'; },
          formatter: function (p) { return (p.data.value >= 0 ? '+' : '') + p.data.value.toFixed(3); },
          color: C('--text-secondary'), fontSize: 9
        }
      }]
    }, true);
  }

  // ======================== Tab1: IC 时序 ========================
  function renderIC(series) {
    var el = document.getElementById('chart-ic');
    if (!el) return;
    if (!S.icChart) S.icChart = echarts.init(el);

    var icData = series.ic_series || {};
    var k1 = icData.k1 || [], k3 = icData.k3 || [];

    S.icChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 55, right: 30, top: 30, bottom: 40 },
      legend: {
        data: ['单日 (k=1)', '3日平滑 (k=3)'], top: 0,
        textStyle: { color: C('--text-secondary'), fontSize: 11 },
        icon: 'roundRect', itemWidth: 14, itemHeight: 8
      },
      tooltip: {
        trigger: 'axis',
        formatter: function (ps) {
          var s = '<b>' + fmt(ps[0].axisValue) + '</b>';
          ps.forEach(function (p) {
            s += '<br/>' + p.marker + ' ' + p.seriesName + ': <b>' +
              (p.data[1] >= 0 ? '+' : '') + p.data[1].toFixed(3) + '</b>';
          });
          return s;
        },
        backgroundColor: C('--bg-card'), borderColor: C('--border-color'),
        textStyle: { color: C('--text-primary'), fontSize: 12 }
      },
      xAxis: {
        type: 'category', data: k1.map(function (x) { return fmt(x[0]); }),
        axisLabel: {
          color: C('--text-secondary'), fontSize: 10,
          interval: Math.floor(k1.length / 8)
        },
        axisLine: { lineStyle: { color: C('--border-color') } }, axisTick: { show: false }
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          color: C('--text-secondary'), fontSize: 10,
          formatter: function (v) { return (v >= 0 ? '+' : '') + v.toFixed(2); }
        },
        splitLine: { lineStyle: { color: C('--border-color') } },
        axisLine: { lineStyle: { color: C('--border-color') } }
      },
      series: [
        {
          name: '单日 (k=1)', type: 'line',
          data: k1.map(function (x) { return [fmt(x[0]), x[1]]; }),
          smooth: true, symbol: 'circle', symbolSize: 5,
          lineStyle: { color: C('--s1'), width: 2 }, itemStyle: { color: C('--s1') }
        },
        {
          name: '3日平滑 (k=3)', type: 'line',
          data: k3.map(function (x) { return [fmt(x[0]), x[1]]; }),
          smooth: true, symbol: 'circle', symbolSize: 5,
          lineStyle: { color: C('--s2'), width: 2 }, itemStyle: { color: C('--s2') }
        }
      ],
      markLine: {
        symbol: 'none', silent: true,
        data: [{ yAxis: 0, lineStyle: { color: C('--border-color'), type: 'dashed' } }]
      }
    }, true);
  }

  // ======================== Tab1: 五分组前瞻超额收益 ========================
  function renderQuintile(series) {
    var el = document.getElementById('chart-quint');
    if (!el) return;
    if (!S.quintChart) S.quintChart = echarts.init(el);

    var qp = series.quintile_perf || {};
    var q1 = qp.k1 || [], q3 = qp.k3 || [];
    var groups = [
      'q0\n最深净流出', 'q1', 'q2', 'q3',
      'q4\n最深净流入'
    ];

    function seriesFor(q, name, color) {
      return {
        name: name, type: 'bar',
        data: q.map(function (v) { return v == null ? 0 : v; }),
        barWidth: '28%',
        itemStyle: { color: color },
        label: {
          show: true, position: 'top',
          formatter: function (p) { return (p.value >= 0 ? '+' : '') + p.value.toFixed(2) + '%'; },
          color: C('--text-secondary'), fontSize: 10
        }
      };
    }

    S.quintChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 60, right: 30, top: 40, bottom: 30 },
      legend: {
        data: ['k=1 单日', 'k=3 累积'],
        top: 0,
        textStyle: { color: C('--text-secondary'), fontSize: 11 },
        icon: 'roundRect', itemWidth: 14, itemHeight: 8
      },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: function (ps) {
          var s = '<b>' + ps[0].axisValue + '</b>';
          ps.forEach(function (p) {
            s += '<br/>' + p.marker + ' ' + p.seriesName + ': <b>' +
              (p.value >= 0 ? '+' : '') + p.value.toFixed(2) + '%</b>';
          });
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
        axisLabel: {
          color: C('--text-secondary'), fontSize: 10,
          formatter: function (v) { return (v >= 0 ? '+' : '') + v.toFixed(1) + '%'; }
        },
        splitLine: { lineStyle: { color: C('--border-color') } }
      },
      series: [
        seriesFor(q1, 'k=1 单日', C('--s1')),
        seriesFor(q3, 'k=3 累积', C('--s2'))
      ]
    }, true);
  }

  // ======================== Tab1 整体渲染入口 ========================
  function renderIndustryTab(data) {
    if (!data) return;
    var series = data.series;
    if (!series) return;
    renderHeatmap(series);
    renderRanking(series, data.kpi);
    renderIC(series);
    renderQuintile(series);
  }

  // ======================== Tab3: 行业下拉 ========================
  function getDefaultIndustryCode(industryData) {
    if (!industryData || !industryData.series) return null;
    var series = industryData.series;
    var mat = series.share_heat ? series.share_heat['1'] : [];
    if (!mat || !mat.length) return null;
    var lastRow = mat[mat.length - 1];
    if (!lastRow) return null;

    var maxIdx = 0, maxVal = -Infinity;
    for (var i = 0; i < lastRow.length; i++) {
      if (lastRow[i] != null && lastRow[i] > maxVal) {
        maxVal = lastRow[i];
        maxIdx = i;
      }
    }
    var codes = series.industries_code;
    if (codes && codes[maxIdx]) return codes[maxIdx];
    return null;
  }

  function populateIndustrySelect(industryData) {
    var select = document.getElementById('ff-industry-select');
    if (!select || !industryData || !industryData.series) return;

    var names = industryData.series.industries || [];
    var codes = industryData.series.industries_code || [];
    if (!names.length || !codes.length || names.length !== codes.length) return;

    var html = '';
    for (var i = 0; i < names.length; i++) {
      html += '<option value="' + (codes[i] || '') + '">' + names[i] + '</option>';
    }
    select.innerHTML = html;
  }

  // ======================== Tab3: 行业时序图 ========================
  function renderFundflowTab(tsCode, mode) {
    var el = document.getElementById('chart-fundflow');
    if (!el) return;
    if (!S.fundflowChart) S.fundflowChart = echarts.init(el);

    S.fundflowChart.showLoading('default', {
      text: '加载中…',
      textColor: C('--text-secondary'),
      maskColor: 'rgba(0,0,0,0.3)',
    });

    fetch('/api/industry/' + encodeURIComponent(tsCode) + '/timeseries?mode=' + encodeURIComponent(mode))
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        S.fundflowChart.hideLoading();
        if (!data || !data.dates || !data.dates.length) {
          el.innerHTML = '<p style="color:var(--text-secondary);padding:40px;text-align:center">该行业无资金流数据</p>';
          return;
        }

        var dates = data.dates.map(fmt);
        var values = data.values;
        var close = data.close;
        var pct = data.pct_change;
        var modeLabel = data.meta.mode_label;

        var titleEl = document.getElementById('ff-chart-title');
        if (titleEl) titleEl.textContent = data.name + ' — 行业时序';

        function fmtClose(v) {
          if (v == null) return '-';
          return v >= 10000 ? (v / 10000).toFixed(2) + '万' : v.toFixed(2);
        }

        S.fundflowChart.setOption({
          backgroundColor: 'transparent',
          grid: { left: 60, right: 80, top: 30, bottom: 60 },
          legend: {
            data: [
              { name: modeLabel, icon: 'roundRect' },
              { name: '行业指数', icon: 'roundRect' },
              { name: '涨跌幅（%）', icon: 'roundRect' },
            ],
            selected: { '涨跌幅（%）': false },
            top: 0,
            textStyle: { color: C('--text-secondary'), fontSize: 11 },
            icon: 'roundRect', itemWidth: 14, itemHeight: 8,
          },
          tooltip: {
            trigger: 'axis',
            formatter: function (ps) {
              var s = '<b>' + ps[0].axisValue + '</b>';
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
              type: 'value', name: '',
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
        S.fundflowChart.hideLoading();
        el.innerHTML = '<p style="color:var(--accent-red);padding:40px;text-align:center">数据加载失败: ' + err.message + '</p>';
        console.error('fundflow fetch error:', err);
      });
  }

  // ======================== 异常行业检测 ========================
  function fetchAnomalies(threshold) {
    S.anomalyThreshold = threshold;
    var banner = document.getElementById('ff-anomaly-banner');
    var itemsEl = document.getElementById('ff-anomaly-items');
    var metaEl = document.getElementById('ff-anomaly-meta');
    if (!banner || !itemsEl || !metaEl) return;

    metaEl.textContent = '加载中…';
    itemsEl.innerHTML = '<span class="anomaly-overflow">正在获取异常行业数据…</span>';

    fetch('/api/industry/anomalies?threshold=' + threshold + '&limit=10')
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        S.anomalyData = data;
        renderAnomalyBanner(data);
      })
      .catch(function (err) {
        console.error('anomaly fetch error:', err);
        S.anomalyData = null;
        metaEl.textContent = '';
        itemsEl.innerHTML = '<span class="anomaly-overflow">⚠️ 无法获取异常数据' +
          (err.message !== 'Failed to fetch' ? ': ' + err.message : '') + '</span>';
      });
  }

  function renderAnomalyBanner(data) {
    var itemsEl = document.getElementById('ff-anomaly-items');
    var metaEl = document.getElementById('ff-anomaly-meta');
    if (!itemsEl || !metaEl) return;

    metaEl.textContent = 'Top ' + Math.min(data.total_anomalies, data.limit) +
      ' / ' + data.total_anomalies + ' 异常行业（共 ' + data.total_industries + ' 行业）';

    if (!data.anomalies || !data.anomalies.length) {
      itemsEl.innerHTML = '<span class="anomaly-overflow">无行业超过阈值 |z|>' + data.threshold.toFixed(1) + '</span>';
      return;
    }

    var currentCode = S.ffIndustryCode;
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

    if (data.total_anomalies > data.anomalies.length) {
      var extra = data.total_anomalies - data.anomalies.length;
      itemsEl.insertAdjacentHTML('beforeend', '<span class="anomaly-overflow">… 还有 ' + extra + ' 个</span>');
    }

    itemsEl.querySelectorAll('.anomaly-item').forEach(function (el) {
      el.addEventListener('click', function () {
        var code = this.dataset.code;
        if (!code) return;
        var select = document.getElementById('ff-industry-select');
        if (select) {
          select.value = code;
          S.ffIndustryCode = code;
          var mode = document.getElementById('ff-mode-select').value;
          renderFundflowTab(code, mode);
          renderAnomalyBanner(S.anomalyData);
        }
      });
    });
  }

  function setupAnomalyBanner() {
    document.querySelectorAll('.anomaly-threshold-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var t = parseFloat(this.dataset.t);
        if (isNaN(t)) return;
        document.querySelectorAll('.anomaly-threshold-btn').forEach(function (b) {
          b.classList.remove('active');
        });
        this.classList.add('active');
        document.getElementById('ff-threshold-input').value = t;
        fetchAnomalies(t);
      });
    });

    var input = document.getElementById('ff-threshold-input');
    if (input) {
      input.addEventListener('input', function () {
        var t = parseFloat(this.value);
        if (isNaN(t) || t < 1 || t > 5) return;
        document.querySelectorAll('.anomaly-threshold-btn').forEach(function (b) {
          b.classList.remove('active');
        });
        if (S._anomalyDebounce) clearTimeout(S._anomalyDebounce);
        S._anomalyDebounce = setTimeout(function () {
          fetchAnomalies(t);
          S._anomalyDebounce = null;
        }, 300);
      });
    }
  }

  // ======================== 析构 ========================
  function destroy() {
    Object.keys(S).forEach(function (k) {
      if (k.endsWith('Chart') && S[k]) {
        S[k].dispose();
        S[k] = null;
      }
    });
    S.data = null;
    S.ffIndustryCode = null;
    S.anomalyData = null;
    if (S._anomalyDebounce) {
      clearTimeout(S._anomalyDebounce);
      S._anomalyDebounce = null;
    }
  }

  // ======================== 公开 API ========================
  window.initIndustry = function initIndustry(containerId) {
    var container = document.getElementById(containerId);
    if (!container) {
      console.error('[industry] container not found:', containerId);
      return;
    }

    // 注入 HTML
    container.innerHTML = buildHTML();

    // 加载数据 + 渲染
    loadJson('data/industry.json').then(function (industry) {
      S.data = industry;

      // Tab1: 行业资金流图表
      renderIndustryTab(industry);

      // Tab3: 行业下拉
      populateIndustrySelect(industry);
      var defaultCode = getDefaultIndustryCode(industry);
      if (defaultCode) {
        S.ffIndustryCode = defaultCode;
        var select = document.getElementById('ff-industry-select');
        if (select) select.value = defaultCode;
        renderFundflowTab(defaultCode, 'raw');
      }

      // Tab3: 行业选择
      var indSelect = document.getElementById('ff-industry-select');
      if (indSelect) {
        indSelect.onchange = function () {
          S.ffIndustryCode = this.value;
          var mode = document.getElementById('ff-mode-select').value;
          renderFundflowTab(this.value, mode);
          if (S.anomalyData) renderAnomalyBanner(S.anomalyData);
        };
      }

      // Tab3: 模式选择
      var modeSelect = document.getElementById('ff-mode-select');
      if (modeSelect) {
        modeSelect.onchange = function () {
          var code = S.ffIndustryCode;
          if (code) renderFundflowTab(code, this.value);
        };
      }

      // 异常检测
      setupAnomalyBanner();
      fetchAnomalies(S.anomalyThreshold);
    }).catch(function (e) {
      console.error('[industry] data load failed:', e);
      container.innerHTML = '<p style="color:var(--accent-red);padding:24px;text-align:center">加载行业数据失败: ' + e.message + '</p>';
    });

    // 返回销毁方法
    return { destroy: destroy };
  };

})();
