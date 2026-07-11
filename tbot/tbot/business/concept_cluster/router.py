# SPDX-License-Identifier: MIT
"""concept_cluster HTTP router — 涨停概念聚合 API。

POST /api/concept/generate 触发 AI 概念聚类，返回力导向图 JSON。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from tbot.business.concept_cluster import ConceptClusterService
from tbot.engines.ai import AIService

router = APIRouter(prefix="/api/concept", tags=["concept"])


@router.post("/generate")
def generate(body: dict[str, Any]) -> dict[str, Any]:
    """执行涨停概念聚合，返回力导向图 JSON。

    Request body (JSON):
        date (str, optional): 目标日期 YYYYMMDD。不传则取最新交易日。
        mode (str, optional): 聚合模式 "concept" | "theme" | "full"。
                              默认 "full"。

    Returns:
        包装响应::

            {"code": 0, "message": "ok", "data": {graph_dict}}
            {"code": 404, "message": "无数据", "data": None}
    """
    svc = ConceptClusterService(ai=AIService())
    result = svc.cluster(
        date=body.get("date"),
        mode=body.get("mode", "full"),
    )
    if result is None:
        return {"code": 404, "message": "无数据", "data": None}
    return {"code": 0, "message": "ok", "data": result}
