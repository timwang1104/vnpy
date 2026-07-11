# SPDX-License-Identifier: MIT
"""Overview router — GET /api/overview。

大盘概览 KPI 的 HTTP API 端点。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from tbot.business.overview.service import OverviewService
from tbot.config.settings import ConfigManager
from tbot.engines.database.manager import DatabaseManager

router = APIRouter(tags=["overview"])


# ── 依赖注入 ──────────────────────────────────────────────────────────


def _get_service() -> OverviewService:
    """提供 OverviewService 实例（基于配置的 data_dir）。"""
    config = ConfigManager()
    data_dir = config.get("database.data_dir", "data")
    mgr = DatabaseManager(data_dir)
    return OverviewService(mgr)


# ── 端点 ─────────────────────────────────────────────────────────────


@router.get("/api/overview")
def get_overview(
    date: str = Query(..., description="交易日期 YYYYMMDD"),
    service: OverviewService = Depends(_get_service),
) -> dict:
    """获取大盘概览 KPI（沪深净流入、涨停/跌停统计、迷你时序）。"""
    return service.get_kpi(date)
