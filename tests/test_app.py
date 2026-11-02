# -*- coding: utf-8 -*-
"""Talos Studio 端到端测试

覆盖：抠图、形象 CRUD、上传、人设、启用、设置读写、环境检查、场次数据、
以及聊天和出图的入参校验。涉及外网的部分全部做了隔离。
"""
import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as studio  # noqa: E402


def make_png(bg=(255, 255, 255), fg=(220, 60, 60), size=(240, 240)) -> bytes:
    im = Image.new("RGB", size, bg)
    a = np.array(im)
    h, w, _ = a.shape
    yy, xx = np.mgrid[0:h, 0:w]
    mask = ((xx - w // 2) ** 2 + (yy - h // 2) ** 2) < (min(h, w) // 3) ** 2
    a[mask] = fg
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把形象目录和引擎目录都指到临时目录，避免污染真实数据。"""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    engine = tmp_path / "engine"
    (engine / "assets" / "poses").mkdir(parents=True)
    (engine / "config.json").write_text(json.dumps({
        "brain": {"api": "https://api.deepseek.com/v1", "api_key": "sk-real-key",
                  "model": "deepseek-chat", "max_tokens": 200, "temperature": 0.9},
        "voices": {"talos": "zh-CN-XiaoyiNeural", "rival": "zh-CN-YunjianNeural",
                   "guest": "zh-CN-YunxiNeural"},
        "persona": {"talos": "旧人设", "rival": "", "guest": ""},
        "tts": {"provider": "edge", "fish_api_key": ""},
        "images": {"provider": "svg", "siliconflow_api_key": ""},
        "goal": {"name": "今日互动", "target": 300, "unit": "次"},
        "layout": {"scale": 1.0, "x": 0, "y": 0},
        "idle_seconds": 90,
        "auto_act": False,
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(studio, "PROFILES", profiles)
    monkeypatch.setattr(studio, "ENGINE", engine)
    client = TestClient(studio.app)
    return {"client": client, "profiles": profiles, "engine": engine}


# ---------------- 抠图 ----------------
def test_key_background_removes_flat_bg():
    im = Image.open(io.BytesIO(make_png()))
    out = studio.key_background(im)
    a = np.array(out)
    assert a[2, 2, 3] < 12, "四角应该被抠掉"
    assert a[120, 120, 3] > 240, "主体应该保留"


def test_key_background_keeps_photo_like_images():
    """背景不干净时不应该乱抠。"""
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 255, (120, 120, 3), dtype=np.uint8)
    out = studio.key_background(Image.fromarray(arr))
    assert np.array(out)[:, :, 3].mean() > 250


def test_normalize_crops_and_limits_height():
    im = Image.new("RGBA", (400, 900), (0, 0, 0, 0))
    im.paste((200, 100, 100, 255), (100, 200, 300, 800))
    out = studio.normalize(im, max_h=400)
    assert out.height == 400
    assert out.width == round(200 * 400 / 600)   # 等比缩放


# ---------------- 形象 CRUD ----------------
def test_profile_crud(env):
    c = env["client"]
    r = c.post("/api/profiles", json={"name": "小蓝"})
    assert r.status_code == 200
    pid = r.json()["id"]

    listed = c.get("/api/profiles").json()["profiles"]
    assert len(listed) == 1 and listed[0]["name"] == "小蓝"

    got = c.get(f"/api/profiles/{pid}").json()
    assert got["id"] == pid

    assert c.delete(f"/api/profiles/{pid}").json()["ok"]
    assert c.get("/api/profiles").json()["profiles"] == []


def test_upload_and_serve_pose(env):
    c = env["client"]
    pid = c.post("/api/profiles", json={"name": "测试"}).json()["id"]
    r = c.post(f"/api/profiles/{pid}/upload",
               files={"file": ("a.png", make_png(), "image/png")},
               data={"slot": "idle"})
    assert r.status_code == 200 and r.json()["ok"]

    pose = c.get(f"/pose/{pid}/idle")
    assert pose.status_code == 200
    img = Image.open(io.BytesIO(pose.content))
    assert img.mode == "RGBA"

    files = c.get(f"/api/profiles/{pid}").json()["poses"]
    assert "idle" in files


def test_upload_rejects_bad_slot(env):
    c = env["client"]
    pid = c.post("/api/profiles", json={"name": "测试"}).json()["id"]
    r = c.post(f"/api/profiles/{pid}/upload",
               files={"file": ("a.png", make_png(), "image/png")},
               data={"slot": "nope"})
    assert r.status_code == 400


# ---------------- 人设 ----------------
def test_persona_builds_prompt_and_saves(env):
    c = env["client"]
    pid = c.post("/api/profiles", json={"name": "小绿"}).json()["id"]
    r = c.post(f"/api/profiles/{pid}/persona", json={
        "name": "小绿", "identity": "AI 助理", "skills": "画图",
        "tone": "短句", "limits": "不聊政治", "voice": "zh-CN-XiaoxiaoNeural"})
    body = r.json()
    assert "小绿" in body["prompt"] and "AI 助理" in body["prompt"]
    assert "不聊政治" in body["prompt"]

    saved = json.loads((env["profiles"] / pid / "profile.json").read_text(encoding="utf-8"))
    assert saved["persona"]["voice"] == "zh-CN-XiaoxiaoNeural"


def test_activate_writes_engine_config_and_poses(env, monkeypatch):
    monkeypatch.setattr(studio, "restart_engine", lambda: False)
    c = env["client"]
    pid = c.post("/api/profiles", json={"name": "小绿"}).json()["id"]
    c.post(f"/api/profiles/{pid}/upload",
           files={"file": ("a.png", make_png(), "image/png")}, data={"slot": "idle"})
    c.post(f"/api/profiles/{pid}/persona", json={"name": "小绿", "identity": "AI 助理"})

    r = c.post(f"/api/profiles/{pid}/activate")
    assert r.json()["ok"]

    cfg = json.loads((env["engine"] / "config.json").read_text(encoding="utf-8"))
    assert "小绿" in cfg["persona"]["talos"]
    assert (env["engine"] / "assets" / "poses" / "c_idle.png").exists()
    # 缺的姿态会用 idle 补上
    assert (env["engine"] / "assets" / "poses" / "c_type.png").exists()


# ---------------- 设置 ----------------
def test_settings_masks_and_keeps_key(env):
    c = env["client"]
    cfg = c.get("/api/settings").json()["config"]
    assert cfg["brain"]["api_key"] == "***"

    r = c.post("/api/settings", json={"idle_seconds": 120, "goal": {"target": 500}})
    assert r.json()["ok"]

    saved = json.loads((env["engine"] / "config.json").read_text(encoding="utf-8"))
    assert saved["idle_seconds"] == 120 and saved["goal"]["target"] == 500
    assert saved["brain"]["api_key"] == "sk-real-key", "掩码不能把真 Key 冲掉"


def test_settings_updates_layout(env):
    c = env["client"]
    c.post("/api/settings", json={"layout": {"scale": 1.2, "x": -30, "y": 40}})
    saved = json.loads((env["engine"] / "config.json").read_text(encoding="utf-8"))
    assert saved["layout"] == {"scale": 1.2, "x": -30, "y": 40}


# ---------------- 环境检查 ----------------
def test_preflight_shape(env):
    r = env["client"].get("/api/preflight").json()
    names = [c["name"] for c in r["checks"]]
    assert "OBS Studio" in names and "弹幕监听程序" in names
    assert all("ok" in c for c in r["checks"])


def test_setup_flags(env):
    s = env["client"].get("/api/setup").json()
    for k in ("obs", "live_mate", "key", "profile", "poses", "engine"):
        assert k in s


# ---------------- 场次数据 ----------------
def test_sessions_endpoint(env):
    rows = [{"id": 1, "start": "2026-11-01 20:00", "duration": 600, "danmaku": 12,
             "gifts": 1, "follows": 2, "tokens": 3400, "acts": 1, "mood": 62, "final": True}]
    (env["engine"] / "sessions.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    got = env["client"].get("/api/sessions").json()["sessions"]
    assert got[0]["danmaku"] == 12


def test_sessions_empty_when_missing(env):
    assert env["client"].get("/api/sessions").json()["sessions"] == []


# ---------------- 入参校验 ----------------
def test_gen_requires_description(env):
    r = env["client"].post("/api/gen", json={"desc": ""})
    assert r.status_code == 400


def test_chat_unknown_profile(env):
    r = env["client"].post("/api/chat", json={"profile": "nope", "messages": []})
    assert r.status_code == 404


def test_adopt_rejects_missing_file(env):
    c = env["client"]
    pid = c.post("/api/profiles", json={"name": "x"}).json()["id"]
    r = c.post(f"/api/profiles/{pid}/adopt", json={"url": "/gen/none.jpg", "slot": "idle"})
    assert r.status_code == 404


def test_home_page_served(env):
    r = env["client"].get("/")
    assert r.status_code == 200 and "Talos" in r.text
