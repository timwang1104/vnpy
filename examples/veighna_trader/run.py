"""VeighNa Trader GUI 启动脚本（适配 feat/add-tushare-datafeed 分支）。

与上游 examples/veighna_trader/run.py 的差异：
- 数据服务改用仓库自带的 vnpy_tushare（通过 SETTINGS["datafeed.name"]="tushare"
  让 vnpy.trader.datafeed.get_datafeed() 反射加载，无需 add_app/add_gateway）。
- gateway / app 全部改为「按需容错加载」：装了才挂，没装跳过并打印一行提示，
  避免缺包时直接 ImportError 起不来。
- 默认不挂 CtpGateway（本分支聚焦数据下载/研究，无需实盘交易接口）；
  需要实盘时把下面的 USE_CTP 改 True 并 pip install vnpy_ctp。

环境变量（由 ~/.zshrc 提供，本脚本不硬编码 token）：
- TUSHARE_API_KEY:  tushare pro 15000 积分 key
- TUSHARE_BASE_URL: 代理地址，缺省回退到 vnpy_tushare.tushare_data.DEFAULT_PROXY
  也可改用全局配置 vt_setting.json 的 datafeed.username / datafeed.password。
"""

from vnpy.event import EventEngine

from vnpy.trader.engine import MainEngine
from vnpy.trader.setting import SETTINGS
from vnpy.trader.ui import MainWindow, create_qapp

# 数据服务：反射约定 module = vnpy_<datafeed.name>，模块须导出 Datafeed
SETTINGS["datafeed.name"] = "tushare"
# token / 代理留空 → 走环境变量 TUSHARE_API_KEY / TUSHARE_BASE_URL
# 若想固化到 vt_setting.json，可在此处赋值：
# SETTINGS["datafeed.password"] = "<56位key>"
# SETTINGS["datafeed.username"] = "https://tt.xiaodefa.cn"

# 是否挂载 CTP 交易网关（实盘/仿真需要；纯数据研究保持 False）
USE_CTP: bool = False


def _try_add_gateway(main_engine: MainEngine, module_name: str, attr: str) -> None:
    """按需挂载交易网关：装了才挂，没装跳过。"""
    try:
        module = __import__(module_name, fromlist=[attr])
        gateway = getattr(module, attr)
    except ImportError:
        print(f"[skip] {module_name} 未安装，跳过 {attr}")
        return
    main_engine.add_gateway(gateway)
    print(f"[ok]   已挂载网关 {attr}")


def _try_add_app(main_engine: MainEngine, module_name: str, attr: str) -> None:
    """按需挂载上层应用：装了才挂，没装跳过。"""
    try:
        module = __import__(module_name, fromlist=[attr])
        app = getattr(module, attr)
    except ImportError:
        print(f"[skip] {module_name} 未安装，跳过 {attr}")
        return
    main_engine.add_app(app)
    print(f"[ok]   已挂载应用 {attr}")


def main() -> None:
    """"""
    qapp = create_qapp()

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)

    # 交易网关（按需，默认仅 CTP 关闭）
    if USE_CTP:
        _try_add_gateway(main_engine, "vnpy_ctp", "CtpGateway")
    # _try_add_gateway(main_engine, "vnpy_ctptest", "CtptestGateway")
    # _try_add_gateway(main_engine, "vnpy_mini", "MiniGateway")
    # _try_add_gateway(main_engine, "vnpy_femas", "FemasGateway")
    # _try_add_gateway(main_engine, "vnpy_sopt", "SoptGateway")
    # _try_add_gateway(main_engine, "vnpy_esunny", "EsunnyGateway")
    # _try_add_gateway(main_engine, "vnpy_xtp", "XtpGateway")
    # _try_add_gateway(main_engine, "vnpy_tora", "ToraStockGateway")
    # _try_add_gateway(main_engine, "vnpy_ib", "IbGateway")
    # _try_add_gateway(main_engine, "vnpy_tap", "TapGateway")
    # _try_add_gateway(main_engine, "vnpy_da", "DaGateway")
    # _try_add_gateway(main_engine, "vnpy_rohon", "RohonGateway")
    # _try_add_gateway(main_engine, "vnpy_tts", "TtsGateway")

    # 上层应用（按需）
    # _try_add_app(main_engine, "vnpy_paperaccount", "PaperAccountApp")
    _try_add_app(main_engine, "vnpy_ctastrategy", "CtaStrategyApp")
    _try_add_app(main_engine, "vnpy_ctabacktester", "CtaBacktesterApp")
    # _try_add_app(main_engine, "vnpy_spreadtrading", "SpreadTradingApp")
    # _try_add_app(main_engine, "vnpy_algotrading", "AlgoTradingApp")
    # _try_add_app(main_engine, "vnpy_optionmaster", "OptionMasterApp")
    # _try_add_app(main_engine, "vnpy_portfoliostrategy", "PortfolioStrategyApp")
    # _try_add_app(main_engine, "vnpy_scripttrader", "ScriptTraderApp")
    # _try_add_app(main_engine, "vnpy_chartwizard", "ChartWizardApp")
    # _try_add_app(main_engine, "vnpy_rpcservice", "RpcServiceApp")
    # _try_add_app(main_engine, "vnpy_excelrtd", "ExcelRtdApp")
    _try_add_app(main_engine, "vnpy_datamanager", "DataManagerApp")
    # _try_add_app(main_engine, "vnpy_datarecorder", "DataRecorderApp")
    # _try_add_app(main_engine, "vnpy_riskmanager", "RiskManagerApp")
    # _try_add_app(main_engine, "vnpy_webtrader", "WebTraderApp")
    # _try_add_app(main_engine, "vnpy_portfoliomanager", "PortfolioManagerApp")

    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()

    qapp.exec()


if __name__ == "__main__":
    main()
