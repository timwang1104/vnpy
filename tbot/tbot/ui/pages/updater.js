/**
 * updater.js — 数据更新管理页面模块
 *
 * 从 market_research/report/static/app.js 提取的更新管理 Tab 独立模块。
 * 提供状态轮询、触发更新、SSE 日志流、Cron 管理等功能。
 *
 * Usage:
 *   <script src="/static/shared.js"></script>
 *   <script src="/pages/updater.js"></script>
 *   <script>initUpdater('updater');</script>
 *
 * API 端点（tbot 后端）:
 *   POST /api/data/update        — 触发数据更新
 *   GET  /api/data/status        — 查询更新状态
 *   GET  /api/data/log           — SSE 日志流
 *   POST /api/data/cron/install  — 安装 crontab
 *   POST /api/data/cron/remove   — 移除 crontab
 *   GET  /api/data/cron/status   — 查询 crontab 状态
 */

(function () {
  'use strict';

  // ==================== 依赖检查 ====================

  if (!window.__shared) {
    console.error('updater.js 依赖 shared.js，请先加载 shared.js');
    return;
  }

  var _ = window.__shared;

  // ==================== 内部状态 ====================

  /** SSE EventSource 引用 */
  var _sseSource = null;

  /** 运行中标记（用于定时器判活） */
  var _running = false;

  /** 已耗时计时器 */
  var _elapsedTimer = null;

  // ==================== 工具函数 ====================

  /**
   * 将状态数值映射为显示文本和 CSS 类名。
   * @param {string} status — 'idle' | 'running' | 'completed' | 'error'
   * @returns {{text: string, cls: string}}
   */
  function statusDisplay(status) {
    var map = {
      idle:      { text: '就绪',      cls: 'upd-status-idle' },
      running:   { text: '⏳ 运行中', cls: 'upd-status-running' },
      completed: { text: '✅ 已完成',  cls: 'upd-status-done' },
      error:     { text: '❌ 失败',    cls: 'upd-status-error' },
    };
    return map[status] || { text: status, cls: 'upd-status-idle' };
  }

  // ==================== API 请求 ====================

  /**
   * 刷新更新器状态 UI。
   * 调用 GET /api/data/status 并更新各个显示元素。
   */
  function refreshStatus() {
    fetch('/api/data/status')
      .then(function (r) { return r.json(); })
      .then(function (state) {
        updateUI(state);
        refreshCronStatus();
      })
      .catch(function () {
        // 后端可能未就绪，静默处理
      });
  }

  /**
   * 刷新 cron 状态 UI。
   * 调用 GET /api/data/cron/status。
   */
  function refreshCronStatus() {
    fetch('/api/data/cron/status')
      .then(function (r) { return r.json(); })
      .then(function (info) {
        var el = document.getElementById('upd-cron-status');
        if (info.installed) {
          el.textContent = '✅ 已注册';
          el.className = 'updater-value upd-status-done';
        } else {
          el.textContent = '未注册';
          el.className = 'updater-value upd-status-idle';
        }
      })
      .catch(function () {
        document.getElementById('upd-cron-status').textContent = '查询失败';
      });
  }

  /**
   * 根据后端返回的状态对象更新整个 UI。
   * @param {Object} state — 来自 /api/data/status 的响应
   */
  function updateUI(state) {
    var statusEl     = document.getElementById('upd-status');
    var startedEl    = document.getElementById('upd-started');
    var elapsedEl    = document.getElementById('upd-elapsed');
    var progressLabel= document.getElementById('upd-progress-label');
    var progressFill = document.getElementById('upd-progress-fill');
    var triggerBtn   = document.getElementById('upd-trigger-btn');
    var reloadBtn    = document.getElementById('upd-reload-btn');
    var lastResultsEl= document.getElementById('upd-last-results');

    if (!statusEl) return;  // DOM 尚未就绪

    // — 状态 —
    var s = statusDisplay(state.status);
    statusEl.textContent = s.text;
    statusEl.className = 'updater-value ' + s.cls;

    // — 开始时间 —
    startedEl.textContent = state.started_at ? state.started_at.slice(0, 19) : '—';

    // — 进度文字 —
    if (state.progress && state.progress.label) {
      progressLabel.textContent = state.progress.label +
        ' (' + state.progress.current + '/' + state.progress.total + ')';
    } else {
      progressLabel.textContent = '—';
    }

    // — 进度条 —
    if (state.status === 'running') {
      progressFill.className = 'update-progress-fill running';
    } else if (state.status === 'completed') {
      progressFill.className = 'update-progress-fill done';
    } else if (state.status === 'error') {
      progressFill.className = 'update-progress-fill error';
    } else {
      progressFill.className = 'update-progress-fill';
      progressFill.style.width = '0%';
    }

    // — 按钮状态 —
    if (state.status === 'running') {
      triggerBtn.disabled = true;
      triggerBtn.textContent = '⏳ 运行中…';
      _running = true;
    } else {
      triggerBtn.disabled = false;
      triggerBtn.textContent = '🔄 启动更新';
      _running = false;
    }
    reloadBtn.style.display =
      (state.status === 'completed' || state.status === 'error')
        ? 'inline-block' : 'none';

    // — 耗时计时器 —
    if (state.status === 'running' && state.started_at) {
      var started = new Date(state.started_at);
      var elapsed = Math.floor((Date.now() - started.getTime()) / 1000);
      elapsedEl.textContent = elapsed + 's';
      if (!_elapsedTimer) {
        _elapsedTimer = setInterval(function () {
          if (_running) {
            var s = new Date(state.started_at);
            var e = Math.floor((Date.now() - s.getTime()) / 1000);
            var el = document.getElementById('upd-elapsed');
            if (el) el.textContent = e + 's';
          } else {
            clearInterval(_elapsedTimer);
            _elapsedTimer = null;
          }
        }, 1000);
      }
    } else if (state.finished_at && state.started_at) {
      var s = new Date(state.started_at);
      var f = new Date(state.finished_at);
      elapsedEl.textContent = Math.floor((f.getTime() - s.getTime()) / 1000) + 's';
    } else {
      elapsedEl.textContent = '—';
    }

    // — 上次更新结果表 —
    if (state.last_results && state.last_results.length > 0) {
      var html = '<table class="data-table"><thead><tr>' +
        '<th>表</th><th>写入</th><th>成功天数</th><th>失败天数</th><th>总数</th>' +
        '</tr></thead><tbody>';
      for (var i = 0; i < state.last_results.length; i++) {
        var r = state.last_results[i];
        if (r.status === 'ok') {
          html += '<tr>' +
            '<td>' + (r.table || r.status || '—') + '</td>' +
            '<td>' + (r.inserted || r.concept_count || 0) + '</td>' +
            '<td>' + (r.ok_days || r.ok_stocks || 0) + '</td>' +
            '<td>' + (r.fail_days || r.fail_stocks || 0) + '</td>' +
            '<td>' + (r.total || 0) + '</td>' +
            '</tr>';
        }
      }
      html += '</tbody></table>';
      lastResultsEl.innerHTML = html;
    } else {
      lastResultsEl.innerHTML = '';
    }
  }

  // ==================== 触发更新 ====================

  /**
   * 触发数据更新。调用 POST /api/data/update 后连接 SSE。
   */
  function triggerUpdate() {
    var triggerBtn = document.getElementById('upd-trigger-btn');
    triggerBtn.disabled = true;
    triggerBtn.textContent = '⏳ 启动中…';

    fetch('/api/data/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ do_build: true }),
    })
      .then(function (resp) { return resp.json(); })
      .then(function (result) {
        if (result.status === 'error') {
          appendLog('[错误] ' + (result.message || '启动失败'));
          triggerBtn.disabled = false;
          triggerBtn.textContent = '🔄 启动更新';
          return;
        }
        // 连接 SSE 日志流
        connectSSE();
        // 轮询状态
        refreshStatus();
      })
      .catch(function (e) {
        appendLog('[错误] 请求失败: ' + e.message);
        triggerBtn.disabled = false;
        triggerBtn.textContent = '🔄 启动更新';
      });
  }

  // ==================== SSE 日志流 ====================

  /**
   * 连接 /api/data/log SSE 流，接收实时日志。
   */
  function connectSSE() {
    // 关闭旧连接
    if (_sseSource) {
      _sseSource.close();
      _sseSource = null;
    }

    var logEl = document.getElementById('upd-log-output');
    if (logEl) logEl.textContent = '';

    var evtSource = new EventSource('/api/data/log');
    _sseSource = evtSource;

    evtSource.onmessage = function (e) {
      appendLog(e.data);
    };

    evtSource.addEventListener('complete', function () {
      refreshStatus();
      _sseSource = null;
      appendLog('\n[完成] 数据更新已完成');
    });

    evtSource.addEventListener('error', function () {
      refreshStatus();
      _sseSource = null;
      appendLog('\n[错误] 更新异常终止');
    });
  }

  /**
   * 追加日志行到日志输出区。
   * @param {string} line
   */
  function appendLog(line) {
    var el = document.getElementById('upd-log-output');
    if (!el) return;
    el.textContent += line + '\n';
    el.scrollTop = el.scrollHeight;
  }

  // ==================== 事件绑定 ====================

  /**
   * 绑定所有更新管理页面的 DOM 事件。
   */
  function bindEvents() {
    // 触发更新
    var triggerBtn = document.getElementById('upd-trigger-btn');
    if (triggerBtn) {
      triggerBtn.addEventListener('click', function () {
        if (this.disabled) return;
        triggerUpdate();
      });
    }

    // 刷新页面
    var reloadBtn = document.getElementById('upd-reload-btn');
    if (reloadBtn) {
      reloadBtn.addEventListener('click', function () {
        location.reload();
      });
    }

    // 安装 cron
    var cronInstallBtn = document.getElementById('upd-cron-install');
    if (cronInstallBtn) {
      cronInstallBtn.addEventListener('click', async function () {
        try {
          var resp = await fetch('/api/data/cron/install', { method: 'POST' });
          var data = await resp.json();
          if (data.status === 'ok') {
            document.getElementById('upd-cron-status').textContent = '✅ 已注册';
          } else {
            document.getElementById('upd-cron-status').textContent = '注册失败';
          }
        } catch (e) {
          document.getElementById('upd-cron-status').textContent = '请求失败';
        }
        refreshCronStatus();
      });
    }

    // 移除 cron
    var cronRemoveBtn = document.getElementById('upd-cron-remove');
    if (cronRemoveBtn) {
      cronRemoveBtn.addEventListener('click', async function () {
        try {
          var resp = await fetch('/api/data/cron/remove', { method: 'POST' });
          var data = await resp.json();
          if (data.status === 'ok') {
            document.getElementById('upd-cron-status').textContent = '未注册';
          } else {
            document.getElementById('upd-cron-status').textContent = '移除失败';
          }
        } catch (e) {
          document.getElementById('upd-cron-status').textContent = '请求失败';
        }
        refreshCronStatus();
      });
    }
  }

  // ==================== 公共 API ====================

  /**
   * 初始化更新管理页面。
   * @param {string} containerId — 容器 DOM id（与 REGISTRY 中一致，如 'updater'）
   */
  window.initUpdater = function (containerId) {
    bindEvents();

    // 初始状态查询
    refreshStatus();
    refreshCronStatus();
  };

})();
