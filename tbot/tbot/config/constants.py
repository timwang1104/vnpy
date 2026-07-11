"""常量定义。"""

from enum import Enum


class Interval(str, Enum):
    """K 线周期。"""
    MINUTE = "1m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    MINUTE_60 = "60m"
    DAILY = "d"
    WEEKLY = "w"
