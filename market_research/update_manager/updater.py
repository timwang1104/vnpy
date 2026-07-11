# SPDX-License-Identifier: MIT
"""
updater.py — 数据更新编排 + 状态管理

包含 6 类增量更新（涨停池、行业资金流、大盘资金流、个股资金流、
概念/公司信息、日线行情）+ build report。

状态管理自洽，提供日志缓冲和线程安全锁，供 server 直接 import 调用。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from collections.abc import Callable

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
DEFAULT_TUSHARE_DB = str(DATA_DIR / "tushare.db")
DEFAULT_HISTORY_DB = str(DATA_DIR / "history.db")

# 让 Python 能找到 examples/tushare_fetch/ 下的 ts_db 工具模块
sys.path.insert(0, str(REPO_ROOT / "examples" / "tushare_fetch"))

from ts_db import (  # noqa: E402
    LIMIT_UP_COLS,
    IND_FF_COLS,
    MKT_FF_COLS,
    STOCK_FF_COLS,
    connect,
    init_tables,
    upsert_rows,
    existing_dates,
    existing_stock_codes,
    count_rows,
)

# ---------------------------------------------------------------------------
# tushare proxy 辅助
# ---------------------------------------------------------------------------

DEFAULT_PROXY: str = "https://tt.xiaodefa.cn"
ENV_TOKEN: str = "TUSHARE_API_KEY"
ENV_PROXY: str = "TUSHARE_BASE_URL"


def create_pro(token: str, proxy_url: str | None = None):
    """创建走代理的 tushare pro 实例。"""
    if not token:
        raise ValueError("tushare token 不能为空")
    import tushare as ts

    ts.set_token(token)
    pro = ts.pro_api()
    pro._DataApi__http_url = proxy_url or DEFAULT_PROXY
    return pro


def load_token_script() -> str:
    token = os.environ.get(ENV_TOKEN, "") or ""
    if not token:
        raise RuntimeError("未检测到 tushare token，请先 export TUSHARE_API_KEY=<56位key>")
    return token


def load_proxy_url() -> str:
    return os.environ.get(ENV_PROXY, "") or DEFAULT_PROXY


# ---------------------------------------------------------------------------
# 状态管理（线程安全）
# ---------------------------------------------------------------------------

_state: dict = {
    "status": "idle",  # idle | running | completed | error
    "started_at": None,
    "finished_at": None,
    "error": None,
    "progress": {"current": 0, "total": 0, "label": ""},  # 当前进度
    "last_results": [],  # 各表更新结果
    "schedule": {"next_run": None, "cron_expr": "30 18 * * 1-5"},
}
_log_buffer: list[str] = []
_lock = threading.Lock()


def get_status() -> dict:
    with _lock:
        return dict(_state)


def get_log_lines() -> list[str]:
    with _lock:
        return list(_log_buffer)


def clear_log() -> None:
    with _lock:
        _log_buffer.clear()


def _set_progress(current: int, total: int, label: str) -> None:
    with _lock:
        _state["progress"] = {"current": current, "total": total, "label": label}


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

_LOG_CALLBACKS: list[Callable[[str], None]] = []


def register_log_callback(cb: Callable[[str], None]) -> None:
    _LOG_CALLBACKS.append(cb)


def unregister_log_callback(cb: Callable[[str], None]) -> None:
    try:
        _LOG_CALLBACKS.remove(cb)
    except ValueError:
        pass


def log(msg: str, end: str = "\n") -> None:
    """带时间戳的日志，同时写入缓冲区和回调。"""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, end=end, flush=True)
    with _lock:
        _log_buffer.append(line)
    for cb in _LOG_CALLBACKS:
        try:
            cb(line)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 交易日历
# ---------------------------------------------------------------------------

def get_open_dates(pro, start: str, end: str) -> list[str]:
    """获取[start, end]之间的开市日。代理不支持则回退自然日。"""
    try:
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
        if cal is not None and not cal.empty and "cal_date" in cal.columns:
            return sorted(cal["cal_date"].astype(str).tolist())
    except Exception as e:
        log(f"[提示] trade_cal 不可用（{type(e).__name__}: {str(e)[:60]}），回退自然日")
    dates = []
    d = datetime.strptime(start, "%Y%m%d")
    end_dt = datetime.strptime(end, "%Y%m%d")
    while d <= end_dt:
        dates.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return dates


def resolve_date_range(since: str | None, force_start: str | None,
                       force_end: str | None) -> tuple[str, str, bool]:
    """解析日期范围和是否强制模式。"""
    force = force_start is not None
    if force:
        return force_start, force_end or datetime.now().strftime("%Y%m%d"), True
    if since:
        start = since
    else:
        start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    return start, end, False


# ---------------------------------------------------------------------------
# 各表更新
# ---------------------------------------------------------------------------

def _df_to_rows(df, cols: list[str]) -> list[list]:
    """pandas DataFrame -> 按列顺序取值。"""
    rows = []
    for _, row in df.iterrows():
        rows.append([row.get(c) for c in cols])
    return rows


# ---------------------------------------------------------------------------
# 通用表更新（三表数据驱动）
# ---------------------------------------------------------------------------

_TABLE_UPDATE_SPEC = [
    {
        "table": "limit_up_pool",
        "api_func": "limit_list_d",
        "cols": LIMIT_UP_COLS,
        "label": "涨停",
    },
    {
        "table": "ind_fundflow",
        "api_func": "moneyflow_ind_dc",
        "cols": IND_FF_COLS,
        "label": "行业资金",
    },
    {
        "table": "mkt_fundflow",
        "api_func": "moneyflow_mkt_dc",
        "cols": MKT_FF_COLS,
        "label": "大盘资金",
    },
]


def _update_table_by_date(
    pro, conn, open_dates: list[str], force: bool,
    start: str, end: str, spec: dict,
) -> dict:
    """通用日期遍历更新函数。

    用 spec 内容参数化：
      spec["table"]    — 表名
      spec["api_func"] — pro.xxx() 的 API 方法名
      spec["cols"]     — 列集
      spec["label"]    — 日志前缀标签
    """
    table = spec["table"]
    cols = spec["cols"]
    api_func = spec["api_func"]
    label = spec["label"]

    if force:
        conn.execute(f"DELETE FROM {table} WHERE trade_date >= ? AND trade_date <= ?",
                      (start, end))
        conn.commit()
        skip: set[str] = set()
        log(f"[{label}] 强制模式，已删除 {start}~{end} 数据")
    else:
        skip = existing_dates(conn, table, start, end)
        if skip:
            log(f"[{label}] 已有 {len(skip)} 天数据，跳过")

    ok = skip_n = fail_n = inserted_n = 0
    for trade_date in open_dates:
        if trade_date in skip:
            skip_n += 1
            continue
        try:
            df = getattr(pro, api_func)(trade_date=trade_date)
            time.sleep(0.1)
        except Exception as e:
            fail_n += 1
            log(f"[{label}] {trade_date} 失败: {str(e)[:80]}")
            continue
        if df is None or df.empty:
            skip_n += 1
            continue
        rows = _df_to_rows(df, cols)
        n = upsert_rows(conn, table, cols, rows)
        if n:
            ok += 1
            inserted_n += n
            log(f"[{label}] {trade_date} 写入 {n} 行")
        else:
            skip_n += 1

    return {"table": table, "ok_days": ok, "skip_days": skip_n,
            "fail_days": fail_n, "inserted": inserted_n,
            "total": count_rows(conn, table)}


def update_stock_fundflow(pro, conn, start: str, end: str, sleep_s: float = 0.15) -> dict:
    """更新个股资金流 (stock_fundflow)，依赖 limit_up_pool 已有数据。"""
    codes = existing_stock_codes(conn, start, end)
    if not codes:
        log(f"[个股资金] limit_up_pool 中 {start}~{end} 范围无 ts_code，跳过")
        return {"table": "stock_fundflow", "ok_stocks": 0, "skip_stocks": 0,
                "fail_stocks": 0, "inserted": 0,
                "total": count_rows(conn, "stock_fundflow")}

    log(f"[个股资金] 待抓 {len(codes)} 只股票")
    ok = fail = inserted_n = 0
    for ts_code in codes:
        try:
            df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end)
            time.sleep(sleep_s)
        except Exception as e:
            fail += 1
            log(f"[个股资金] {ts_code} 失败: {str(e)[:80]}")
            continue
        if df is None or df.empty:
            continue
        rows = _df_to_rows(df, STOCK_FF_COLS)
        n = upsert_rows(conn, "stock_fundflow", STOCK_FF_COLS, rows)
        if n:
            ok += 1
            inserted_n += n
            if ok % 20 == 0:
                log(f"[个股资金] 进度 {ok}/{len(codes)}，已写入 {inserted_n} 行")

    return {"table": "stock_fundflow", "ok_stocks": ok, "fail_stocks": fail,
            "inserted": inserted_n, "total": count_rows(conn, "stock_fundflow")}


def _is_concept_stale(db_path: str, max_age_days: int = 7) -> bool:
    """检查概念数据是否已过期（超过 max_age_days 未更新）。"""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE name='ths_concept'")
        if not cur.fetchone():
            conn.close()
            return True
        cur.execute("SELECT MAX(updated_at) FROM ths_concept")
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            last = datetime.fromisoformat(row[0])
            age = (datetime.now() - last).days
            return age >= max_age_days
        return True
    except Exception:
        return True


def update_concept_data(db_path: str, force: bool = False) -> dict:
    """更新同花顺概念板块、成分股和公司信息。"""
    if not force and not _is_concept_stale(db_path, max_age_days=7):
        log("[概念] 7 日内已更新，跳过（--concept 强制更新）")
        return {"concept_count": 0, "member_count": 0, "company_count": 0, "skipped": True}

    cc = __import__(
        "market_research.compute.concept_cluster",
        fromlist=["update_concept_db", "update_stock_company", "ensure_extended_schema"],
    )
    conn = cc.ensure_extended_schema(db_path)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")

    log("[概念] 拉取同花顺概念及成分股...")
    try:
        concept_cnt, member_cnt = cc.update_concept_db(conn)
    except Exception as e:
        log(f"[概念] 概念更新失败: {e}")
        concept_cnt, member_cnt = 0, 0

    log("[概念] 拉取公司基本面...")
    try:
        company_cnt = cc.update_stock_company(conn)
    except Exception as e:
        log(f"[概念] 公司信息更新失败: {e}")
        company_cnt = 0

    conn.close()
    return {"concept_count": concept_cnt, "member_count": member_cnt,
            "company_count": company_cnt, "skipped": False}


def update_history(history_db: str) -> dict:
    """增量更新日线行情 (history.db)。"""
    dd = __import__(
        "market_research.simulator.data_downloader",
        fromlist=["DataDownloader"],
    )
    dl = dd.DataDownloader(history_db=history_db)
    log("[日线] 增量更新日线数据...")
    try:
        dl.incremental_update()
    except Exception as e:
        log(f"[日线] 增量更新失败: {e}")
        return {"status": "error", "error": str(e)}

    import sqlite3
    conn = sqlite3.connect(history_db)
    cur = conn.execute("SELECT COUNT(*) FROM daily_bars")
    total = cur.fetchone()[0]
    conn.close()
    return {"status": "ok", "total_rows": total}


def build_report(tushare_db: str, window: int = 240, concept: bool = False) -> dict:
    """触发 market_research build。"""
    b = __import__("market_research.builder", fromlist=["build"])
    out_dir = REPO_ROOT / "market_research" / "report"
    log("[Build] 重建 report 目录...")
    try:
        b.build(db_path=tushare_db, out_dir=out_dir, window=window,
                concept=concept)
        log(f"[Build] 完成 → {out_dir}")
        return {"status": "ok", "out_dir": str(out_dir)}
    except Exception as e:
        log(f"[Build] 失败: {e}")
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# 状态检查（schedule 信息）
# ---------------------------------------------------------------------------

def get_schedule() -> dict:
    """返回当前 cron 注册状态。"""
    from market_research.update_manager.cron import show_cron_info
    return show_cron_info()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

TASK_TOTAL = 6  # 总更新阶段数


def run_update(
    tushare_db: str = DEFAULT_TUSHARE_DB,
    history_db: str = DEFAULT_HISTORY_DB,
    since: str | None = None,
    force_start: str | None = None,
    force_end: str | None = None,
    no_concept: bool = False,
    force_concept: bool = False,
    no_stock_ff: bool = False,
    tushare_only: bool = False,
    history_only: bool = False,
    do_build: bool = False,
    concept: bool = False,
    window: int = 240,
) -> int:
    """运行数据更新流程。返回退出码。"""
    with _lock:
        if _state["status"] == "running":
            log("[错误] 更新正在进行中，拒绝并发")
            return 1
        _state["status"] = "running"
        _state["started_at"] = datetime.now().isoformat()
        _state["finished_at"] = None
        _state["error"] = None
        _state["last_results"] = []
        _log_buffer.clear()

    start_ts = datetime.now()
    log("=" * 50)
    log(f"开始数据更新 ({start_ts.strftime('%Y-%m-%d %H:%M:%S')})")

    start, end, force = resolve_date_range(since, force_start, force_end)
    log(f"日期范围: {start} ~ {end}" + (" (强制模式)" if force else ""))

    results: list[dict] = []
    has_error = False
    task_index = 0

    try:
        if not history_only:
            try:
                token = load_token_script()
                proxy_url = load_proxy_url()
                pro = create_pro(token, proxy_url)
            except RuntimeError as e:
                log(f"[错误] tushare token 未配置: {e}")
                log("[提示] 请先 export TUSHARE_API_KEY=<56位key>")
                if not do_build:
                    _finalize_state("error", str(e), results)
                    return 1
                pro = None
                log("[提示] 跳过 tushare 数据更新（仅执行 build）")

            if pro:
                conn = connect(tushare_db)
                init_tables(conn)
                open_dates = get_open_dates(pro, start, end)
                log(f"[信息] 开市日: {len(open_dates)} 个")

                for spec in _TABLE_UPDATE_SPEC:
                    if not tushare_only:
                        task_index += 1
                        _set_progress(task_index, TASK_TOTAL, spec["label"])
                        log(f"--- {spec['label']} ---")
                        r = _update_table_by_date(pro, conn, open_dates, force, start, end, spec)
                        results.append(r)
                        log(f"  ✓ {spec['label']}: 写入 {r['inserted']} 行, 总 {r['total']} 行")

                if not tushare_only and not no_stock_ff:
                    task_index += 1
                    _set_progress(task_index, TASK_TOTAL, "个股资金流")
                    log("--- 个股资金流 ---")
                    r = update_stock_fundflow(pro, conn, start, end)
                    results.append(r)
                    log(f"  ✓ 个股资金流: 写入 {r['inserted']} 行, 总 {r['total']} 行")

                conn.close()

                if not tushare_only and not no_concept:
                    task_index += 1
                    _set_progress(task_index, TASK_TOTAL, "概念/公司信息")
                    log("--- 概念/公司信息 ---")
                    r = update_concept_data(tushare_db, force=force_concept)
                    results.append(r)
                    if r.get("skipped"):
                        log("  ∼ 概念数据已是最新，跳过")
                    else:
                        log(f"  ✓ 概念: {r['concept_count']} 概念, {r['member_count']} 归属, {r['company_count']} 公司")

        if not tushare_only:
            task_index += 1
            _set_progress(task_index, TASK_TOTAL, "日线行情")
            log("--- 日线行情 ---")
            r = update_history(history_db)
            results.append(r)
            if r.get("status") == "ok":
                log(f"  ✓ 日线行情: 总 {r['total_rows']} 行")
            else:
                log(f"  ✗ 日线行情: {r.get('error', '失败')}")
                has_error = True

        if do_build:
            _set_progress(task_index, TASK_TOTAL, "重建报告")
            log("--- 重建报告 ---")
            r = build_report(tushare_db, window=window, concept=concept)
            results.append(r)
            if r.get("status") == "ok":
                log(f"  ✓ 报告已重建: {r['out_dir']}")
            else:
                log(f"  ✗ 报告重建失败: {r.get('error', '')}")
                has_error = True

    except KeyboardInterrupt:
        log("\n[中断] 用户取消")
        has_error = True
    except Exception as e:
        log(f"[错误] 更新异常: {e}")
        import traceback
        log(traceback.format_exc())
        has_error = True

    # --- 汇总 ---
    elapsed = (datetime.now() - start_ts).total_seconds()
    log("=" * 50)
    if has_error:
        log(f"更新完成（部分失败），耗时 {elapsed:.0f} 秒")
    else:
        log(f"更新完成，耗时 {elapsed:.0f} 秒")
    log("=" * 50)

    exit_code = 1 if has_error else 0
    _finalize_state("completed" if not has_error else "error",
                    None if not has_error else "部分任务失败",
                    results)
    return exit_code


def _finalize_state(status: str, error: str | None, results: list[dict]) -> None:
    """安全更新终结状态。"""
    with _lock:
        _state["status"] = status
        _state["error"] = error
        _state["finished_at"] = datetime.now().isoformat()
        _state["last_results"] = results
        _state["progress"] = {"current": 0, "total": 0, "label": ""}
