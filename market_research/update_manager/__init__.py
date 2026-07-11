# SPDX-License-Identifier: MIT
"""
update_manager 包 — CLI 入口、公共 API

用法:
    python -m market_research.update_manager                  # 更新全部
    python -m market_research.update_manager --tushare-only   # 仅 tushare.db
    python -m market_research.update_manager --install-cron   # 注册定时任务
"""
from __future__ import annotations

from market_research.update_manager.updater import (
    build_report,
    clear_log,
    get_log_lines,
    get_schedule,
    get_status,
    register_log_callback,
    run_update,
    unregister_log_callback,
)
from market_research.update_manager.cron import (
    install_cron,
    remove_cron,
    show_cron,
)

__all__ = [
    "run_update",
    "get_status",
    "get_log_lines",
    "clear_log",
    "get_schedule",
    "register_log_callback",
    "unregister_log_callback",
    "install_cron",
    "remove_cron",
    "show_cron",
    "build_report",
]
