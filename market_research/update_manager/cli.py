# SPDX-License-Identifier: MIT
"""
cli.py — 命令行入口

用法:
    python -m market_research.update_manager [选项]
"""
from __future__ import annotations

import argparse
import sys

from market_research.update_manager.updater import (
    DEFAULT_HISTORY_DB,
    DEFAULT_TUSHARE_DB,
    clear_log,
    run_update,
)
from market_research.update_manager.cron import install_cron, remove_cron, show_cron


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="统一数据更新 CLI — 断点续传 tushare.db + history.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    date_group = parser.add_argument_group("日期选项")
    date_group.add_argument("--since", default=None,
                            help="起始日期（默认 10 天前），如 20260701")
    date_group.add_argument("--start", default=None,
                            help="强制模式起始日期，与 --force 配合")
    date_group.add_argument("--end", default=None,
                            help="强制模式结束日期（默认今天），与 --force 配合")

    scope_group = parser.add_argument_group("范围选项")
    scope_group.add_argument("--force", action="store_true", dest="force_mode",
                             help="强制模式（先删后插，需配合 --start/--end）")
    scope_group.add_argument("--tushare-only", action="store_true",
                             help="仅更新 tushare.db")
    scope_group.add_argument("--history-only", action="store_true",
                             help="仅更新 history.db")
    scope_group.add_argument("--no-concept", action="store_true",
                             help="跳过概念/公司信息更新")
    scope_group.add_argument("--concept", action="store_true", dest="force_concept",
                             help="强制更新概念/公司信息")
    scope_group.add_argument("--no-stock-ff", action="store_true",
                             help="跳过个股资金流更新")

    db_group = parser.add_argument_group("数据库选项")
    db_group.add_argument("--tushare-db", default=DEFAULT_TUSHARE_DB,
                          help=f"tushare.db 路径（默认 {DEFAULT_TUSHARE_DB}）")
    db_group.add_argument("--history-db", default=DEFAULT_HISTORY_DB,
                          help=f"history.db 路径（默认 {DEFAULT_HISTORY_DB}）")
    db_group.add_argument("--window", type=int, default=240,
                          help="Build report 滚动窗口（默认 240 交易日）")

    parser.add_argument("--build", action="store_true",
                        help="更新完后 build report")
    parser.add_argument("--concept-graph", action="store_true",
                        help="Build 时运行 AI 概念聚合（慢）")

    cron_group = parser.add_argument_group("定时任务")
    cron_group.add_argument("--install-cron", action="store_true",
                            help="注册 crontab（周一到五 18:30）")
    cron_group.add_argument("--remove-cron", action="store_true",
                            help="移除 crontab")
    cron_group.add_argument("--show-cron", action="store_true",
                            help="查看当前 cron 注册情况")

    args = parser.parse_args(argv)

    # cron 子命令
    if args.install_cron:
        clear_log()
        install_cron()
        return 0
    if args.remove_cron:
        clear_log()
        remove_cron()
        return 0
    if args.show_cron:
        clear_log()
        show_cron()
        return 0

    # 数据更新
    if args.force_mode and not args.start:
        print("[错误] --force 模式需要 --start 参数", file=sys.stderr)
        return 1

    return run_update(
        tushare_db=args.tushare_db,
        history_db=args.history_db,
        since=args.since,
        force_start=args.start if args.force_mode else None,
        force_end=args.end,
        no_concept=args.no_concept,
        force_concept=args.force_concept,
        no_stock_ff=args.no_stock_ff,
        tushare_only=args.tushare_only,
        history_only=args.history_only,
        do_build=args.build,
        concept=args.concept_graph,
        window=args.window,
    )


if __name__ == "__main__":
    raise SystemExit(main())
