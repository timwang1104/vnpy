/**
 * limitup.js — 涨停池独立模块
 *
 * 从 market_research/report/static/app.js 中提取的涨停池标签页全部代码，
 * 封装为可独立使用的 initLimitup(containerId) 入口函数。
 *
 * 依赖：ECharts 全局变量 echarts
 *
 * 用法：
 *   <div id="limitup">
 *     <!-- 日期导航 -->
 *     <div class="date-row">
 *       <button id="btn-prev-day" class="btn">‹ 上一日</button>
 *       <div class="date-picker" id="date-picker">
 *         <span id="limitup-current-date" class="current-date dp-trigger">—</span>
 *         <div class="dp-popup" id="dp-popup">…</div>
 *       </div>
 *       <button id="btn-next-day" class="btn">下一日 ›</button>
 *     </div>
 *     <!-- 日详情 -->
 *     <div class="chart-box">
 *       <h3>涨停概况</h3>
 *       <div id="limitup-day-content"></div>
 *     </div>
 *     <!-- 概念图 -->
 *     <div class="chart-box">
 *       <h3>概念聚合（力导向图）
 *         <button id="btn-generate-concept" class="btn btn-sm">🤖 生成概念图</button>
 *         <span id="concept-gen-status" class="text-muted"></span>
 *       </h3>
 *       <div id="chart-concept-graph" class="chart-container" style="height:500px"></div>
 *     </div>
 *     <!-- 连板时序 -->
 *     <div class="chart-box">
 *       <h3>连板高度时序</h3>
 *       <div id="chart-limitup" class="chart-container" style="height:200px"></div>
 *     </div>
 *   </div>
 *
 *   <script src="echarts.min.js"></script>
 *   <script src="limitup.js"></script>
 *   <script>initLimitup('limitup');</script>
 */
(function (global) {
  'use strict';

  // ==================== CSS 变量 ====================
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function parseHex(h) {
    h = h.replace('#', '');
    if (h.length === 3) h = h.split('').map(function (x) { return x + x; }).join('');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }

  function interpColor(hexA, hexB, t) {
    var a = parseHex(hexA), b = parseHex(hexB);
    return 'rgb(' + Math.round(a[0] + (b[0] - a[0]) * t) + ',' +
      Math.round(a[1] + (b[1] - a[1]) * t) + ',' +
      Math.round(a[2] + (b[2] - a[2]) * t) + ')';
  }

  function divColor(v) {
    var a = Math.max(-1, Math.min(1, v));
    if (Math.abs(a) < 0.02) return cssVar('--div-mid');
    if (a > 0) {
      return a < 0.5
        ? interpColor(cssVar('--div-mid'), cssVar('--div-pos-soft'), a / 0.5)
        : interpColor(cssVar('--div-pos-soft'), cssVar('--div-pos'), (a - 0.5) / 0.5);
    }
    return -a < 0.5
      ? interpColor(cssVar('--div-mid'), cssVar('--div-neg-soft'), -a / 0.5)
      : interpColor(cssVar('--div-neg-soft'), cssVar('--div-neg'), (-a - 0.5) / 0.5);
  }

  // ==================== 工具函数 ====================
  function fmtDate(d) {
    return d.slice(0, 4) + '-' + d.slice(4, 6) + '-' + d.slice(6, 8);
  }

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

  function signClass(v) {
    return v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : '';
  }

  // ==================== 模块状态 ====================
  var state = {
    limitup: null,       // limitup_main.json data
    calendar: null,      // calendar.json data
    limitupDate: null,   // current selected limitup date
    ltChart: null,       // ECharts instance: 涨停时序
    conceptChart: null,  // ECharts instance: 概念力导向图
    // 日期选择器内部状态
    _tradeDates: [],
    _dpOpen: false,
    _viewYear: 0,
    _viewMonth: 0,
  };

  // ==================== 日期选择器 ====================

  function closeDatePicker() {
    var popup = document.getElementById('dp-popup');
    if (popup) popup.classList.add('hidden');
    state._dpOpen = false;
  }

  function toggleDatePicker(selectedDate) {
    state._dpOpen = !state._dpOpen;
    var popup = document.getElementById('dp-popup');
    if (popup) popup.classList.toggle('hidden', !state._dpOpen);
    if (state._dpOpen) {
      state._viewYear = +selectedDate.slice(0, 4);
      state._viewMonth = +selectedDate.slice(4, 6) - 1;
      renderCalendarPopup(selectedDate);
    }
  }

  function renderCalendarPopup(selectedDate) {
    var monthYearEl = document.getElementById('dp-month-year');
    if (monthYearEl) {
      monthYearEl.textContent = state._viewYear + '年' +
        String(state._viewMonth + 1).padStart(2, '0') + '月';
    }

    var tradeDateSet = new Set(state._tradeDates);
    var daysContainer = document.getElementById('dp-days');
    if (!daysContainer) return;

    var firstDay = new Date(state._viewYear, state._viewMonth, 1).getDay();
    var daysInMonth = new Date(state._viewYear, state._viewMonth + 1, 0).getDate();

    var html = '';
    for (var i = 0; i < firstDay; i++) {
      html += '<div class="dp-day empty"></div>';
    }

    for (var day = 1; day <= daysInMonth; day++) {
      var dateStr = state._viewYear +
        String(state._viewMonth + 1).padStart(2, '0') +
        String(day).padStart(2, '0');
      var hasData = tradeDateSet.has(dateStr);
      var cls = 'dp-day';
      if (!hasData) cls += ' muted';
      if (dateStr === selectedDate) cls += ' selected';
      html += '<div class="' + cls + '" data-date="' + dateStr + '">' + day + '</div>';
    }

    daysContainer.innerHTML = html;

    daysContainer.querySelectorAll('.dp-day:not(.empty):not(.muted)').forEach(function (el) {
      el.addEventListener('click', function () {
        selectLimitupDate(el.dataset.date);
        closeDatePicker();
      });
    });
  }

  function renderDatePicker(calendar, selectedDate) {
    closeDatePicker();
    var trigger = document.getElementById('limitup-current-date');
    if (!trigger) return;

    var weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
    var dateObj = new Date(+selectedDate.slice(0, 4), +selectedDate.slice(4, 6) - 1, +selectedDate.slice(6, 8));
    trigger.textContent = selectedDate.slice(0, 4) + '-' +
      selectedDate.slice(4, 6) + '-' +
      selectedDate.slice(6, 8) + ' ' +
      weekdays[dateObj.getDay()];

    state._tradeDates = (calendar.dates || []).filter(function (e) { return e.has_data; }).map(function (e) { return e.date; });
    state._viewYear = +selectedDate.slice(0, 4);
    state._viewMonth = +selectedDate.slice(4, 6) - 1;

    trigger.onclick = function (e) {
      e.stopPropagation();
      toggleDatePicker(selectedDate);
    };

    var prevMonthBtn = document.getElementById('dp-prev-month');
    if (prevMonthBtn) {
      prevMonthBtn.onclick = function (e) {
        e.stopPropagation();
        state._viewMonth--;
        if (state._viewMonth < 0) { state._viewMonth = 11; state._viewYear--; }
        renderCalendarPopup(selectedDate);
      };
    }

    var nextMonthBtn = document.getElementById('dp-next-month');
    if (nextMonthBtn) {
      nextMonthBtn.onclick = function (e) {
        e.stopPropagation();
        state._viewMonth++;
        if (state._viewMonth > 11) { state._viewMonth = 0; state._viewYear++; }
        renderCalendarPopup(selectedDate);
      };
    }

    var prevDayBtn = document.getElementById('btn-prev-day');
    if (prevDayBtn) {
      prevDayBtn.onclick = function () {
        var idx = state._tradeDates.indexOf(selectedDate);
        if (idx > 0) selectLimitupDate(state._tradeDates[idx - 1]);
      };
    }

    var nextDayBtn = document.getElementById('btn-next-day');
    if (nextDayBtn) {
      nextDayBtn.onclick = function () {
        var idx = state._tradeDates.indexOf(selectedDate);
        if (idx < state._tradeDates.length - 1) selectLimitupDate(state._tradeDates[idx + 1]);
      };
    }

    renderCalendarPopup(selectedDate);
  }

  // 外部点击关闭日期选择器（全局一次性绑定）
  function setupDatePickerOutsideClick() {
    document.addEventListener('click', function (e) {
      var picker = document.getElementById('date-picker');
      if (state._dpOpen && picker && !picker.contains(e.target)) {
        closeDatePicker();
      }
    });
  }

  // ==================== 涨停日期选择 ====================

  function selectLimitupDate(d) {
    state.limitupDate = d;
    renderLimitupTab(state.limitup, state.calendar, d);
    // 通知外部 chat context（如果存在）
    if (typeof updateChatContext === 'function') updateChatContext();
  }

  // ==================== 涨停日详情 ====================

  function renderLimitupDay(dayData, selectedDate) {
    var container = document.getElementById('limitup-day-content');
    if (!container) return;
    if (!dayData || !dayData.tables) {
      container.innerHTML = '<p style="color:var(--text-secondary)">无数据</p>';
      renderConceptGraph(null, null);
      return;
    }

    var kpi = dayData.kpi || {};
    var tiers = (dayData.tables.tiers || []).slice(0, 10);

    var html = '<div class="kpi-row">';
    html += '<span>涨停 ' + kpi.limit_up_cnt + ' 炸板 ' + kpi.limit_break_cnt +
      ' 跌停 ' + kpi.limit_down_cnt + ' 最高板 ' + kpi.max_limit_times + '</span>';
    html += '</div>';

    if (tiers.length) {
      html += '<h3 style="margin:12px 0 8px">梯队</h3>';
      html += '<table class="data-table"><thead><tr><th>连板</th><th>数量</th><th>股票</th><th>行业</th><th>封板时间</th><th>炸板时间</th><th>封单(亿)</th></tr></thead><tbody>';
      for (var ti = 0; ti < tiers.length; ti++) {
        var t = tiers[ti];
        for (var mi = 0; mi < t.members.length; mi++) {
          var m = t.members[mi];
          html += '<tr><td>' + t.limit_times + '板</td><td>' + t.count + '</td><td>' + m.name + '</td><td>' + m.industry + '</td>';
          html += '<td>' + m.first_time + '</td><td>' + (m.last_time !== m.first_time ? m.last_time : '') + '</td>';
          html += '<td>' + (m.fd_amount / 1e8).toFixed(1) + '</td></tr>';
        }
      }
      html += '</tbody></table>';
    }

    container.innerHTML = html;

    // 提取当前日涨停标的 ts_code 用于概念图过滤
    var stockCodes = new Set();
    if (dayData && dayData.tables && dayData.tables.tiers) {
      for (var _ti = 0; _ti < dayData.tables.tiers.length; _ti++) {
        var _t = dayData.tables.tiers[_ti];
        if (_t.members) {
          for (var _mj = 0; _mj < _t.members.length; _mj++) {
            if (_t.members[_mj].ts_code) {
              stockCodes.add(_t.members[_mj].ts_code);
            }
          }
        }
      }
    }
    renderConceptGraph(selectedDate, stockCodes);
  }

  // ==================== 涨停概况图表 ====================

  function renderLimitupChart(data) {
    var el = document.getElementById('chart-limitup');
    if (!el) return;
    if (!state.ltChart) state.ltChart = echarts.init(el);

    var dates = data.dates || [];
    var byDate = data.by_date || {};

    var upCounts = [];
    for (var i = 0; i < dates.length; i++) {
      var d = dates[i];
      var day = byDate[d];
      upCounts.push([d, day && day.kpi ? (day.kpi.limit_up_cnt || 0) : 0]);
    }

    state.ltChart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 45, right: 20, top: 20, bottom: 30 },
      tooltip: {
        trigger: 'axis',
        formatter: function (ps) {
          return '<b>' + fmtDate(ps[0].axisValue) + '</b><br/>涨停: <b>' + ps[0].data[1] + '</b>';
        },
        backgroundColor: cssVar('--bg-card'),
        borderColor: cssVar('--border-color'),
        textStyle: { color: cssVar('--text-primary'), fontSize: 12 }
      },
      xAxis: {
        type: 'category',
        data: upCounts.map(function (x) { return fmtDate(x[0]); }),
        axisLabel: {
          color: cssVar('--text-secondary'),
          fontSize: 10,
          interval: Math.floor(upCounts.length / 8)
        },
        axisLine: { lineStyle: { color: cssVar('--border-color') } },
        axisTick: { show: false }
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: cssVar('--text-secondary'), fontSize: 10 },
        splitLine: { lineStyle: { color: cssVar('--border-color') } },
        axisLine: { lineStyle: { color: cssVar('--border-color') } }
      },
      series: [{
        type: 'line',
        data: upCounts.map(function (x) { return [fmtDate(x[0]), x[1]]; }),
        lineStyle: { color: cssVar('--accent-red'), width: 1.5 },
        itemStyle: { color: cssVar('--accent-red') },
        symbol: 'none',
        areaStyle: { color: cssVar('--accent-red'), opacity: 0.08 }
      }]
    }, true);
  }

  // ==================== 概念力导向图 ====================

  function renderConceptGraph(selectedDate, dayStockCodes) {
    var el = document.getElementById('chart-concept-graph');
    if (!el) return;
    if (!selectedDate) {
      if (state.conceptChart) state.conceptChart.clear();
      return;
    }

    loadJson('data/concept_graph.json')
      .then(function (conceptData) {
        if (!conceptData || !conceptData.concepts || !conceptData.concepts.length) {
          el.innerHTML = '<p style="color:var(--text-secondary);text-align:center;padding:40px">当日无概念聚合数据</p>';
          return;
        }

        // 过滤：仅保留当前日期涨停标的所属的概念
        if (dayStockCodes && dayStockCodes.size > 0) {
          for (var _ci = conceptData.concepts.length - 1; _ci >= 0; _ci--) {
            var _c = conceptData.concepts[_ci];
            if (_c.members) {
              _c.members = _c.members.filter(function (_m) { return dayStockCodes.has(_m.ts_code); });
            }
            _c.member_count = (_c.members || []).length;
            if (_c.member_count === 0) {
              conceptData.concepts.splice(_ci, 1);
            }
          }
          if (conceptData.links) {
            conceptData.links = conceptData.links.filter(function (_l) {
              return dayStockCodes.has(_l.target);
            });
          }
        } else if (conceptData.meta && conceptData.meta.date && selectedDate && conceptData.meta.date !== selectedDate) {
          console.info('concept graph date mismatch: graph=' + conceptData.meta.date + ' selected=' + selectedDate);
        }

        if (!conceptData.concepts.length) {
          el.innerHTML = '<p style="color:var(--text-secondary);text-align:center;padding:40px">当前日期无概念聚合数据（请运行 concept_cluster.py 生成）</p>';
          if (state.conceptChart) state.conceptChart.clear();
          return;
        }

        var nodes = [];
        var links = [];
        var categories = [];

        var concepts = conceptData.concepts || [];
        var hasThemes = (conceptData.themes || []).length > 0;

        categories.push({ name: '概念' });
        categories.push({ name: '主题' });
        categories.push({ name: '涨停标的' });

        for (var ci = 0; ci < concepts.length; ci++) {
          var c = concepts[ci];
          var nodeSize = Math.max(20, c.heat * 60);
          nodes.push({
            name: c.name,
            value: c.heat,
            symbolSize: nodeSize,
            category: 0,
            itemStyle: { color: interpColor(cssVar('--div-pos-soft'), cssVar('--div-pos'), c.heat) },
            label: { show: true, fontSize: Math.max(10, nodeSize * 0.3), fontWeight: 'bold' },
            _type: 'concept',
            _heat: c.heat,
            _member_count: c.member_count,
            _members: c.members,
          });

          if (c.members) {
            for (var mi = 0; mi < c.members.length; mi++) {
              var m = c.members[mi];
              var stockNodeId = m.ts_code;
              if (!nodes.some(function (n) { return n.name === stockNodeId; })) {
                var stockSize = Math.min(15 + m.limit_times * 3, 30);
                var stockColor = m.limit_times >= 3 ? cssVar('--accent-red') : cssVar('--text-secondary');
                nodes.push({
                  name: stockNodeId,
                  value: m.limit_times,
                  symbolSize: stockSize,
                  category: 2,
                  itemStyle: { color: stockColor },
                  label: { show: true, fontSize: 10, formatter: m.name },
                  _type: 'stock',
                  _name: m.name,
                  _limit_times: m.limit_times,
                  _fd_amount: m.fd_amount,
                  _first_time: m.first_time,
                  _industry: m.industry,
                });
              }
              links.push({
                source: c.name,
                target: stockNodeId,
                value: 1,
                lineStyle: { width: 1, opacity: 0.25, color: '#888' },
              });
            }
          }
        }

        var themes = conceptData.themes || [];
        for (var ti = 0; ti < themes.length; ti++) {
          var t = themes[ti];
          var tSize = Math.max(24, t.heat * 70);
          nodes.push({
            name: t.name,
            value: t.heat,
            symbolSize: tSize,
            category: 1,
            itemStyle: { color: cssVar('--s2') },
            label: { show: true, fontSize: Math.max(11, tSize * 0.28), fontWeight: 'bold' },
            _type: 'theme',
            _heat: t.heat,
            _description: t.description,
            _member_count: t.member_count,
          });

          if (t.sub_concepts) {
            for (var si = 0; si < t.sub_concepts.length; si++) {
              links.push({
                source: t.name,
                target: t.sub_concepts[si],
                value: 1,
                lineStyle: { width: 2, opacity: 0.4, color: cssVar('--s2'), curveness: 0.3 },
              });
            }
          }
        }

        var extraLinks = conceptData.links || [];
        for (var li = 0; li < extraLinks.length; li++) {
          var ek = extraLinks[li];
          links.push({
            source: ek.source,
            target: ek.target,
            value: 1,
            lineStyle: { width: 1, opacity: 0.2, color: '#888' },
          });
        }

        var option = {
          backgroundColor: 'transparent',
          title: {
            text: hasThemes ? '概念主题力向导图' : '概念力向导图',
            textStyle: { color: cssVar('--text-secondary'), fontSize: 13, fontWeight: 'normal' },
            left: 8, top: 4,
          },
          tooltip: {
            trigger: 'item',
            formatter: function (p) {
              if (p.data._type === 'concept') {
                var members = p.data._members || [];
                var list = '';
                for (var i = 0; i < Math.min(members.length, 5); i++) {
                  list += '<br/>  · ' + members[i].name + ' (' + members[i].limit_times + '板)';
                }
                if (members.length > 5) list += '<br/>  · … 还有 ' + (members.length - 5) + ' 只';
                return '<b>' + p.name + '</b>' +
                  '<br/>热度: <b>' + p.data._heat.toFixed(2) + '</b>' +
                  '<br/>标的: <b>' + p.data._member_count + '</b> 只' + list;
              } else if (p.data._type === 'theme') {
                return '<b>' + p.name + '</b>' +
                  '<br/>主题热度: <b>' + p.data._heat.toFixed(2) + '</b>' +
                  '<br/>涵盖: <b>' + p.data._member_count + '</b> 只' +
                  (p.data._description ? '<br/><br/>' + p.data._description : '');
              } else if (p.data._type === 'stock') {
                return '<b>' + (p.data._name || p.name) + '</b>' +
                  '<br/>代码: ' + p.name +
                  '<br/>连板: <b>' + p.data._limit_times + '板</b>' +
                  (p.data._industry ? '<br/>行业: ' + p.data._industry : '') +
                  (p.data._fd_amount ? '<br/>封单: ' + (p.data._fd_amount / 1e8).toFixed(1) + '亿' : '') +
                  (p.data._first_time ? '<br/>封板: ' + p.data._first_time : '');
              }
              return p.name;
            },
            backgroundColor: cssVar('--bg-card'),
            borderColor: cssVar('--border-color'),
            textStyle: { color: cssVar('--text-primary'), fontSize: 12 },
          },
          legend: {
            data: categories,
            top: 0, left: 'center',
            textStyle: { color: cssVar('--text-secondary'), fontSize: 11 },
            icon: 'roundRect', itemWidth: 14, itemHeight: 8,
          },
          series: [{
            type: 'graph',
            layout: 'force',
            force: {
              repulsion: 300,
              edgeLength: [80, 200],
              layoutAnimation: false,
              friction: 0.1,
            },
            roam: true,
            draggable: true,
            data: nodes,
            links: links,
            categories: categories,
            edgeSymbol: ['none', 'none'],
            lineStyle: { color: 'source', opacity: 0.25, width: 1 },
            label: { show: true, position: 'right', color: cssVar('--text-primary'), fontSize: 10 },
            emphasis: {
              focus: 'adjacency',
              lineStyle: { width: 3, opacity: 0.8 },
            },
            itemStyle: {
              borderColor: cssVar('--bg-card'),
              borderWidth: 1,
            },
          }],
        };

        if (!state.conceptChart) {
          state.conceptChart = echarts.init(el);
        }
        state.conceptChart.setOption(option, true);
        state.conceptChart.resize();
      })
      .catch(function (err) {
        if (state.conceptChart) state.conceptChart.clear();
        el.innerHTML = '<p style="color:var(--text-secondary);text-align:center;padding:40px">概念数据未就绪（运行 concept_cluster.py 生成）</p>';
        console.info('concept graph not available:', err.message);
      });
  }

  // ==================== 概念生成按钮 ====================

  function initConceptGenerateBtn() {
    var btn = document.getElementById('btn-generate-concept');
    var statusEl = document.getElementById('concept-gen-status');
    if (!btn) return;

    btn.addEventListener('click', async function () {
      if (btn.disabled) return;
      btn.disabled = true;
      btn.textContent = '⏳ 生成中…';
      statusEl.textContent = '正在调用 AI 生成概念图…';
      statusEl.style.color = 'var(--accent-yellow)';

      try {
        var resp = await fetch('/api/concept/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: 'concept' }),
        });
        var result = await resp.json();

        if (result.status === 'error') {
          statusEl.textContent = '✗ 失败: ' + (result.message || 'unknown');
          statusEl.style.color = 'var(--accent-red)';
          btn.disabled = false;
          btn.textContent = '🤖 生成概念图';
          return;
        }

        if (result.n_concepts === 0) {
          statusEl.textContent = '∼ 当日涨停无可用概念数据';
          statusEl.style.color = 'var(--text-secondary)';
        } else {
          statusEl.textContent = '✓ 已生成 ' + result.n_concepts + ' 个概念（' + (result.date || '') + '）';
          statusEl.style.color = 'var(--accent-green)';
        }

        // 重新加载概念图
        state.conceptGraph = null;
        var limitupDate = state.limitupDate;
        if (limitupDate) {
          var stockCodes = new Set();
          var limitup = state.limitup;
          if (limitup && limitup.by_date && limitup.by_date[limitupDate] &&
              limitup.by_date[limitupDate].tables &&
              limitup.by_date[limitupDate].tables.tiers) {
            var tiers = limitup.by_date[limitupDate].tables.tiers;
            for (var ti = 0; ti < tiers.length; ti++) {
              var members = tiers[ti].members || [];
              for (var mi = 0; mi < members.length; mi++) {
                if (members[mi].ts_code) stockCodes.add(members[mi].ts_code);
              }
            }
          }
          renderConceptGraph(limitupDate, stockCodes);
        }
      } catch (e) {
        statusEl.textContent = '✗ 请求失败: ' + e.message;
        statusEl.style.color = 'var(--accent-red)';
      } finally {
        btn.disabled = false;
        btn.textContent = '🤖 生成概念图';
      }
    });
  }

  // ==================== 涨停池主渲染 ====================

  function renderLimitupTab(data, calendar, selectedDate) {
    if (!data || !calendar) return;
    renderDatePicker(calendar, selectedDate);
    if (data.by_date && data.by_date[selectedDate]) {
      renderLimitupDay(data.by_date[selectedDate], selectedDate);
    } else {
      loadJson('data/limitup/' + selectedDate + '.json')
        .then(function (dayData) { renderLimitupDay(dayData, selectedDate); })
        .catch(function () {
          var container = document.getElementById('limitup-day-content');
          if (container) {
            container.innerHTML = '<p style="color:var(--text-secondary)">该日无涨停数据</p>';
          }
        });
    }
    renderLimitupChart(data);
  }

  // ==================== 窗口 resize ====================

  function setupResizeHandler() {
    window.addEventListener('resize', function () {
      ['ltChart', 'conceptChart'].forEach(function (k) {
        if (state[k]) state[k].resize();
      });
    });
  }

  // ==================== 入口 ====================

  /**
   * 初始化涨停池标签页
   *
   * @param {string} containerId - 包含涨停池 DOM 结构的容器元素 ID
   */
  function initLimitup(containerId) {
    var container = document.getElementById(containerId);
    if (!container) {
      console.warn('limitup.js: container #' + containerId + ' not found');
      return;
    }

    // 一次性设置
    setupDatePickerOutsideClick();
    setupResizeHandler();
    initConceptGenerateBtn();

    // 加载数据
    Promise.all([
      loadJson('data/limitup_main.json'),
      loadJson('data/calendar.json'),
    ]).then(function (results) {
      var limitupMain = results[0];
      var calendar = results[1];

      state.limitup = limitupMain;
      state.calendar = calendar;

      // 默认选中最后一个交易日
      var calDates = calendar.dates || [];
      var lastTrade = null;
      for (var i = calDates.length - 1; i >= 0; i--) {
        if (calDates[i].has_data) {
          lastTrade = calDates[i];
          break;
        }
      }
      state.limitupDate = lastTrade ? lastTrade.date : '';

      renderLimitupTab(limitupMain, calendar, state.limitupDate);
    }).catch(function (err) {
      console.error('limitup.js: data load failed', err);
      container.innerHTML = '<div style="color:var(--accent-red);padding:12px">加载数据失败: ' + err.message + '</div>';
    });
  }

  // 暴露模块接口
  global.initLimitup = initLimitup;

})(typeof window !== 'undefined' ? window : this);
