/**
 * tbot shared UI utilities
 *
 * Extracted from market_research/report/static/app.js — common functions
 * shared across dashboard views.
 *
 * Usage:
 *   <script src="/static/shared.js"></script>
 *   <script src="/static/app.js"></script>
 */

(function () {
  'use strict';

  // ==================== CSS variable access ====================

  const _CSS = getComputedStyle(document.documentElement);

  /**
   * Read a CSS custom property value (trimmed).
   * @param {string} name — property name, e.g. '--bg-card'
   * @returns {string}
   */
  function cssVar(name) {
    return _CSS.getPropertyValue(name).trim();
  }

  /**
   * Short alias for cssVar().  Kept for backward compat with
   * code that used `C(n)` in the original app.js.
   * @param {string} n
   * @returns {string}
   */
  const C = cssVar;

  // ==================== Color utilities ====================

  /**
   * Parse a hex colour string to an RGB tuple.
   * Accepts 3- and 6-digit forms (with or without leading #).
   * @param {string} h
   * @returns {[number, number, number]}
   */
  function parseHex(h) {
    h = h.replace('#', '');
    if (h.length === 3) h = h.split('').map(function (x) { return x + x; }).join('');
    return [
      parseInt(h.slice(0, 2), 16),
      parseInt(h.slice(2, 4), 16),
      parseInt(h.slice(4, 6), 16),
    ];
  }

  /**
   * Linear RGB interpolation between two hex colours.
   * @param {string} hexA
   * @param {string} hexB
   * @param {number} t — 0..1
   * @returns {string} css rgb() string
   */
  function interpColor(hexA, hexB, t) {
    var a = parseHex(hexA);
    var b = parseHex(hexB);
    return 'rgb(' +
      Math.round(a[0] + (b[0] - a[0]) * t) + ',' +
      Math.round(a[1] + (b[1] - a[1]) * t) + ',' +
      Math.round(a[2] + (b[2] - a[2]) * t) +
    ')';
  }

  /**
   * Map a signed value to a diverging colour (blue-neg / gray-mid / red-pos).
   * Uses CSS variables --div-neg, --div-mid, --div-pos etc.
   * @param {number} v — normalized value in [-1, 1]
   * @returns {string} css colour
   */
  function divColor(v) {
    var a = Math.max(-1, Math.min(1, v));
    if (Math.abs(a) < 0.02) return C('--div-mid');
    if (a > 0) {
      return a < 0.5
        ? interpColor(C('--div-mid'), C('--div-pos-soft'), a / 0.5)
        : interpColor(C('--div-pos-soft'), C('--div-pos'), (a - 0.5) / 0.5);
    }
    return -a < 0.5
      ? interpColor(C('--div-mid'), C('--div-neg-soft'), -a / 0.5)
      : interpColor(C('--div-neg-soft'), C('--div-neg'), (-a - 0.5) / 0.5);
  }

  // ==================== Date formatting ====================

  /**
   * Format a YYYYMMDD numeric string to YYYY-MM-DD.
   * @param {string} d — e.g. '20260213'
   * @returns {string} e.g. '2026-02-13'
   */
  function fmtDate(d) {
    return d.slice(0, 4) + '-' + d.slice(4, 6) + '-' + d.slice(6, 8);
  }

  // ==================== Data fetching ====================

  /**
   * Fetch a JSON resource with cache-busting query parameter.
   * Rejects on non-2xx status.
   * @param {string} path — URL or relative path
   * @returns {Promise<any>}
   */
  function loadJson(path) {
    var sep = path.indexOf('?') >= 0 ? '&' : '?';
    return fetch(path + sep + 't=' + Date.now()).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + path);
      return r.json();
    });
  }

  // ==================== Financial formatting ====================

  /**
   * Format a numeric value as CNY with auto-scaling (亿 / 万).
   * @param {number|null|undefined} v
   * @returns {string}
   */
  function fmtMoney(v) {
    if (v == null) return '-';
    var abs = Math.abs(v);
    if (abs >= 1e8) return (v / 1e8).toFixed(1) + '亿';
    if (abs >= 1e4) return (v / 1e4).toFixed(0) + '万';
    return v.toFixed(0);
  }

  /**
   * Return a css class name based on value sign ('up' / 'down' / '').
   * @param {number|null|undefined} v
   * @returns {string}
   */
  function signClass(v) {
    if (v == null) return '';
    return v > 0 ? 'up' : v < 0 ? 'down' : '';
  }

  // ==================== Tab / routing ====================

  /**
   * Activate a tab by its hash fragment (without the leading #).
   * Toggles .active class on sections as well as sidebar links.
   * Resizes any known ECharts instances after a short delay.
   *
   * Called automatically on hashchange and on initial load.
   *
   * @param {string} hash — location.hash value (with or without #)
   */
  function activateTab(hash) {
    var id = hash.replace('#', '') || 'industry';

    var sections = document.querySelectorAll('.tab-content');
    sections.forEach(function (el) {
      el.classList.toggle('active', el.id === id);
    });

    var links = document.querySelectorAll('.tab-link');
    links.forEach(function (el) {
      el.classList.toggle('active', el.getAttribute('href') === '#' + id);
    });

    // Hooks for tab-specific logic
    if (typeof window.__onTabActivate === 'function') {
      window.__onTabActivate(id);
    }

    // Resize ECharts instances after layout settles
    setTimeout(function () {
      knownChartKeys().forEach(function (k) {
        if (window.__STATE && window.__STATE[k] && typeof window.__STATE[k].resize === 'function') {
          window.__STATE[k].resize();
        }
      });
    }, 50);
  }

  /**
   * Resize all known ECharts instances immediately.
   * Useful after UI transitions (panel open/close, etc.).
   */
  function resizeCharts() {
    knownChartKeys().forEach(function (k) {
      if (window.__STATE && window.__STATE[k] && typeof window.__STATE[k].resize === 'function') {
        window.__STATE[k].resize();
      }
    });
  }

  /**
   * Known ECharts instance keys stored in the global STATE object.
   * Override by setting window.__knownChartKeys.
   * @returns {string[]}
   */
  function knownChartKeys() {
    if (Array.isArray(window.__knownChartKeys)) return window.__knownChartKeys;
    return [
      'heatChart', 'icChart', 'quintChart', 'rankChart',
      'ltChart', 'fundflowChart', 'simChart', 'conceptChart', 'updChart',
    ];
  }

  // ==================== WebSocket helper ====================

  /**
   * Build a WebSocket URL from the current page's protocol and host.
   * @param {string} [path='/api/chat/ws'] — WS path
   * @returns {string}
   */
  function getWsUrl(path) {
    var loc = window.location;
    var proto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
    return proto + '//' + loc.host + (path || '/api/chat/ws');
  }

  // ==================== Hash routing (auto-setup) ====================

  window.addEventListener('hashchange', function () {
    activateTab(location.hash);
  });

  if (!location.hash) {
    location.hash = '#industry';
  }

  // Expose on DOMContentLoaded (executed after the initial hash assignment)
  document.addEventListener('DOMContentLoaded', function () {
    activateTab(location.hash);
  });

  // Global resize handler
  window.addEventListener('resize', function () {
    resizeCharts();
  });

  // ==================== Public API ====================

  window.__shared = {
    cssVar: cssVar,
    C: C,
    parseHex: parseHex,
    interpColor: interpColor,
    divColor: divColor,
    fmtDate: fmtDate,
    loadJson: loadJson,
    fmtMoney: fmtMoney,
    signClass: signClass,
    activateTab: activateTab,
    resizeCharts: resizeCharts,
    knownChartKeys: knownChartKeys,
    getWsUrl: getWsUrl,
  };

})();
