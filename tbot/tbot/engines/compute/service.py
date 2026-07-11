# SPDX-License-Identifier: MIT
"""TBot compute engine — pure math functions. No DB access, no I/O."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence


def rolling_zscore(seq: list[float], window: int = 60) -> list[float | None]:
    """滚动 Z-score：前 window 个元素返回 None，之后用最近 window 个算。"""
    result: list[float | None] = []
    for i in range(len(seq)):
        if i < window:
            result.append(None)
        else:
            chunk = seq[i - window:i]
            n = len(chunk)
            mu = sum(chunk) / n
            var = sum((x - mu) ** 2 for x in chunk) / n
            sigma = var ** 0.5
            result.append((seq[i] - mu) / sigma if sigma else 0.0)
    return result


def rank_list(vals: Sequence[float]) -> list[float]:
    """O(n log n) 平均秩，处理并列。"""
    idx = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[idx[j + 1]] == vals[idx[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[idx[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman 秩相关系数。不足 3 个有效观测返回 None。"""
    n = len(xs)
    if n < 3:
        return None
    rx = rank_list(xs)
    ry = rank_list(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    sxy = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    if sxx == 0 or syy == 0:
        return None
    return sxy / ((sxx ** 0.5) * (syy ** 0.5))


def daily_share(
    by_date: dict[str, list[tuple]],
    d: str,
) -> dict[str, float] | None:
    """单日 share 矩阵。

    by_date[d] = [(code, md, pct_change, close, name), ...] 格式。
    share = code 的 md / sum(abs(md)).
    """
    items = by_date[d]
    s = sum(abs(x[1]) for x in items)
    if s == 0:
        return None
    return {items[i][0]: items[i][1] / s for i in range(len(items))}


def smoothed_share(
    by_date: dict[str, list[tuple]],
    dates: list[str],
    di: int,
    k: int,
) -> dict[str, float] | None:
    """k 日平滑 share（Rolling sum of md in each industry, normalized）。

    k=1 退化为 daily_share。
    """
    if k <= 1:
        return daily_share(by_date, dates[di])
    agg: dict[str, float] = defaultdict(float)
    for lag in range(k):
        if di - lag < 0:
            break
        for code, md, *_ in by_date[dates[di - lag]]:
            agg[code] += md
    s = sum(abs(v) for v in agg.values())
    if s == 0:
        return None
    return {c: v / s for c, v in agg.items()}


def fwd_returns(
    code_close: dict[str, list[float]],
    date_idx: dict[str, dict[str, int]],
    dates: list[str],
    code: str,
    di: int,
    h: int,
) -> float | None:
    """前向 h 日收益（%）。

    code_close[code] = [close1, close2, ...] 对应 code_dates[code] 的序列。
    date_idx[code] = {date_str: index_in_series}.
    """
    ti = date_idx[code].get(dates[di])
    if ti is None or ti + h >= len(code_close[code]):
        return None
    return (code_close[code][ti + h] / code_close[code][ti] - 1) * 100


def ic_series(
    by_date: dict[str, list[tuple]],
    dates: list[str],
    code_close: dict[str, list[float]],
    date_idx: dict[str, dict[str, int]],
    k: int,
    h: int = 5,
    window: int = 40,
) -> list[tuple[str, float]]:
    """逐日 Spearman IC 序列（行业 share 与前瞻 h 日收益）。

    每 window 个交易日采样一次。
    """
    out: list[tuple[str, float]] = []
    for di in range(k - 1, len(dates) - h):
        if (di - (k - 1)) % window != 0:
            continue
        sh = smoothed_share(by_date, dates, di, k)
        if sh is None:
            continue
        xs: list[float] = []
        ys: list[float] = []
        for c, v in sh.items():
            r = fwd_returns(code_close, date_idx, dates, c, di, h)
            if r is not None:
                xs.append(v)
                ys.append(r)
        ic = spearman(xs, ys)
        if ic is not None:
            out.append((dates[di], round(ic, 4)))
    return out


def quintile_perf(
    by_date: dict[str, list[tuple]],
    dates: list[str],
    code_close: dict[str, list[float]],
    date_idx: dict[str, dict[str, int]],
    k: int,
    h: int = 5,
    n_groups: int = 5,
) -> list[float | None]:
    """分位超额收益。

    按 share 排序分 N 组，每组平均收益减全样本平均收益。
    """
    group_ret: list[list[float]] = [[] for _ in range(n_groups)]
    for di in range(k - 1, len(dates) - h):
        sh = smoothed_share(by_date, dates, di, k)
        if sh is None:
            continue
        items: list[tuple[float, float]] = []
        for c, v in sh.items():
            r = fwd_returns(code_close, date_idx, dates, c, di, h)
            if r is not None:
                items.append((v, r))
        if len(items) < n_groups * 2:
            continue
        items.sort(key=lambda x: x[0])
        n = len(items)
        all_m = sum(x[1] for x in items) / n
        for g in range(n_groups):
            seg = items[g * n // n_groups:(g + 1) * n // n_groups]
            if seg:
                gm = sum(x[1] for x in seg) / len(seg)
                group_ret[g].append(gm - all_m)
    return [round(sum(g) / len(g), 4) if g else None for g in group_ret]


def total_ic(
    by_date: dict[str, list[tuple]],
    dates: list[str],
    code_close: dict[str, list[float]],
    date_idx: dict[str, dict[str, int]],
    k: int,
    h: int = 5,
) -> float | None:
    """全样本总 IC（所有行业-所有日期合并成一个 Spearman）。"""
    xs: list[float] = []
    ys: list[float] = []
    for di in range(k - 1, len(dates) - h):
        sh = smoothed_share(by_date, dates, di, k)
        if sh is None:
            continue
        for c, v in sh.items():
            r = fwd_returns(code_close, date_idx, dates, c, di, h)
            if r is not None:
                xs.append(v)
                ys.append(r)
    return spearman(xs, ys)
