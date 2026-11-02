# -*- coding: utf-8 -*-
"""macOS 弹幕采集：mitmproxy 插件

用法（macOS / Linux）：
    pip install mitmproxy websockets
    mitmdump -s barrage/mitm_addon.py --listen-port 8899

它做两件事：
  1. 截获直播伴侣的 WebSocket 帧，解出弹幕
  2. 在本机 8888 开一个 WebSocket，把消息推给直播引擎

首次使用要把 mitmproxy 的证书装进钥匙串，见 barrage/README.md
"""
from __future__ import annotations

import asyncio
import json
import threading

from . import proto

TARGET_HINTS = ("webcast", "im/pub", "douyin", "amemv")


class Bridge:
    """把解出来的消息广播给引擎。"""

    def __init__(self, port: int = 8888):
        self.port = port
        self.clients: set = set()
        self.loop = asyncio.new_event_loop()
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._serve())

    async def _serve(self):
        import websockets

        async def handler(ws):
            self.clients.add(ws)
            try:
                async for _ in ws:
                    pass
            except Exception:
                pass
            finally:
                self.clients.discard(ws)

        async with websockets.serve(handler, "127.0.0.1", self.port):
            print(f"[bridge] 引擎可以连 ws://127.0.0.1:{self.port}", flush=True)
            await asyncio.Future()

    def push(self, event: dict):
        if not self.clients:
            return
        text = json.dumps(event, ensure_ascii=False)
        for ws in list(self.clients):
            asyncio.run_coroutine_threadsafe(self._send(ws, text), self.loop)

    async def _send(self, ws, text):
        try:
            await ws.send(text)
        except Exception:
            self.clients.discard(ws)


bridge = Bridge()


def _looks_like_target(url: str) -> bool:
    low = (url or "").lower()
    return any(h in low for h in TARGET_HINTS)


def websocket_message(flow):
    """mitmproxy 钩子：每收到一帧 WebSocket 消息就过一遍。"""
    bridge.start()
    if flow.websocket is None or not flow.websocket.messages:
        return
    msg = flow.websocket.messages[-1]
    if msg.from_client:
        return
    try:
        if not _looks_like_target(flow.request.pretty_url):
            return
        raw = msg.content if isinstance(msg.content, bytes) else bytes(msg.content)
        for method, body in proto.decode_push_frame(raw):
            event = proto.to_engine_event(method, body)
            if event:
                bridge.push(event)
    except Exception as e:
        # 单帧解析失败不该影响后面的帧
        print(f"[bridge] 解析失败 {type(e).__name__}: {e}", flush=True)
