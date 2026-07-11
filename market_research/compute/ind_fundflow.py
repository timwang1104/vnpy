# SPDX-License-Identifier: MIT
"""compute_ind_fundflow — 行业板块资金流信号计算。

读 db → 构 panel/by_date → 调 _stats → window 截断 → 组装 {meta,kpi,series,tables}。

算法继承自 examples/fundflow_viz/build_data.py：
- 用白名单固定行业截面（content_type='行业' + 20250902 前的一级行业 code 集合）
- Spearman IC / quintile / smoothed_share 数学逻辑零改动
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict

from market_research.compute._stats import (
    daily_share,
    ic_series,
    quintile_perf,
    smoothed_share,
    total_ic,
)


def _build_whitelist(cur: sqlite3.Cursor) -> dict[str, str]:
    """取 20250902 的一级行业（content_type='行业'）作为白名单。

    2025-09-03 后 content_type 从 ~87 个暴增到 ~509 个（混入多级分类），
    用切换前截面固定行业集合，避免晚期口径污染。
    """
    cur.execute(
        "SELECT DISTINCT ts_code, name FROM ind_fundflow "
        "WHERE content_type='行业' AND trade_date='20250902' ORDER BY name"
    )
    return dict(cur.fetchall())


def compute_ind_fundflow(db: sqlite3.Connection, window: int = 240) -> dict:
    """计算行业资金流信号。

    Args:
        db: data/tushare.db 连接
        window: 滚动窗口交易日数，默认 240

    Returns:
        统一 schema dict: {meta, kpi, series, tables}
    """
    cur = db.cursor()

    # --- 1. 白名单 ---
    whitelist = _build_whitelist(cur)
    white_codes = set(whitelist)

    # --- 2. 读原始数据 ---
    placeholders = ",".join("'" + c + "'" for c in white_codes)
    cur.execute(
        "SELECT trade_date, ts_code, name, pct_change, close, buy_md_amount "
        "FROM ind_fundflow "
        f"WHERE ts_code IN ({placeholders}) "
        "ORDER BY ts_code, trade_date"
    )
    rows = cur.fetchall()

    if not rows:
        return {
            "meta": {"tab": "industry", "date": "", "window": window},
            "kpi": {},
            "series": [],
            "tables": {},
        }

    # --- 3. 构 panel / by_date / date_idx ---
    panel: dict[str, list[tuple[str, float, float, float]]] = defaultdict(list)
    for d, c, _nm, pc, cl, md in rows:
        panel[c].append((d, pc, cl, md))

    by_date: dict[str, list[tuple[str, float, float, float, str]]] = defaultdict(list)
    for code, seq in panel.items():
        for d, pc, cl, md in seq:
            by_date[d].append((code, md, pc, cl, whitelist[code]))

    all_dates = sorted(by_date.keys())

    # --- 4. window 截断 ---
    if len(all_dates) > window:
        cutoff = all_dates[-window]
        by_date = {d: v for d, v in by_date.items() if d >= cutoff}
        dates = sorted(by_date.keys())
    else:
        dates = all_dates

    # --- 5. 构 code_close / date_idx for fwd returns ---
    code_close: dict[str, list[float]] = {}
    date_idx: dict[str, dict[str, int]] = {}
    for code, seq in panel.items():
        code_close[code] = [r[2] for r in seq]  # close values in chronological order
        date_idx[code] = {r[0]: i for i, r in enumerate(seq)}

    # --- 6. 行业列表（按白名单顺序排序）---
    top_codes = sorted(white_codes, key=lambda c: whitelist[c])
    code_pos = {c: i for i, c in enumerate(top_codes)}

    # --- 7. Share matrices (k=1,3,5) ---
    share_mats: dict[str, list[list[float | None]]] = {}
    for k in (1, 3, 5):
        mat: list[list[float | None]] = [
            [None] * len(top_codes) for _ in range(len(dates))
        ]
        for di, d in enumerate(dates):
            sh = smoothed_share(by_date, dates, di, k) if k > 1 else daily_share(by_date, d)
            if sh is None:
                continue
            for c, v in sh.items():
                if c in code_pos:
                    mat[di][code_pos[c]] = round(v, 5)
        share_mats[str(k)] = mat

    # --- 8. IC / quintile / total IC ---
    ic1 = ic_series(by_date, dates, code_close, date_idx, k=1, h=5, window=40)
    ic3 = ic_series(by_date, dates, code_close, date_idx, k=3, h=5, window=40)
    q1 = quintile_perf(by_date, dates, code_close, date_idx, k=1, h=5, n_groups=5)
    q3 = quintile_perf(by_date, dates, code_close, date_idx, k=3, h=5, n_groups=5)
    ic1_total = total_ic(by_date, dates, code_close, date_idx, k=1, h=5)
    ic3_total = total_ic(by_date, dates, code_close, date_idx, k=3, h=5)

    # --- 9. 最新日行业排名 ---
    last_d = dates[-1]
    sh_last = daily_share(by_date, last_d)
    if sh_last:
        ranked = sorted(
            [(whitelist[c], c, sh_last[c]) for c in sh_last if c in whitelist],
            key=lambda x: x[2],
        )
        last_ranking = [
            {"name": n, "code": c, "share": round(s, 5)} for (n, c, s) in ranked
        ]
    else:
        last_ranking = []

    # --- 10. KPI ---
    n_ind = len(sh_last) if sh_last else 0
    pos = sum(1 for v in (sh_last or {}).values() if v > 0)
    neg = sum(1 for v in (sh_last or {}).values() if v < 0)
    ic1_avg = round(sum(x[1] for x in ic1) / len(ic1), 4) if ic1 else None
    ic3_avg = round(sum(x[1] for x in ic3) / len(ic3), 4) if ic3 else None

    kpi = {
        "n_industries": n_ind,
        "n_dates": len(dates),
        "date_min": dates[0],
        "date_max": dates[-1],
        "last_date": last_d,
        "pos_count": pos,
        "neg_count": neg,
        "ic1_avg_5d": ic1_avg,
        "ic3_avg_5d": ic3_avg,
        "ic1_total_5d": round(ic1_total, 4) if ic1_total is not None else None,
        "ic3_total_5d": round(ic3_total, 4) if ic3_total is not None else None,
        "whitelist_size": len(whitelist),
        "cutover_date": "20250903",
    }

    # --- 11. 组装 unified schema ---
    return {
        "meta": {
            "tab": "industry",
            "date": last_d,
            "window": window,
        },
        "kpi": kpi,
        "series": {
            "dates": dates,
            "industries": [whitelist[c] for c in top_codes],
            "industries_code": top_codes,
            "share_heat": share_mats,
            "ic_series": {"k1": ic1, "k3": ic3},
            "quintile_perf": {"k1": q1, "k3": q3},
            "daily_count": [[d, len(by_date[d])] for d in dates],
        },
        "tables": {
            "last_ranking": last_ranking,
        },
    }
