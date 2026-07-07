"""
vnpy_tushare —— VeighNa 的 TuShare 数据服务支持（通过第三方代理 tt.xiaodefa.cn）。

导出：
- Datafeed: 供 vnpy.trader.datafeed.get_datafeed() 反射加载的类名
- TuShareDatafeed: 同上，显式命名别名
- create_pro / setup_realtime_verify / load_token_*: 供示例脚本复用
"""

from __future__ import annotations

from .datafeed import TuShareDatafeed
from .tushare_data import (
    DEFAULT_PROXY,
    REALTIME_VERIFY_URL,
    create_pro,
    exchange_to_suffix,
    interval_to_freq,
    load_proxy_url,
    load_token_datafeed,
    load_token_script,
    setup_realtime_verify,
    to_yyyymmdd,
)

# get_datafeed() 反射约定：模块须暴露 Datafeed
Datafeed = TuShareDatafeed

__version__ = "0.1.0"

__all__ = [
    "Datafeed",
    "TuShareDatafeed",
    "DEFAULT_PROXY",
    "REALTIME_VERIFY_URL",
    "create_pro",
    "setup_realtime_verify",
    "exchange_to_suffix",
    "interval_to_freq",
    "load_token_datafeed",
    "load_token_script",
    "load_proxy_url",
    "to_yyyymmdd",
    "__version__",
]