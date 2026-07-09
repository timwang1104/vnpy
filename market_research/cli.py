# SPDX-License-Identifier: MIT
"""CLI — build / serve / run 三命令。

用法:
    market_research build [--db PATH] [--window N] [--date YYYYMMDD] [--out DIR]
    market_research serve [--dir DIR] [--port PORT] [--no-browser]
    market_research run   [build 参数] [--port PORT] [--no-browser]
"""
from __future__ import annotations

import argparse
import sys

from market_research.builder import build
from market_research.server import serve


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="market_research",
        description="Market research report generator and server for vnpy data assets",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # build
    build_p = sub.add_parser("build", help="Generate report directory")
    build_p.add_argument("--db", default="data/tushare.db",
                         help="Path to tushare.db (default: data/tushare.db)")
    build_p.add_argument("--window", type=int, default=240,
                         help="Rolling window in trading days (default: 240)")
    build_p.add_argument("--date", default=None,
                         help="Override snapshot date YYYYMMDD")
    build_p.add_argument("--out", default="report",
                         help="Output report directory (default: report)")

    # serve
    serve_p = sub.add_parser("serve", help="Start HTTP server for report directory")
    serve_p.add_argument("--dir", default="report",
                         help="Report directory to serve (default: report)")
    serve_p.add_argument("--port", type=int, default=8765,
                         help="Starting port, incremented if busy (default: 8765)")
    serve_p.add_argument("--no-browser", action="store_true",
                         help="Do not open browser automatically")
    serve_p.add_argument("--db-path", default=None,
                         help="Path to tushare.db for live API (default: none, static only)")

    # run (build + serve)
    run_p = sub.add_parser("run", help="Build + serve in one command")
    run_p.add_argument("--db", default="data/tushare.db",
                       help="Path to tushare.db (default: data/tushare.db)")
    run_p.add_argument("--window", type=int, default=240,
                       help="Rolling window in trading days (default: 240)")
    run_p.add_argument("--date", default=None,
                       help="Override snapshot date YYYYMMDD")
    run_p.add_argument("--out", default="report",
                       help="Output report directory (default: report)")
    run_p.add_argument("--port", type=int, default=8765,
                       help="Starting port, incremented if busy (default: 8765)")
    run_p.add_argument("--no-browser", action="store_true",
                       help="Do not open browser automatically")
    run_p.add_argument("--db-path", default=None,
                       help="Path to tushare.db for live API (default: none)")

    args = parser.parse_args(argv)

    if args.command == "build":
        try:
            build(args.db, args.out, window=args.window, date=args.date)
        except FileNotFoundError as e:
            print(f"[market_research] 错误: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"[market_research] Build 失败: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "serve":
        try:
            serve(args.dir, port=args.port, no_browser=args.no_browser,
                  db_path=args.db_path)
        except FileNotFoundError as e:
            print(f"[market_research] 错误: {e}", file=sys.stderr)
            sys.exit(1)
        except KeyboardInterrupt:
            print("\n[market_research] 服务已停止")

    elif args.command == "run":
        try:
            build(args.db, args.out, window=args.window, date=args.date)
            print()
            serve(args.out, port=args.port, no_browser=args.no_browser,
                  db_path=args.db_path)
        except FileNotFoundError as e:
            print(f"[market_research] 错误: {e}", file=sys.stderr)
            sys.exit(1)
        except KeyboardInterrupt:
            print("\n[market_research] 服务已停止")
