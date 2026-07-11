"""Crontab 管理 — 数据定时更新注册。"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_TBOT_DIR = _REPO_ROOT / "tbot"
_CRON_ID = "tbot_data_update"
_CRON_TIME = "30 18 * * 1-5"


def install_cron() -> bool:
    """安装 crontab 定时任务（交易日 18:30 自动更新）。"""
    script = str(_TBOT_DIR / ".claude" / "scripts" / "data_update.sh")
    if not Path(script).exists():
        script = f"cd {_TBOT_DIR} && python3 -m tbot.main data update"

    entry = f"{_CRON_TIME} cd {_TBOT_DIR} && tbot data update > /tmp/tbot_cron.log 2>&1"

    try:
        existing = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5
        ).stdout
    except subprocess.TimeoutExpired:
        existing = ""

    lines = [l for l in existing.splitlines() if _CRON_ID not in l]
    lines.append(f"# {_CRON_ID}")
    lines.append(entry)
    lines.append("")

    proc = subprocess.run(
        ["crontab", "-"], input="\n".join(lines), text=True, timeout=5
    )
    return proc.returncode == 0


def remove_cron() -> bool:
    """移除 crontab 定时任务。"""
    try:
        existing = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5
        ).stdout
    except subprocess.TimeoutExpired:
        return True

    lines = [l for l in existing.splitlines() if _CRON_ID not in l]
    proc = subprocess.run(
        ["crontab", "-"], input="\n".join(lines) + "\n", text=True, timeout=5
    )
    return proc.returncode == 0


def show_cron_info() -> dict:
    """显示 cron 注册状态。"""
    try:
        existing = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5
        ).stdout
    except subprocess.TimeoutExpired:
        existing = ""

    installed = _CRON_ID in existing
    return {
        "installed": installed,
        "schedule": _CRON_TIME,
        "command": "tbot data update",
        "note": "仅在工作日 18:30 执行" if installed else "未安装",
    }
