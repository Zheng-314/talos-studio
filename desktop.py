# -*- coding: utf-8 -*-
"""Talos Studio · 桌面窗口版

把本地服务装进一个独立窗口里跑：没有地址栏、有自己的图标、关窗口就退出。
pywebview 起不来时（缺 WebView2），自动退回 Edge 的应用模式。
"""
from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

HOST = "127.0.0.1"
PORT = 8400
URL = f"http://{HOST}:{PORT}"


def port_open(port: int = PORT) -> bool:
    s = socket.socket()
    try:
        s.settimeout(0.3)
        return s.connect_ex((HOST, port)) == 0
    finally:
        s.close()


def run_server() -> None:
    import uvicorn
    from app import app
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", access_log=False)


def wait_ready(timeout: float = 30) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if port_open():
            return True
        time.sleep(0.15)
    return False


def edge_app_window() -> bool:
    """退路：用 Edge 的应用模式开一个无地址栏窗口。"""
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ]
    for exe in candidates:
        if exe.exists():
            subprocess.Popen([str(exe), f"--app={URL}", "--window-size=1280,880"])
            return True
    return False


def main() -> None:
    # 服务已经在跑就直接开窗，不重复起
    if not port_open():
        threading.Thread(target=run_server, daemon=True).start()
        if not wait_ready():
            print("服务没能起来，退出。", file=sys.stderr)
            sys.exit(1)

    # 自带运行时里 .NET 的 WebView2 后端不稳，用 TALOS_WEBVIEW=edge 直接走 Edge 应用窗口
    import os
    mode = os.environ.get("TALOS_WEBVIEW", "auto").lower()
    webview = None
    if mode != "edge":
        try:
            import webview  # noqa: F401
        except Exception:
            webview = None

    if webview is not None:
        try:
            icon = ROOT / "static" / ("character.ico" if sys.platform == "win32" else "character.icns")
            window = webview.create_window(
                "Talos Studio", URL,
                width=1280, height=880, min_size=(980, 680),
                background_color="#FAF9F5",
                text_select=False,
            )
            webview.start(icon=str(icon) if icon.exists() else None)
            return
        except Exception as e:
            print(f"窗口启动失败，退回浏览器：{e}", file=sys.stderr)

    if not edge_app_window():
        webbrowser.open(URL)
        # 保持进程存活，让服务继续跑
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
