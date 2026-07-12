# SPDX-License-Identifier: MIT
"""DataUpdateService — thread-safe wrapper around market_research/update_manager.

Provides:
    - run(): launch a background update thread
    - get_status() / get_log(): query running state and log buffer
    - create_sse() / close_sse(): SSE log streaming lifecycle
    - install_cron() / remove_cron() / cron_status(): crontab management
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncGenerator
from typing import Any

from tbot.business.data_update.cron import (
    install_cron as _install_cron,
    remove_cron as _remove_cron,
    show_cron_info as _cron_status,
)
from tbot.business.data_update.updater import (
    get_log_lines,
    get_status as _updater_status,
    register_completion_callback,
    register_log_callback,
    run_update as _run_update,
    unregister_completion_callback,
    unregister_log_callback,
)

# ---------------------------------------------------------------------------
# SSE 桥接：updater 后台线程 → asyncio SSE 流
# ---------------------------------------------------------------------------


class _SSESubscriber:
    """SSE 日志订阅者。

    ！！！本类只能从 asyncio 事件循环中创建 ！！！

    接收来自 updater 模块回调（后台线程）的日志行，
    通过 asyncio.Queue + loop.call_soon_threadsafe 桥接到 SSE async generator。
    支持推送命名事件（complete / error）供 EventSource.addEventListener 消费。

    用法:
        sub = _SSESubscriber(loop)
        register_log_callback(sub)
        register_completion_callback(sub)  # 注册完成回调
        async for line in sub.stream():
            ...
        unregister_log_callback(sub)
        unregister_completion_callback(sub)
        sub.close()
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._loop = loop

    def __call__(self, line: str) -> None:
        """updater 回调入口（后台线程调用）。向队列推送普通日志行。"""
        self._loop.call_soon_threadsafe(
            self._queue.put_nowait, ("message", line)
        )

    def send_event(self, event_type: str, data: str = "") -> None:
        """推送一个命名 SSE 事件（如 complete / error）。"""
        self._loop.call_soon_threadsafe(
            self._queue.put_nowait, (event_type, data)
        )

    def close(self) -> None:
        """发送哨兵值，让 stream() 退出。"""
        self._loop.call_soon_threadsafe(self._queue.put_nowait, ("", ""))

    async def stream(self) -> AsyncGenerator[str, None]:
        """异步生成 SSE 行，由 StreamingResponse 消费。

        普通日志行（event_type="message"）以 "data: ..." 格式输出；
        命名事件（event_type="complete" 等）输出 "event: ...\ndata: ..." 格式。
        """
        while True:
            event_type, data = await self._queue.get()
            if not event_type:  # 空串 = 哨兵
                break
            if event_type == "message":
                yield f"data: {data}\n\n"
            else:
                yield f"event: {event_type}\ndata: {data}\n\n"


# ---------------------------------------------------------------------------
# DataUpdateService
# ---------------------------------------------------------------------------


class DataUpdateService:
    """数据更新服务。

    线程安全：内部使用 threading.Lock 保护后台线程引用。
    依赖 updater 模块的模块级状态做并发防护（run_update 内已拒绝并发）。
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ── 数据更新 ─────────────────────────────────────────────────────

    def run(self, **kwargs: Any) -> dict[str, Any]:
        """在后台线程启动数据更新。

        接受的参数与 market_research.update_manager.updater.run_update 相同。
        不阻塞调用者，立即返回。

        Returns
        -------
        {"status": "ok", "message": "更新已启动"}
        {"status": "error", "message": "更新正在进行中，拒绝并发"}
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"status": "error", "message": "更新正在进行中，拒绝并发"}

            self._thread = threading.Thread(
                target=_run_update,
                kwargs=kwargs,
                daemon=True,
            )
            self._thread.start()
            return {"status": "ok", "message": "更新已启动"}

    # ── 状态查询 ─────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """返回当前更新状态。

        除 updater.get_status() 中的字段外，额外附加:
            thread_alive — 后台线程是否存活
        """
        status = _updater_status()
        with self._lock:
            status["thread_alive"] = (
                self._thread is not None and self._thread.is_alive()
            )
        return status

    def get_log(self) -> list[str]:
        """返回日志缓冲区全部内容。"""
        return get_log_lines()

    # ── SSE 日志流 ───────────────────────────────────────────────────

    def create_sse(self, loop: asyncio.AbstractEventLoop) -> _SSESubscriber:
        """创建并注册 SSE 订阅者。

        同时注册日志回调和完成回调。完成回调在更新完成或出错时
        向 SSE 流推送命名事件（complete / error）。

        Parameters
        ----------
        loop : asyncio.AbstractEventLoop
            当前 asyncio 事件循环（在请求处理器中调用
            asyncio.get_running_loop() 获取）。
        """
        sub = _SSESubscriber(loop)
        register_log_callback(sub)
        # 存储 bound method 引用以确保 unregister 时能正确匹配
        sub._completion_cb = sub.send_event
        register_completion_callback(sub._completion_cb)
        return sub

    def close_sse(self, sub: _SSESubscriber) -> None:
        """注销并关闭 SSE 订阅者。"""
        unregister_log_callback(sub)
        if hasattr(sub, "_completion_cb"):
            unregister_completion_callback(sub._completion_cb)
        sub.close()

    # ── Cron 管理 ────────────────────────────────────────────────────

    @staticmethod
    def install_cron() -> bool:
        """注册 crontab 定时任务（周一至周五 18:30）。"""
        return _install_cron()

    @staticmethod
    def remove_cron() -> bool:
        """移除 crontab 定时任务。"""
        return _remove_cron()

    @staticmethod
    def cron_status() -> dict[str, Any]:
        """查询 crontab 注册信息。"""
        return _cron_status()


# 模块级单例 — router 及其他组件直接 import
service = DataUpdateService()
