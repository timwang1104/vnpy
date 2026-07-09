# SPDX-License-Identifier: MIT
"""FastAPI StaticFiles server + industry timeseries API — 托管 report 目录，端口 8765（被占递增）。

用法:
    serve("report")                        # 纯静态（向后兼容）
    serve("report", db_path="data/tushare.db")  # 静态 + API
"""
from __future__ import annotations

import socket
import sqlite3
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def find_free_port(start: int = 8765, max_tries: int = 20) -> int:
    """从 start 开始尝试绑定端口，被占递增。"""
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"无法找到空闲端口 (从 {start} 试了 {max_tries} 个)")


# ==================== API helpers ====================

def _rolling_zscore(seq: list[float], window: int = 60) -> list[float | None]:
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


def _query_industry_timeseries(
    cur: sqlite3.Cursor,
    ts_code: str,
    mode: str = "raw",
) -> dict | None:
    """查 ind_fundflow 表，返回单行业时序。

    Args:
        cur: DB cursor
        ts_code: 行业代码（如 BK0450.DC）
        mode: raw | share | zscore

    Returns:
        API schema dict，或 None（行业不存在）
    """
    cur.execute(
        "SELECT trade_date, buy_md_amount, pct_change, close, name "
        "FROM ind_fundflow WHERE ts_code=? AND content_type='行业' "
        "ORDER BY trade_date",
        (ts_code,),
    )
    rows = cur.fetchall()
    if not rows:
        return None

    name: str = rows[0][4]
    dates = [r[0] for r in rows]
    raw_values = [float(r[1]) for r in rows]
    pct_change = [float(r[2]) for r in rows]
    close = [float(r[3]) for r in rows]

    values: list[float] | list[float | None]
    if mode == "raw":
        values = [v / 1e8 for v in raw_values]
        mode_label = "中单净额（亿元）"
    elif mode == "share":
        s = sum(abs(v) for v in raw_values)
        values = [v / s for v in raw_values] if s else raw_values
        mode_label = "md_share"
    elif mode == "zscore":
        values = _rolling_zscore(raw_values, window=60)
        mode_label = "Z-score（60日滚动）"
    else:
        values = [v / 1e8 for v in raw_values]
        mode_label = "中单净额（亿元）"

    return {
        "ts_code": ts_code,
        "name": name,
        "mode": mode,
        "dates": dates,
        "values": values,
        "close": close,
        "pct_change": pct_change,
        "meta": {
            "n_dates": len(dates),
            "date_min": dates[0],
            "date_max": dates[-1],
            "mode_label": mode_label,
        },
    }


# ==================== Server factory ====================

def _build_app(report_dir: Path, db: sqlite3.Connection | None) -> FastAPI:
    """构建 FastAPI app（静态文件 + 可选的 API）。"""
    app = FastAPI(title="Market Research")

    # --- API 路由（静态 catch-all 之前注册） ---
    if db is not None:

        @app.get("/api/industry/{ts_code}/timeseries")
        async def api_industry_timeseries(ts_code: str, mode: str = "raw") -> dict:
            if mode not in ("raw", "share", "zscore"):
                raise HTTPException(status_code=400, detail=f"不支持的 mode: {mode}")
            result = _query_industry_timeseries(db.cursor(), ts_code, mode)
            if result is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"行业代码 {ts_code} 不存在",
                )
            return result

    # --- 静态文件 ---
    static_dir = report_dir / "static"
    data_dir = report_dir / "data"

    if static_dir.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(static_dir)),
            name="static",
        )
    if data_dir.exists():
        app.mount(
            "/data",
            StaticFiles(directory=str(data_dir)),
            name="data",
        )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(report_dir / "index.html"))

    return app


# ==================== Serve entry ====================

def serve(
    report_dir: str | Path = "report",
    port: int = 8765,
    no_browser: bool = False,
    db_path: str | Path | None = None,
) -> None:
    """启动 FastAPI 服务。

    Args:
        report_dir: report 目录路径
        port: 起始端口，被占则递增
        no_browser: True 时不打开浏览器
        db_path: 可选，tushare.db 路径，提供时启用 API 路由
    """
    report_dir = Path(report_dir).resolve()
    if not report_dir.exists():
        raise FileNotFoundError(f"Report directory not found: {report_dir}")

    db: sqlite3.Connection | None = None
    if db_path:
        db_path = Path(db_path).resolve()
        if not db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")
        db = sqlite3.connect(str(db_path))

    app = _build_app(report_dir, db)
    actual_port = find_free_port(start=port)

    url = f"http://127.0.0.1:{actual_port}/"

    if not no_browser:
        webbrowser.open(url)

    if actual_port != port:
        print(f"[market_research] 端口 {port} 被占，使用 {actual_port}")
    print(f"[market_research] Serve at {url}")
    print("[market_research] Press Ctrl+C to stop")

    try:
        uvicorn.run(app, host="127.0.0.1", port=actual_port, log_level="info")
    finally:
        if db is not None:
            db.close()
