# SPDX-License-Identifier: MIT
"""FastAPI StaticFiles server — 托管 report 目录，端口 8765（被占递增）。"""
from __future__ import annotations

import socket
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def find_free_port(start: int = 8765, max_tries: int = 20) -> int:
    """从 start 开始尝试绑定端口，被占递增。"""
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"无法找到空闲端口 (从 {start} 试了 {max_tries} 个)")


def serve(
    report_dir: str | Path = "report",
    port: int = 8765,
    no_browser: bool = False,
) -> None:
    """启动 FastAPI StaticFiles 服务。

    Args:
        report_dir: report 目录路径
        port: 起始端口，被占则递增
        no_browser: True 时不打开浏览器
    """
    report_dir = Path(report_dir).resolve()
    if not report_dir.exists():
        raise FileNotFoundError(f"Report directory not found: {report_dir}")

    actual_port = find_free_port(start=port)

    app = FastAPI(title="Market Research")
    app.mount("/", StaticFiles(directory=str(report_dir), html=True), name="report")

    url = f"http://127.0.0.1:{actual_port}/"

    if not no_browser:
        webbrowser.open(url)

    if actual_port != port:
        print(f"[market_research] 端口 {port} 被占，使用 {actual_port}")
    print(f"[market_research] Serve at {url}")
    print("[market_research] Press Ctrl+C to stop")

    uvicorn.run(app, host="127.0.0.1", port=actual_port, log_level="info")
