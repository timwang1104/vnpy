# SPDX-License-Identifier: MIT
"""tbot data 子命令 — CLI 入口处理器。

提供 handle_data_update / handle_data_status 两个函数，
由 tbot/main.py 在 subparser dispatch 时调用。
"""

from __future__ import annotations

import sys
import time

from tbot.business.data_update.service import DataUpdateService, service as _default_service
from tbot.config.settings import ConfigManager
from tbot.engines.database.manager import DatabaseManager


def handle_data_update(args) -> None:
    """tbot data update — 一键更新所有数据（同步阻塞）。"""
    # 确保数据目录存在（通过 DatabaseManager 统一管理）
    cfg = ConfigManager()
    data_dir = cfg.get("database.data_dir", "data")
    DatabaseManager(data_dir)

    svc = DataUpdateService()
    ret = svc.run(do_build=True)

    if ret["status"] == "error":
        print(f"[tbot] {ret['message']}")
        sys.exit(1)

    print("[tbot] 等待更新完成...")
    try:
        while True:
            status = svc.get_status()
            if not status.get("thread_alive", False):
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[tbot] 用户中断")
        sys.exit(1)

    final = svc.get_status()
    if final["status"] == "completed":
        print(f"[tbot] 更新完成")
    else:
        print(f"[tbot] 更新异常: {final.get('error', 'unknown')}")
        sys.exit(1)


def handle_data_status(args) -> None:
    """tbot data status — 查看当前更新状态。"""
    status = _default_service.get_status()
    print(f"[tbot] 状态: {status.get('status', 'unknown')}")
    if status.get("started_at"):
        print(f"    开始: {status['started_at']}")
    if status.get("finished_at"):
        print(f"    结束: {status['finished_at']}")
    if status.get("error"):
        print(f"    错误: {status['error']}")
    prog = status.get("progress", {})
    if prog.get("total", 0) > 0:
        print(f"    进度: {prog['current']}/{prog['total']} - {prog.get('label', '')}")
    print(f"    线程存活: {status.get('thread_alive', False)}")
    results = status.get("last_results", [])
    if results:
        print(f"    最近更新 ({len(results)} 阶段):")
        for r in results:
            if r.get("status") == "ok":
                print(f"      Build: 完成")
            elif r.get("status") == "error":
                print(f"      Build: 失败 - {r.get('error', '')}")
            elif "table" in r:
                print(f"      {r['table']}: 写入 {r.get('inserted', 0)} 行, 共 {r.get('total', 0)} 行")
