# SPDX-License-Identifier: MIT
"""data_update — 数据更新业务域。

封装 market_research/update_manager 的数据更新、cron 管理、日志流能力。
"""

from __future__ import annotations

from tbot.business.data_update.service import DataUpdateService
from tbot.business.data_update.router import router

__all__ = ["DataUpdateService", "router"]
