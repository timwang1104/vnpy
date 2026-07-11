# SPDX-License-Identifier: MIT
"""ChatHandler — WebSocket ↔ Claude CLI agent bridge.

实现 /api/chat/ws 端点，管理一个 WebSocket 连接与 Claude Code CLI
子进程之间的双向流式通信。

消息协议 (JSON Lines, 每行一个 JSON):

  ↑ client → server:
     {"type":"query","agent":"claude","message":"...","session_id":null,"context":{...}}
     {"type":"cancel"}

  ↓ server → client:
     {"type":"chunk","data":"..."}
     {"type":"done","session_id":"xxx","agent":"claude"}
     {"type":"error","message":"..."}

用法:
    from tbot.business.chat.ws_handler import register_routes
    register_routes(app)  # 注册 /api/chat/ws 端点
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

# ---------------------------------------------------------------------------
# 上下文格式
# ---------------------------------------------------------------------------


def _format_context(context: dict[str, Any]) -> str:
    """将页面上下文格式化为自然语言前缀，注入 system prompt。"""
    if not context:
        return ""

    lines: list[str] = ["## 当前页面上下文"]

    tab = context.get("tab", "")
    tab_labels = {
        "industry": "行业资金流",
        "limitup": "涨停池",
        "fundflow": "行业时序",
        "simulator": "模拟盘",
    }
    lines.append(f"当前页面: {tab_labels.get(tab, tab)}")

    # Tab-specific info
    if tab == "fundflow":
        if context.get("industry"):
            lines.append(f"行业: {context['industry']}")
        if context.get("mode"):
            lines.append(f"指标: {context['mode']}")
    elif tab == "limitup":
        if context.get("date"):
            lines.append(f"日期: {context['date']}")

    # Overview stats
    stats = context.get("stats", "")
    if stats:
        lines.append(f"概览: {stats}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 输出解析
# ---------------------------------------------------------------------------


def _parse_claude_output(stdout: str) -> dict[str, Any]:
    """解析 claude --output-format json 的输出，提取 result + session_id。"""
    result: dict[str, Any] = {"text": "", "session_id": None}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get("type") == "result":
                if obj.get("subtype") in ("success",):
                    result["text"] = obj.get("result", "")
                    result["session_id"] = obj.get("session_id")
        except json.JSONDecodeError:
            continue
    return result


# ---------------------------------------------------------------------------
# 便利工具
# ---------------------------------------------------------------------------


def _build_claude_cmd(prompt: str, session_id: str | None) -> list[str]:
    """构建 claude CLI 命令。"""
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if session_id:
        cmd += ["--resume", session_id]
    return cmd


def _split_into_chunks(text: str, max_chunk: int = 100) -> list[str]:
    """将文本分割为流式片段，优先按标点符号分割。

    Args:
        text: 待分割的文本。
        max_chunk: 每个片段的最大字符数。

    Returns:
        分割后的片段列表。
    """
    if not text:
        return [""]

    chunks: list[str] = []
    # 优先按句号、问号、感叹号分割
    parts = re.split(r"(?<=[。！？.!?])", text)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= max_chunk:
            chunks.append(part)
        else:
            # 超过最大长度，进一步按逗号分割
            sub_parts = re.split(r"(?<=[，、,])", part)
            for sp in sub_parts:
                sp = sp.strip()
                if not sp:
                    continue
                if len(sp) <= max_chunk:
                    chunks.append(sp)
                else:
                    # 强行截断
                    for i in range(0, len(sp), max_chunk):
                        chunks.append(sp[i : i + max_chunk])

    return chunks


# ---------------------------------------------------------------------------
# ChatHandler — 核心类
# ---------------------------------------------------------------------------


class ChatHandler:
    """管理一个 WebSocket 连接的 Claude CLI agent 子进程。

    处理 WebSocket 生命周期：接受连接、接收消息、调用 Claude CLI、
    流式回传结果、清理资源。

    用法:
        handler = ChatHandler(websocket)
        await handler.handle()
    """

    # CLI 超时（秒）
    TIMEOUT = 30

    def __init__(self, ws: WebSocket, context: dict[str, Any] | None = None) -> None:
        """初始化 ChatHandler。

        Args:
            ws: FastAPI WebSocket 对象。
            context: 可选的初始页面上下文。
        """
        self.ws = ws
        self.context: dict[str, Any] = context or {}
        self.session_id: str | None = None
        self._running = False
        self._cancel_event = asyncio.Event()

    # ------------------------------------------------------------------
    #  公共接口
    # ------------------------------------------------------------------

    async def handle(self) -> None:
        """主循环：接收 WebSocket 消息并路由到对应处理函数。"""
        try:
            async for raw in self.ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._send_error("无效的 JSON 消息")
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "cancel":
                    self._cancel_event.set()
                elif msg_type == "query":
                    await self._handle_query(msg)
                elif msg_type == "context_update":
                    self.context = msg.get("context", {})
                else:
                    await self._send_error(f"未知消息类型: {msg_type}")

        except WebSocketDisconnect:
            pass
        finally:
            self._cleanup()

    # ------------------------------------------------------------------
    #  内部方法
    # ------------------------------------------------------------------

    async def _send(self, data: dict[str, Any]) -> None:
        """发送 JSON 消息给前端。

        Args:
            data: 要发送的字典，将被 JSON 序列化。
        """
        try:
            await self.ws.send_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass  # connection closed

    async def _send_error(self, message: str) -> None:
        """发送错误消息。

        Args:
            message: 错误描述文本。
        """
        await self._send({"type": "error", "message": message})

    def _cleanup(self) -> None:
        """清理资源并标记取消。"""
        self._cancel_event.set()

    async def _handle_query(self, msg: dict[str, Any]) -> None:
        """处理用户查询消息。

        Args:
            msg: 解析后的查询消息字典。
        """
        if self._running:
            await self._send_error("上一个请求还在处理中")
            return

        message = msg.get("message", "").strip()
        context = msg.get("context", self.context)

        if not message:
            await self._send_error("消息不能为空")
            return

        self.context = context
        self._running = True
        self._cancel_event.clear()

        try:
            await self._run_claude(message, context)
        finally:
            self._running = False

    async def _run_claude(
        self, message: str, context: dict[str, Any]
    ) -> None:
        """调用 Claude CLI 并流式输出结果。

        Args:
            message: 用户消息文本。
            context: 当前页面上下文。
        """
        context_prefix = _format_context(context)
        prompt = f"{context_prefix}\n{message}" if context_prefix else message

        cmd = _build_claude_cmd(prompt, self.session_id)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.TIMEOUT,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                await self._send_error("Claude Code 超时（30s），请简化问题后重试")
                return

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                err_msg = stderr.strip() or f"Claude Code 异常退出（code={proc.returncode}）"
                await self._send_error(err_msg)
                return

            parsed = _parse_claude_output(stdout)
            text = parsed.get("text", "")
            sid = parsed.get("session_id")

            if sid:
                self.session_id = sid

            if text:
                chunks = _split_into_chunks(text)
                for chunk in chunks:
                    if self._cancel_event.is_set():
                        await self._send_error("已取消")
                        return
                    await self._send({"type": "chunk", "data": chunk})
                    await asyncio.sleep(0.01)

            await self._send({
                "type": "done",
                "session_id": self.session_id or "",
                "agent": "claude",
            })

        except FileNotFoundError:
            await self._send_error(
                "Claude Code CLI 未安装，请确认 `claude` 命令可用"
            )
        except Exception as e:
            await self._send_error(f"Claude Code 调用异常: {e}")


# ---------------------------------------------------------------------------
# 路由注册
# ---------------------------------------------------------------------------


def register_routes(app: FastAPI) -> None:
    """注册 /api/chat/ws WebSocket 端点到 FastAPI 应用。

    Args:
        app: FastAPI 应用实例。
    """

    @app.websocket("/api/chat/ws")
    async def chat_ws(websocket: WebSocket) -> None:
        """WebSocket 聊天端点。"""
        await websocket.accept()

        # 阅读可选的起始上下文（从 URL query 参数）
        from urllib.parse import parse_qs

        query = parse_qs(websocket.url.query)
        initial_context: dict[str, Any] = {}
        ctx_str = query.get("context", [None])[0]
        if ctx_str:
            try:
                initial_context = json.loads(ctx_str)
            except json.JSONDecodeError:
                pass

        handler = ChatHandler(websocket, context=initial_context)
        await handler.handle()
