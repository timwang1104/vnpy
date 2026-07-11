# SPDX-License-Identifier: MIT
"""Simulator FastAPI router — 提供 /api/sim/*  REST 接口。

Usage:
    from fastapi import FastAPI
    from tbot.engines.database.manager import DatabaseManager
    from tbot.business.simulator import SimulatorService, create_simulator_router

    app = FastAPI()
    mgr = DatabaseManager("data")
    svc = SimulatorService(mgr)
    app.include_router(create_simulator_router(svc))
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from tbot.business.simulator.service import SimulatorService

# ── Router factory ──────────────────────────────────────────────


def create_simulator_router(svc: SimulatorService) -> APIRouter:
    """创建模拟盘 API 路由。

    Args:
        svc: SimulatorService 实例（非线程安全，同一服务应在单线程/协程中访问）。

    Returns:
        已挂载 ``/api/sim`` 前缀的 ``APIRouter``。
    """
    router = APIRouter(prefix="/api/sim")

    # ── 策略列表 ────────────────────────────────────────────

    @router.get("/strategies")
    async def list_strategies():
        """返回所有已注册策略。"""
        strategies = svc.get_strategies()
        return {"strategies": strategies}

    @router.post("/discover")
    async def discover_strategies():
        """扫描 strategies/ 目录并注册新策略。"""
        count = svc.discover_strategies()

        # 重新查询总数
        strategies = svc.get_strategies()
        return {"status": "ok", "strategy_count": len(strategies), "new": count}

    # ── 单策略 ──────────────────────────────────────────────

    @router.get("/strategies/{strategy_id}")
    async def get_strategy(strategy_id: int):
        """返回单条策略详情（含最近一次运行批次信息）。"""
        result = svc.get_strategy(strategy_id)
        if result is None:
            raise HTTPException(status_code=404, detail="策略不存在")
        return result

    # ── 运行策略 ────────────────────────────────────────────

    @router.post("/strategies/{strategy_id}/run")
    async def run_strategy(strategy_id: int, body: dict[str, Any] | None = None):
        """运行策略回测。

        Request body (JSON):
            setting (dict, optional): 策略参数覆盖。
            start_date (str, optional): 起始日期 YYYYMMDD，默认 20200101。
            end_date (str, optional): 截止日期 YYYYMMDD，默认 20261231。
        """
        body = body or {}
        setting = body.get("setting", {})
        start_date = body.get("start_date", "20200101")
        end_date = body.get("end_date", "20261231")

        result = svc.run_strategy(
            strategy_id,
            start_date=start_date,
            end_date=end_date,
            setting=setting,
        )
        return result

    # ── 权益曲线 ────────────────────────────────────────────

    @router.get("/strategies/{strategy_id}/equity")
    async def get_equity(strategy_id: int):
        """查询最新批次的权益曲线。"""
        return svc.get_equity(strategy_id)

    # ── 持仓 ────────────────────────────────────────────────

    @router.get("/strategies/{strategy_id}/positions")
    async def get_positions(strategy_id: int):
        """查询最新批次的持仓记录。"""
        return svc.get_positions(strategy_id)

    # ── 交易记录 ────────────────────────────────────────────

    @router.get("/strategies/{strategy_id}/trades")
    async def get_trades(strategy_id: int):
        """查询最新批次的交易记录。"""
        return svc.get_trades(strategy_id)

    return router
