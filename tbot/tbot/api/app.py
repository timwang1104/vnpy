"""FastAPI app factory — 创建并配置 FastAPI 应用实例。

创建 FastAPI 应用、注册 7 个业务域路由、挂载 CORS 中间件和静态文件。

用法::

    from tbot.api.app import create_app

    app = create_app()
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from tbot.business.chat.ws_handler import register_routes
from tbot.business.concept_cluster.router import router as concept_cluster_router
from tbot.business.data_update.router import router as data_update_router
from tbot.business.industry.router import router as industry_router
from tbot.business.limitup.router import router as limitup_router
from tbot.business.overview.router import router as overview_router
from tbot.business.simulator.router import create_simulator_router
from tbot.business.simulator.service import SimulatorService
from tbot.config.settings import ConfigManager
from tbot.engines.database.manager import DatabaseManager

_REPORT_DIR = Path(__file__).resolve().parent.parent.parent / "report"


def create_app(db_mgr: DatabaseManager | None = None) -> FastAPI:
    """创建 FastAPI 应用，挂载所有业务路由、中间件和静态文件。

    Args:
        db_mgr: 可选的 DatabaseManager 实例，不传时自动创建。

    Returns:
        配置完成的 FastAPI 应用实例。
    """
    app = FastAPI(
        title="TBot API",
        description="基于 vnpy + DuckDB 的量化投研工具后端 API",
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # CORS 中间件
    # ------------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # 业务路由（6 个 APIRouter）
    # ------------------------------------------------------------------
    app.include_router(concept_cluster_router)
    app.include_router(data_update_router)
    app.include_router(industry_router)
    app.include_router(limitup_router)
    app.include_router(overview_router)

    # 模拟盘路由需注入服务实例
    cfg = ConfigManager()
    data_dir = cfg.get("database.data_dir", "data")
    mgr = db_mgr or DatabaseManager(data_dir)
    svc = SimulatorService(mgr)
    app.include_router(create_simulator_router(svc))

    # ------------------------------------------------------------------
    # Chat WebSocket（直接注册到 app）
    # ------------------------------------------------------------------
    register_routes(app)

    # ------------------------------------------------------------------
    # 静态文件（根路径）
    # ------------------------------------------------------------------
    if _REPORT_DIR.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(_REPORT_DIR), html=True),
            name="report",
        )

    return app
