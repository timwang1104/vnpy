# SPDX-License-Identifier: MIT
"""Limitup router — 涨停板聚合查询 API。

GET /api/limitup/{date}
GET /api/limitup/history
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from tbot.business.limitup.service import LimitUpService
from tbot.config.settings import ConfigManager
from tbot.engines.database.manager import DatabaseManager

router = APIRouter(tags=["limitup"])


# ── 依赖注入 ──────────────────────────────────────────────────────────


def _get_service() -> LimitUpService:
    """提供 LimitUpService 实例（基于配置的 data_dir）。"""
    config = ConfigManager()
    data_dir = config.get("database.data_dir", "data")
    mgr = DatabaseManager(data_dir)
    return LimitUpService(mgr)


# ── 端点 ─────────────────────────────────────────────────────────────


@router.get("/api/limitup/{date}")
def get_limitup(
    date: str,
    service: LimitUpService = Depends(_get_service),
) -> dict:
    """获取指定日期的涨停聚合数据（梯队 + 行业聚集度）。

    路径参数 date: 交易日期 YYYYMMDD。
    """
    return service.get_daily_aggregation(date)


@router.get("/api/limitup/history")
def get_limitup_history(
    window: int = Query(20, ge=1, le=100, description="交易日数量"),
    service: LimitUpService = Depends(_get_service),
) -> list[dict]:
    """获取最近 N 个交易日的涨停聚合历史。"""
    return service.get_history(window)
