# SPDX-License-Identifier: MIT
"""FastAPI StaticFiles server + industry timeseries API + simulator API — 托管 report 目录，端口 8765（被占递增）。

用法:
    serve("report")                                          # 纯静态（向后兼容）
    serve("report", db_path="data/tushare.db")               # 静态 + 行业 API
    serve("report", db_path="data/tushare.db",               # 静态 + 行业 + 模拟盘
          sim_db="data/simulator.db")
"""
from __future__ import annotations

import json as json_module
import socket
import sqlite3
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket
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


def _get_all_industry_codes(cur: sqlite3.Cursor) -> list[tuple[str, str]]:
    """获取所有行业代码和名称。"""
    cur.execute(
        "SELECT DISTINCT ts_code, name FROM ind_fundflow "
        "WHERE content_type='行业' ORDER BY ts_code"
    )
    return [(r[0], r[1]) for r in cur.fetchall()]


def _compute_latest_anomalies(
    cur: sqlite3.Cursor,
    threshold: float = 2.0,
    limit: int = 10,
) -> dict:
    """计算全行业最新 z-score，返回异常排名。

    Args:
        cur: DB cursor
        threshold: |z-score| 阈值，默认 2.0
        limit: 最大返回条数

    Returns:
        API schema dict
    """
    codes = _get_all_industry_codes(cur)
    anomalies: list[dict] = []

    for ts_code, name in codes:
        cur.execute(
            "SELECT trade_date, buy_md_amount FROM ind_fundflow "
            "WHERE ts_code=? AND content_type='行业' ORDER BY trade_date",
            (ts_code,),
        )
        rows = cur.fetchall()
        if len(rows) < 61:  # 需要至少 61 个交易日才能算 z-score
            continue

        raw_values = [float(r[1]) for r in rows]
        zscores = _rolling_zscore(raw_values, window=60)

        # 取最新 z-score（非 None 的最后一个）
        latest_z = None
        for z in reversed(zscores):
            if z is not None:
                latest_z = z
                break

        if latest_z is None:
            continue

        if abs(latest_z) > threshold:
            anomalies.append({
                "ts_code": ts_code,
                "name": name,
                "latest_zscore": round(latest_z, 4),
                "latest_value": round(raw_values[-1] / 1e8, 4),
                "date": rows[-1][0],
            })

    # 按 |z-score| 降序
    anomalies.sort(key=lambda x: abs(x["latest_zscore"]), reverse=True)

    total = len(anomalies)
    truncated = anomalies[:limit]

    return {
        "threshold": threshold,
        "limit": limit,
        "total_anomalies": total,
        "total_industries": len(codes),
        "anomalies": truncated,
    }

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

# 默认路径（相对于项目根目录）
_DEFAULT_SIM_DB = str(Path(__file__).resolve().parent.parent / "data" / "simulator.db")
_DEFAULT_HISTORY_DB = str(Path(__file__).resolve().parent.parent / "data" / "history.db")


def _add_simulator_routes(
    app: FastAPI,
    sim_db_path: str,
    history_db_path: str,
) -> None:
    """添加模拟盘 API 路由"""
    from market_research.simulator.db_writer import DBWriter
    from market_research.simulator.engine import BatchEngine

    # 确保 DB 存在
    engine = BatchEngine(history_db=history_db_path, sim_db=sim_db_path)

    # 创建表
    DBWriter(sim_db_path, 0)._init_schema()

    # 启动时自动扫描策略目录
    engine.discover_strategies()

    def _get_conn() -> sqlite3.Connection:
        return sqlite3.connect(sim_db_path)

    # ── 策略列表 ────────────────────────────────────────

    @app.get("/api/sim/strategies")
    async def list_strategies():
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, class_name, parameters, enabled, description, author, created_at "
            "FROM strategies ORDER BY id"
        )
        rows = cur.fetchall()
        conn.close()

        return {
            "strategies": [
                {
                    "id": r[0],
                    "name": r[1],
                    "class_name": r[2],
                    "parameters": json_module.loads(r[3]) if r[3] else [],
                    "enabled": bool(r[4]),
                    "description": r[5] or "",
                    "author": r[6] or "",
                    "created_at": r[7] or "",
                }
                for r in rows
            ]
        }

    @app.post("/api/sim/discover")
    async def discover_strategies():
        """扫描 strategies/ 目录并注册新策略"""
        engine.discover_strategies()
        # 重新查询
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM strategies")
        count = cur.fetchone()[0]
        conn.close()
        return {"status": "ok", "strategy_count": count}

    @app.get("/api/sim/strategies/{strategy_id}")
    async def get_strategy(strategy_id: int):
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, class_name, parameters, enabled, description, author, created_at, updated_at "
            "FROM strategies WHERE id=?",
            (strategy_id,),
        )
        r = cur.fetchone()
        if not r:
            conn.close()
            raise HTTPException(404, "策略不存在")

        # 也查最近一次运行
        cur.execute(
            "SELECT id, status, start_date, end_date, initial_capital, "
            "final_equity, total_return, created_at "
            "FROM run_batches WHERE strategy_id=? ORDER BY id DESC LIMIT 1",
            (strategy_id,),
        )
        batch = cur.fetchone()
        conn.close()

        return {
            "id": r[0],
            "name": r[1],
            "class_name": r[2],
            "parameters": json_module.loads(r[3]) if r[3] else [],
            "enabled": bool(r[4]),
            "description": r[5] or "",
            "author": r[6] or "",
            "created_at": r[7] or "",
            "updated_at": r[8] or "",
            "latest_batch": {
                "id": batch[0],
                "status": batch[1],
                "start_date": batch[2] or "",
                "end_date": batch[3] or "",
                "initial_capital": batch[4] or 1_000_000,
                "final_equity": batch[5],
                "total_return": batch[6],
                "run_at": batch[7] or "",
            } if batch else None,
        }

    # ── 运行策略 ────────────────────────────────────────

    @app.post("/api/sim/strategies/{strategy_id}/run")
    async def run_strategy(
        strategy_id: int,
        body: dict | None = None,
    ):
        body = body or {}
        setting = body.get("setting", {})
        start_date = body.get("start_date", "20200101")
        end_date = body.get("end_date", "20261231")

        try:
            batch_id = engine.run_strategy(
                strategy_id,
                start_date=start_date,
                end_date=end_date,
                setting=setting,
            )
            return {"status": "ok", "batch_id": batch_id}
        except Exception as e:
            import traceback

            return {
                "status": "error",
                "message": str(e),
                "traceback": traceback.format_exc(),
            }

    # ── 权益曲线 ────────────────────────────────────────

    @app.get("/api/sim/strategies/{strategy_id}/equity")
    async def get_equity(strategy_id: int):
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT b.id FROM run_batches b "
            "WHERE b.strategy_id=? ORDER BY b.id DESC LIMIT 1",
            (strategy_id,),
        )
        batch = cur.fetchone()
        if not batch:
            conn.close()
            return {"dates": [], "equity": [], "cash": [], "market_value": []}

        cur.execute(
            "SELECT trade_date, equity, cash, market_value "
            "FROM equity_curves WHERE batch_id=? ORDER BY trade_date",
            (batch[0],),
        )
        rows = cur.fetchall()
        conn.close()

        return {
            "dates": [r[0] for r in rows],
            "equity": [r[1] for r in rows],
            "cash": [r[2] for r in rows],
            "market_value": [r[3] for r in rows],
        }

    # ── 持仓 ────────────────────────────────────────────

    @app.get("/api/sim/strategies/{strategy_id}/positions")
    async def get_positions(strategy_id: int):
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT b.id FROM run_batches b "
            "WHERE b.strategy_id=? ORDER BY b.id DESC LIMIT 1",
            (strategy_id,),
        )
        batch = cur.fetchone()
        if not batch:
            conn.close()
            return {"positions": []}

        cur.execute(
            "SELECT ts_code, volume, avg_price, market_value, pnl, pnl_pct, trade_date "
            "FROM positions WHERE batch_id=? ORDER BY market_value DESC",
            (batch[0],),
        )
        rows = cur.fetchall()
        conn.close()

        return {
            "positions": [
                {
                    "ts_code": r[0],
                    "volume": r[1],
                    "avg_price": r[2],
                    "market_value": r[3],
                    "pnl": r[4],
                    "pnl_pct": r[5],
                    "trade_date": r[6],
                }
                for r in rows
            ]
        }

    # ── 交易记录 ────────────────────────────────────────

    @app.get("/api/sim/strategies/{strategy_id}/trades")
    async def get_trades(strategy_id: int):
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT b.id FROM run_batches b "
            "WHERE b.strategy_id=? ORDER BY b.id DESC LIMIT 1",
            (strategy_id,),
        )
        batch = cur.fetchone()
        if not batch:
            conn.close()
            return {"trades": []}

        cur.execute(
            "SELECT ts_code, direction, price, volume, amount, pnl, trade_date, comment "
            "FROM trades WHERE batch_id=? ORDER BY trade_date, id",
            (batch[0],),
        )
        rows = cur.fetchall()
        conn.close()

        return {
            "trades": [
                {
                    "ts_code": r[0],
                    "direction": r[1],
                    "price": r[2],
                    "volume": r[3],
                    "amount": r[4],
                    "pnl": r[5],
                    "trade_date": r[6],
                    "comment": r[7] or "",
                }
                for r in rows
            ]
        }


def _build_app(report_dir: Path, db: sqlite3.Connection | None,
               sim_db: str | None = None) -> FastAPI:
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

        @app.get("/api/industry/anomalies")
        async def api_industry_anomalies(
            threshold: float = 2.0,
            limit: int = 10,
        ) -> dict:
            return _compute_latest_anomalies(db.cursor(), threshold, limit)

    # --- 模拟盘 API ---
    if sim_db:
        _add_simulator_routes(app, sim_db, _DEFAULT_HISTORY_DB)

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

    # --- AI Agent 对话 WebSocket ---
    from market_research.chat_worker import chat_endpoint_handler

    @app.websocket("/api/chat/ws")
    async def ws_chat(websocket: WebSocket):
        await chat_endpoint_handler(websocket)

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
    sim_db: str | None = None,
) -> None:
    """启动 FastAPI 服务。

    Args:
        report_dir: report 目录路径
        port: 起始端口，被占则递增
        no_browser: True 时不打开浏览器
        db_path: 可选，tushare.db 路径，提供时启用行业 API
        sim_db: 可选，simulator.db 路径，提供时启用模拟盘 API
    """
    report_dir = Path(report_dir).resolve()
    if not report_dir.exists():
        raise FileNotFoundError(f"Report directory not found: {report_dir}")

    db: sqlite3.Connection | None = None
    if db_path:
        db_path = Path(db_path).resolve()
        if not db_path.exists():
            # 创建空文件
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.touch()
        db = sqlite3.connect(str(db_path))

    app = _build_app(report_dir, db, sim_db=sim_db)
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
