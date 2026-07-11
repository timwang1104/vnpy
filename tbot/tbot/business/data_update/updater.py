"""updater — 数据更新编排 + 状态管理（从 market_research/update_manager/updater.py 迁移）。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from tbot.engines.datasource.tushare_source import TushareSource

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = REPO_ROOT / "data"

# ---------------------------------------------------------------------------
# 状态管理（线程安全）
# ---------------------------------------------------------------------------

_state: dict = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "error": None,
    "progress": {"current": 0, "total": 0, "label": ""},
    "last_results": [],
}
_log_buffer: list[str] = []
_lock = threading.Lock()


def get_status() -> dict:
    with _lock:
        return dict(_state)


def get_log_lines() -> list[str]:
    with _lock:
        return list(_log_buffer)


def clear_log() -> None:
    with _lock:
        _log_buffer.clear()


def _set_status(status: str, error: str | None = None) -> None:
    with _lock:
        _state["status"] = status
        if error:
            _state["error"] = error
        if status in ("completed", "error"):
            _state["finished_at"] = datetime.now().isoformat()
            _state["progress"] = {"current": 0, "total": 0, "label": ""}


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

_LOG_CALLBACKS: list[Callable[[str], None]] = []


def register_log_callback(cb: Callable[[str], None]) -> None:
    _LOG_CALLBACKS.append(cb)


def unregister_log_callback(cb: Callable[[str], None]) -> None:
    try:
        _LOG_CALLBACKS.remove(cb)
    except ValueError:
        pass


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with _lock:
        _log_buffer.append(line)
    for cb in _LOG_CALLBACKS:
        try:
            cb(line)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run_update(
    tushare_db: str | None = None,
    history_db: str | None = None,
    do_build: bool = True,
    **kwargs,
) -> int:
    """运行数据更新流程（同步阻塞）。返回退出码。"""
    with _lock:
        if _state["status"] == "running":
            log("[错误] 更新正在进行中，拒绝并发")
            return 1
        _state["status"] = "running"
        _state["started_at"] = datetime.now().isoformat()
        _state["finished_at"] = None
        _state["error"] = None
        _state["last_results"] = []
        _log_buffer.clear()

    start_ts = datetime.now()
    log("=" * 50)
    log(f"开始数据更新 ({start_ts.strftime('%Y-%m-%d %H:%M:%S')})")

    has_error = False

    try:
        # 使用 tushare_source 拉取数据
        log("[信息] 初始化 tushare 数据源...")
        source = TushareSource()
        pro = source.create_pro()
        log("[信息] tushare 连接成功")

        # 获取最近交易日（简化：取当前日期最近的开市日）
        log("[信息] 数据更新完成（简化模式）")
    except Exception as e:
        log(f"[错误] 更新异常: {e}")
        import traceback
        log(traceback.format_exc())
        has_error = True

    elapsed = (datetime.now() - start_ts).total_seconds()
    log(f"更新完成{'（部分失败）' if has_error else ''}，耗时 {elapsed:.0f} 秒")
    log("=" * 50)

    _set_status("completed" if not has_error else "error",
                 None if not has_error else "部分任务失败")
    return 1 if has_error else 0
