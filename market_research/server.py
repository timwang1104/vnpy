# SPDX-License-Identifier: MIT
"""FastAPI StaticFiles server + industry timeseries API + simulator API + data update API — 托管 report 目录，端口 8765（被占递增）。

用法:
    serve()                                                    # 默认 market_research/report/
    serve(report_dir="path/to/report")                         # 指定目录
    serve(db_path="data/tushare.db")                           # 静态 + 行业 API
    serve(db_path="data/tushare.db", sim_db="data/sim.db")    # 静态 + 行业 + 模拟盘
"""
from __future__ import annotations

import asyncio
import json as json_module
import socket
import sqlite3
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, StreamingResponse
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


# ==================== 数据更新状态管理（委托 update_manager） ====================

from market_research.update_manager import (  # noqa: E402
    run_update as um_run_update,
    get_status as um_get_status,
    get_log_lines as um_get_log_lines,
    clear_log as um_clear_log,
)

_update_log_lock = threading.Lock()


def _run_update_in_thread(tushare_db: str, history_db: str, do_build: bool) -> None:
    """在后台线程中调用 update_manager.run_update()。"""
    # 直接在子线程调用，run_update 内部管理状态/日志/锁
    try:
        um_run_update(
            tushare_db=tushare_db,
            history_db=history_db,
            since=datetime.now().strftime("%Y%m%d"),
            do_build=do_build,
        )
    except Exception as e:
        print(f"[CRITICAL] _run_update_in_thread error: {e}", flush=True)
        import traceback
        traceback.print_exc()


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
               sim_db: str | None = None,
               db_path: str | Path | None = None) -> FastAPI:
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

    # --- 数据更新 API ---
    repo_root = Path(__file__).resolve().parent.parent
    default_tushare = str(repo_root / "data" / "tushare.db")
    default_history = str(repo_root / "data" / "history.db")

    @app.post("/api/data/update")
    async def api_data_update(body: dict = Body(default=None)):  # noqa: B008
        """触发一次数据更新（异步执行）。"""
        try:
            status = um_get_status()
            if status["status"] == "running":
                return {"status": "error", "message": "更新正在进行中"}

            body = body or {}
            do_build = body.get("build", True)

            # 启动前清日志
            um_clear_log()

            thread = threading.Thread(
                target=_run_update_in_thread,
                args=(body.get("tushare_db", default_tushare),
                      body.get("history_db", default_history),
                      do_build),
                daemon=True,
            )
            thread.start()

            return {"status": "ok", "message": "更新已启动"}
        except Exception as e:
            import traceback
            print(f"[CRITICAL] api_data_update error: {e}", flush=True)
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    @app.get("/api/data/update/status")
    async def api_update_status():
        """查询更新状态。"""
        state = await asyncio.to_thread(um_get_status)
        state["log_lines"] = len(await asyncio.to_thread(um_get_log_lines))
        return state

    @app.get("/api/data/update/log")
    async def api_update_log():
        """SSE 流式输出更新日志。"""
        async def event_generator():
            last_index = 0
            terminated = False
            while not terminated:
                lines = await asyncio.to_thread(um_get_log_lines)
                current_status = (await asyncio.to_thread(um_get_status))["status"]

                new_lines = lines[last_index:]
                last_index = len(lines)

                for line in new_lines:
                    yield f"data: {line}\n\n"

                if current_status == "completed":
                    yield "event: complete\ndata: __UPDATE_DONE__\n\n"
                    return
                elif current_status == "error":
                    yield "event: error\ndata: __UPDATE_ERROR__\n\n"
                    return
                elif current_status == "idle":
                    terminated = True
                    break

                await asyncio.sleep(0.3)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # --- 数据更新 Cron API ---
    from market_research.update_manager.cron import install_cron, remove_cron, show_cron_info

    @app.post("/api/data/cron/install")
    async def api_cron_install():
        ok = install_cron()
        return {"status": "ok" if ok else "error"}

    @app.post("/api/data/cron/remove")
    async def api_cron_remove():
        ok = remove_cron()
        return {"status": "ok" if ok else "error"}

    @app.get("/api/data/cron/status")
    async def api_cron_status():
        return show_cron_info()

    # --- 概念图生成 API ---

    @app.post("/api/concept/generate")
    async def api_concept_generate(body: dict = Body(default=None)):  # noqa: B008
        """触发概念图生成（后台线程运行，不阻塞事件循环）。"""
        if not db_path:
            return {"status": "error", "message": "未指定数据库路径"}

        body = body or {}
        date = body.get("date") or None
        mode = body.get("mode") or "concept"

        from market_research.compute.concept_cluster import compute_concept_graph

        loop = asyncio.get_event_loop()

        def _generate():
            return compute_concept_graph(
                db_path=str(db_path),
                date=date,
                mode=mode,
            )

        try:
            graph = await loop.run_in_executor(None, _generate)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

        if graph is None:
            return {"status": "ok", "note": "无数据（当日无涨停或 AI API 未配置）",
                    "n_concepts": 0}

        graph["meta"]["generated_at"] = datetime.now().isoformat()
        concept_path = report_dir / "data" / "concept_graph.json"
        import json
        with open(concept_path, "w", encoding="utf-8") as f:
            json.dump(graph, f, ensure_ascii=False)

        return {
            "status": "ok",
            "n_concepts": graph["meta"].get("n_concepts", 0),
            "date": graph["meta"].get("date", ""),
        }

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
    report_dir: str | Path | None = None,
    port: int = 8765,
    no_browser: bool = False,
    db_path: str | Path | None = None,
    sim_db: str | None = None,
) -> None:
    """启动 FastAPI 服务。

    Args:
        report_dir: report 目录路径（默认 market_research/report/）
        port: 起始端口，被占则递增
        no_browser: True 时不打开浏览器
        db_path: 可选，tushare.db 路径，提供时启用行业 API
        sim_db: 可选，simulator.db 路径，提供时启用模拟盘 API
    """
    report_dir = Path(report_dir or (
        Path(__file__).parent / "report"
    )).resolve()
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
        # WAL + busy_timeout：允许并发写入，避免 update.py 写数据时报 "database is locked"
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=NORMAL")

    app = _build_app(report_dir, db, sim_db=sim_db, db_path=db_path)
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
