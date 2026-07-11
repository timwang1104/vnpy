# SPDX-License-Identifier: MIT
"""Unified database writer for simulator results.

Uses in-memory queue + background flush thread to batch writes.
Phase 2: swap underlying transport to ZMQ REQ → DB proxy process.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from queue import Empty, Queue

# 默认数据库路径（相对项目根目录）
_DEFAULT_SIM_DB = str(
    Path(__file__).resolve().parent.parent.parent / "data" / "simulator.db"
)

# SQL 建表语句
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS strategies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    class_name  TEXT NOT NULL,
    parameters  TEXT,
    enabled     INTEGER DEFAULT 0,
    description TEXT DEFAULT '',
    author      TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS run_batches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL,
    status          TEXT DEFAULT 'pending',
    start_date      TEXT,
    end_date        TEXT,
    initial_capital REAL DEFAULT 1000000,
    final_equity    REAL,
    total_return    REAL,
    message         TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);

CREATE TABLE IF NOT EXISTS equity_curves (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL,
    trade_date  TEXT NOT NULL,
    equity      REAL NOT NULL,
    cash        REAL,
    market_value REAL,
    FOREIGN KEY (batch_id) REFERENCES run_batches(id)
);
CREATE INDEX IF NOT EXISTS idx_equity_batch ON equity_curves(batch_id, trade_date);

CREATE TABLE IF NOT EXISTS positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL,
    ts_code     TEXT NOT NULL,
    volume      REAL NOT NULL,
    avg_price   REAL,
    market_value REAL,
    pnl         REAL,
    pnl_pct     REAL,
    trade_date  TEXT,
    FOREIGN KEY (batch_id) REFERENCES run_batches(id)
);
CREATE INDEX IF NOT EXISTS idx_positions_batch ON positions(batch_id);

CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL,
    ts_code     TEXT NOT NULL,
    direction   TEXT NOT NULL,
    price       REAL,
    volume      REAL,
    amount      REAL,
    pnl         REAL,
    trade_date  TEXT,
    comment     TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (batch_id) REFERENCES run_batches(id)
);
CREATE INDEX IF NOT EXISTS idx_trades_batch ON trades(batch_id);
"""


class DBWriter:
    """统一数据库写入器

    用法:
        writer = DBWriter(batch_id=1)
        writer.record_equity("20240102", 1000000.0, 500000.0, 500000.0)
        writer.record_trade("000001.SZ", "buy", 10.0, 1000, 0, "20240102")
        writer.flush()  # 强制刷入
        writer.close()
    """

    def __init__(
        self,
        db_path: str = _DEFAULT_SIM_DB,
        batch_id: int = 0,
        flush_interval: float = 3.0,
        max_batch: int = 100,
    ):
        self.db_path = db_path
        self.batch_id = batch_id
        self.flush_interval = flush_interval
        self.max_batch = max_batch
        self._queue: Queue = Queue()
        self._closed = False

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

        # 启动后台 flush 线程
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="dbwriter-flush"
        )
        self._flush_thread.start()

    def _init_schema(self) -> None:
        """初始化数据库表结构"""
        conn = sqlite3.connect(self.db_path)
        conn.executescript(_SCHEMA_SQL)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
        conn.close()

    # ── 公开接口 ───────────────────────────────────────

    def record_equity(
        self, trade_date: str, equity: float, cash: float, market_value: float
    ) -> None:
        """记录单日权益快照"""
        self._queue.put(("equity", (self.batch_id, trade_date, equity, cash, market_value)))

    def record_position(
        self,
        trade_date: str,
        ts_code: str,
        volume: float,
        avg_price: float,
        market_value: float,
        pnl: float,
        pnl_pct: float,
    ) -> None:
        """记录持仓快照"""
        self._queue.put(
            (
                "position",
                (self.batch_id, ts_code, volume, avg_price, market_value, pnl, pnl_pct, trade_date),
            )
        )

    def record_trade(
        self,
        ts_code: str,
        direction: str,
        price: float,
        volume: float,
        amount: float,
        pnl: float,
        trade_date: str,
        comment: str = "",
    ) -> None:
        """记录交易"""
        self._queue.put(
            (
                "trade",
                (self.batch_id, ts_code, direction, price, volume, amount, pnl, trade_date, comment),
            )
        )

    def update_batch_status(
        self,
        status: str,
        final_equity: float | None = None,
        total_return: float | None = None,
        message: str = "",
    ) -> None:
        """更新运行批次状态"""
        self._queue.put(
            ("batch_status", (status, final_equity, total_return, message, self.batch_id))
        )

    def flush(self) -> None:
        """强制刷入队列中所有待写入数据（同步等待）"""
        # 先 drain 队列到临时列表
        items: list[tuple[str, tuple]] = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except Empty:
                break
        if items:
            self._do_flush(items)

    def close(self) -> None:
        """关闭写入器，刷入剩余数据"""
        self._closed = True
        self.flush()

    # ── 内部实现 ───────────────────────────────────────

    def _flush_loop(self) -> None:
        """后台线程：定时 flush"""
        while not self._closed:
            time.sleep(self.flush_interval)
            self.flush()

    def _do_flush(self, items: list[tuple[str, tuple]]) -> None:
        """执行批量写入"""
        if not items:
            return

        conn = sqlite3.connect(self.db_path)
        conn.execute("BEGIN")
        try:
            for item_type, data in items:
                if item_type == "equity":
                    conn.execute(
                        "INSERT INTO equity_curves (batch_id, trade_date, equity, cash, market_value) "
                        "VALUES (?,?,?,?,?)",
                        data,
                    )
                elif item_type == "position":
                    conn.execute(
                        "INSERT INTO positions (batch_id, ts_code, volume, avg_price, "
                        "market_value, pnl, pnl_pct, trade_date) VALUES (?,?,?,?,?,?,?,?)",
                        data,
                    )
                elif item_type == "trade":
                    conn.execute(
                        "INSERT INTO trades (batch_id, ts_code, direction, price, volume, "
                        "amount, pnl, trade_date, comment) VALUES (?,?,?,?,?,?,?,?,?)",
                        data,
                    )
                elif item_type == "batch_status":
                    conn.execute(
                        "UPDATE run_batches SET status=?, final_equity=?, total_return=?, "
                        "message=? WHERE id=?",
                        data,
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
