"""日志工具。"""

from __future__ import annotations

import logging
import sys
from datetime import datetime


def setup_logger(name: str = "tbot", level: str = "INFO") -> logging.Logger:
    """初始化控制台日志。"""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)

    return logger


class LogBuffer:
    """内存日志缓冲（线程安全），供 Web SSE 使用。"""

    def __init__(self, max_lines: int = 1000) -> None:
        self._lines: list[str] = []
        self._max_lines = max_lines

    def write(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self._lines.append(line)
        if len(self._lines) > self._max_lines:
            self._lines.pop(0)

    def get_lines(self) -> list[str]:
        return list(self._lines)

    def clear(self) -> None:
        self._lines.clear()
