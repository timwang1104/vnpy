# SPDX-License-Identifier: MIT
"""
cron.py — Cron 定时任务管理（注册 / 移除 / 查询）
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data"

CRON_COMMENT = "# data-update (vnpy market research)"
CRON_LINE = (
    f"30 18 * * 1-5 cd {REPO_ROOT} && python3 -m market_research.update_manager "
    f"--build >> {DATA_DIR}/cron.log 2>&1"
)


def _get_crontab() -> str:
    """读取当前 crontab，不存在则返回空字符串。"""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout
        return ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _set_crontab(content: str) -> bool:
    """写入 crontab。"""
    try:
        proc = subprocess.Popen(
            ["crontab", "-"],
            stdin=subprocess.PIPE, text=True,
        )
        proc.communicate(input=content, timeout=10)
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def install_cron() -> bool:
    """注册定时任务（周一到周五 18:30）。"""
    existing = _get_crontab()
    if CRON_COMMENT in existing:
        log("[Cron] 已存在，跳过注册")
        return True

    new_cron = existing.strip()
    if new_cron and not new_cron.endswith("\n"):
        new_cron += "\n"
    new_cron += f"{CRON_COMMENT}\n{CRON_LINE}\n"

    if _set_crontab(new_cron):
        log("[Cron] 已注册：周一至周五 18:30 自动更新数据")
        log(f"[Cron] 命令: {CRON_LINE}")
        return True
    log("[Cron] 注册失败（无法执行 crontab 命令）")
    return False


def remove_cron() -> bool:
    """移除定时任务。"""
    existing = _get_crontab()
    if CRON_COMMENT not in existing:
        log("[Cron] 未找到注册项")
        return True

    lines = existing.splitlines(keepends=True)
    filtered = []
    skip = False
    for line in lines:
        if CRON_COMMENT in line:
            skip = True
            continue
        if skip:
            skip = False
            continue
        filtered.append(line)

    if _set_crontab("".join(filtered)):
        log("[Cron] 已移除")
        return True
    log("[Cron] 移除失败")
    return False


def show_cron() -> None:
    """展示当前 cron 中本项目的更新任务。"""
    existing = _get_crontab()
    if CRON_COMMENT in existing:
        log("[Cron] 当前注册的定时任务：")
        lines = existing.splitlines()
        for i, line in enumerate(lines):
            if CRON_COMMENT in line and i + 1 < len(lines):
                log(f"  {lines[i+1]}")
    else:
        log("[Cron] 未注册定时任务")
        log("[Cron] 可用 `python -m market_research.update_manager --install-cron` 注册")


def show_cron_info() -> dict:
    """返回 cron 注册信息（供面板查询）。"""
    existing = _get_crontab()
    registered = CRON_COMMENT in existing
    cron_line = ""
    if registered:
        lines = existing.splitlines()
        for i, line in enumerate(lines):
            if CRON_COMMENT in line and i + 1 < len(lines):
                cron_line = lines[i + 1].strip()
                break
    return {
        "registered": registered,
        "cron_expr": "30 18 * * 1-5",
        "cron_line": cron_line,
    }


def log(msg: str, end: str = "\n") -> None:
    """共用的日志输出，直接打印（不与 updater 共享缓冲区）。"""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, end=end, flush=True)
