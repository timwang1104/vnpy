# SPDX-License-Identifier: MIT
"""Builder — 编排 compute/*，生成 report 目录。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time
from pathlib import Path

from market_research.compute.calendar import build_calendar
from market_research.compute.concept_cluster import compute_concept_graph
from market_research.compute.ind_fundflow import compute_ind_fundflow
from market_research.compute.limitup import compute_limitup_history
from market_research.compute.overview import compute_overview


def resolve_latest_date(
    db: sqlite3.Connection,
    now: datetime | None = None,
    override: str | None = None,
) -> str:
    """解析最新可用交易日。

    收盘守卫（15:30 阈值）：若最新日是今天且当前时间 < 15:30，自动回退到上一交易日。
    override 非空时直接返回。

    Returns:
        YYYYMMDD 格式日期字符串
    """
    if override:
        return override

    now = now or datetime.now()
    cur = db.cursor()

    # 取全部交易日（降序）
    all_dates = get_all_trade_dates(cur)
    if not all_dates:
        raise ValueError("No trade dates found in database")

    latest = all_dates[-1]

    # 收盘守卫
    today_str = now.strftime("%Y%m%d")
    if latest == today_str and now.time() < time(15, 30):
        if len(all_dates) >= 2:
            fallback = all_dates[-2]
            print(
                f"[market_research] 收盘守卫：最新日 {latest} 是今天且当前时 {now.time()!s} < 15:30，"
                f"回退到上一交易日 {fallback}"
            )
            return fallback
        return latest

    return latest


def get_all_trade_dates(cur: sqlite3.Cursor) -> list[str]:
    """取所有表的交易日并集，排序后返回。"""
    cur.execute("SELECT DISTINCT trade_date FROM ind_fundflow")
    dates = set(r[0] for r in cur.fetchall())
    try:
        cur.execute("SELECT DISTINCT trade_date FROM limit_up_pool")
        dates.update(r[0] for r in cur.fetchall())
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute("SELECT DISTINCT trade_date FROM mkt_fundflow")
        dates.update(r[0] for r in cur.fetchall())
    except sqlite3.OperationalError:
        pass
    return sorted(dates)


def build(
    db_path: str | Path,
    out_dir: str | Path,
    window: int = 240,
    date: str | None = None,
    concept: bool = False,
) -> None:
    """执行 build：调 compute/* 写 report 目录。

    产物:
        report/data/calendar.json
        report/data/industry.json
        report/data/limitup_main.json
        report/data/limitup/<date>.json (每日期)
        report/data/concept_graph.json (可选，需 --concept)
    """
    db_path = Path(db_path)
    out_dir = Path(out_dir)

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    db = sqlite3.connect(str(db_path))

    # resolve date
    snapshot_date = resolve_latest_date(db, override=date)

    # ---------- 创建输出目录 ----------
    data_dir = out_dir / "data"
    limitup_dir = data_dir / "limitup"
    data_dir.mkdir(parents=True, exist_ok=True)
    limitup_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 行业资金流 ----------
    print("[market_research] 计算行业资金流信号...")
    industry_data = compute_ind_fundflow(db, window=window)
    industry_data["meta"]["generated_at"] = datetime.now().isoformat()

    with open(data_dir / "industry.json", "w", encoding="utf-8") as f:
        json.dump(industry_data, f, ensure_ascii=False)
    print(f"  → industry.json ({len(industry_data['series']['dates'])} 个交易日, "
          f"{industry_data['kpi']['n_industries']} 个行业)")

    # ---------- 涨停池 ----------
    print("[market_research] 计算涨停池数据...")

    # 全量历史遍历
    all_limitup: dict[str, dict] = {}
    for dt_str, day_data in compute_limitup_history(db):
        all_limitup[dt_str] = day_data

    # 近 window 天 → 主 JSON
    all_dates = sorted(all_limitup.keys())
    main_dates = all_dates[-window:] if len(all_dates) > window else all_dates
    main_data = {
        "meta": {
            "tab": "limitup",
            "date": snapshot_date,
            "window": window,
            "generated_at": datetime.now().isoformat(),
        },
        "dates": main_dates,
        "by_date": {d: all_limitup[d] for d in main_dates},
    }
    with open(data_dir / "limitup_main.json", "w", encoding="utf-8") as f:
        json.dump(main_data, f, ensure_ascii=False)
    print(f"  → limitup_main.json ({len(main_dates)} 天主数据)")

    # 历史分片（不在主 JSON 中的日期）
    history_dates = [d for d in all_dates if d not in main_dates]
    slice_count = 0
    for dt_str in history_dates:
        data = all_limitup[dt_str]
        data["meta"]["generated_at"] = datetime.now().isoformat()
        with open(limitup_dir / f"{dt_str}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        slice_count += 1
    print(f"  → limitup/ ({slice_count} 个历史日期分片)")

    # ---------- 概览 KPI ----------
    print("[market_research] 计算概览...")
    overview_data = compute_overview(db, snapshot_date)
    overview_data["meta"]["generated_at"] = datetime.now().isoformat()

    # 概览数据注入 main JSON（前端方便）
    main_data["overview"] = overview_data
    with open(data_dir / "limitup_main.json", "w", encoding="utf-8") as f:
        json.dump(main_data, f, ensure_ascii=False)

    # ---------- 交易日历 ----------
    print("[market_research] 生成交易日历...")
    trade_dates_all = get_all_trade_dates(db.cursor())
    start_date = trade_dates_all[0] if trade_dates_all else "20200101"
    end_date = trade_dates_all[-1] if trade_dates_all else "20261231"
    calendar = build_calendar(start_date, end_date, trade_dates_all)
    with open(data_dir / "calendar.json", "w", encoding="utf-8") as f:
        json.dump(calendar, f, ensure_ascii=False)
    print(f"  → calendar.json ({calendar['meta']['count']} 个交易日)")

    # ---------- 概念力导向图（需加 --concept 标志） ----------
    if concept:
        print("[market_research] 生成概念聚合图（AI Agent）...")
        try:
            concept_graph = compute_concept_graph(
                db_path=db_path,
                date=snapshot_date,
                mode="concept",
            )
            if concept_graph is not None:
                concept_graph["meta"]["generated_at"] = datetime.now().isoformat()
                with open(data_dir / "concept_graph.json", "w", encoding="utf-8") as f:
                    json.dump(concept_graph, f, ensure_ascii=False)
                print(f"  → concept_graph.json ({concept_graph['meta']['n_concepts']} 个概念)")
            else:
                print("  ∼ concept_graph.json 无数据（可能 AI API 未配置或当日无涨停）")
        except Exception as e:
            print(f"  ∼ concept_graph.json 生成跳过: {e}")

    db.close()
    print(f"\n[market_research] Build 完成 → {out_dir.resolve()}")
