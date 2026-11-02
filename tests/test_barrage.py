# -*- coding: utf-8 -*-
"""弹幕解码器的测试：用手工构造的 protobuf 验证解析链路。"""
import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from barrage import proto


# ---- 极简 protobuf 编码器，只用于造测试数据 ----
def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def ld(field: int, payload: bytes) -> bytes:
    return varint((field << 3) | 2) + varint(len(payload)) + payload


def vi(field: int, value: int) -> bytes:
    return varint((field << 3) | 0) + varint(value)


def text(field: int, s: str) -> bytes:
    return ld(field, s.encode("utf-8"))


def build_user(nickname: str) -> bytes:
    return vi(1, 12345) + text(3, nickname)


def build_chat(user: str, content: str) -> bytes:
    return ld(1, b"\x08\x01") + ld(2, build_user(user)) + text(3, content)


def build_response(method: str, body: bytes) -> bytes:
    message = text(1, method) + ld(2, body)
    return ld(1, message)


def build_push_frame(response: bytes, gz: bool = True) -> bytes:
    payload = gzip.compress(response) if gz else response
    return vi(1, 1) + text(4, "msg") + ld(7, payload)


# ---- 测试 ----
def test_decode_chat():
    m = proto.decode_chat(build_chat("小鱼", "这个是真人吗"))
    assert m == {"user": "小鱼", "content": "这个是真人吗"}


def test_decode_chat_without_nickname():
    body = ld(1, b"\x08\x01") + ld(2, b"") + text(3, "你好")
    m = proto.decode_chat(body)
    assert m["user"] == "观众" and m["content"] == "你好"


def test_decode_chat_empty_content_returns_none():
    assert proto.decode_chat(ld(2, build_user("x"))) is None


def test_decode_response_multiple_messages():
    resp = build_response("WebcastChatMessage", build_chat("A", "1")) + \
           build_response("WebcastChatMessage", build_chat("B", "2"))
    msgs = proto.decode_response(resp)
    assert [m[0] for m in msgs] == ["WebcastChatMessage", "WebcastChatMessage"]
    assert proto.decode_chat(msgs[1][1])["content"] == "2"


def test_decode_push_frame_gzip_and_plain():
    resp = build_response("WebcastChatMessage", build_chat("阿树", "能读论文吗"))
    for gz in (True, False):
        msgs = proto.decode_push_frame(build_push_frame(resp, gz))
        assert msgs and msgs[0][0] == "WebcastChatMessage"


def test_to_engine_event_chat():
    ev = proto.to_engine_event("WebcastChatMessage", build_chat("小鱼", "你好"))
    assert ev["Type"] == 1
    data = json.loads(ev["Data"])
    assert data["User"]["Nickname"] == "小鱼" and data["Content"] == "你好"


def test_to_engine_event_gift():
    body = ld(7, build_user("coco")) + ld(15, text(16, "小心心"))
    ev = proto.to_engine_event("WebcastGiftMessage", body)
    assert ev["Type"] == 5
    assert json.loads(ev["Data"])["GiftName"] == "小心心"


def test_to_engine_event_unknown_returns_none():
    assert proto.to_engine_event("WebcastLikeMessage", b"") is None


def test_parse_handles_unusual_wire_types():
    # 固定长度字段（1 号和 5 号 wire type）也要能跳过
    buf = varint((1 << 3) | 1) + b"\x00" * 8 + varint((2 << 3) | 5) + b"\x00" * 4 + text(3, "ok")
    fields = proto.parse(buf)
    assert fields[0][0] == 1 and fields[-1][2] == b"ok"


def test_parse_truncated_does_not_raise():
    try:
        proto.parse(b"\x0a\xff")
    except Exception:
        pytest.fail("截断的数据不应该抛异常，应该尽量解析")
