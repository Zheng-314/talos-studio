# -*- coding: utf-8 -*-
"""抖音弹幕解码（跨平台，纯 Python）

Windows 上用的是 .NET 的抓包程序，macOS 没有对应实现。
这里用 mitmproxy 截同一路 WebSocket 流量，手工解析 protobuf 取出弹幕。

字段号来自公开的逆向资料，未在真实直播间里做过长稳验证，
接不通时优先怀疑字段号变了。
"""
from __future__ import annotations

import gzip
import json


def read_varint(buf: bytes, i: int):
    shift = 0
    result = 0
    while True:
        if i >= len(buf):
            raise ValueError("varint 越界")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 70:
            raise ValueError("varint 过长")


def parse(buf: bytes):
    """把一段 protobuf 拆成 [(字段号, 类型, 值)]

    数据被截断或格式不对时，返回已经解出来的部分，不抛异常——
    直播间里一帧坏掉不该把整条链路带崩。
    """
    out = []
    i = 0
    n = len(buf)
    while i < n:
        try:
            key, i = read_varint(buf, i)
            field, wt = key >> 3, key & 0x07
            if field == 0:
                break
            if wt == 0:
                val, i = read_varint(buf, i)
            elif wt == 2:
                ln, i = read_varint(buf, i)
                if i + ln > n:
                    break
                val = buf[i:i + ln]
                i += ln
            elif wt == 1:
                if i + 8 > n:
                    break
                val = buf[i:i + 8]
                i += 8
            elif wt == 5:
                if i + 4 > n:
                    break
                val = buf[i:i + 4]
                i += 4
            else:
                break
        except Exception:
            break
        out.append((field, wt, val))
    return out


def _get(fields, no, default=None):
    for f, wt, v in fields:
        if f == no:
            return v
    return default


def _text(val) -> str:
    if isinstance(val, (bytes, bytearray)):
        try:
            return val.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    return str(val)


def maybe_gunzip(data: bytes) -> bytes:
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except Exception:
            return data
    return data


def decode_push_frame(raw: bytes):
    """PushFrame -> [(method, payload_bytes)]

    抖音的 WebSocket 帧先包一层 PushFrame，payload 通常是 gzip 过的 Response。
    """
    frames = parse(raw)
    payload = _get(frames, 7)
    if not payload:
        # 有些版本直接把 Response 塞在 1 号字段
        payload = _get(frames, 1)
    if not payload:
        return []
    return decode_response(maybe_gunzip(payload))


def decode_response(data: bytes):
    """Response -> [(method, payload_bytes)]"""
    msgs = []
    for field, wt, val in parse(data):
        if field == 1 and isinstance(val, (bytes, bytearray)):
            m = parse(val)
            method = _text(_get(m, 1, b""))
            body = _get(m, 2, b"")
            if method and body:
                msgs.append((method, body))
    return msgs


def decode_chat(body: bytes) -> dict | None:
    """WebcastChatMessage -> {user, content}"""
    f = parse(body)
    content = _text(_get(f, 3, b""))
    user_raw = _get(f, 2)
    nickname = ""
    if isinstance(user_raw, (bytes, bytearray)):
        u = parse(user_raw)
        nickname = _text(_get(u, 3, b"")) or _text(_get(u, 1, b""))
    if not content:
        return None
    return {"user": nickname or "观众", "content": content}


def decode_gift(body: bytes) -> dict | None:
    """WebcastGiftMessage -> {user, gift}"""
    f = parse(body)
    user_raw = _get(f, 7) or _get(f, 2)
    nickname = ""
    if isinstance(user_raw, (bytes, bytearray)):
        nickname = _text(_get(parse(user_raw), 3, b""))
    gift = None
    gr = _get(f, 15)
    if isinstance(gr, (bytes, bytearray)):
        gift = _text(_get(parse(gr), 16, b"")) or _text(_get(parse(gr), 2, b""))
    if not gift:
        return None
    return {"user": nickname or "观众", "gift": gift}


def to_engine_event(method: str, body: bytes) -> dict | None:
    """转成引擎认识的消息格式（和 Windows 抓包程序一致）"""
    if method.endswith("ChatMessage"):
        m = decode_chat(body)
        if not m:
            return None
        return {"Type": 1, "Data": json.dumps({"User": {"Nickname": m["user"]},
                                               "Content": m["content"]}, ensure_ascii=False)}
    if method.endswith("GiftMessage"):
        m = decode_gift(body)
        if not m:
            return None
        return {"Type": 5, "Data": json.dumps({"User": {"Nickname": m["user"]},
                                               "GiftName": m["gift"]}, ensure_ascii=False)}
    if method.endswith("MemberMessage"):
        f = parse(body)
        u = _get(f, 2)
        name = ""
        if isinstance(u, (bytes, bytearray)):
            name = _text(_get(parse(u), 3, b""))
        return {"Type": 3, "Data": json.dumps({"User": {"Nickname": name or "观众"}},
                                              ensure_ascii=False)}
    return None
