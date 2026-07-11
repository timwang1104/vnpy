"""Simulator business domain — 模拟盘策略管理、回测执行、结果查询。

Usage:
    from tbot.engines.database.manager import DatabaseManager
    from tbot.business.simulator import SimulatorService, create_simulator_router

    mgr = DatabaseManager("data")
    svc = SimulatorService(mgr)
    router = create_simulator_router(svc)
    app.include_router(router)
"""

from tbot.business.simulator.service import SimulatorService
from tbot.business.simulator.router import create_simulator_router

__all__ = [
    "SimulatorService",
    "create_simulator_router",
]
