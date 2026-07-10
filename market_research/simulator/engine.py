# SPDX-License-Identifier: MIT
"""Batch backtesting engine for paper-trading simulator.

Loads strategies from market_research/strategies/, feeds daily bars from
history.db, processes signals, and writes results to simulator.db.
"""

from __future__ import annotations

import importlib
import inspect
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Type

from market_research.simulator.db_writer import DBWriter
from market_research.simulator.models import BarData
from market_research.simulator.strategy_base import SimStrategyBase

# 默认数据库路径
_DEFAULT_HISTORY_DB = str(
    Path(__file__).resolve().parent.parent.parent / "data" / "history.db"
)
_DEFAULT_SIM_DB = str(
    Path(__file__).resolve().parent.parent.parent / "data" / "simulator.db"
)

# 策略存放目录
_STRATEGIES_DIR = str(
    Path(__file__).resolve().parent.parent / "strategies"
)


def _infer_limit_pct(ts_code: str) -> float:
    """推断股票涨跌停限制

    - 688/300 开头: 科创/创业板 20%
    - 8 开头: 北交所 30%
    - ST: 5%
    - 默认: 10%
    """
    if ts_code.startswith("688") or ts_code.startswith("300"):
        return 0.20
    if ts_code.startswith("8"):
        return 0.30
    # ST 检测需要额外数据，暂不处理
    return 0.10


class BatchEngine:
    """批量回放引擎

    用法:
        engine = BatchEngine()
        engine.discover_strategies()  # 注册扫描到的策略
        engine.run_all()              # 运行所有已启用策略
    """

    def __init__(
        self,
        history_db: str = _DEFAULT_HISTORY_DB,
        sim_db: str = _DEFAULT_SIM_DB,
        strategies_dir: str = _STRATEGIES_DIR,
    ):
        self.history_db = history_db
        self.sim_db = sim_db
        self.strategies_dir = strategies_dir

    # ── 策略发现 ────────────────────────────────────────

    def discover_strategies(self) -> None:
        """扫描 strategies/ 目录，发现所有 SimStrategyBase 子类并写入 DB"""
        sys.path.insert(0, os.path.dirname(self.strategies_dir))
        strategy_dir = Path(self.strategies_dir)

        if not strategy_dir.exists():
            print(f"[engine] 策略目录不存在: {strategy_dir}")
            return

        py_files = sorted(strategy_dir.glob("*.py"))
        found = 0

        conn = sqlite3.connect(self.sim_db)
        # 确保表存在
        DBWriter(self.sim_db, 0).close()

        for fpath in py_files:
            if fpath.name == "__init__.py":
                continue

            mod_name = f"market_research.strategies.{fpath.stem}"
            try:
                mod = importlib.import_module(mod_name)
            except Exception as e:
                print(f"[engine] 导入 {mod_name} 失败: {e}")
                continue

            # 找 SimStrategyBase 的子类
            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if (
                    issubclass(obj, SimStrategyBase)
                    and obj is not SimStrategyBase
                    and not getattr(obj, "_abstract", False)
                ):
                    self._register_strategy(conn, obj)
                    found += 1

        conn.close()
        print(f"[engine] 发现 {found} 个策略")

    def _register_strategy(
        self, conn: sqlite3.Connection, cls: Type[SimStrategyBase]
    ) -> None:
        """将策略类写入 strategies 表（如已存在则跳过）"""
        cur = conn.cursor()
        cur.execute("SELECT id FROM strategies WHERE class_name=?", (cls.__name__,))
        if cur.fetchone():
            return  # 已注册

        import json

        cur.execute(
            "INSERT INTO strategies (name, class_name, parameters, enabled, description, author) "
            "VALUES (?,?,?,?,?,?)",
            (
                getattr(cls, "__doc__", cls.__name__).strip() if cls.__doc__ else cls.__name__,
                cls.__name__,
                json.dumps(cls.parameters, ensure_ascii=False) if cls.parameters else "[]",
                1,  # 默认启用
                (cls.__doc__ or "").strip(),
                cls.author,
            ),
        )
        conn.commit()
        print(f"[engine]   + 注册策略: {cls.__name__}")

    # ── 运行 ────────────────────────────────────────────

    def run_strategy(
        self,
        strategy_id: int,
        start_date: str = "20200101",
        end_date: str = "20261231",
        setting: dict | None = None,
    ) -> int:
        """运行单个策略回放

        Returns:
            batch_id
        """
        # 1. 加载策略类
        conn = sqlite3.connect(self.sim_db)
        cur = conn.cursor()
        cur.execute(
            "SELECT class_name, parameters FROM strategies WHERE id=?",
            (strategy_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"策略 ID {strategy_id} 不存在")
        class_name, params_json = row
        conn.close()

        # 从 strategies/ 目录找类
        cls = self._load_class(class_name)
        if cls is None:
            raise ImportError(f"无法加载策略类: {class_name}")

        # 2. 创建运行批次
        batch_id = self._create_batch(strategy_id, start_date, end_date, setting)

        # 3. 初始化策略
        strategy = cls(strategy_id=str(class_name), setting=setting or {})

        # 4. 创建 DBWriter
        writer = DBWriter(db_path=self.sim_db, batch_id=batch_id)
        try:
            writer.update_batch_status("running")
            self._run_backtest(strategy, writer, start_date, end_date)
            # 更新为完成
            final_equity = strategy.cash + sum(
                pos["volume"] * pos["avg_price"] for pos in strategy.positions.values()
            )
            total_return = (
                (final_equity - strategy.capital) / strategy.capital * 100
            )
            writer.update_batch_status(
                "completed",
                final_equity=final_equity,
                total_return=total_return,
            )
            writer.flush()
        except Exception as e:
            import traceback

            writer.update_batch_status("failed", message=f"{e}\n{traceback.format_exc()}")
            writer.flush()
            raise
        finally:
            writer.close()

        print(
            f"[engine] 策略 {class_name} 完成: "
            f"batch_id={batch_id}, "
            f"最终权益={final_equity:.2f}, "
            f"收益率={total_return:.2f}%"
        )
        return batch_id

    def run_all(self, setting: dict | None = None) -> List[int]:
        """运行所有已启用策略"""
        conn = sqlite3.connect(self.sim_db)
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM strategies WHERE enabled=1")
        strategies = cur.fetchall()
        conn.close()

        batch_ids = []
        for sid, sname in strategies:
            print(f"[engine] 运行策略: {sname} (id={sid})")
            bid = self.run_strategy(sid, setting=setting)
            batch_ids.append(bid)
        return batch_ids

    # ── 内部 ────────────────────────────────────────────

    def _load_class(self, class_name: str) -> Type[SimStrategyBase] | None:
        """从 strategies 目录按类名查找"""
        sys.path.insert(0, os.path.dirname(self.strategies_dir))
        strategy_dir = Path(self.strategies_dir)

        for fpath in sorted(strategy_dir.glob("*.py")):
            if fpath.name == "__init__.py":
                continue
            mod_name = f"market_research.strategies.{fpath.stem}"
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
        self, strategy_id: int, start_date: str, end_date: str, setting: dict | None
    ) -> int:
        """创建 run_batches 记录，返回 batch_id"""
        import json

        conn = sqlite3.connect(self.sim_db)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO run_batches (strategy_id, status, start_date, end_date, "
            "initial_capital, message) VALUES (?,?,?,?,?,?)",
            (
                strategy_id,
                "pending",
                start_date,
                end_date,
                (setting or {}).get("capital", 1_000_000),
                json.dumps(setting or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        batch_id = cur.lastrowid
        conn.close()
        return batch_id

    def _run_backtest(
        self,
        strategy: SimStrategyBase,
        writer: DBWriter,
        start_date: str,
        end_date: str,
    ) -> None:
        """执行回放循环"""

        # 获取日期列表
        all_dates = self._load_dates(start_date, end_date)
        if not all_dates:
            print("[engine] 指定日期范围内无数据")
            return

        # 初始化策略
        strategy.on_init()

        # 前一天的收盘价（用于涨跌停判断）
        prev_close_map: Dict[str, float] = {}

        for i, date in enumerate(all_dates):
            strategy.current_date = date

            # 加载当日所有 bars
            bars = self._load_bars(date)
            if not bars:
                continue

            # 调用策略
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
                    # 检查是否跌停（不能买）
                    if bar.close <= prev_close * (1 - limit_pct):
                        continue
                    needed = sig.price * sig.volume
                    if needed > strategy.cash:
                        # 尽可能买
                        max_vol = int(strategy.cash / sig.price / 100) * 100
                        if max_vol < 100:
                            continue
                        sig.volume = max_vol
                        needed = sig.price * sig.volume

                    strategy.cash -= needed
                    pos = strategy.positions.get(sig.ts_code, {"volume": 0, "avg_price": 0.0})
                    total_cost = pos["avg_price"] * pos["volume"] + needed
                    pos["volume"] += sig.volume
                    pos["avg_price"] = total_cost / pos["volume"] if pos["volume"] > 0 else 0
                    strategy.positions[sig.ts_code] = pos

                    writer.record_trade(
                        sig.ts_code, "buy", sig.price, sig.volume, needed, 0, date,
                        comment="",
                    )

                elif sig.action == "sell":
                    pos = strategy.positions.get(sig.ts_code)
                    if pos is None or pos["volume"] <= 0:
                        continue
                    # 检查是否涨停（不能卖）
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

                    writer.record_trade(
                        sig.ts_code, "sell", sig.price, sell_vol, proceeds, trade_pnl, date,
                        comment="",
                    )

            # 计算当日权益
            total_mv = 0.0
            for code, pos in list(strategy.positions.items()):
                bar = bars.get(code)
                if bar:
                    mkt_val = pos["volume"] * bar.close
                    cost_val = pos["volume"] * pos["avg_price"]
                    pnl = mkt_val - cost_val
                    pnl_pct = (pnl / cost_val * 100) if cost_val > 0 else 0.0
                    total_mv += mkt_val
                    writer.record_position(
                        date, code, pos["volume"], pos["avg_price"], mkt_val, pnl, pnl_pct,
                    )
                else:
                    total_mv += pos["volume"] * pos["avg_price"]

            equity = strategy.cash + total_mv
            writer.record_equity(date, equity, strategy.cash, total_mv)

            # 更新 prev_close
            for code, bar in bars.items():
                prev_close_map[code] = bar.close

        # 强制刷入
        writer.flush()

    def _load_dates(self, start_date: str, end_date: str) -> List[str]:
        """从 history.db 获取日期列表（升序）"""
        conn = sqlite3.connect(self.history_db)
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT trade_date FROM daily_bars "
            "WHERE trade_date>=? AND trade_date<=? "
            "ORDER BY trade_date",
            (start_date, end_date),
        )
        dates = [r[0] for r in cur.fetchall()]
        conn.close()
        return dates

    def _load_bars(self, date: str) -> Dict[str, BarData]:
        """加载某一天的日 K 线数据"""
        conn = sqlite3.connect(self.history_db)
        cur = conn.cursor()
        cur.execute(
            "SELECT ts_code, trade_date, open, high, low, close, volume, amount "
            "FROM daily_bars WHERE trade_date=?",
            (date,),
        )
        bars: Dict[str, BarData] = {}
        for row in cur.fetchall():
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
        conn.close()
        return bars
