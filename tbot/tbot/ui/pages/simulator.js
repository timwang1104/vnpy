/* simulator.js — 模拟盘页面模块
   移植自 app.js Tab4 模拟盘代码，封装为 initSimulator(containerId)。
   依赖：ECharts (window.echarts) */

(function () {
  'use strict';

  // ==================== CSS 变量 ====================
  var CSS = typeof getComputedStyle !== 'undefined' ? getComputedStyle(document.documentElement) : null;
  function C(n) { return CSS ? CSS.getPropertyValue(n).trim() : ''; }

  function parseHex(h) {
    h = h.replace('#','');
    if (h.length===3) h = h.split('').map(function(x){return x+x;}).join('');
    return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16)];
  }
  function interp(hexA, hexB, t) {
    var a = parseHex(hexA), b = parseHex(hexB);
    return 'rgb(' + Math.round(a[0]+(b[0]-a[0])*t) + ',' + Math.round(a[1]+(b[1]-a[1])*t) + ',' + Math.round(a[2]+(b[2]-a[2])*t) + ')';
  }
  function divColor(v) {
    var a = Math.max(-1, Math.min(1, v));
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

  function fmtMoney(v) {
    if (v == null) return '-';
    var abs = Math.abs(v);
    if (abs >= 1e8) return (v / 1e8).toFixed(1) + '亿';
    if (abs >= 1e4) return (v / 1e4).toFixed(0) + '万';
    return v.toFixed(0);
  }

  // ==================== 实例状态（每个 container 独立） ====================
  var instances = {};

  function getState(id) {
    if (!instances[id]) {
      instances[id] = {
        strategies: [],
        chart: null,
        running: false,
      };
    }
    return instances[id];
  }

  // ==================== 默认 HTML 模板 ====================
  function buildHTML() {
    return '' +
      '<div class="sim-toolbar">' +
        '<button id="btn-sim-discover" class="btn">刷新策略</button>' +
      '</div>' +
      '<div class="sim-strategy-list" id="sim-strategy-list">' +
        '<p class="text-muted">加载中…</p>' +
      '</div>' +
      '<div class="sim-result-section" id="sim-result-header" style="display:none">' +
        '<h3 style="margin:16px 0 8px">运行结果</h3>' +
        '<div class="sim-result-summary" id="sim-result-summary"></div>' +
        '<div class="sim-chart-container" id="chart-sim-equity" style="height:320px;margin:12px 0"></div>' +
        '<h4 style="margin:12px 0 6px">持仓</h4>' +
        '<div id="sim-positions-table"></div>' +
        '<h4 style="margin:12px 0 6px">交易记录</h4>' +
        '<div id="sim-trades-table"></div>' +
      '</div>';
  }

  // ==================== 渲染策略列表 ====================
  function renderStrategyList(containerId) {
    var state = getState(containerId);
    var listEl = document.getElementById('sim-strategy-list');
    if (!listEl) return;

    // 隐藏老的结果
    var headerEl = document.getElementById('sim-result-header');
    if (headerEl) headerEl.style.display = 'none';

    if (!state.strategies.length) {
      listEl.innerHTML = '<p class="text-muted">暂无策略，请点击"刷新策略"或放入 strategies/ 目录</p>';
      return;
    }

    var html = '';
    for (var si = 0; si < state.strategies.length; si++) {
      var s = state.strategies[si];
      var params = s.parameters || [];
      var latest = s.latest_batch || null;

      html += '<div class="sim-card" data-id="' + s.id + '">';
      html += '<div class="sim-card-header">';
      html += '<span class="sim-card-name">' + s.name + '</span>';
      html += '<span class="sim-card-author">' + (s.author || 'unknown') + '</span>';
      if (latest && latest.status === 'completed') {
        var ret = latest.total_return;
        html += '<span class="sim-card-return ' + (ret >= 0 ? 'up' : 'down') + '">收益: ' + (ret >= 0 ? '+' : '') + ret.toFixed(2) + '%</span>';
      }
      html += '</div>';

      html += '<div class="sim-card-params">';
      for (var pi = 0; pi < params.length; pi++) {
        var p = params[pi];
        var name = p.name;
        var label = p.label || name;
        var type = p.type || 'string';
        var def = p.default !== undefined ? p.default : '';
        html += '<label class="sim-param">' + label + ': ';
        if (type === 'int' || type === 'float') {
          html += '<input type="number" class="sim-param-input" data-pname="' + name + '" value="' + def + '" step="' + (type === 'float' ? '0.1' : '1') + '">';
        } else {
          html += '<input type="text" class="sim-param-input" data-pname="' + name + '" value="' + def + '">';
        }
        html += '</label>';
      }
      html += '<button class="btn sim-run-btn" data-id="' + s.id + '">运行</button>';
      html += '</div>';

      if (latest) {
        html += '<div class="sim-card-meta">';
        html += '最近运行: ' + (latest.run_at ? latest.run_at.slice(0,10) : '-') + ' ';
        html += '| 状态: ' + latest.status + ' ';
        html += '| 最终权益: ' + (latest.final_equity ? fmtMoney(latest.final_equity) : '-');
        html += '</div>';
      }

      html += '</div>';
    }
    listEl.innerHTML = html;

    // 绑定运行按钮
    var btns = listEl.querySelectorAll('.sim-run-btn');
    for (var bi = 0; bi < btns.length; bi++) {
      (function(btn) {
        btn.addEventListener('click', function () {
          runSimStrategy(containerId, this.dataset.id);
        });
      })(btns[bi]);
    }
  }

  // ==================== 运行策略 ====================
  async function runSimStrategy(containerId, strategyId) {
    var state = getState(containerId);
    if (state.running) {
      alert('已有策略在运行，请等待完成');
      return;
    }
    state.running = true;

    var card = document.querySelector('[data-id="' + strategyId + '"]');
    var inputs = card ? card.querySelectorAll('.sim-param-input') : [];
    var setting = {};
    for (var ii = 0; ii < inputs.length; ii++) {
      setting[inputs[ii].dataset.pname] = inputs[ii].value;
    }

    var runBtn = card ? card.querySelector('.sim-run-btn') : null;
    if (runBtn) {
      runBtn.textContent = '运行中…';
      runBtn.disabled = true;
    }

    try {
      var resp = await fetch('/api/sim/strategies/' + strategyId + '/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ setting: setting }),
      });
      var result = await resp.json();

      if (result.status === 'error') {
        alert('运行失败: ' + (result.message || '未知错误'));
        return;
      }

      // Small delay then reload data
      await new Promise(function(r) { setTimeout(r, 1000); });

      // Reload strategy detail to get updated batch info
      var detailResp = await fetch('/api/sim/strategies/' + strategyId);
      if (detailResp.ok) {
        var detail = await detailResp.json();
        var idx = -1;
        for (var fi = 0; fi < state.strategies.length; fi++) {
          if (state.strategies[fi].id == strategyId) { idx = fi; break; }
        }
        if (idx >= 0) {
          state.strategies[idx] = detail;
        }
        renderStrategyList(containerId);
      }

      // Load results
      await loadResults(containerId, strategyId);

    } catch (e) {
      console.error('run error:', e);
      alert('运行出错: ' + e.message);
    } finally {
      state.running = false;
      if (runBtn) {
        runBtn.textContent = '运行';
        runBtn.disabled = false;
      }
    }
  }

  // ==================== 加载结果 ====================
  async function loadResults(containerId, strategyId) {
    var headerEl = document.getElementById('sim-result-header');
    if (!headerEl) return;
    headerEl.style.display = 'block';
    document.getElementById('sim-result-summary').textContent = '加载结果中…';

    try {
      var responses = await Promise.all([
        fetch('/api/sim/strategies/' + strategyId + '/equity'),
        fetch('/api/sim/strategies/' + strategyId + '/positions'),
        fetch('/api/sim/strategies/' + strategyId + '/trades'),
      ]);

      if (!responses[0].ok || !responses[1].ok || !responses[2].ok) {
        document.getElementById('sim-result-summary').textContent = '结果加载失败';
        return;
      }

      var equityData = await responses[0].json();
      var positionsData = await responses[1].json();
      var tradesData = await responses[2].json();

      // Summary
      var dates = equityData.dates || [];
      var equity = equityData.equity || [];
      if (dates.length > 0) {
        var first = equity[0] || 0;
        var last = equity[equity.length - 1] || 0;
        var ret = first > 0 ? ((last - first) / first * 100) : 0;
        document.getElementById('sim-result-summary').innerHTML =
          '运行期间: ' + dates[0] + ' ~ ' + dates[dates.length-1] + ' | ' +
          '初始权益: ' + fmtMoney(first) + ' | 最终权益: ' + fmtMoney(last) + ' | ' +
          '收益率: <span class="' + (ret >= 0 ? 'up' : 'down') + '">' + (ret >= 0 ? '+' : '') + ret.toFixed(2) + '%</span> | ' +
          '交易次数: ' + (tradesData.trades || []).length;
      }

      // Equity chart
      renderEquityChart(containerId, dates, equity);

      // Positions table
      renderPositionsTable(positionsData.positions || []);

      // Trades table
      renderTradesTable(tradesData.trades || []);

    } catch (e) {
      console.error('load results error:', e);
      document.getElementById('sim-result-summary').textContent = '结果加载出错: ' + e.message;
    }
  }

  // ==================== 权益曲线 ====================
  function renderEquityChart(containerId, dates, equity) {
    var state = getState(containerId);
    var el = document.getElementById('chart-sim-equity');
    if (!el) return;

    if (!dates.length) {
      if (state.chart) {
        state.chart.clear();
        state.chart = null;
      }
      el.innerHTML = '<p class="text-muted">暂无数据</p>';
      return;
    }

    if (!state.chart) state.chart = echarts.init(el);

    var fmtDates = dates.map(fmt);
    var firstVal = equity[0] || 1;

    state.chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 70, right: 30, top: 30, bottom: 40 },
      tooltip: {
        trigger: 'axis',
        formatter: function(ps) {
          var p = ps[0];
          return '<b>' + p.axisValue + '</b><br/>权益: <b>' + fmtMoney(p.data) + '</b>';
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
        axisLabel: { color: C('--text-secondary'), fontSize: 10, formatter: function(v) { return fmtMoney(v); } },
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

  // ==================== 持仓表 ====================
  function renderPositionsTable(positions) {
    var el = document.getElementById('sim-positions-table');
    if (!el) return;

    if (!positions.length) {
      el.innerHTML = '<p class="text-muted">无持仓</p>';
      return;
    }

    var html = '<table class="data-table"><thead><tr>' +
      '<th>股票</th><th>数量</th><th>均价</th><th>市值</th><th>盈亏</th><th>盈亏%</th></tr></thead><tbody>';
    for (var pi = 0; pi < positions.length; pi++) {
      var p = positions[pi];
      var pnlCls = p.pnl >= 0 ? 'up' : 'down';
      html += '<tr><td>' + p.ts_code + '</td><td>' + p.volume + '</td><td>' + p.avg_price.toFixed(2) + '</td>' +
        '<td>' + fmtMoney(p.market_value) + '</td>' +
        '<td class="' + pnlCls + '">' + fmtMoney(p.pnl) + '</td>' +
        '<td class="' + pnlCls + '">' + (p.pnl_pct >= 0 ? '+' : '') + p.pnl_pct.toFixed(2) + '%</td></tr>';
    }
    html += '</tbody></table>';
    el.innerHTML = html;
  }

  // ==================== 交易记录表 ====================
  function renderTradesTable(trades) {
    var el = document.getElementById('sim-trades-table');
    if (!el) return;

    if (!trades.length) {
      el.innerHTML = '<p class="text-muted">无交易记录</p>';
      return;
    }

    var html = '<table class="data-table"><thead><tr>' +
      '<th>日期</th><th>股票</th><th>方向</th><th>价格</th><th>数量</th><th>金额</th><th>盈亏</th></tr></thead><tbody>';
    // Show last 100 trades
    var showTrades = trades.slice(-100);
    for (var ti = 0; ti < showTrades.length; ti++) {
      var t = showTrades[ti];
      var dirCls = t.direction === 'buy' ? 'up' : 'down';
      var pnlCls = t.pnl >= 0 ? 'up' : 'down';
      var formattedDate = t.trade_date ? fmt(t.trade_date) : '-';
      html += '<tr><td>' + formattedDate + '</td><td>' + t.ts_code + '</td>' +
        '<td class="' + dirCls + '">' + (t.direction === 'buy' ? '买入' : '卖出') + '</td>' +
        '<td>' + t.price.toFixed(2) + '</td><td>' + t.volume + '</td><td>' + fmtMoney(t.amount) + '</td>' +
        '<td class="' + pnlCls + '">' + (t.pnl != 0 ? fmtMoney(t.pnl) : '-') + '</td></tr>';
    }
    html += '</tbody></table>';
    el.innerHTML = html;
  }

  // ==================== 发现策略 ====================
  async function discoverStrategies(containerId) {
    var state = getState(containerId);
    try {
      // 先触发后端扫描 strategies/ 目录
      await fetch('/api/sim/discover', { method: 'POST' });
      // 再读取最新策略列表
      var resp = await fetch('/api/sim/strategies');
      if (resp.ok) {
        var data = await resp.json();
        state.strategies = data.strategies || [];
      }
    } catch (e) {
      console.error('discover strategies error:', e);
    }
  }

  // ==================== Resize 处理 ====================
  function handleResize(containerId) {
    var state = getState(containerId);
    if (state.chart) state.chart.resize();
  }

  // ==================== 公共 API ====================
  window.initSimulator = function (containerId) {
    var container = document.getElementById(containerId);
    if (!container) {
      console.error('simulator: container #' + containerId + ' not found');
      return null;
    }

    // 注入 HTML 结构
    container.innerHTML = buildHTML();

    // 初始化状态
    getState(containerId);

    // 绑定发现按钮
    var discoverBtn = document.getElementById('btn-sim-discover');
    if (discoverBtn) {
      discoverBtn.addEventListener('click', async function () {
        await discoverStrategies(containerId);
        renderStrategyList(containerId);
      });
    }

    // 自动加载策略列表
    setTimeout(function () {
      discoverStrategies(containerId).then(function () {
        renderStrategyList(containerId);
      });
    }, 0);

    // 返回控制器，方便外部调用
    return {
      refresh: function () {
        discoverStrategies(containerId).then(function () {
          renderStrategyList(containerId);
        });
      },
      run: function (strategyId) {
        return runSimStrategy(containerId, strategyId);
      },
      loadResults: function (strategyId) {
        return loadResults(containerId, strategyId);
      },
      resize: function () {
        handleResize(containerId);
      },
      getState: function () {
        return getState(containerId);
      },
    };
  };

})();
