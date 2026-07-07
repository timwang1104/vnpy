"""
TuShare proxy helpers for vnpy_tushare.

封装三种代理调用形态（详见长期记忆 tushare-proxy-usage）：
1. 普通接口——创建 pro 后改写 `pro._DataApi__http_url`；
2. 模块级接口（如 `ts.pro_bar`）——额外传 `api=pro`；
3. 实时接口（realtime_quote / realtime_tick / realtime_list）——还需改写
   `tushare.stock.cons.verify_token_url`。

Token 一律由外部传入，本模块禁止硬编码任何 key。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.setting import SETTINGS

if TYPE_CHECKING:
    from tushare.pro.client import DataApi  # noqa: F401


# 默认代理地址（用户 15000 积分 key 所走的第三方代理）
DEFAULT_PROXY: str = "https://tt.xiaodefa.cn"
# 实时接口的 token 校验地址（仅 realtime_* 系列需要）
REALTIME_VERIFY_URL: str = "https://tt.xiaodefa.cn/dataapi/sdk-event"

# 环境变量名（用户已在 ~/.zshrc 配置 TUSHARE_API_KEY / TUSHARE_BASE_URL）
ENV_TOKEN: str = "TUSHARE_API_KEY"
ENV_PROXY: str = "TUSHARE_BASE_URL"

# 交易所 -> ts_code 后缀
EXCHANGE_SUFFIX_MAP: dict[Exchange, str] = {
    # 股票
    Exchange.SSE: ".SH",
    Exchange.SZSE: ".SZ",
    Exchange.BSE: ".BJ",
    # 期货
    Exchange.CFFEX: ".CFE",
    Exchange.SHFE: ".SHF",
    Exchange.CZCE: ".CZC",
    Exchange.DCE: ".DCE",
    Exchange.INE: ".INE",
    Exchange.GFEX: ".GFE",
}

# Interval -> tushare pro_bar freq
INTERVAL_FREQ_MAP: dict[Interval, str] = {
    Interval.MINUTE: "min",
    Interval.HOUR: "60min",
    Interval.DAILY: "D",
    Interval.WEEKLY: "W",
}


def create_pro(token: str, proxy_url: Optional[str] = None) -> "DataApi":
    """
    创建一个走代理的 tushare pro 实例。

    :param token: tushare pro 56 位 token
    :param proxy_url: 代理地址，留空则使用 DEFAULT_PROXY
    :return: 已改写私有 http_url 的 pro 实例，可直接调用 pro.daily / pro.daily_basic 等
    """
    if not token:
        raise ValueError("tushare token 不能为空，请配置 datafeed.password 或环境变量 TUSHARE_API_KEY")

    import tushare as ts  # 延迟导入：tushare 是可选依赖，未安装时不影响纯逻辑函数

    ts.set_token(token)
    pro = ts.pro_api()
    # 改写私有 URL（tushare SDK 升级时单点适配）
    # noinspection PyUnresolvedReferences
    pro._DataApi__http_url = proxy_url or DEFAULT_PROXY
    return pro


def setup_realtime_verify(verify_url: Optional[str] = None) -> None:
    """
    改写 tushare.stock.cons.verify_token_url，使 realtime_quote / realtime_tick /
    realtime_list 走代理校验。仅在调用实时接口前使用，隔离全局副作用。
    """
    from tushare.stock import cons as ct

    ct.verify_token_url = verify_url or REALTIME_VERIFY_URL


def exchange_to_suffix(exchange: Exchange) -> Optional[str]:
    """vnpy Exchange -> tushare ts_code 后缀；未覆盖的交易所返回 None。"""
    return EXCHANGE_SUFFIX_MAP.get(exchange)


def interval_to_freq(interval: Interval) -> Optional[str]:
    """vnpy Interval -> tushare pro_bar 的 freq 参数；TICK 不在此映射（走 tick 接口）。"""
    return INTERVAL_FREQ_MAP.get(interval)


def to_yyyymmdd(dt: datetime) -> str:
    """datetime -> tushare 期望的 'YYYYMMDD' 字符串。"""
    return dt.strftime("%Y%m%d")


def load_token_datafeed() -> str:
    """
    VeighNa 集成路径的 token 读取：优先 SETTINGS['datafeed.password']，
    回退环境变量 TUSHARE_API_KEY。两者皆空则抛错。
    """
    token: str = SETTINGS.get("datafeed.password", "") or ""
    if not token:
        token = os.environ.get(ENV_TOKEN, "") or ""
    if not token:
        raise RuntimeError(
            "未检测到 tushare token，请在全局配置 datafeed.password 或环境变量 "
            "TUSHARE_API_KEY 中设置"
        )
    return token


def load_token_script() -> str:
    """
    示例脚本路径的 token 读取：优先环境变量 TUSHARE_API_KEY，
    回退 SETTINGS['datafeed.password']。两者皆空则抛错。
    """
    token: str = os.environ.get(ENV_TOKEN, "") or ""
    if not token:
        token = SETTINGS.get("datafeed.password", "") or ""
    if not token:
        raise RuntimeError(
            "未检测到 tushare token，请先 `export TUSHARE_API_KEY=<56位key>` "
            "或在全局配置 datafeed.password 中设置"
        )
    return token


def load_proxy_url() -> str:
    """
    代理 URL 读取：优先环境变量 TUSHARE_BASE_URL，回退 SETTINGS['datafeed.username']
    字段承载代理地址（沿用 vnpy 'token 放 password、其余放 username' 的隐含约定），
    皆空则用默认代理 DEFAULT_PROXY。
    """
    url: str = os.environ.get(ENV_PROXY, "") or ""
    if not url:
        url = SETTINGS.get("datafeed.username", "") or ""
    if not url:
        url = DEFAULT_PROXY
    return url