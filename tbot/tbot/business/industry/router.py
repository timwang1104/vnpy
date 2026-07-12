"""Industry FastAPI router — 行业板块资金流接口。

Endpoints
---------
GET /api/industry/{code}/timeseries
    单行业时序数据。
GET /api/industry/anomalies
    异常行业检测。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, Query

from tbot.business.industry.service import IndustryService
from tbot.config.settings import ConfigManager
from tbot.engines.database.manager import DatabaseManager
from tbot.engines.database.service import DatabaseService

# ── 依赖注入 ──────────────────────────────────────────────────────


def _get_data_dir() -> str:
    """从 YAML 配置读取数据库目录。"""
    cfg = ConfigManager()
    return cfg.get("database.data_dir", default="data")


@lru_cache
def get_db_service() -> DatabaseService:
    """创建并缓存 DatabaseService 单例。"""
    mgr = DatabaseManager(_get_data_dir())
    return DatabaseService(mgr)


@lru_cache
def get_industry_service() -> IndustryService:
    """创建并缓存 IndustryService 单例。"""
    return IndustryService(get_db_service())


# ── 路由 ──────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/industry", tags=["industry"])


@router.get("/{code}/timeseries")
def get_timeseries(
    code: str,
    mode: str = Query(
        default="pct",
        description="返回值类型: raw=全字段, pct=涨跌幅, buy_md=资金流, close=收盘价, share=占比",
    ),
    svc: IndustryService = Depends(get_industry_service),
) -> dict[str, Any]:
    """行业板块资金流时序（单行业）。

    返回指定行业的资金流净额 / 涨跌幅 / 收盘价历史。
    可通过 ``mode`` 控制返回字段。
    """
    return svc.get_timeseries(code, mode)


@router.get("/anomalies")
def get_anomalies(
    threshold: float = Query(
        default=2.0,
        ge=0.1,
        description="异常阈值（mean(|share|) 倍数，default 2.0）",
    ),
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="最大返回条数（default 10, max 100）",
    ),
    svc: IndustryService = Depends(get_industry_service),
) -> dict[str, Any]:
    """检测资金流异常行业。

    基于最近 60 个交易日的资金流占比，
    将占比绝对值超过阈值（``threshold × mean(|share|)``）的行业判定为异常，
    按异常程度降序排列。

    返回格式: {threshold, limit, total_anomalies, total_industries,
               anomalies: [{ts_code, name, latest_zscore, share}]}
    """
    return svc.get_anomalies(threshold, limit)
