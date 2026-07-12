"""TBot CLI entry point."""

from __future__ import annotations

import argparse
import json

import sys
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> None:
    """TBot CLI - 量化投研工具"""
    parser = argparse.ArgumentParser(
        prog="tbot",
        description="TBot — 基于 vnpy + DuckDB 的量化投研工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── serve ─────────────────────────────────────────────────────────
    sp = sub.add_parser("serve", help="启动 Web 服务")
    sp.add_argument("--port", type=int, default=8765, help="端口号 (default: 8765)")
    sp.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    # ── data ──────────────────────────────────────────────────────────
    data_p = sub.add_parser("data", help="数据管理")
    data_sub = data_p.add_subparsers(dest="data_command", required=True)
    data_sub.add_parser("update", help="一键更新所有数据")
    data_sub.add_parser("status", help="查看数据更新状态")

    # ── report ────────────────────────────────────────────────────────
    report_p = sub.add_parser("report", help="报告管理")
    report_sub = report_p.add_subparsers(dest="report_command", required=True)
    rb = report_sub.add_parser("build", help="生成静态报告 JSON")
    rb.add_argument("--db-path", default=None, help="tushare.db 路径（默认按约定推定）")
    rb.add_argument(
        "--out-dir",
        default=None,
        help="报告输出目录（默认 ./report）",
    )
    rb.add_argument("--window", type=int, default=240, help="资金流信号窗口 (default: 240)")
    rb.add_argument(
        "--concept-graph",
        action="store_true",
        help="同时生成概念聚合力导向图（需 Claude API key）",
    )

    # ── concept ───────────────────────────────────────────────────────
    concept_p = sub.add_parser("concept", help="涨停概念聚合 AI")
    concept_sub = concept_p.add_subparsers(dest="concept_command", required=True)
    cc = concept_sub.add_parser("cluster", help="AI 概念聚类，输出力导向图 JSON")
    cc.add_argument("--date", default=None, help="目标日期 YYYYMMDD（默认取最新交易日）")
    cc.add_argument(
        "--mode",
        default="full",
        choices=["concept", "theme", "full"],
        help="聚合模式 (default: full)",
    )
    cc.add_argument(
        "--output",
        default=None,
        help="输出 JSON 文件路径（默认打印到 stdout）",
    )

    # ── backtest ──────────────────────────────────────────────────────
    bt_p = sub.add_parser("backtest", help="回测管理")
    bt_sub = bt_p.add_subparsers(dest="backtest_command", required=True)
    bt_sub.add_parser("list", help="列出可用的回测策略")
    br = bt_sub.add_parser("run", help="运行回测策略")
    br.add_argument("--strategy", required=True, help="策略类名或注册 ID")
    br.add_argument("--start-date", default="", help="回测起始日 YYYYMMDD")
    br.add_argument("--end-date", default="", help="回测截止日 YYYYMMDD")
    br.add_argument("--capital", type=float, default=1_000_000, help="初始资金 (default: 1,000,000)")
    br.add_argument("--data-dir", default=None, help="DuckDB 数据目录（默认 ./data）")

    # ── db ────────────────────────────────────────────────────────────
    db_p = sub.add_parser("db", help="数据库工具")
    db_sub = db_p.add_subparsers(dest="db_command", required=True)
    dm = db_sub.add_parser("migrate", help="SQLite → DuckDB 迁移")
    dm.add_argument("--source", default=None, help="SQLite 源文件路径（默认 data/tushare.db）")
    dm.add_argument(
        "--data-dir",
        default=None,
        help="DuckDB 数据目录（默认 ./data）",
    )
    dm.add_argument(
        "--tables",
        nargs="*",
        default=None,
        help="仅迁移指定表（默认迁移所有已知表）",
    )
    dm.add_argument(
        "--daily-bars-source",
        default=None,
        help="日线行情 SQLite 源文件路径（默认 data/history.db）；传入后同时迁移 daily_bars",
    )
    dm.add_argument(
        "--skip-daily-bars",
        action="store_true",
        help="跳过 daily_bars 迁移",
    )

    args = parser.parse_args(argv)

    # ── dispatch ──────────────────────────────────────────────────────
    if args.command == "serve":
        _run_serve(args)
    elif args.command == "data":
        _run_data(args)
    elif args.command == "report":
        _run_report(args)
    elif args.command == "concept":
        _run_concept(args)
    elif args.command == "backtest":
        _run_backtest(args)
    elif args.command == "db":
        _run_db(args)


# ═══════════════════════════════════════════════════════════════════════
# serve
# ═══════════════════════════════════════════════════════════════════════


def _run_serve(args) -> None:
    """启动 Web 服务。"""
    import socket
    import webbrowser

    import uvicorn

    from tbot.api.app import create_app
    from tbot.config.settings import ConfigManager
    from tbot.engines.database.manager import DatabaseManager

    cfg = ConfigManager()
    data_dir = cfg.get("database.data_dir", "data")
    mgr = DatabaseManager(data_dir)
    app = create_app(mgr)

    port = args.port
    actual_port = port
    for p in range(port, port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                actual_port = p
                break
            except OSError:
                continue

    if not args.no_browser:
        webbrowser.open(f"http://127.0.0.1:{actual_port}/")

    print(f"[tbot] TBot API serve at http://127.0.0.1:{actual_port}")
    print("[tbot] Press Ctrl+C to stop")
    uvicorn.run(app, host="127.0.0.1", port=actual_port, log_level="info")


# ═══════════════════════════════════════════════════════════════════════
# data
# ═══════════════════════════════════════════════════════════════════════


def _run_data(args) -> None:
    """分发 data 子命令。"""
    from tbot.business.data_update.cli import handle_data_status, handle_data_update

    if args.data_command == "update":
        handle_data_update(args)
    elif args.data_command == "status":
        handle_data_status(args)


# ═══════════════════════════════════════════════════════════════════════
# report
# ═══════════════════════════════════════════════════════════════════════


def _run_report(args) -> None:
    """分发 report 子命令。"""
    from tbot.report_builder import ReportBuilder

    if args.report_command == "build":
        out_dir = args.out_dir or "report"
        builder = ReportBuilder()
        result = builder.build(
            db_path=args.db_path,  # kept for backward compat, ignored
            out_dir=out_dir,
            window=args.window,
            concept=args.concept_graph,
        )
        if result["success"]:
            print(f"[tbot] 报告生成完成: {result['out_dir']}")
            for f in result["files"]:
                print(f"  → {f}")
        else:
            print(f"[tbot] 报告生成失败")
            sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════
# concept
# ═══════════════════════════════════════════════════════════════════════


def _run_concept(args) -> None:
    """分发 concept 子命令。"""
    if args.concept_command == "cluster":
        _run_concept_cluster(args)


def _run_concept_cluster(args) -> None:
    """AI 概念聚类。"""
    from tbot.business.concept_cluster import ConceptClusterService
    from tbot.engines.ai import AIService

    svc = ConceptClusterService(ai=AIService())
    result = svc.cluster(date=args.date, mode=args.mode)

    if result is None:
        print("[tbot] 无数据或聚类失败", file=sys.stderr)
        sys.exit(1)

    output_path = args.output
    if output_path:
        dst = Path(output_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[tbot] 概念聚类结果已保存到 {dst}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════════════════════
# backtest
# ═══════════════════════════════════════════════════════════════════════


def _run_backtest(args) -> None:
    """分发 backtest 子命令。"""
    if args.backtest_command == "list":
        _list_strategies()
    elif args.backtest_command == "run":
        _run_backtest_strategy(args)


def _list_strategies() -> None:
    """列出 tbot.strategies 中可用的回测策略。"""
    import importlib
    import pkgutil

    from tbot.engines.backtest.strategy_base import SimStrategyBase

    strategies: list[dict[str, Any]] = []

    # 扫描 tbot.strategies 子模块
    pkg = importlib.import_module("tbot.strategies")
    for importer, modname, ispkg in pkgutil.walk_packages(
        pkg.__path__, prefix=f"{pkg.__name__}."
    ):
        try:
            mod = importlib.import_module(modname)
        except Exception:
            continue
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, SimStrategyBase)
                and attr is not SimStrategyBase
            ):
                params = getattr(attr, "parameters", [])
                param_summary = ", ".join(
                    f"{p.get('name', '?')}:{p.get('type', '?')}"
                    for p in params
                )
                strategies.append(
                    {
                        "id": attr_name,
                        "module": modname,
                        "author": getattr(attr, "author", ""),
                        "parameters": param_summary,
                    }
                )

    if not strategies:
        print("[tbot] 暂无可用的回测策略")
        return

    print(f"[tbot] 可用策略 ({len(strategies)}):")
    for s in strategies:
        print(f"  {s['id']}")
        print(f"      模块: {s['module']}")
        if s["author"]:
            print(f"      作者: {s['author']}")
        if s["parameters"]:
            print(f"      参数: {s['parameters']}")
        print()


def _run_backtest_strategy(args) -> None:
    """运行回测策略。"""
    import importlib

    from tbot.engines.backtest.adapter import DuckDuckLab
    from tbot.engines.backtest.strategy_base import SimStrategyBase

    data_dir = args.data_dir or _resolve_data_dir()

    # 动态导入策略类
    strategy_cls = _import_strategy_class(args.strategy)
    if strategy_cls is None:
        sys.exit(1)

    # 实例化
    strategy = strategy_cls(args.strategy)
    strategy.capital = args.capital
    strategy.cash = args.capital

    # 构造回测引擎
    lab = DuckDuckLab(data_dir)

    # 加载日线数据
    start = args.start_date or "20200101"
    end = args.end_date or "20251231"
    print(f"[tbot] 回测策略: {args.strategy}")
    print(f"[tbot] 数据目录: {data_dir}")
    print(f"[tbot] 日期范围: {start} ~ {end}")
    print(f"[tbot] 初始资金: {args.capital:,.0f}")

    # 获取所有交易日期
    from tbot.engines.database.manager import DatabaseManager
    from tbot.engines.database.service import DatabaseService

    mgr = DatabaseManager(data_dir)
    db_svc = DatabaseService(mgr)
    trade_dates_raw = db_svc.get_all_trade_dates()
    trade_dates = sorted(
        d["trade_date"]
        for d in trade_dates_raw
        if start <= d["trade_date"] <= end
    )

    if not trade_dates:
        print("[tbot] 无交易日期数据，请先更新数据", file=sys.stderr)
        sys.exit(1)

    print(f"[tbot] 交易日数: {len(trade_dates)}")

    # 回放逐日数据
    strategy.on_init()

    for dt in trade_dates:
        records = db_svc.get_limitup_by_date(dt)
        # 收集当日所有股票日线
        bars: dict[str, Any] = {}
        for r in records:
            ts_code = r["ts_code"]
            # 使用 DuckDuckLab 按需加载 bars
            import datetime

            dt_obj = datetime.datetime.strptime(dt, "%Y%m%d")
            bar_list = lab.load_bar_data(
                ts_code, "1d", dt_obj, dt_obj
            )
            if bar_list:
                bars[ts_code] = bar_list[0]

        strategy.current_date = dt
        strategy.on_bars(dt, bars)

        # 处理信号（目前模拟：仅打印）
        signals = strategy.get_signals()
        for sig in signals:
            print(
                f"  [{dt}] {sig.action.upper()} "
                f"{sig.ts_code} @ {sig.price} x {sig.volume}"
            )

    print(f"[tbot] 回测完成")


def _import_strategy_class(name: str) -> type[SimStrategyBase] | None:
    """按类名或全限定名导入策略类。"""
    import importlib

    from tbot.engines.backtest.strategy_base import SimStrategyBase

    # 尝试按全限定名导入
    if "." in name:
        parts = name.rsplit(".", 1)
        try:
            mod = importlib.import_module(parts[0])
            cls = getattr(mod, parts[1], None)
            if cls is not None and isinstance(cls, type) and issubclass(cls, SimStrategyBase):
                return cls
        except Exception:
            pass
        print(f"[tbot] 无法导入策略: {name}", file=sys.stderr)
        return None

    # 尝试从 tbot.strategies 下查找
    import pkgutil

    pkg = importlib.import_module("tbot.strategies")
    for importer, modname, ispkg in pkgutil.walk_packages(
        pkg.__path__, prefix=f"{pkg.__name__}."
    ):
        try:
            mod = importlib.import_module(modname)
            cls = getattr(mod, name, None)
            if cls is not None and isinstance(cls, type) and issubclass(cls, SimStrategyBase) and cls is not SimStrategyBase:
                return cls
        except Exception:
            continue

    print(f"[tbot] 未找到策略 '{name}'（请先注册到 tbot.strategies 下）", file=sys.stderr)
    return None


# ═══════════════════════════════════════════════════════════════════════
# db
# ═══════════════════════════════════════════════════════════════════════


def _run_db(args) -> None:
    """分发 db 子命令。"""
    if args.db_command == "migrate":
        _run_db_migrate(args)


def _run_db_migrate(args) -> None:
    """SQLite → DuckDB 迁移。"""
    source = args.source or str(
        Path(__file__).resolve().parent.parent.parent / "data" / "tushare.db"
    )
    data_dir = args.data_dir or _resolve_data_dir()

    source_path = Path(source)
    if not source_path.exists():
        print(f"[tbot] SQLite 源文件不存在: {source_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[tbot] 迁移: {source_path} → {data_dir}/")
    _migrate_sqlite_to_duckdb(source_path, data_dir, args.tables)

    # 同时迁移 daily_bars（如果指定了来源且未跳过）
    if args.skip_daily_bars:
        print("[tbot] 已跳过 daily_bars 迁移")
    else:
        daily_src = args.daily_bars_source
        if daily_src is None:
            daily_src = str(
                Path(__file__).resolve().parent.parent.parent / "data" / "history.db"
            )
        daily_path = Path(daily_src)
        if daily_path.exists():
            print(f"[tbot] 迁移 daily_bars: {daily_path} → {data_dir}/")
            _migrate_daily_bars(daily_path, data_dir)
        else:
            print(f"[tbot] 未找到 daily_bars 源文件: {daily_path}（跳过日线迁移）")


def _migrate_sqlite_to_duckdb(
    source: Path,
    data_dir: str | Path,
    tables: list[str] | None = None,
) -> None:
    """执行 SQLite → DuckDB 迁移。

    从 tushare.db（SQLite）读取已知业务表，逐表写入 DuckDB market_overview_a 库。
    """
    import sqlite3

    import duckdb

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    dst_path = data_dir / "market_overview_a.db"

    # 已知待迁移表（可根据需要扩展）
    KNOWN_TABLES = {
        "limit_up_pool": "涨停池",
        "stock_company": "上市公司基本信息",
        "ths_member": "同花顺概念成员",
        "ths_index": "同花顺概念指数",
        "ind_fundflow": "行业资金流",
        "mkt_fundflow": "市场资金流",
        "trade_cal": "交易日历",
    }

    tables_to_migrate = tables or list(KNOWN_TABLES.keys())

    src_conn = sqlite3.connect(str(source))
    try:
        src_cur = src_conn.cursor()

        dst_conn = duckdb.connect(str(dst_path))
        try:
            dst_conn.execute("SET threads TO 4")

            for table in tables_to_migrate:
                label = KNOWN_TABLES.get(table, table)
                try:
                    # 检查源表是否存在
                    src_cur.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name=?",
                        (table,),
                    )
                    if not src_cur.fetchone():
                        print(f"  ~ 跳过 {table}（源库中不存在）")
                        continue

                    # 读取全部数据
                    src_cur.execute(f"SELECT * FROM [{table}]")
                    rows = src_cur.fetchall()
                    if not rows:
                        print(f"  ~ 跳过 {table}（无数据）")
                        continue

                    # 获取列名
                    col_names = [desc[0] for desc in src_cur.description]

                    # 在 DuckDB 中创建表（所有列用 VARCHAR 类型，DuckDB 自动推断）
                    col_defs = ", ".join(f'"{c}" VARCHAR' for c in col_names)
                    dst_conn.execute(f'DROP TABLE IF EXISTS "{table}"')
                    dst_conn.execute(
                        f'CREATE TABLE "{table}" ({col_defs})'
                    )

                    # 批量写入（每 2000 行一批）
                    BATCH = 2000
                    inserted = 0
                    for batch_start in range(0, len(rows), BATCH):
                        batch = rows[batch_start:batch_start + BATCH]
                        values_list: list[str] = []
                        for row in batch:
                            vals = ", ".join(
                                _duckdb_literal(v) for v in row
                            )
                            values_list.append(f"({vals})")
                        dst_conn.execute(
                            f'INSERT INTO "{table}" VALUES '
                            + ",\n".join(values_list)
                        )
                        inserted += len(batch)

                    print(f"  ✓ {table} ({label}): {inserted} 行")
                except Exception as e:
                    print(f"  ✗ {table} 迁移失败: {e}", file=sys.stderr)

            dst_conn.close()
        finally:
            pass  # duckdb connection auto-closes on scope exit

        src_conn.close()
    finally:
        pass

    print(f"\n[tbot] 迁移完成 → {dst_path}")


def _migrate_daily_bars(
    source: Path,
    data_dir: str | Path,
) -> None:
    """迁移 SQLite history.db -> DuckDB market_a.db 的 daily_bars 表。

    使用批量 INSERT 提高迁移速度，自动分 chunk 写入。
    """
    import sqlite3
    import math

    import duckdb

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    dst_path = data_dir / "market_a.db"

    CHUNK = 500_000  # 每批 50 万行

    src_conn = sqlite3.connect(str(source))
    try:
        src_cur = src_conn.cursor()
        src_cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='daily_bars'"
        )
        if not src_cur.fetchone():
            print("  ~ 源库中不存在 daily_bars 表")
            return

        # 获取总行数
        src_cur.execute("SELECT count(*) FROM daily_bars")
        total = src_cur.fetchone()[0]
        if total == 0:
            print("  ~ daily_bars 无数据")
            return

        # 获取列信息
        src_cur.execute("PRAGMA table_info(daily_bars)")
        cols = [r[1] for r in src_cur.fetchall()]
        col_list = ", ".join(f'"{c}"' for c in cols)

        dst_conn = duckdb.connect(str(dst_path))
        try:
            dst_conn.execute("SET threads TO 4")
            dst_conn.execute("DROP TABLE IF EXISTS daily_bars")

            # 用列名建表（所有列用 TEXT 类型，后续写入 DuckDB 自动推断）
            col_type_pairs = ", ".join(f'"{c}" TEXT' for c in cols)
            dst_conn.execute(
                f"CREATE TABLE daily_bars ({col_type_pairs})"
            )

            offset = 0
            while offset < total:
                src_cur.execute(
                    f"SELECT * FROM daily_bars ORDER BY ts_code, trade_date "
                    f"LIMIT {CHUNK} OFFSET {offset}"
                )
                rows = src_cur.fetchall()
                if not rows:
                    break

                # 构造批量 INSERT
                values_sql: list[str] = []
                for row in rows:
                    vals = ", ".join(_duckdb_literal(v) for v in row)
                    values_sql.append(f"({vals})")
                dst_conn.execute(
                    f'INSERT INTO "daily_bars" ({col_list}) VALUES '
                    + ",\n".join(values_sql)
                )
                offset += len(rows)
                pct = min(100, round(offset / total * 100))
                print(f"  daily_bars: {offset}/{total} ({pct}%)", end="\r", flush=True)

            print()
            print(f"  ✓ daily_bars: {total} 行")

            dst_conn.close()
        finally:
            pass

        src_conn.close()
    finally:
        pass

    print(f"\n[tbot] 日线迁移完成 → {dst_path}")


def _duckdb_literal(value: Any) -> str:
    """将 Python 值转为 DuckDB SQL 字面量。"""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        # 浮点数 NaN/Inf 转 NULL
        import math

        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return "NULL"
        return repr(value)
    if isinstance(value, str):
        # 转义单引号
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    if isinstance(value, bytes):
        escaped = value.decode("utf-8", errors="replace").replace("'", "''")
        return f"'{escaped}'"
    return repr(value)


# ═══════════════════════════════════════════════════════════════════════
# 路径工具
# ═══════════════════════════════════════════════════════════════════════


def _resolve_data_dir() -> str:
    """解析 data 目录路径（与 DatabaseManager 默认一致）。"""
    from tbot.config.settings import ConfigManager

    cfg = ConfigManager()
    return cfg.get("database.data_dir", "data")


if __name__ == "__main__":
    main()
