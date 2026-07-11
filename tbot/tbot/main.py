"""TBot CLI entry point."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    """TBot CLI - 量化投研工具"""
    parser = argparse.ArgumentParser(
        prog="tbot",
        description="TBot — 基于 vnpy + DuckDB 的量化投研工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # serve
    sp = sub.add_parser("serve", help="启动 Web 服务")
    sp.add_argument("--port", type=int, default=8765, help="端口号 (default: 8765)")
    sp.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    # data
    data_p = sub.add_parser("data", help="数据管理")
    data_sub = data_p.add_subparsers(dest="data_command", required=True)
    data_sub.add_parser("update", help="一键更新所有数据")
    data_sub.add_parser("status", help="查看数据状态")

    # report
    report_p = sub.add_parser("report", help="报告管理")
    report_sub = report_p.add_subparsers(dest="report_command", required=True)
    report_sub.add_parser("build", help="生成静态报告")

    # concept
    concept_p = sub.add_parser("concept", help="概念聚合 AI")
    concept_sub = concept_p.add_subparsers(dest="concept_command", required=True)
    cc = concept_sub.add_parser("cluster", help="AI 概念聚类")
    cc.add_argument("--date", default=None, help="目标日期 YYYYMMDD")

    # backtest
    bt_p = sub.add_parser("backtest", help="回测管理")
    bt_sub = bt_p.add_subparsers(dest="backtest_command", required=True)
    bt_sub.add_parser("list", help="列出已注册策略")
    bt_sub.add_parser("run", help="运行策略（待实现）")

    # db
    db_p = sub.add_parser("db", help="数据库工具")
    db_sub = db_p.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("migrate", help="SQLite → DuckDB 迁移")

    args = parser.parse_args(argv)

    if args.command == "serve":
        print("[tbot] tbot serve — 待实现")
    elif args.command == "data":
        print(f"[tbot] tbot data {args.data_command} — 待实现")
    elif args.command == "report":
        print(f"[tbot] tbot report {args.report_command} — 待实现")
    elif args.command == "concept":
        print(f"[tbot] tbot concept {args.concept_command} — 待实现")
    elif args.command == "backtest":
        print(f"[tbot] tbot backtest {args.backtest_command} — 待实现")
    elif args.command == "db":
        print(f"[tbot] tbot db {args.db_command} — 待实现")


if __name__ == "__main__":
    main()
