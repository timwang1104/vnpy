# SPDX-License-Identifier: MIT
"""ReportBuilder — 编排业务服务，生成 report/ 静态 JSON 文件。

对标 market_research/builder.py 的 build() 函数，将 compute/* 直调
替换为 tbot business services（industry / limitup / overview），
并使用 DuckDB (DatabaseManager) 后端。

用法::

    from tbot.report_builder import ReportBuilder
    builder = ReportBuilder()
    result = builder.build(
        db_path="data/tushare.db",
        out_dir="report",
        window=240,
        concept=False,
    )
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class ReportBuilder:
    """报告构建器 —— 编排行业 / 涨停 / 概览等业务服务，输出静态 JSON。"""

    def build(
        self,
        db_path: str | Path,
        out_dir: str | Path,
        window: int = 240,
        concept: bool = False,
    ) -> dict[str, Any]:
        """执行 build，在 out_dir 下生成 report/data/ 静态 JSON。

        Parameters
        ----------
        db_path : str | Path
            SQLite 数据库路径（兼容旧版；后续切换到 DuckDB DatabaseManager）。
        out_dir : str | Path
            报告输出目录。
        window : int
            资金流信号滚动窗口，默认 240 个交易日。
        concept : bool
            是否同时生成概念聚合力导向图。

        Returns
        -------
        dict
            ``{success: bool, out_dir: str, files: list[str]}``.
        """
        db_path = Path(db_path)
        out_dir = Path(out_dir)

        if not db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")

        # ---------- 创建输出目录 ----------
        data_dir = out_dir / "data"
        limitup_dir = data_dir / "limitup"
        data_dir.mkdir(parents=True, exist_ok=True)
        limitup_dir.mkdir(parents=True, exist_ok=True)

        generated_files: list[str] = []

        # ---------- 行业资金流 ----------
        print("[report_builder] 计算行业资金流信号...")
        # TODO: 替换为 IndustryService(db_service).get_full_report(window=window)
        industry_data = _mock_industry_data(window)
        industry_data["meta"]["generated_at"] = datetime.now().isoformat()
        _write_json(data_dir / "industry.json", industry_data)
        generated_files.append(str(data_dir / "industry.json"))
        print(f"  → industry.json ({len(industry_data['series']['dates'])} 个交易日)")

        # ---------- 涨停池 ----------
        print("[report_builder] 计算涨停池数据...")
        # TODO: 替换为 LimitUpService(mgr).get_history(window=window)
        all_dates = sorted(_MOCK_LIMITUP.keys())
        main_dates = all_dates[-window:] if len(all_dates) > window else all_dates

        main_data: dict[str, Any] = {
            "meta": {
                "tab": "limitup",
                "date": all_dates[-1] if all_dates else "",
                "window": window,
                "generated_at": datetime.now().isoformat(),
            },
            "dates": main_dates,
            "by_date": {d: _MOCK_LIMITUP[d] for d in main_dates},
        }
        _write_json(data_dir / "limitup_main.json", main_data)
        generated_files.append(str(data_dir / "limitup_main.json"))
        print(f"  → limitup_main.json ({len(main_dates)} 天主数据)")

        # 历史分片（不在主 JSON 中的日期）
        history_dates = [d for d in all_dates if d not in main_dates]
        for dt_str in history_dates:
            day_data = {
                "meta": {
                    "tab": "limitup",
                    "date": dt_str,
                    "generated_at": datetime.now().isoformat(),
                },
                **_MOCK_LIMITUP[dt_str],
            }
            _write_json(limitup_dir / f"{dt_str}.json", day_data)
            generated_files.append(str(limitup_dir / f"{dt_str}.json"))
        print(f"  → limitup/ ({len(history_dates)} 个历史日期分片)")

        # ---------- 概览 KPI ----------
        print("[report_builder] 计算概览...")
        # TODO: 替换为 OverviewService(mgr).get_kpi(date=snapshot_date)
        snapshot_date = all_dates[-1] if all_dates else "20260101"
        overview_data = _mock_overview_data(snapshot_date)
        overview_data["meta"]["generated_at"] = datetime.now().isoformat()

        # 概览数据注入 main JSON（前端方便）
        main_data["overview"] = overview_data
        _write_json(data_dir / "limitup_main.json", main_data)

        # 独立 overview.json
        _write_json(data_dir / "overview.json", overview_data)
        generated_files.append(str(data_dir / "overview.json"))

        # ---------- 交易日历 ----------
        print("[report_builder] 生成交易日历...")
        # TODO: 替换为 DatabaseService / 交易日历模块
        all_trade_dates = all_dates if all_dates else ["20200102"]
        calendar = _mock_calendar(all_trade_dates)
        _write_json(data_dir / "calendar.json", calendar)
        generated_files.append(str(data_dir / "calendar.json"))
        print(f"  → calendar.json ({calendar['meta']['count']} 个交易日)")

        # ---------- 概念力导向图（可选） ----------
        if concept:
            print("[report_builder] 生成概念聚合图（AI Agent）...")
            # TODO: 替换为 ConceptClusterService(mgr).build_graph(date=snapshot_date)
            print("  ∼ concept_graph generation not yet implemented (concept=True stubbed)")

        print(f"\n[report_builder] Build 完成 → {out_dir.resolve()}")
        return {
            "success": True,
            "out_dir": str(out_dir.resolve()),
            "files": generated_files,
        }


# ── helpers ─────────────────────────────────────────────────────────────


def _write_json(path: Path, data: Any) -> None:
    """写入 JSON 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _mock_industry_data(window: int) -> dict[str, Any]:
    """Mock 行业资金流数据，待替换为 IndustryService. """
    return {
        "meta": {"tab": "industry", "date": "20260101", "window": window},
        "kpi": {"n_industries": 0, "n_dates": 0},
        "series": {"dates": [], "industries": []},
        "tables": {},
    }


_MOCK_LIMITUP: dict[str, dict[str, Any]] = {
    "20260101": {
        "kpi": {"limit_up_cnt": 0, "limit_break_cnt": 0, "limit_down_cnt": 0, "max_limit_times": 0},
        "tables": {"tiers": []},
    },
}


def _mock_overview_data(date: str) -> dict[str, Any]:
    """Mock 大盘概览数据，待替换为 OverviewService. """
    return {
        "meta": {"tab": "overview", "date": date},
        "kpi": {
            "net_inflow": None,
            "large_inflow": None,
            "mid_inflow": None,
            "small_inflow": None,
            "limit_up_cnt": 0,
            "limit_break_cnt": 0,
            "limit_down_cnt": 0,
            "max_limit_times": 0,
        },
        "series": {"market_flow_mini": []},
    }


def _mock_calendar(trade_dates: list[str]) -> dict[str, Any]:
    """Mock 交易日历，待替换为日历构建模块。"""
    return {
        "meta": {"tab": "calendar", "count": len(trade_dates)},
        "dates": trade_dates,
    }
