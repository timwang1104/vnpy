"""Industry business domain — 行业板块资金流分析。

核心服务: IndustryService（时序/异常检测/全量报告）
HTTP 路由: router（FastAPI APIRouter）
"""

from tbot.business.industry.service import IndustryService
from tbot.business.industry.router import router

__all__ = [
    "IndustryService",
    "router",
]
