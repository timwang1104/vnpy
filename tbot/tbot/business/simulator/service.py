# SPDX-License-Identifier: MIT
"""SimulatorService — 模拟盘业务逻辑。

基于 engines/backtest（策略基类、回测模型）和 engines/database（DuckDB 连接管理）实现。
为 router 层提供策略注册、回测运行、权益/持仓/成交查询。

Usage:
    from tbot.engines.database.manager import DatabaseManager
    from tbot.business.simulator.service import SimulatorService

    mgr = DatabaseManager("data")
    svc = SimulatorService(mgr)
    strategies = svc.get_strategies()
    result = svc.run_strategy(1, start_date="20240101", end_date="20241231")
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from tbot.engines.backtest.models import BarData
from tbot.engines.backtest.strategy_base import SimStrategyBase
from tbot.engines.database.manager import DatabaseManager

logger = logging.getLogger("tbot.simulator")


# ── 模块级工具 ────────────────────────────────────────────────


def _infer_limit_pct(ts_code: str) -> float:
    """推断股票涨跌停限制。

    - 688/300 开头: 科创/创业板 20%
    - 8 开头: 北交所 30%
    - 默认: 10%
    """
    if ts_code.startswith("688") or ts_code.startswith("300"):
        return 0.20
    if ts_code.startswith("8"):
        return 0.30
    return 0.10


def _fetch_dicts(result: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """将 DuckDB execute() 结果转换为 list[dict]。"""
    columns = [desc[0] for desc in result.description]
    rows = result.fetchall()
    return [dict(zip(columns, row)) for row in rows]


# ── 服务类 ────────────────────────────────────────────────────


class SimulatorService:
    """模拟盘业务服务。

    职责:
        - 策略注册与查询（research.strategies 表）
        - 回测执行（加载 K 线 → 驱动策略 → 处理信号 → 写入结果）
        - 回测结果查询（权益曲线、持仓、成交记录）

    Thread-safety:
        非线程安全。每个线程/协程应使用独立的 SimulatorService 实例，
        或由调用者在外部加锁。
    """

    def __init__(
        self,
        db_mgr: DatabaseManager,
        strategies_dir: str | Path | None = None,
    ) -> None:
        """初始化。

        Args:
            db_mgr: DatabaseManager 实例，用于连接 market_a、research 等分库。
            strategies_dir: 策略代码目录。默认使用 tbot/tbot/strategies/。
        """
        self._db = db_mgr
        if strategies_dir is None:
            strategies_dir = Path(__file__).resolve().parent.parent.parent / "strategies"
        self._strategies_dir = Path(strategies_dir)

    # ── 策略管理 ──────────────────────────────────────────────

    def get_strategies(self) -> list[dict[str, Any]]:
        """返回所有已注册策略。"""
        conn = self._db.get_research()
        try:
            result = conn.execute(
                "SELECT id, name, class_name, parameters, enabled, description, "
                "author, created_at FROM strategies ORDER BY id"
            )
            strategies = _fetch_dicts(result)
            for s in strategies:
                s["parameters"] = (
                    json.loads(s["parameters"]) if s.get("parameters") else []
                )
                s["enabled"] = bool(s["enabled"])
            return strategies
        finally:
            conn.close()

    def get_strategy(self, strategy_id: int) -> dict[str, Any] | None:
        """返回单条策略详情（含最近一次运行批次信息）。"""
        conn = self._db.get_research()
        try:
            row = conn.execute(
                "SELECT id, name, class_name, parameters, enabled, description, "
                "author, created_at, updated_at FROM strategies WHERE id = ?",
                [strategy_id],
            ).fetchone()
            if not row:
                return None

            columns = [
                "id", "name", "class_name", "parameters", "enabled",
                "description", "author", "created_at", "updated_at",
            ]
            strategy = dict(zip(columns, row))
            strategy["parameters"] = (
                json.loads(strategy["parameters"]) if strategy.get("parameters") else []
            )
            strategy["enabled"] = bool(strategy["enabled"])

            # 查最近一次运行批次
            batch = conn.execute(
                "SELECT id, status, start_date, end_date, initial_capital, "
                "final_equity, total_return, created_at "
                "FROM run_batches WHERE strategy_id = ? ORDER BY id DESC LIMIT 1",
                [strategy_id],
            ).fetchone()

            strategy["latest_batch"] = None
            if batch:
                bc = [
                    "id", "status", "start_date", "end_date",
                    "initial_capital", "final_equity", "total_return", "run_at",
                ]
                strategy["latest_batch"] = dict(zip(bc, batch))

            return strategy
        finally:
            conn.close()

    def discover_strategies(self) -> int:
        """扫描 strategies/ 目录，发现 SimStrategyBase 子类并注册到数据库。

        Returns:
            新注册的策略数量（已存在的跳过不计）。
        """
        sys.path.insert(0, str(self._strategies_dir.parent))
        found = 0

        for fpath in sorted(self._strategies_dir.glob("*.py")):
            if fpath.name == "__init__.py":
                continue

            mod_name = f"tbot.strategies.{fpath.stem}"
            try:
                mod = importlib.import_module(mod_name)
            except Exception as e:
                logger.warning("导入 %s 失败: %s", mod_name, e)
                continue

            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if (
                    issubclass(obj, SimStrategyBase)
                    and obj is not SimStrategyBase
                    and not getattr(obj, "_abstract", False)
                ):
                    self._register_strategy(obj)
                    found += 1

        logger.info("发现 %d 个策略", found)
        return found

    def _register_strategy(self, cls: type[SimStrategyBase]) -> None:
        """将策略类写入 strategies 表（如已存在则跳过）。"""
        conn = self._db.get_research()
        try:
            row = conn.execute(
                "SELECT id FROM strategies WHERE class_name = ?",
                [cls.__name__],
            ).fetchone()
            if row:
                return  # 已注册

            conn.execute(
                "INSERT INTO strategies "
                "(name, class_name, parameters, enabled, description, author, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    cls.__doc__.strip() if cls.__doc__ else cls.__name__,
                    cls.__name__,
                    json.dumps(cls.parameters, ensure_ascii=False)
                    if cls.parameters else "[]",
                    1,  # 默认启用
                    (cls.__doc__ or "").strip(),
                    cls.author,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ],
            )
            logger.info("注册策略: %s", cls.__name__)
        finally:
            conn.close()

    # ── 回测执行 ──────────────────────────────────────────────

    def run_strategy(
        self,
        strategy_id: int,
        start_date: str = "20200101",
        end_date: str = "20261231",
        setting: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """运行单个策略回测。

        Args:
            strategy_id: 策略 ID（research.strategies 表主键）。
            start_date: 起始日期 YYYYMMDD。
            end_date: 截止日期 YYYYMMDD。
            setting: 策略参数（可覆写策略类声明的默认参数，
                     也支持 ``capital`` 键设定初始资金）。

        Returns:
            dict::

                {"status": "ok"|"error",
                 "batch_id": int,
                 "final_equity": float,    # 仅 ok
                 "total_return": float,    # 仅 ok
                 "message": str}           # 仅 error
        """
        # 1. 加载策略类
        conn = self._db.get_research()
        try:
            row = conn.execute(
                "SELECT class_name FROM strategies WHERE id = ?",
                [strategy_id],
            ).fetchone()
            if not row:
                raise ValueError(f"策略 ID {strategy_id} 不存在")
            class_name = row[0]
        finally:
            conn.close()

        cls = self._load_class(class_name)
        if cls is None:
            raise ImportError(f"无法加载策略类: {class_name}")

        setting = setting or {}
        capital = setting.get("capital", 1_000_000)
        batch_id = self._create_batch(
            strategy_id, start_date, end_date, capital, setting,
        )

        # 2. 初始化策略实例
        strategy = cls(strategy_id=str(class_name), setting=setting)

        # 3. 执行回测
        try:
            self._update_batch_status(batch_id, "running")
            self._run_backtest(strategy, batch_id, start_date, end_date)

            final_equity = strategy.cash + sum(
                pos["volume"] * pos["avg_price"]
                for pos in strategy.positions.values()
            )
            total_return = (
                (final_equity - strategy.capital) / strategy.capital * 100
            )

            self._update_batch_completion(
                batch_id, "completed", final_equity, total_return,
            )

            logger.info(
                "策略 %s batch=%d 完成: equity=%.2f return=%.2f%%",
                class_name, batch_id, final_equity, total_return,
            )

            return {
                "status": "ok",
                "batch_id": batch_id,
                "final_equity": round(final_equity, 2),
                "total_return": round(total_return, 2),
            }

        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            self._update_batch_completion(batch_id, "failed", message=f"{e}\n{tb}")
            logger.error("策略 %s batch=%d 失败: %s", class_name, batch_id, e)
            return {
                "status": "error",
                "batch_id": batch_id,
                "message": str(e),
            }

    def _load_class(self, class_name: str) -> type[SimStrategyBase] | None:
        """从 strategies 目录按类名查找策略类。"""
        sys.path.insert(0, str(self._strategies_dir.parent))

        for fpath in sorted(self._strategies_dir.glob("*.py")):
            if fpath.name == "__init__.py":
                continue
            mod_name = f"tbot.strategies.{fpath.stem}"
            try:
                mod = importlib.import_module(mod_name)
            except Exception:
                continue
            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if (
                    issubclass(obj, SimStrategyBase)
                    and obj is not SimStrategyBase
                    and obj.__name__ == class_name
                ):
                    return obj
        return None

    def _create_batch(
        self,
        strategy_id: int,
        start_date: str,
        end_date: str,
        capital: float,
        setting: dict[str, Any] | None,
    ) -> int:
        """创建 run_batches 记录并返回 batch_id。"""
        conn = self._db.get_research()
        try:
            result = conn.execute(
                "INSERT INTO run_batches "
                "(strategy_id, status, start_date, end_date, "
                "initial_capital, message, created_at) "
                "VALUES (?, 'pending', ?, ?, ?, ?, ?) "
                "RETURNING id",
                [
                    strategy_id,
                    start_date,
                    end_date,
                    capital,
                    json.dumps(setting or {}, ensure_ascii=False),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ],
            )
            return result.fetchone()[0]
        finally:
            conn.close()

    def _run_backtest(
        self,
        strategy: SimStrategyBase,
        batch_id: int,
        start_date: str,
        end_date: str,
    ) -> None:
        """执行回测主循环（单连接批处理写入）。"""
        # 获取交易日历
        all_dates = self._load_dates(start_date, end_date)
        if not all_dates:
            logger.warning("日期范围 %s ~ %s 内无数据", start_date, end_date)
            return

        strategy.on_init()

        prev_close_map: dict[str, float] = {}
        conn = self._db.get_research()

        try:
            for date in all_dates:
                strategy.current_date = date

                bars = self._load_bars(date)
                if not bars:
                    continue

                strategy.on_bars(date, bars)

                # 处理信号
                signals = strategy.get_signals()
                for sig in signals:
                    bar = bars.get(sig.ts_code)
                    if bar is None:
                        continue

                    prev_close = prev_close_map.get(sig.ts_code, bar.close)
                    limit_pct = _infer_limit_pct(sig.ts_code)

                    if sig.action == "buy":
                        # 跌停不买
                        if bar.close <= prev_close * (1 - limit_pct):
                            continue
                        needed = sig.price * sig.volume
                        if needed > strategy.cash:
                            max_vol = int(strategy.cash / sig.price / 100) * 100
                            if max_vol < 100:
                                continue
                            sig.volume = max_vol
                            needed = sig.price * sig.volume

                        strategy.cash -= needed
                        pos = strategy.positions.get(
                            sig.ts_code, {"volume": 0, "avg_price": 0.0},
                        )
                        total_cost = pos["avg_price"] * pos["volume"] + needed
                        pos["volume"] += sig.volume
                        pos["avg_price"] = (
                            total_cost / pos["volume"] if pos["volume"] > 0 else 0
                        )
                        strategy.positions[sig.ts_code] = pos

                        conn.execute(
                            "INSERT INTO trades "
                            "(batch_id, ts_code, direction, price, volume, "
                            "amount, pnl, trade_date) "
                            "VALUES (?, ?, 'buy', ?, ?, ?, 0, ?)",
                            [batch_id, sig.ts_code, sig.price, sig.volume,
                             needed, date],
                        )

                    elif sig.action == "sell":
                        pos = strategy.positions.get(sig.ts_code)
                        if pos is None or pos["volume"] <= 0:
                            continue
                        # 涨停不卖
                        if bar.close >= prev_close * (1 + limit_pct):
                            continue
                        sell_vol = min(sig.volume, pos["volume"])
                        proceeds = sig.price * sell_vol
                        cost = pos["avg_price"] * sell_vol
                        trade_pnl = proceeds - cost
                        strategy.cash += proceeds

                        pos["volume"] -= sell_vol
                        if pos["volume"] <= 0:
                            strategy.positions.pop(sig.ts_code, None)
                        else:
                            strategy.positions[sig.ts_code] = pos

                        conn.execute(
                            "INSERT INTO trades "
                            "(batch_id, ts_code, direction, price, volume, "
                            "amount, pnl, trade_date) "
                            "VALUES (?, ?, 'sell', ?, ?, ?, ?, ?)",
                            [batch_id, sig.ts_code, sig.price, sell_vol,
                             proceeds, trade_pnl, date],
                        )

                # 计算当日权益
                total_mv = 0.0
                for code, pos in list(strategy.positions.items()):
                    bar = bars.get(code)
                    if bar:
                        mkt_val = pos["volume"] * bar.close
                        cost_val = pos["volume"] * pos["avg_price"]
                        pnl = mkt_val - cost_val
                        pnl_pct = (
                            (pnl / cost_val * 100) if cost_val > 0 else 0.0
                        )
                        total_mv += mkt_val
                        conn.execute(
                            "INSERT INTO positions "
                            "(batch_id, ts_code, trade_date, volume, avg_price, "
                            "market_value, pnl, pnl_pct) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            [batch_id, code, date, pos["volume"],
                             pos["avg_price"], mkt_val, pnl, pnl_pct],
                        )
                    else:
                        total_mv += pos["volume"] * pos["avg_price"]

                equity = strategy.cash + total_mv
                conn.execute(
                    "INSERT INTO equity_curves "
                    "(batch_id, trade_date, equity, cash, market_value) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [batch_id, date, equity, strategy.cash, total_mv],
                )

                # 更新前收盘价
                for code, bar in bars.items():
                    prev_close_map[code] = bar.close

        finally:
            conn.close()

    def _load_dates(self, start_date: str, end_date: str) -> list[str]:
        """从 market_a 获取交易日期列表（升序）。"""
        conn = self._db.get_market()
        try:
            result = conn.execute(
                "SELECT DISTINCT trade_date FROM daily_bars "
                "WHERE trade_date >= ? AND trade_date <= ? "
                "ORDER BY trade_date",
                [start_date, end_date],
            )
            return [row[0] for row in result.fetchall()]
        finally:
            conn.close()

    def _load_bars(self, date: str) -> dict[str, BarData]:
        """加载某一天的日 K 线数据。"""
        conn = self._db.get_market()
        try:
            result = conn.execute(
                "SELECT ts_code, trade_date, open, high, low, close, volume, amount "
                "FROM daily_bars WHERE trade_date = ?",
                [date],
            )
            bars: dict[str, BarData] = {}
            for row in result.fetchall():
                bars[row[0]] = BarData(
                    ts_code=row[0],
                    trade_date=row[1],
                    open=float(row[2] or 0),
                    high=float(row[3] or 0),
                    low=float(row[4] or 0),
                    close=float(row[5] or 0),
                    volume=float(row[6] or 0),
                    amount=float(row[7] or 0),
                )
            return bars
        finally:
            conn.close()

    # ── 批次状态管理 ──────────────────────────────────────────

    def _update_batch_status(self, batch_id: int, status: str) -> None:
        """更新运行批次状态。"""
        conn = self._db.get_research()
        try:
            conn.execute(
                "UPDATE run_batches SET status = ? WHERE id = ?",
                [status, batch_id],
            )
        finally:
            conn.close()

    def _update_batch_completion(
        self,
        batch_id: int,
        status: str,
        final_equity: float | None = None,
        total_return: float | None = None,
        message: str = "",
    ) -> None:
        """更新运行批次为终态（completed / failed）。"""
        conn = self._db.get_research()
        try:
            conn.execute(
                "UPDATE run_batches SET status = ?, final_equity = ?, "
                "total_return = ?, message = ? WHERE id = ?",
                [status, final_equity, total_return, message, batch_id],
            )
        finally:
            conn.close()

    # ── 结果查询 ──────────────────────────────────────────────

    def get_equity(self, strategy_id: int) -> dict[str, Any]:
        """查询最新批次的权益曲线。"""
        conn = self._db.get_research()
        try:
            row = conn.execute(
                "SELECT id FROM run_batches "
                "WHERE strategy_id = ? ORDER BY id DESC LIMIT 1",
                [strategy_id],
            ).fetchone()
            if not row:
                return {"dates": [], "equity": [], "cash": [], "market_value": []}

            result = conn.execute(
                "SELECT trade_date, equity, cash, market_value "
                "FROM equity_curves WHERE batch_id = ? ORDER BY trade_date",
                [row[0]],
            )
            rows = result.fetchall()
            return {
                "dates": [r[0] for r in rows],
                "equity": [r[1] for r in rows],
                "cash": [r[2] for r in rows],
                "market_value": [r[3] for r in rows],
            }
        finally:
            conn.close()

    def get_positions(self, strategy_id: int) -> dict[str, Any]:
        """查询最新批次的持仓记录。"""
        conn = self._db.get_research()
        try:
            row = conn.execute(
                "SELECT id FROM run_batches "
                "WHERE strategy_id = ? ORDER BY id DESC LIMIT 1",
                [strategy_id],
            ).fetchone()
            if not row:
                return {"positions": []}

            result = conn.execute(
                "SELECT ts_code, volume, avg_price, market_value, pnl, pnl_pct, trade_date "
                "FROM positions WHERE batch_id = ? ORDER BY market_value DESC",
                [row[0]],
            )
            rows = result.fetchall()
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
                ],
            }
        finally:
            conn.close()

    def get_trades(self, strategy_id: int) -> dict[str, Any]:
        """查询最新批次的交易记录。"""
        conn = self._db.get_research()
        try:
            row = conn.execute(
                "SELECT id FROM run_batches "
                "WHERE strategy_id = ? ORDER BY id DESC LIMIT 1",
                [strategy_id],
            ).fetchone()
            if not row:
                return {"trades": []}

            result = conn.execute(
                "SELECT ts_code, direction, price, volume, amount, pnl, trade_date, comment "
                "FROM trades WHERE batch_id = ? ORDER BY trade_date, id",
                [row[0]],
            )
            rows = result.fetchall()
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
                ],
            }
        finally:
            conn.close()
