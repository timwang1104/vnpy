"""IndustryService — 行业板块业务领域。

三大核心方法:
- get_timeseries(code, mode) — 单行业时序
- get_anomalies(threshold, limit) — 资金流异常检测
- get_full_report(window) — 全量资金流信号报告

底层使用 DatabaseService 做数据查询，compute engine 做信号计算。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from tbot.engines.compute.service import (
    daily_share,
    ic_series,
    quintile_perf,
    smoothed_share,
    total_ic,
)
from tbot.engines.database.service import DatabaseService


class IndustryService:
    """行业板块资金流服务。

    参数
    ----------
    db_service : DatabaseService
        DuckDB 业务查询封装。
    """

    def __init__(self, db_service: DatabaseService) -> None:
        self._db = db_service

    # ── 内部：完整计算管道 ──────────────────────────────────────────

    def _compute(self, window: int = 240) -> dict[str, Any]:
        """行业资金流全量计算（DuckDB 后端）。

        对标 market_research/compute/ind_fundflow.py compute_ind_fundflow，
        将 SQLite 查询替换为 DuckDB 查询，计算逻辑复用 tbot compute engine。
        """
        conn = self._db._mgr.get_overview()
        try:
            # --- 1. 白名单（取最新交易日截面）---
            result = conn.execute(
                "SELECT DISTINCT ts_code, name FROM ind_fundflow "
                "WHERE content_type='行业' "
                "AND trade_date = ("
                "  SELECT max(trade_date) FROM ind_fundflow WHERE content_type='行业'"
                ") ORDER BY name"
            )
            whitelist: dict[str, str] = dict(result.fetchall())
            white_codes = set(whitelist)

            # --- 2. 读取原始数据 ---
            codes_quoted = ",".join(f"'{c}'" for c in white_codes)
            result = conn.execute(
                "SELECT trade_date, ts_code, name, pct_change, close, buy_md_amount "
                "FROM ind_fundflow "
                f"WHERE ts_code IN ({codes_quoted}) "
                "ORDER BY ts_code, trade_date"
            )
            rows = result.fetchall()
        finally:
            conn.close()

        if not rows:
            return {
                "meta": {"tab": "industry", "date": "", "window": window},
                "kpi": {},
                "series": [],
                "tables": {},
            }

        # --- 3. 构 panel / by_date（处理 DuckDB VARCHAR→float 转换）---
        panel: dict[str, list[tuple[str, float, float, float]]] = defaultdict(list)
        for d, c, _nm, pc, cl, md in rows:
            pc_f = float(pc) if pc is not None else 0.0
            cl_f = float(cl) if cl is not None else 0.0
            md_f = float(md) if md is not None else 0.0
            panel[c].append((d, pc_f, cl_f, md_f))

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

        # --- 5. code_close / date_idx ---
        code_close: dict[str, list[float]] = {}
        date_idx: dict[str, dict[str, int]] = {}
        for code, seq in panel.items():
            code_close[code] = [r[2] for r in seq]
            date_idx[code] = {r[0]: i for i, r in enumerate(seq)}

        # --- 6. 行业列表排序 ---
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

        # --- 9. 最新日排名 ---
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

        kpi: dict[str, Any] = {
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
        }

        return {
            "meta": {"tab": "industry", "date": last_d, "window": window},
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
            "tables": {"last_ranking": last_ranking},
        }

    # ── 内部：单行业占比计算 ────────────────────────────────────────

    def _compute_share_series(self, code_raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """计算单个行业每日资金流市场占比。

        share = industry_buy_md / sum(abs(all_industries_buy_md))

        Parameters
        ----------
        code_raw : list[dict]
            来自 get_ind_fundflow_raw(code, "行业")。

        Returns
        -------
        list[dict]
            每项 {"date": str, "share": float}。
        """
        if not code_raw:
            return []

        dates = [r["trade_date"] for r in code_raw]
        code_md = {r["trade_date"]: r["buy_md_amount"] for r in code_raw}

        conn = self._db._mgr.get_overview()
        try:
            placeholders = ",".join(f"'{d}'" for d in dates)
            result = conn.execute(
                "SELECT trade_date, ts_code, buy_md_amount FROM ind_fundflow "
                f"WHERE trade_date IN ({placeholders}) AND content_type='行业'"
            )
            all_rows = result.fetchall()
        finally:
            conn.close()

        by_date: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for d, c, md in all_rows:
            by_date[d].append((c, md))

        out: list[dict[str, Any]] = []
        for d in dates:
            items = by_date.get(d, [])
            total = sum(abs(md) for _, md in items)
            if total == 0:
                continue
            md = code_md.get(d)
            if md is None:
                continue
            out.append({"date": d, "share": round(md / total, 5)})
        return out

    # ── 外部接口 ────────────────────────────────────────────────────

    def get_timeseries(
        self,
        code: str,
        mode: str = "pct",
    ) -> dict[str, Any]:
        """行业板块资金流时序。

        Parameters
        ----------
        code : str
            行业 ts_code。
        mode : str
            返回值类型:
            - "raw"    : 全字段（buy_md_amount / pct_change / close）
            - "pct"    : 涨跌幅
            - "buy_md" : 资金流净额
            - "close"  : 收盘价
            - "share"  : 资金流占比（需全行业对比）

        Returns
        -------
        dict
            ``{dates: [...], values: [...], close: [...], pct_change: [...],
              meta: {mode_label, date_min, date_max}, name: str}``
        """
        raw = self._db.get_ind_fundflow_raw(code, "行业")
        if not raw:
            return {
                "dates": [],
                "values": [],
                "close": [],
                "pct_change": [],
                "meta": {"mode_label": "", "date_min": "", "date_max": ""},
                "name": code,
            }

        # 获取行业名称
        name = self._get_industry_name(code)

        # 将原始数据按 mode 转成 columnar 格式
        mode_key: str
        mode_label: str
        if mode == "raw":
            mode_key = "buy_md_amount"
            mode_label = "资金流净额"
        elif mode == "pct":
            mode_key = "pct_change"
            mode_label = "涨跌幅（%）"
        elif mode == "buy_md":
            mode_key = "buy_md_amount"
            mode_label = "资金流净额"
        elif mode == "close":
            mode_key = "close"
            mode_label = "收盘价"
        elif mode == "share":
            share_series = self._compute_share_series(raw)
            if share_series:
                dates = [r["date"] for r in share_series]
                values = [r["share"] for r in share_series]
                return {
                    "dates": dates,
                    "values": values,
                    "close": [],
                    "pct_change": [],
                    "meta": {
                        "mode_label": "资金流占比",
                        "date_min": dates[0] if dates else "",
                        "date_max": dates[-1] if dates else "",
                    },
                    "name": name,
                }
            return {
                "dates": [],
                "values": [],
                "close": [],
                "pct_change": [],
                "meta": {"mode_label": "资金流占比", "date_min": "", "date_max": ""},
                "name": name,
            }
        else:
            msg = f"Unknown mode: {mode!r} (raw/pct/buy_md/close/share)"
            raise ValueError(msg)

        dates = [r["trade_date"] for r in raw]
        values = [_to_float(r.get(mode_key)) for r in raw]
        close_vals = [_to_float(r.get("close")) for r in raw]
        pct_vals = [_to_float(r.get("pct_change")) for r in raw]

        return {
            "dates": dates,
            "values": values,
            "close": close_vals,
            "pct_change": pct_vals,
            "meta": {
                "mode_label": mode_label,
                "date_min": dates[0] if dates else "",
                "date_max": dates[-1] if dates else "",
            },
            "name": name,
        }

    def _get_industry_name(self, code: str) -> str:
        """从白名单查询行业名称。"""
        conn = self._db._mgr.get_overview()
        try:
            result = conn.execute(
                "SELECT DISTINCT name FROM ind_fundflow "
                "WHERE content_type='行业' AND ts_code=? LIMIT 1",
                [code],
            ).fetchone()
            return result[0] if result else code
        except Exception:
            return code
        finally:
            conn.close()

    def get_anomalies(
        self,
        threshold: float = 2.0,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """检测资金流异常行业。

        以最近 60 个交易日的资金流占比为样本，
        将占比的绝对值超过 ``threshold × mean(|share|)`` 的行业标记为异常，
        按异常程度降序排列。

        Parameters
        ----------
        threshold : float
            mean(|share|) 的倍数，默认 2.0（超过 2 倍均值视为异常）。
        limit : int
            最大返回条数，默认 10。

        Returns
        -------
        list[dict]
            每项含 {name, code, share, z_score}。
        """
        report = self._compute(window=60)
        ranking = report.get("tables", {}).get("last_ranking", [])
        if not ranking:
            return []

        abs_shares = [abs(s["share"]) for s in ranking]
        mean_abs = sum(abs_shares) / len(abs_shares) if abs_shares else 0.0
        if mean_abs == 0.0:
            return []

        anomalies = [s for s in ranking if abs(s["share"]) > threshold * mean_abs]
        anomalies.sort(key=lambda x: abs(x["share"]), reverse=True)

        return [
            {
                "name": a["name"],
                "code": a["code"],
                "share": a["share"],
                "z_score": round(a["share"] / mean_abs, 4),
            }
            for a in anomalies[:limit]
        ]

    def get_full_report(self, window: int = 240) -> dict[str, Any]:
        """完整行业资金流信号报告。

        Parameters
        ----------
        window : int
            滚动窗口交易日数，默认 240。

        Returns
        -------
        dict
            {meta, kpi, series, tables} — 同 ind_fundflow 输出 schema。
        """
        return self._compute(window=window)


def _to_float(v: object) -> float | None:
    """将 DuckDB 可能返回的 VARCHAR / None 转换为 float 或 None。"""
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
