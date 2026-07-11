/* chat.js — 独立聊天页面组件
 *
 * 从 market_research/report/static/app.js 提取的聊天模块。
 *
 * 用法:
 *   const chat = initChat('chat-container');
 *   chat.open();
 *   chat.close();
 *   chat.send('你好');
 *   chat.destroy();
 *
 * 容器需要自行提供以下 CSS 变量: --bg-primary, --bg-card, --bg-hover,
 *   --text-primary, --text-secondary, --accent-blue, --accent-red, --s1, --s2;
 *   --border-color, --viz-1, --viz-2（若不存在则使用 fallback 值）。
 *
 * 上下文注入：
 *   父页面可通过 chat.setContextProvider(fn) 注册一个返回上下文对象的函数，
 *   每次发送消息/切换 tab 时自动收集并注入 WebSocket。
 */

(function () {
  'use strict';

  // ==================== 默认 CSS 变量 fallback ====================
  const CSS = getComputedStyle(document.documentElement);
  function cssVar(name, fallback) {
    return CSS.getPropertyValue(name).trim() || fallback;
  }

  // ==================== 内置 HTML 模板 ====================
  function buildInlineStyles(instanceId) {
    // 使用实例 ID 前缀避免全局样式冲突；若需全局只需首次注入
    if (buildInlineStyles._injected) return;
    buildInlineStyles._injected = true;

    const styles = document.createElement('style');
    styles.textContent = `
/* ---------- chat.js 组件样式 (与 market_research 一致) ---------- */
.chat-fab-${instanceId} {
  position: fixed; bottom: 24px; right: 24px; z-index: 300;
  width: 56px; height: 56px; border-radius: 50%;
  border: none; cursor: pointer;
  background: ${cssVar('--accent-blue', '#4a7cf7')}; color: #fff;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 4px 16px rgba(0,0,0,0.3);
  transition: transform 0.2s, opacity 0.3s, box-shadow 0.2s;
}
.chat-fab-${instanceId}:hover {
  transform: scale(1.08);
  box-shadow: 0 6px 20px rgba(74,124,247,0.4);
}
.chat-fab-${instanceId}:active { transform: scale(0.95); }
.chat-fab-${instanceId}.hidden { opacity: 0; pointer-events: none; }

.chat-panel-${instanceId} {
  position: fixed; top: 0; right: 0; z-index: 400;
  width: 400px; height: 100vh;
  background: ${cssVar('--bg-primary', '#1a1d29')};
  border-left: 1px solid ${cssVar('--border-color', '#3a3f54')};
  display: flex; flex-direction: column;
  transform: translateX(0);
  transition: transform 0.3s ease;
}
.chat-panel-${instanceId}.hidden {
  transform: translateX(100%);
}

.chat-panel-header-${instanceId} {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px;
  background: ${cssVar('--bg-card', '#232736')};
  border-bottom: 1px solid ${cssVar('--border-color', '#3a3f54')};
  flex-shrink: 0;
}
.chat-panel-title-${instanceId} {
  display: flex; align-items: center; gap: 8px;
  font-size: 15px; font-weight: 600; color: ${cssVar('--text-primary', '#e8eaf0')};
}
.chat-panel-title-${instanceId} svg { opacity: 0.7; }
.chat-panel-controls-${instanceId} {
  display: flex; align-items: center; gap: 8px;
}

.chat-agent-select-${instanceId} {
  padding: 4px 10px; font-size: 12px;
  border: 1px solid ${cssVar('--border-color', '#3a3f54')}; border-radius: 4px;
  background: ${cssVar('--bg-hover', '#2c3042')}; color: ${cssVar('--text-primary', '#e8eaf0')}; cursor: pointer;
  -webkit-appearance: none; -moz-appearance: none; appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg width='10' height='6' viewBox='0 0 10 6' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%23a0a5b5' stroke-width='1.5' fill='none'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 6px center; padding-right: 22px;
}
.chat-agent-select-${instanceId}:hover { background-color: ${cssVar('--bg-hover', '#2c3042')}; }
.chat-agent-select-${instanceId} option { background: ${cssVar('--bg-card', '#232736')}; color: ${cssVar('--text-primary', '#e8eaf0')}; }

.chat-close-btn-${instanceId} {
  background: none; border: none; color: ${cssVar('--text-secondary', '#a0a5b5')};
  font-size: 18px; cursor: pointer; padding: 2px 6px; border-radius: 4px;
  line-height: 1;
}
.chat-close-btn-${instanceId}:hover { background: ${cssVar('--bg-hover', '#2c3042')}; color: ${cssVar('--text-primary', '#e8eaf0')}; }

.chat-messages-${instanceId} {
  flex: 1; overflow-y: auto; padding: 12px 16px;
  display: flex; flex-direction: column; gap: 12px;
}

.chat-welcome-${instanceId} {
  text-align: center; padding: 32px 16px;
  color: ${cssVar('--text-secondary', '#a0a5b5')}; font-size: 13px;
}
.chat-welcome-${instanceId} p + p { margin-top: 8px; }

.chat-msg-${instanceId} {
  max-width: 85%; padding: 10px 14px;
  border-radius: 10px; font-size: 13px; line-height: 1.55;
  position: relative; word-break: break-word;
}
.chat-msg-${instanceId}.user {
  align-self: flex-end;
  background: ${cssVar('--accent-blue', '#4a7cf7')}; color: #fff;
  border-bottom-right-radius: 4px;
}
.chat-msg-${instanceId}.agent {
  align-self: flex-start;
  background: ${cssVar('--bg-card', '#232736')}; color: ${cssVar('--text-primary', '#e8eaf0')};
  border: 1px solid ${cssVar('--border-color', '#3a3f54')};
  border-bottom-left-radius: 4px;
}
.chat-msg-agent-badge-${instanceId} {
  display: inline-block; font-size: 10px; padding: 1px 6px;
  border-radius: 8px; margin-bottom: 4px; font-weight: 500;
  letter-spacing: 0.3px;
}
.chat-msg-${instanceId}.agent-claude .chat-msg-agent-badge-${instanceId} {
  background: rgba(84,112,198,0.2); color: ${cssVar('--viz-1', '#5470c6')};
}
.chat-msg-${instanceId}.agent-hermes .chat-msg-agent-badge-${instanceId} {
  background: rgba(145,204,117,0.2); color: ${cssVar('--viz-2', '#91cc75')};
}
.chat-msg-${instanceId}.error {
  align-self: flex-start;
  background: rgba(239,90,111,0.1); color: ${cssVar('--accent-red', '#ef5a6f')};
  border: 1px solid rgba(239,90,111,0.3);
  border-bottom-left-radius: 4px;
}

.chat-typing-${instanceId} {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 12px 16px;
}
.chat-typing-${instanceId} span {
  width: 6px; height: 6px; border-radius: 50%;
  background: ${cssVar('--text-secondary', '#a0a5b5')};
  animation: chat-blink-${instanceId} 1.4s infinite both;
}
.chat-typing-${instanceId} span:nth-child(2) { animation-delay: 0.2s; }
.chat-typing-${instanceId} span:nth-child(3) { animation-delay: 0.4s; }

@keyframes chat-blink-${instanceId} {
  0%, 80%, 100% { opacity: 0.3; }
  40% { opacity: 1; }
}

.chat-input-area-${instanceId} {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 16px;
  background: ${cssVar('--bg-card', '#232736')};
  border-top: 1px solid ${cssVar('--border-color', '#3a3f54')};
  flex-shrink: 0;
}
.chat-input-${instanceId} {
  flex: 1; padding: 8px 12px; font-size: 13px;
  border: 1px solid ${cssVar('--border-color', '#3a3f54')}; border-radius: 6px;
  background: ${cssVar('--bg-primary', '#1a1d29')}; color: ${cssVar('--text-primary', '#e8eaf0')};
  outline: none; transition: border-color 0.2s;
}
.chat-input-${instanceId}:focus { border-color: ${cssVar('--accent-blue', '#4a7cf7')}; }
.chat-input-${instanceId}::placeholder { color: ${cssVar('--text-secondary', '#a0a5b5')}; }

.chat-send-btn-${instanceId} {
  padding: 8px 16px; font-size: 13px; font-weight: 500;
  border: none; border-radius: 6px; cursor: pointer;
  background: ${cssVar('--accent-blue', '#4a7cf7')}; color: #fff;
  transition: opacity 0.2s;
}
.chat-send-btn-${instanceId}:hover { opacity: 0.85; }
.chat-send-btn-${instanceId}:disabled {
  opacity: 0.4; cursor: not-allowed;
}

body.chat-open-${instanceId} .main-content {
  margin-right: 400px;
  transition: margin-right 0.3s ease;
}
`;
    document.head.appendChild(styles);
  }

  // ==================== 聊天页组件 ====================

  /**
   * initChat(containerId) — 在指定容器中初始化聊天页面。
   *
   * @param {string} containerId  容器元素的 ID（必须已存在于 DOM 中）。
   * @returns {object} 公开 API: { open, close, toggle, send, destroy, setContextProvider }.
   */
  window.initChat = function initChat(containerId) {
    var container = document.getElementById(containerId);
    if (!container) {
      console.error('chat.js: 容器 #' + containerId + ' 不存在');
      return null;
    }

    // 实例 ID（用于 CSS 隔离）
    var instId = containerId.replace(/[^a-zA-Z0-9_-]/g, '_');

    // ==================== 内部状态 ====================
    var state = {
      open: false,
      ws: null,
      agent: 'claude',
      sessionId: null,              // { claude: 'xxx', hermes: 'yyy' }
      messages: [],
      waiting: false,
      context: {},
      contextProvider: null,        // 可选的 context 收集函数
      destroyHandlers: [],
    };

    // ==================== 工具 ====================
    function getWsUrl() {
      var loc = window.location;
      var proto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
      return proto + '//' + loc.host + '/api/chat/ws';
    }

    // ==================== 注入 HTML ====================
    function buildDOM() {
      // 如果容器已有结构则跳过注入
      if (container.querySelector('.chat-panel-' + instId)) return;

      container.innerHTML =
        '<!-- FAB -->' +
        '<button class="chat-fab-' + instId + '" id="chat-fab-' + instId + '" title="AI 助手" aria-label="打开 AI 助手">' +
          '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>' +
          '</svg>' +
        '</button>' +
        '<!-- 侧边栏面板 -->' +
        '<aside class="chat-panel-' + instId + ' hidden" id="chat-panel-' + instId + '">' +
          '<div class="chat-panel-header-' + instId + '">' +
            '<div class="chat-panel-title-' + instId + '">' +
              '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
                '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>' +
              '</svg>' +
              '<span>AI 助手</span>' +
            '</div>' +
            '<div class="chat-panel-controls-' + instId + '">' +
              '<select class="chat-agent-select-' + instId + '" id="chat-agent-select-' + instId + '" title="切换 AI 引擎">' +
                '<option value="claude">Claude Code</option>' +
                '<option value="hermes">Hermes Agent</option>' +
              '</select>' +
              '<button class="chat-close-btn-' + instId + '" id="chat-close-btn-' + instId + '" title="关闭侧边栏">✕</button>' +
            '</div>' +
          '</div>' +
          '<div class="chat-messages-' + instId + '" id="chat-messages-' + instId + '">' +
            '<div class="chat-welcome-' + instId + '">' +
              '<p>选择 AI 引擎后输入问题，开始信息搜寻。</p>' +
              '<p class="text-muted" style="color:' + cssVar('--text-secondary', '#a0a5b5') + '">当前上下文会自动注入，让 AI 了解你正在查看的内容。</p>' +
            '</div>' +
          '</div>' +
          '<div class="chat-input-area-' + instId + '">' +
            '<input type="text" class="chat-input-' + instId + '" id="chat-input-' + instId + '" placeholder="输入问题…" />' +
            '<button class="chat-send-btn-' + instId + '" id="chat-send-btn-' + instId + '" disabled>发送</button>' +
          '</div>' +
        '</aside>';
    }

    // ==================== DOM 引用（惰性获取）====================
    function el(id) { return container.querySelector('#' + id + '-' + instId); }

    // ==================== 聊天核心逻辑 ====================

    function toggleChat() {
      if (state.open) closeChat();
      else openChat();
    }

    function openChat() {
      if (state.open) return;
      state.open = true;

      el('chat-panel').classList.remove('hidden');
      el('chat-fab').classList.add('hidden');
      document.body.classList.add('chat-open-' + instId);

      collectContext();
      connectWs();

      setTimeout(function () { el('chat-input').focus(); }, 50);
    }

    function closeChat() {
      if (!state.open) return;
      state.open = false;

      el('chat-panel').classList.add('hidden');
      el('chat-fab').classList.remove('hidden');
      document.body.classList.remove('chat-open-' + instId);
    }

    function collectContext() {
      var ctx = {};
      // 如果有外部 contextProvider，优先使用
      if (typeof state.contextProvider === 'function') {
        var external = state.contextProvider();
        if (external && typeof external === 'object') {
          ctx = external;
        }
      }
      state.context = ctx;
      // 连接已建立时推送更新
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: 'context_update', context: ctx }));
      }
    }

    // ==================== WebSocket ====================

    function connectWs() {
      if (state.ws && state.ws.readyState === WebSocket.OPEN) return;

      try {
        var ws = new WebSocket(getWsUrl());
        state.ws = ws;

        ws.onopen = function () {
          ws.send(JSON.stringify({ type: 'context_update', context: state.context }));
        };

        ws.onmessage = function (event) {
          try {
            var data = JSON.parse(event.data);
            handleWsMessage(data);
          } catch (e) {
            console.error('chat ws parse error:', e);
          }
        };

        ws.onclose = function () {
          if (state.open) {
            // 3s 后自动重连
            setTimeout(connectWs, 3000);
          }
        };

        ws.onerror = function () { /* onclose 会紧随其后 */ };

      } catch (e) {
        console.error('chat ws connect error:', e);
        appendMessage('error', '无法连接 AI 服务，请检查网络');
      }
    }

    function handleWsMessage(data) {
      var type = data.type;

      if (type === 'chunk') {
        appendChunk(data.data || '');
      } else if (type === 'done') {
        if (data.session_id) {
          if (!state.sessionId) state.sessionId = {};
          state.sessionId[state.agent] = data.session_id;
        }
        state.waiting = false;
        hideTyping();
        var sendBtn = el('chat-send-btn');
        if (sendBtn) sendBtn.disabled = false;
      } else if (type === 'error') {
        state.waiting = false;
        hideTyping();
        appendMessage('error', data.message || 'AI 服务异常');
        var sendBtn2 = el('chat-send-btn');
        if (sendBtn2) sendBtn2.disabled = false;
      } else if (type === 'agent_switched') {
        // 已由 UI 更新反映
      }
    }

    // ==================== 发送消息 ====================

    function sendMessage(text) {
      if (!text || !text.trim()) return;
      if (state.waiting) return;

      var input = el('chat-input');
      input.value = '';
      el('chat-send-btn').disabled = true;

      // 添加用户消息
      appendMessage('user', text);
      state.messages.push({ role: 'user', content: text });

      // 打字指示器
      showTyping(state.agent);
      state.waiting = true;

      // 发送前收集最新上下文
      collectContext();

      var ws = state.ws;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        connectWs();
        setTimeout(function () {
          if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
            state.waiting = false;
            hideTyping();
            appendMessage('error', '无法连接 AI 服务，请重试');
            return;
          }
          doSendQuery(text);
        }, 500);
      } else {
        doSendQuery(text);
      }
    }

    function doSendQuery(text) {
      var ws = state.ws;
      var msg = {
        type: 'query',
        agent: state.agent,
        message: text,
        context: state.context,
      };
      if (state.sessionId && state.sessionId[state.agent]) {
        msg.session_id = state.sessionId[state.agent];
      } else {
        msg.session_id = null;
      }
      ws.send(JSON.stringify(msg));
    }

    // ==================== 消息渲染 ====================

    function appendMessage(role, content) {
      var container = el('chat-messages');
      if (!container) return;

      // 隐藏欢迎信息
      var welcome = container.querySelector('.chat-welcome-' + instId);
      if (welcome) welcome.style.display = 'none';

      var div = document.createElement('div');
      div.className = 'chat-msg-' + instId + ' ' + role;

      if (role === 'agent' && state.agent) {
        div.classList.add('agent-' + state.agent);
        var badge = document.createElement('span');
        badge.className = 'chat-msg-agent-badge-' + instId;
        badge.textContent = state.agent === 'claude' ? 'CLAUDE' : 'HERMES';
        div.appendChild(badge);
        var textEl = document.createElement('div');
        textEl.className = 'chat-msg-content';
        textEl.textContent = content || '';
        div.appendChild(textEl);
      } else if (role === 'user') {
        div.textContent = content || '';
      } else if (role === 'error') {
        div.textContent = '⚠ ' + (content || '未知错误');
      }

      container.appendChild(div);
      container.scrollTop = container.scrollHeight;
    }

    function appendChunk(text) {
      var container = el('chat-messages');
      if (!container) return;

      // 查找最后一个 agent 消息的 content 元素
      var contents = container.querySelectorAll('.chat-msg-' + instId + '.agent .chat-msg-content');
      var contentEl = contents[contents.length - 1];

      if (!contentEl) {
        // 首个 chunk：替换打字指示器为正式 agent 消息
        hideTyping();
        var welcome = container.querySelector('.chat-welcome-' + instId);
        if (welcome) welcome.style.display = 'none';

        var div = document.createElement('div');
        div.className = 'chat-msg-' + instId + ' agent agent-' + state.agent;
        var badge = document.createElement('span');
        badge.className = 'chat-msg-agent-badge-' + instId;
        badge.textContent = state.agent === 'claude' ? 'CLAUDE' : 'HERMES';
        div.appendChild(badge);
        contentEl = document.createElement('div');
        contentEl.className = 'chat-msg-content';
        div.appendChild(contentEl);
        container.appendChild(div);
      }

      contentEl.textContent += text;
      container.scrollTop = container.scrollHeight;
    }

    // ==================== 打字指示器 ====================

    function showTyping(agent) {
      var container = el('chat-messages');
      if (!container) return;

      hideTyping();

      var div = document.createElement('div');
      div.id = 'chat-typing-indicator-' + instId;
      div.className = 'chat-msg-' + instId + ' agent';
      if (agent) div.classList.add('agent-' + agent);

      var typing = document.createElement('div');
      typing.className = 'chat-typing-' + instId;
      typing.innerHTML = '<span></span><span></span><span></span>';
      div.appendChild(typing);

      container.appendChild(div);
      container.scrollTop = container.scrollHeight;
    }

    function hideTyping() {
      var el = document.getElementById('chat-typing-indicator-' + instId);
      if (el) el.remove();
    }

    // ==================== 事件绑定 ====================

    function bindEvents() {
      // FAB → toggle
      var fab = el('chat-fab');
      if (fab) fab.addEventListener('click', toggleChat);

      // 关闭按钮
      var closeBtn = el('chat-close-btn');
      if (closeBtn) closeBtn.addEventListener('click', closeChat);

      // ESC 关闭
      var escHandler = function (e) {
        if (e.key === 'Escape' && state.open) closeChat();
      };
      document.addEventListener('keydown', escHandler);
      state.destroyHandlers.push(function () {
        document.removeEventListener('keydown', escHandler);
      });

      // Agent 切换
      var agentSelect = el('chat-agent-select');
      if (agentSelect) {
        agentSelect.addEventListener('change', function () {
          var oldAgent = state.agent;
          state.agent = this.value;
          state.sessionId = null;  // 重置会话
          if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({
              type: 'switch_agent',
              agent: state.agent,
            }));
          }
        });
      }

      // 发送按钮
      var sendBtn = el('chat-send-btn');
      if (sendBtn) sendBtn.addEventListener('click', function () {
        sendMessage(el('chat-input').value);
      });

      // 输入框 Enter 发送
      var chatInput = el('chat-input');
      if (chatInput) {
        chatInput.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage(this.value);
          }
        });
        chatInput.addEventListener('input', function () {
          var btn = el('chat-send-btn');
          if (btn) btn.disabled = !this.value.trim();
        });
      }
    }

    // ==================== 初始化 ====================

    buildInlineStyles(instId);
    buildDOM();
    bindEvents();

    // ==================== 公开 API ====================

    return {
      /** 打开聊天面板 */
      open: openChat,

      /** 关闭聊天面板 */
      close: closeChat,

      /** 切换面板开关 */
      toggle: toggleChat,

      /** 发送一条消息 */
      send: function (text) { sendMessage(text); },

      /** 注册外部上下文提供函数。该函数应返回一个对象，每次发送消息前自动调用。 */
      setContextProvider: function (fn) {
        state.contextProvider = fn;
      },

      /** 销毁组件，移除事件监听 */
      destroy: function () {
        // 关闭 WS
        if (state.ws) {
          state.ws.onclose = null;
          state.ws.close();
          state.ws = null;
        }
        // 执行清理函数
        for (var i = 0; i < state.destroyHandlers.length; i++) {
          state.destroyHandlers[i]();
        }
        state.destroyHandlers = [];
        // 清空容器
        container.innerHTML = '';
        document.body.classList.remove('chat-open-' + instId);
      },
    };
  };
})();
