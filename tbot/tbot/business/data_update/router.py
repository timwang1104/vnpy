# SPDX-License-Identifier: MIT
"""data_update router — FastAPI 路由定义。

Endpoints
---------
POST /api/data/update        — 触发数据更新（后台异步执行）
GET  /api/data/status        — 查询更新状态
GET  /api/data/log           — SSE 日志流
POST /api/data/cron/install  — 注册 crontab 定时任务
POST /api/data/cron/remove   — 移除 crontab 定时任务
GET  /api/data/cron/status   — 查询 cron 注册信息
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from tbot.business.data_update.service import service

router = APIRouter(prefix="/api/data", tags=["data_update"])

# 允许通过 API 传递的 run_update 参数白名单
_ALLOWED_RUN_KEYS: set[str] = {
    "since",
    "force_start",
    "force_end",
    "no_concept",
    "force_concept",
    "no_stock_ff",
    "tushare_only",
    "history_only",
    "do_build",
    "concept",
    "window",
}

# ---------------------------------------------------------------------------
# 数据更新
# ---------------------------------------------------------------------------


@router.post("/update", status_code=202)
async def trigger_update(body: dict[str, Any] | None = None) -> JSONResponse:
    """触发数据更新（后台异步执行）。

    可选请求体（所有字段均为可选）:
        since: str          — 起始日期 YYYYMMDD（默认: 10 天前）
        force_start: str    — 强制更新起始（开启强制模式）
        force_end: str      — 强制更新截止
        no_concept: bool    — 跳过概念/公司数据（默认 false）
        force_concept: bool — 强制更新概念（默认 false）
        no_stock_ff: bool   — 跳过个股资金流（默认 false）
        tushare_only: bool  — 仅更新 tushare 三表（默认 false）
        history_only: bool  — 仅更新日线行情（默认 false）
        do_build: bool      — 重建静态报告（默认 false）
        concept: bool       — 构建概念图（默认 false）
        window: int         — 报告计算窗口天数（默认 240）

    已运行返回 409 Conflict。
    """
    kwargs = {k: v for k, v in (body or {}).items() if k in _ALLOWED_RUN_KEYS}
    result = service.run(**kwargs)
    if result["status"] == "error":
        return JSONResponse(result, status_code=409)
    return JSONResponse(result, status_code=202)


@router.get("/status")
async def get_status() -> JSONResponse:
    """查询当前数据更新状态。"""
    return JSONResponse(service.get_status())


# ---------------------------------------------------------------------------
# SSE 日志流
# ---------------------------------------------------------------------------


@router.get("/log")
async def stream_log(request: Request) -> StreamingResponse:
    """SSE (Server-Sent Events) 实时日志流。

    客户端请使用 EventSource 或类似机制消费。
    连接断开后服务端自动清理回调。
    """
    import asyncio

    loop = asyncio.get_running_loop()
    sub = service.create_sse(loop)

    async def event_stream():
        try:
            async for event in sub.stream():
                yield event
        finally:
            service.close_sse(sub)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Cron 管理
# ---------------------------------------------------------------------------


@router.post("/cron/install")
async def install_cron() -> JSONResponse:
    """注册 crontab 定时任务（周一至周五 18:30 自动更新）。"""
    ok = service.install_cron()
    if ok:
        return JSONResponse({"status": "ok", "message": "cron 已注册"})
    return JSONResponse({"status": "error", "message": "cron 注册失败（无法执行 crontab 命令）"}, status_code=500)


@router.post("/cron/remove")
async def remove_cron() -> JSONResponse:
    """移除已注册的 crontab 定时任务。"""
    ok = service.remove_cron()
    if ok:
        return JSONResponse({"status": "ok", "message": "cron 已移除"})
    return JSONResponse({"status": "error", "message": "cron 移除失败（无法执行 crontab 命令）"}, status_code=500)


@router.get("/cron/status")
async def cron_status() -> JSONResponse:
    """查询 crontab 定时任务注册状态。"""
    return JSONResponse(service.cron_status())
