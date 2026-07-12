# SPDX-License-Identifier: MIT
"""ReportBuilder — 编排业务服务，生成 report/ 静态 JSON 文件。

从 DuckDB 读取真实数据，输出前端可消费的 JSON 文件:

    - data/limitup_main.json        涨停池主数据（含概览注入）
    - data/limitup/{YYYYMMDD}.json  涨停池历史分片
    - data/industry.json            行业资金流信号报告
    - data/overview.json            大盘概览 KPI
    - data/calendar.json            交易日历

用法::

    from tbot.report_builder import ReportBuilder

    builder = ReportBuilder(data_dir="data")
    result = builder.build(out_dir="report", window=240)
"""

from __future__ import annotations

import json
import warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from tbot.business.industry.service import IndustryService
from tbot.business.limitup.service import LimitUpService
from tbot.business.overview.service import OverviewService
from tbot.config.settings import ConfigManager
from tbot.engines.database.manager import DatabaseManager
from tbot.engines.database.service import DatabaseService


class ReportBuilder:
    """报告构建器 —— 编排行业 / 涨停 / 概览等业务服务，输出静态 JSON。

    Parameters
    ----------
    data_dir : str | Path, optional
        DuckDB 数据目录。默认从 ConfigManager 读取 ``database.data_dir``。
    db_mgr : DatabaseManager, optional
        外部传入的 DatabaseManager 实例；不传时自动创建。
    """

    def __init__(
        self,
        data_dir: str | Path | None = None,
        db_mgr: DatabaseManager | None = None,
    ) -> None:
        if db_mgr is not None:
            self._mgr = db_mgr
        else:
            if data_dir is None:
                cfg = ConfigManager()
                data_dir = cfg.get("database.data_dir", "data")
            self._mgr = DatabaseManager(data_dir)

        self._db_service = DatabaseService(self._mgr)
        self._industry_service = IndustryService(self._db_service)
        self._limitup_service = LimitUpService(self._mgr)
        self._overview_service = OverviewService(self._mgr)

    def build(
        self,
        db_path: str | Path | None = None,
        out_dir: str | Path = "report",
        window: int = 240,
        concept: bool = False,
    ) -> dict[str, Any]:
        """执行 build，在 ``out_dir`` 下生成 ``report/data/`` 静态 JSON。

        Parameters
        ----------
        db_path : str | Path, optional
            已废弃——仅保留为向后兼容参数，传入会打印弃用警告。
            当前始终使用 DuckDB (DatabaseManager) 后端。
        out_dir : str | Path
            报告输出目录。
        window : int
            资金流信号和涨停池滚动窗口，以交易日为单位。
        concept : bool
            是否同时生成概念聚合力导向图（需调用 AI Agent）。

        Returns
        -------
        dict
            ``{success: bool, out_dir: str, files: list[str]}``.
        """
        if db_path is not None:
            warnings.warn(
                "db_path 参数已废弃，ReportBuilder 始终使用 DuckDB 后端。",
                DeprecationWarning,
                stacklevel=2,
            )

        out_dir = Path(out_dir)
        data_dir = out_dir / "data"
        limitup_dir = data_dir / "limitup"
        data_dir.mkdir(parents=True, exist_ok=True)
        limitup_dir.mkdir(parents=True, exist_ok=True)

        generated_files: list[str] = []

        # ── 1. 行业资金流 ────────────────────────────────────────────
        print("[report_builder] 计算行业资金流信号...")
        try:
            industry_data = self._industry_service.get_full_report(window=window)
            industry_data.setdefault("meta", {})["generated_at"] = datetime.now().isoformat()
        except Exception as exc:
            print(f"  ✗ 行业资金流计算失败: {exc}", flush=True)
            industry_data = {
                "meta": {"tab": "industry", "date": "", "window": window, "generated_at": datetime.now().isoformat()},
                "kpi": {"n_industries": 0, "n_dates": 0},
                "series": {"dates": [], "industries": [], "industries_code": []},
                "tables": {},
            }

        _write_json(data_dir / "industry.json", industry_data)
        generated_files.append(str(data_dir / "industry.json"))
        n_industry_dates = len(industry_data.get("series", {}).get("dates", []))
        print(f"  → industry.json ({n_industry_dates} 个交易日)")

        # ── 2. 涨停池 ────────────────────────────────────────────────
        print("[report_builder] 计算涨停池数据...")
        try:
            limitup_history = self._limitup_service.get_history(window=window)
        except Exception as exc:
            print(f"  ✗ 涨停池查询失败: {exc}", flush=True)
            limitup_history = []

        # 构建 by_date 映射
        limitup_by_date: dict[str, Any] = {}
        for entry in limitup_history:
            d = entry.get("meta", {}).get("date", "")
            if d:
                limitup_by_date[d] = entry

        all_limitup_dates = sorted(limitup_by_date.keys())

        if all_limitup_dates:
            main_dates = all_limitup_dates  # 全部放入主文件
            snapshot_date = all_limitup_dates[-1]
        else:
            main_dates = []
            snapshot_date = ""

        main_meta = {
            "tab": "limitup",
            "date": snapshot_date,
            "window": window,
            "generated_at": datetime.now().isoformat(),
        }
        main_data: dict[str, Any] = {
            "meta": main_meta,
            "dates": main_dates,
            "by_date": {d: limitup_by_date[d] for d in main_dates},
        }
        _write_json(data_dir / "limitup_main.json", main_data)
        generated_files.append(str(data_dir / "limitup_main.json"))
        print(f"  → limitup_main.json ({len(main_dates)} 天主数据)")

        # 历史分片（本版本所有日期都在主文件中，预留历史分片目录）
        print(f"  → limitup/ (0 个历史日期分片)")

        # ── 3. 概览 KPI ──────────────────────────────────────────────
        print("[report_builder] 计算概览...")
        if snapshot_date:
            try:
                overview_data = self._overview_service.get_kpi(date=snapshot_date)
                overview_data.setdefault("meta", {})["generated_at"] = datetime.now().isoformat()
            except Exception as exc:
                print(f"  ✗ 概览 KPI 计算失败: {exc}", flush=True)
                overview_data = _empty_overview(snapshot_date)
        else:
            overview_data = _empty_overview("")

        # 概览数据注入 main JSON（前端从 limitup_main 读取）
        main_data["overview"] = overview_data
        _write_json(data_dir / "limitup_main.json", main_data)

        # 独立 overview.json
        _write_json(data_dir / "overview.json", overview_data)
        generated_files.append(str(data_dir / "overview.json"))

        # ── 4. 交易日历 ──────────────────────────────────────────────
        print("[report_builder] 生成交易日历...")
        try:
            calendar = self._build_calendar(all_limitup_dates)
        except Exception as exc:
            print(f"  ✗ 交易日历生成失败: {exc}", flush=True)
            calendar = {
                "meta": {"tab": "calendar", "count": 0},
                "dates": [],
            }

        _write_json(data_dir / "calendar.json", calendar)
        generated_files.append(str(data_dir / "calendar.json"))
        print(f"  → calendar.json ({calendar['meta']['count']} 个交易日)")

        # ── 5. 概念聚合力导向图（可选） ─────────────────────────────
        if concept:
            print("[report_builder] 生成概念聚合图（AI Agent）...")
            from tbot.business.concept_cluster import ConceptClusterService
            from tbot.engines.ai import AIService

            try:
                cluster_svc = ConceptClusterService(ai=AIService())
                graph = cluster_svc.cluster(date=snapshot_date, mode="full")
                if graph:
                    _write_json(data_dir / "concept_graph.json", graph)
                    generated_files.append(str(data_dir / "concept_graph.json"))
                    n_concepts = len(graph.get("concepts", []))
                    print(f"  → concept_graph.json ({n_concepts} 个概念)")
                else:
                    print("  ∼ 概念聚类无结果")
            except Exception as exc:
                print(f"  ✗ 概念聚类失败: {exc}", flush=True)
                print("  ∼ 跳过 concept_graph.json")

        print(f"\n[report_builder] Build 完成 → {out_dir.resolve()}")
        return {
            "success": True,
            "out_dir": str(out_dir.resolve()),
            "files": generated_files,
        }

    # ── 内部：交易日历构建 ──────────────────────────────────────────

    def _build_calendar(
        self,
        limitup_dates: list[str],
    ) -> dict[str, Any]:
        """构建交易日历。

        从 market_a.daily_bars 获取所有交易日期，
        以 limit_up_pool 是否有数据标记 ``has_data``。

        Parameters
        ----------
        limitup_dates : list[str]
            已知存在涨停数据的日期列表（YYYYMMDD）。

        Returns
        -------
        dict
            ``{meta: {tab, count}, dates: [{date, has_data}, ...]}``.
        """
        trade_records = self._db_service.get_all_trade_dates()
        if not trade_records:
            return {
                "meta": {"tab": "calendar", "count": 0},
                "dates": [],
            }

        limitup_set = set(limitup_dates)
        dates = [
            {"date": r["trade_date"], "has_data": r["trade_date"] in limitup_set}
            for r in trade_records
        ]

        return {
            "meta": {"tab": "calendar", "count": len(dates)},
            "dates": dates,
        }


# ── helpers ─────────────────────────────────────────────────────────────


def _write_json(path: Path, data: Any) -> None:
    """写入 JSON 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _empty_overview(date: str) -> dict[str, Any]:
    """概览数据不可用时的空模板。"""
    return {
        "meta": {"tab": "overview", "date": date, "generated_at": datetime.now().isoformat()},
        "kpi": {
            "net_inflow": None,
            "large_inflow": None,
            "mid_inflow": None,
            "small_inflow": None,
            "pct_change_sh": None,
            "pct_change_sz": None,
            "close_sh": None,
            "close_sz": None,
            "limit_up_cnt": 0,
            "limit_break_cnt": 0,
            "limit_down_cnt": 0,
            "max_limit_times": 0,
        },
        "series": {"market_flow_mini": []},
    }
