# -*- coding: utf-8 -*-
"""从 data/tushare.db 的 ind_fundflow (行业) 计算 md_share 信号与前瞻检验数据。
关键: 行业 content_type 里混了多级分类, 2025-09-03 后从 ~87 暴增到 ~509 个。
用切换前(20250902)的 ts_code 集作为一级行业白名单, 全样本只取这些 code,
保证截面规模稳定、父子不重复计数、IC 不被晚期口径污染。
纯标准库, 不依赖 numpy/pandas。"""
import sqlite3, json, os
from collections import defaultdict

DB = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'tushare.db')
con = sqlite3.connect(DB)
cur = con.cursor()

cur.execute("""SELECT DISTINCT ts_code, name FROM ind_fundflow
  WHERE content_type='行业' AND trade_date='20250902' ORDER BY name""")
L1 = {c: n for c, n in cur.fetchall()}
L1_CODES = set(L1)
print(f"一级行业白名单: {len(L1)} 个")

cur.execute("""SELECT trade_date, ts_code, name, pct_change, close, buy_md_amount
  FROM ind_fundflow WHERE content_type='行业' AND ts_code IN (%s)
  ORDER BY ts_code, trade_date""" % ",".join("'" + c + "'" for c in L1_CODES))
rows = cur.fetchall()

name = dict(L1)
panel = defaultdict(list)
for d, c, nm, pc, cl, md in rows:
    panel[c].append((d, pc, cl, md))
by_date = defaultdict(list)
for code, seq in panel.items():
    for d, pc, cl, md in seq:
        by_date[d].append((code, md, pc, cl, name[code]))
dates = sorted(by_date.keys())
daily_count = {d: len(by_date[d]) for d in dates}

code_dates = {c: [r[0] for r in seq] for c, seq in panel.items()}
code_close = {c: [r[2] for r in seq] for c, seq in panel.items()}
date_idx = {c: {d: i for i, d in enumerate(code_dates[c])} for c in panel}

def rank_list(vals):
    """O(n log n) rank, 处理并列取平均秩"""
    idx = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[idx[j + 1]] == vals[idx[i]]:
            j += 1
        avg = (i + j) / 2.0  # 0-based 平均秩
        for k in range(i, j + 1):
            ranks[idx[k]] = avg
        i = j + 1
    return ranks

def daily_share(d):
    items = by_date[d]
    s = sum(abs(x[1]) for x in items)
    if s == 0: return None
    return {items[i][0]: items[i][1] / s for i in range(len(items))}

def smoothed_share(di, k):
    agg = defaultdict(float)
    for lag in range(k):
        if di - lag < 0: break
        for code, md, *_ in by_date[dates[di - lag]]:
            agg[code] += md
    s = sum(abs(v) for v in agg.values())
    if s == 0: return None
    return {c: v / s for c, v in agg.items()}

top_codes = sorted(L1_CODES, key=lambda c: name[c])
code_pos = {c: i for i, c in enumerate(top_codes)}

share_mats = {}
for k in (1, 3, 5):
    mat = [[None] * len(top_codes) for _ in range(len(dates))]
    for di, d in enumerate(dates):
        sh = smoothed_share(di, k) if k > 1 else daily_share(d)
        if sh is None: continue
        for c, v in sh.items():
            if c in code_pos:
                mat[di][code_pos[c]] = round(v, 5)
    share_mats[k] = mat

def fwd_returns(code, di, h):
    ti = date_idx[code].get(dates[di])
    if ti is None or ti + h >= len(code_close[code]): return None
    return (code_close[code][ti + h] / code_close[code][ti] - 1) * 100

def spearman(xs, ys):
    n = len(xs)
    if n < 3: return None
    rx = rank_list(xs); ry = rank_list(ys)
    mx = sum(rx) / n; my = sum(ry) / n
    sxx = sum((a - mx) ** 2 for a in rx); syy = sum((b - my) ** 2 for b in ry)
    sxy = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    if sxx == 0 or syy == 0: return None
    return sxy / ((sxx ** 0.5) * (syy ** 0.5))

def ic_series(k, h=5, window=40):
    out = []
    for di in range(k - 1, len(dates) - h):
        if (di - (k - 1)) % window != 0: continue
        sh = smoothed_share(di, k) if k > 1 else daily_share(dates[di])
        if sh is None: continue
        xs, ys = [], []
        for c in sh:
            r = fwd_returns(c, di, h)
            if r is not None: xs.append(sh[c]); ys.append(r)
        ic = spearman(xs, ys)
        if ic is not None:
            out.append((dates[di], round(ic, 4)))
    return out

def quintile_perf(k, h=5, N_groups=5):
    group_ret = [[] for _ in range(N_groups)]
    for di in range(k - 1, len(dates) - h):
        sh = smoothed_share(di, k) if k > 1 else daily_share(dates[di])
        if sh is None: continue
        items = []
        for c in sh:
            r = fwd_returns(c, di, h)
            if r is not None: items.append((sh[c], r))
        if len(items) < N_groups * 2: continue
        items.sort(key=lambda x: x[0])
        n = len(items); all_m = sum(x[1] for x in items) / n
        for g in range(N_groups):
            seg = items[g * n // N_groups:(g + 1) * n // N_groups]
            if seg:
                gm = sum(x[1] for x in seg) / len(seg)
                group_ret[g].append(gm - all_m)
    return [round(sum(g) / len(g), 4) if g else None for g in group_ret]

def total_ic(k, h=5):
    xs, ys = [], []
    for di in range(k - 1, len(dates) - h):
        sh = smoothed_share(di, k) if k > 1 else daily_share(dates[di])
        if sh is None: continue
        for c in sh:
            r = fwd_returns(c, di, h)
            if r is not None: xs.append(sh[c]); ys.append(r)
    return spearman(xs, ys)

print("computing IC / quintile ...")
ic1 = ic_series(1, 5, 40)
ic3 = ic_series(3, 5, 40)
q1 = quintile_perf(1, 5, 5)
q3 = quintile_perf(3, 5, 5)
ic1_total = total_ic(1); ic3_total = total_ic(3)
print("done IC")

last_d = dates[-1]
sh_last = daily_share(last_d)
ranked = sorted([(name[c], c, sh_last[c]) for c in sh_last], key=lambda x: x[2])
last_ranking = [{"name": n, "code": c, "share": round(s, 5)} for (n, c, s) in ranked]
daily_count_list = [[d, daily_count[d]] for d in dates]

n_ind = len(sh_last)
pos = sum(1 for v in sh_last.values() if v > 0)
neg = sum(1 for v in sh_last.values() if v < 0)
ic1_avg = round(sum(x[1] for x in ic1) / len(ic1), 4) if ic1 else None
ic3_avg = round(sum(x[1] for x in ic3) / len(ic3), 4) if ic3 else None
kpi = {
    "n_industries": n_ind, "n_dates": len(dates), "date_min": dates[0], "date_max": dates[-1],
    "last_date": last_d, "pos_count": pos, "neg_count": neg,
    "ic1_avg_5d": ic1_avg, "ic3_avg_5d": ic3_avg, "ic1_total_5d": ic1_total, "ic3_total_5d": ic3_total,
    "whitelist_size": len(L1), "cutover_date": "20250903",
}

out = {
    "dates": dates,
    "industries": [name[c] for c in top_codes],
    "industries_code": top_codes,
    "share_heat": {str(k): share_mats[k] for k in (1, 3, 5)},
    "ic_series": {"k1": ic1, "k3": ic3},
    "quintile_perf": {"k1": q1, "k3": q3},
    "last_ranking": last_ranking,
    "daily_count": daily_count_list,
    "kpi": kpi,
}
outpath = os.path.join(os.path.dirname(__file__), 'data.json')
with open(outpath, 'w') as f:
    json.dump(out, f)
print("wrote", outpath, "size KB:", round(os.path.getsize(outpath) / 1024, 1))
print("dates:", len(dates), "daily count early/late:", daily_count[dates[0]], "/", daily_count[dates[-1]])
print("ic1 total 5d:", ic1_total, "ic3 total 5d:", ic3_total, "(应为负)")
print("kpi:", json.dumps(kpi, ensure_ascii=False))
