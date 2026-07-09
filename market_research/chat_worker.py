"""ChatWorker — WebSocket ↔ CLI agent 桥梁。

管理一个 WebSocket 连接的 agent（Claude Code / Hermes Agent）子进程，
通过 JSON Line 协议实现双向流式通信。

Message protocol (JSON Lines, one JSON per line):

  ↑ client → server:
     {"type":"query","agent":"claude","message":"...","session_id":null,"context":{...}}
     {"type":"switch_agent","agent":"hermes"}
     {"type":"cancel"}

  ↓ server → client:
     {"type":"chunk","data":"..."}
     {"type":"done","session_id":"xxx","agent":"claude"}
     {"type":"error","message":"..."}
"""
from __future__ import annotations

import asyncio
import json
import re
import shlex
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect


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
                t = obj.get("subtype", "")
                if t in ("success",):
                    result["text"] = obj.get("result", "")
                    result["session_id"] = obj.get("session_id")
        except json.JSONDecodeError:
            continue
    return result


def _parse_hermes_output(stdout: str) -> dict[str, Any]:
    """解析 hermes chat -q -Q 的输出，提取回复文本 + session_id。"""
    text = stdout
    session_id: str | None = None

    # session_id 在输出的最后一行
    for line in stdout.splitlines():
        m = re.search(r"session_id:\s*(\S+)", line)
        if m:
            session_id = m.group(1)
            break

    # 去除 session_id 行（它在最后一行）
    text = re.sub(r"\nsession_id:\s*\S+\s*$", "", text).strip()
    text = re.sub(r"^session_id:\s*\S+\s*\n?", "", text).strip()

    return {"text": text.strip(), "session_id": session_id}


class ChatWorker:
    """管理一个 WebSocket 连接的 agent 子进程。"""

    # CLI 路径
    CLAUDE_CMD = "claude"
    HERMES_CMD = "hermes"

    # CLI 超时（秒）
    TIMEOUT = 30

    def __init__(self, ws: WebSocket, context: dict[str, Any] | None = None):
        self.ws = ws
        self.agent_type: str = "claude"  # "claude" | "hermes"
        self.session_id: str | None = None
        self.context = context or {}
        self._running = False
        self._cancel_event = asyncio.Event()

    async def send_json(self, data: dict[str, Any]) -> None:
        """发送 JSON 消息给前端。"""
        try:
            await self.ws.send_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass  # connection closed

    async def handle(self) -> None:
        """主循环：接收前端消息并路由。"""
        try:
            async for raw in self.ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self.send_json({"type": "error", "message": "无效的 JSON 消息"})
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "cancel":
                    self._cancel_event.set()
                    continue

                if msg_type == "query":
                    await self._handle_query(msg)

                elif msg_type == "switch_agent":
                    new_agent = msg.get("agent", "claude")
                    if new_agent not in ("claude", "hermes"):
                        await self.send_json({"type": "error", "message": f"不支持的 agent: {new_agent}"})
                        continue
                    self.agent_type = new_agent
                    # 切换 agent 时重置 session_id
                    old_sid = self.session_id
                    self.session_id = None
                    await self.send_json({
                        "type": "agent_switched",
                        "agent": new_agent,
                        "previous_session_id": old_sid,
                    })

                elif msg_type == "context_update":
                    self.context = msg.get("context", {})
                    # 静默更新，不回显
                else:
                    await self.send_json({"type": "error", "message": f"未知消息类型: {msg_type}"})

        except WebSocketDisconnect:
            pass
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        """清理资源。"""
        self._cancel_event.set()

    async def _handle_query(self, msg: dict[str, Any]) -> None:
        """处理用户查询消息。"""
        if self._running:
            await self.send_json({"type": "error", "message": "上一个请求还在处理中"})
            return

        agent = msg.get("agent", self.agent_type)
        message = msg.get("message", "").strip()
        context = msg.get("context", self.context)

        if not message:
            await self.send_json({"type": "error", "message": "消息不能为空"})
            return

        self.agent_type = agent
        self.context = context
        self._running = True
        self._cancel_event.clear()

        try:
            if agent == "claude":
                await self._run_claude(message, context)
            elif agent == "hermes":
                await self._run_hermes(message, context)
            else:
                await self.send_json({"type": "error", "message": f"不支持的 agent: {agent}"})
        finally:
            self._running = False

    async def _run_claude(self, message: str, context: dict[str, Any]) -> None:
        """调用 claude -p --output-format json。"""
        context_prefix = _format_context(context)
        if context_prefix:
            prompt = f"{context_prefix}\n{message}"
        else:
            prompt = message

        cmd = [
            self.CLAUDE_CMD,
            "-p", prompt,
            "--output-format", "json",
        ]
        if self.session_id:
            cmd += ["--resume", self.session_id]

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
                await self.send_json({
                    "type": "error",
                    "message": "Claude Code 超时（30s），请简化问题后重试",
                })
                return

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                err_msg = stderr.strip() or f"Claude Code 异常退出（code={proc.returncode}）"
                await self.send_json({"type": "error", "message": err_msg})
                return

            parsed = _parse_claude_output(stdout)
            text = parsed.get("text", "")
            sid = parsed.get("session_id")

            if sid:
                self.session_id = sid

            if text:
                # 流式输出：按句分割，模拟流式推送
                chunks = self._split_into_chunks(text)
                for chunk in chunks:
                    if self._cancel_event.is_set():
                        await self.send_json({"type": "error", "message": "已取消"})
                        return
                    await self.send_json({"type": "chunk", "data": chunk})
                    await asyncio.sleep(0.01)  # 小延迟让前端有节奏感

            await self.send_json({
                "type": "done",
                "session_id": self.session_id or "",
                "agent": "claude",
            })

        except FileNotFoundError:
            await self.send_json({
                "type": "error",
                "message": "Claude Code CLI 未安装，请确认 `claude` 命令可用",
            })
        except Exception as e:
            await self.send_json({
                "type": "error",
                "message": f"Claude Code 调用异常: {e}",
            })

    async def _run_hermes(self, message: str, context: dict[str, Any]) -> None:
        """调用 hermes chat -q -Q。"""
        context_prefix = _format_context(context)
        if context_prefix:
            prompt = f"{context_prefix}\n{message}"
        else:
            prompt = message

        cmd = [
            self.HERMES_CMD,
            "chat",
            "-q", prompt,
            "-Q",
            "-s", "tool",
        ]
        if self.session_id:
            cmd += ["-r", self.session_id]

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
                await self.send_json({
                    "type": "error",
                    "message": "Hermes Agent 超时（30s），请简化问题后重试",
                })
                return

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                err_msg = stderr.strip() or f"Hermes Agent 异常退出（code={proc.returncode}）"
                await self.send_json({"type": "error", "message": err_msg})
                return

            parsed = _parse_hermes_output(stdout)
            text = parsed.get("text", "")
            sid = parsed.get("session_id")

            if sid:
                self.session_id = sid

            if text:
                chunks = self._split_into_chunks(text)
                for chunk in chunks:
                    if self._cancel_event.is_set():
                        await self.send_json({"type": "error", "message": "已取消"})
                        return
                    await self.send_json({"type": "chunk", "data": chunk})
                    await asyncio.sleep(0.01)

            await self.send_json({
                "type": "done",
                "session_id": self.session_id or "",
                "agent": "hermes",
            })

        except FileNotFoundError:
            await self.send_json({
                "type": "error",
                "message": "Hermes Agent 未安装，请确认 `hermes` 命令可用",
            })
        except Exception as e:
            await self.send_json({
                "type": "error",
                "message": f"Hermes Agent 调用异常: {e}",
            })

    @staticmethod
    def _split_into_chunks(text: str, max_chunk: int = 100) -> list[str]:
        """将文本分割为流式片段。

        尽量按标点符号分割，否则按最大长度截断。
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
                            chunks.append(sp[i:i + max_chunk])

        return chunks


async def chat_endpoint_handler(ws: WebSocket) -> None:
    """FastAPI WebSocket 路由处理器。

    用法 (server.py):
        @app.websocket("/api/chat/ws")
        async def ws_chat(websocket: WebSocket):
            await chat_endpoint_handler(websocket)
    """
    await ws.accept()

    # 可选的起始上下文（从 URL query 参数读取）
    from urllib.parse import parse_qs
    query = parse_qs(ws.url.query)
    initial_context: dict[str, Any] = {}
    ctx_str = query.get("context", [None])[0]
    if ctx_str:
        try:
            initial_context = json.loads(ctx_str)
        except json.JSONDecodeError:
            pass

    worker = ChatWorker(ws, context=initial_context)
    await worker.handle()
