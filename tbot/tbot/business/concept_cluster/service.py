# SPDX-License-Identifier: MIT
"""ConceptClusterService — 涨停概念聚合业务服务。

从 tushare.db (SQLite) 读取涨停股票数据及概念/业务信息，
调用 AIService 执行 AI 概念聚类，返回力导向图 JSON 结构。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from tbot.engines.ai import AIService


class ConceptClusterService:
    """涨停概念聚合业务服务。

    职责:
        1. 连接 tushare.db 查询涨停池 (limit_up_pool)
        2. 补充主营业务 (stock_company) 和同花顺概念标签 (ths_member)
        3. 调用 AIService.concept_cluster() 做 AI 概念聚合
        4. 包装 meta 信息并返回完整 graph 结构

    用法:
        svc = ConceptClusterService(ai=AIService())
        graph = svc.cluster(date="20260710", mode="full")
    """

    def __init__(
        self,
        ai: AIService | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        """初始化概念聚合服务。

        Args:
            ai: AIService 实例，不传则创建默认实例。
            db_path: tushare.db 的路径，不传则按约定路径推定。
        """
        self._ai = ai or AIService()
        self._db_path = str(db_path or self._default_db_path())

    # ---------------------------------------------------------------
    #  Default path resolution
    # ---------------------------------------------------------------

    @staticmethod
    def _default_db_path() -> str:
        """推定 tushare.db 路径。

        从 service.py 向上走到项目根目录，再进入 data/ 目录。
        """
        return str(
            Path(__file__).resolve().parent.parent.parent.parent.parent
            / "data"
            / "tushare.db"
        )

    # ---------------------------------------------------------------
    #  DB helpers (SQLite)
    # ---------------------------------------------------------------

    @staticmethod
    def _load_stock_company_data(
        conn: sqlite3.Connection,
        ts_codes: list[str],
    ) -> dict[str, str]:
        """从 stock_company 表查询主营业务。"""
        cur = conn.cursor()
        placeholders = ",".join("?" for _ in ts_codes)
        cur.execute(
            f"SELECT ts_code, main_business FROM stock_company "
            f"WHERE ts_code IN ({placeholders})",
            ts_codes,
        )
        return {r[0]: (r[1] or "") for r in cur.fetchall()}

    @staticmethod
    def _load_concept_tags(
        conn: sqlite3.Connection,
        ts_codes: list[str],
    ) -> dict[str, list[str]]:
        """从 ths_member 表查询每只股票的概念标签（最多 8 个）。"""
        cur = conn.cursor()
        placeholders = ",".join("?" for _ in ts_codes)
        cur.execute(
            f"SELECT ts_code, concept_name FROM ths_member "
            f"WHERE ts_code IN ({placeholders})",
            ts_codes,
        )
        result: dict[str, list[str]] = {}
        for code, name in cur.fetchall():
            result.setdefault(code, []).append(name)
        return {k: v[:8] for k, v in result.items()}

    # ---------------------------------------------------------------
    #  Cluster
    # ---------------------------------------------------------------

    def cluster(
        self,
        date: str | None = None,
        mode: str = "full",
    ) -> dict[str, Any] | None:
        """执行涨停概念聚合。

        Args:
            date: 目标日期 YYYYMMDD。为 None 则取最新交易日。
            mode: 聚合模式。
                - "concept": 仅按概念分组，合并同义词，不含主题。
                - "theme": 忽略概念，从主营业务提取市场主题。
                - "full": 先按概念分组，再合并为更高层主题。

        Returns:
            力导向图 dict，结构::

                {
                    "meta": {
                        "date": "20260710",
                        "generated_at": "2026-07-11T12:00:00",
                        "mode": "full",
                        "n_limitup": 42,
                        "n_concepts": 8,
                    },
                    "concepts": [
                        {
                            "name": "机器人",
                            "heat": 0.85,
                            "member_count": 5,
                            "members": [
                                {"ts_code": "...", "name": "...",
                                 "limit_times": 2, "industry": "..."},
                            ],
                        },
                    ],
                    "themes": [],
                    "links": [
                        {"source": "机器人", "target": "...", "type": "belongs"},
                    ],
                }

            无数据或全部失败时返回 None。

        Raises:
            sqlite3.Error: DB 连接或查询异常时向上透传。
        """
        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.cursor()

            # --- 确定目标日期 ---
            if date is None:
                cur.execute(
                    "SELECT DISTINCT trade_date FROM limit_up_pool "
                    "ORDER BY trade_date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if not row:
                    return None
                date = row[0]

            # --- 查询涨停数据 ---
            cur.execute(
                """SELECT ts_code, name, industry, limit_times,
                          first_time, last_time, fd_amount, amount
                   FROM limit_up_pool
                   WHERE trade_date=? AND "limit"='U'
                   ORDER BY limit_times DESC, fd_amount DESC""",
                (date,),
            )
            rows = cur.fetchall()
            if not rows:
                return None

            stocks: list[dict[str, Any]] = [
                {
                    "ts_code": r[0],
                    "name": r[1],
                    "industry": r[2] or "",
                    "limit_times": int(r[3]) if r[3] else 0,
                    "first_time": r[4] or "",
                    "last_time": r[5] or "",
                    "fd_amount": float(r[6]) if r[6] else 0,
                    "amount": float(r[7]) if r[7] else 0,
                }
                for r in rows
            ]

            # --- 补充主营业务和概念标签 ---
            ts_codes = [s["ts_code"] for s in stocks]
            main_biz = self._load_stock_company_data(conn, ts_codes)
            concept_tags = self._load_concept_tags(conn, ts_codes)

            for s in stocks:
                s["main_business"] = main_biz.get(s["ts_code"], "")
        finally:
            conn.close()

        # --- 调用 AI 概念聚合 ---
        result = self._ai.concept_cluster(
            stocks, mode=mode, concept_tags=concept_tags,
        )
        if result is None:
            return None

        # --- 包装 meta ---
        return {
            "meta": {
                "date": date,
                "generated_at": datetime.now().isoformat(),
                "mode": mode,
                "n_limitup": len(stocks),
                "n_concepts": len(result.get("concepts", [])),
            },
            "concepts": result.get("concepts", []),
            "themes": result.get("themes", []),
            "links": result.get("links", []),
        }
