# -*- coding: utf-8 -*-
"""Talos Studio · 本地直播搭建器

一个进程管三件事：
  1. 形象：上传图片、自动抠底、存成一套形象（每个形象带自己的人设和声音）
  2. 开播：检查环境、按顺序拉起监听/OBS/引擎、停止
  3. 后台：直接嵌入现有直播控制台

启动：
    ..\\talos-stream\\.venv\\Scripts\\python.exe app.py
"""
from __future__ import annotations

import io
import json
import sys
import re
import shutil
import subprocess
import time
import uuid
from collections import deque
from pathlib import Path

import numpy as np
from fastapi import Body, FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent
PROFILES = ROOT / "profiles"
PROFILES.mkdir(exist_ok=True)


def first_existing(*cands: Path | str) -> Path:
    for c in cands:
        p = Path(c)
        if p.exists():
            return p
    return Path(str(cands[0]))


# 直播引擎：优先找同级目录，其次找常见位置
ENGINE = first_existing(
    ROOT / "talos-stream",
    ROOT.parent / "talos-stream",
    Path(r"C:\Users\ZhengYuanxuan\Desktop\talos-stream"),
)

# Python 解释器：引擎自带的虚拟环境优先，没有就用当前解释器
IS_MAC = sys.platform == "darwin"
ENGINE_PY = (ENGINE / ".venv" / ("bin" if IS_MAC else "Scripts") / "python")
PYEXE = str(ENGINE_PY) if ENGINE_PY.exists() else sys.executable

# 外部程序，全部自动探测（Windows / macOS 两套）
if IS_MAC:
    OBS_EXE = first_existing("/Applications/OBS.app", "/Applications/OBS Studio.app")
    BARRAGE = Path("/nonexistent/mac-no-barrage")
else:
    OBS_EXE = first_existing(
        Path(r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"),
        Path(r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe"),
    )
    BARRAGE = first_existing(
        ENGINE / "tools" / "DouyinBarrageGrab" / "WssBarrageServer.exe",
        ROOT.parent / "talos-live" / "tools" / "DouyinBarrageGrab" / "WssBarrageServer.exe",
        Path(r"C:\Users\ZhengYuanxuan\Desktop\talos-live\tools\DouyinBarrageGrab\WssBarrageServer.exe"),
    )
if IS_MAC:
    LIVE_MATE = first_existing("/Applications/抖音直播伴侣.app")
    LIVE_MATE_LAUNCH = 'open -a "抖音直播伴侣"'
else:
    LIVE_MATE = first_existing(
        Path(r"D:\zhibo\webcast_mate\直播伴侣 Launcher.exe"),
        Path(r"C:\Program Files\抖音直播伴侣\直播伴侣 Launcher.exe"),
        Path(r"C:\Program Files (x86)\抖音直播伴侣\直播伴侣 Launcher.exe"),
    )
    LIVE_MATE_LAUNCH = ""

DEFAULT_PROFILE = ROOT / "default-profile"

SLOTS = ["idle", "type", "happy"]
SLOT_CN = {"idle": "待机", "type": "干活", "happy": "开心"}

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


# ---------------- 形象处理 ----------------
def key_background(im: Image.Image) -> Image.Image:
    """软边缘抠底：按与背景色的距离做渐变透明，能处理渐变背景和半透明边缘。"""
    rgb = im.convert("RGB")
    a = np.array(rgb).astype(np.float32)
    h, w, _ = a.shape

    # 取四边一圈的中位数当背景色
    band = max(2, min(h, w) // 60)
    edge_px = np.concatenate([
        a[:band, :, :].reshape(-1, 3),
        a[-band:, :, :].reshape(-1, 3),
        a[:, :band, :].reshape(-1, 3),
        a[:, -band:, :].reshape(-1, 3),
    ])
    bg = np.median(edge_px, axis=0)

    dist = np.sqrt(((a - bg) ** 2).sum(axis=2))
    t0, t1 = 26.0, 82.0
    alpha = np.clip((dist - t0) / (t1 - t0), 0, 1)

    # 抠得太多或太少都说明背景判断不可靠（照片、复杂背景），保持原图不透明
    keep = float((alpha > 0.5).mean())
    if keep > 0.90 or keep < 0.03:
        return rgb.convert("RGBA")

    # 清掉右下角的水印区
    alpha[-int(h * 0.045):, -int(w * 0.30):] = 0

    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    out.putalpha(Image.fromarray((alpha * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(0.7)))
    return out


def normalize(rgba: Image.Image, max_h: int = 1500) -> Image.Image:
    bb = rgba.split()[3].getbbox()
    if bb:
        rgba = rgba.crop(bb)
    if rgba.height > max_h:
        w = int(rgba.width * max_h / rgba.height)
        rgba = rgba.resize((w, max_h), Image.LANCZOS)
    return rgba


def process_upload(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im = key_background(im)
    return normalize(im)


# ---------------- 形象档案 ----------------
def profile_dir(pid: str) -> Path:
    return PROFILES / pid


def load_profile(pid: str) -> dict:
    f = profile_dir(pid) / "profile.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def save_profile(pid: str, data: dict) -> None:
    d = profile_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "profile.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_profiles() -> list[dict]:
    out = []
    for d in sorted(PROFILES.iterdir()):
        if not d.is_dir():
            continue
        data = load_profile(d.name)
        poses = {s: (d / "poses" / f"c_{s}.png").exists() for s in SLOTS}
        out.append({"id": d.name, "name": data.get("name") or d.name,
                    "persona": data.get("persona", {}), "poses": poses,
                    "active": data.get("active", False)})
    return out


# ---------------- 接口 ----------------
@app.get("/")
async def index():
    return FileResponse(str(ROOT / "static" / "studio.html"))


def ensure_default_profile() -> None:
    """第一次启动时，给用户铺一个能直接开播的默认形象。"""
    if any(PROFILES.iterdir()):
        return
    if not (DEFAULT_PROFILE / "poses" / "c_idle.png").exists():
        return
    pid = "p" + uuid.uuid4().hex[:8]
    dst = PROFILES / pid
    shutil.copytree(DEFAULT_PROFILE, dst)
    data = {
        "name": "Talos",
        "persona": {
            "identity": "住在电脑里的 AI 助理，Talos 的官方形象",
            "skills": "现场用 SVG 画观众点题的东西、陪人聊天",
            "tone": "短句、口语、有点小骄傲但很可靠",
            "limits": "不聊政治，不承诺收益",
            "voice": "zh-CN-XiaoyiNeural",
        },
        "created": int(time.time()),
        "default": True,
        "active": True,
    }
    data["persona"]["prompt"] = build_prompt(data)
    save_profile(pid, data)


@app.get("/api/profiles")
async def api_profiles():
    return {"profiles": list_profiles()}


@app.post("/api/profiles")
async def api_create(payload: dict = Body(...)):
    name = (payload.get("name") or "我的 AI").strip()[:20]
    pid = "p" + uuid.uuid4().hex[:8]
    save_profile(pid, {"name": name, "persona": {}, "created": int(time.time())})
    return {"ok": True, "id": pid}


@app.delete("/api/profiles/{pid}")
async def api_delete(pid: str):
    d = profile_dir(pid)
    if d.exists():
        shutil.rmtree(d)
    return {"ok": True}


@app.get("/api/profiles/{pid}")
async def api_profile(pid: str):
    data = load_profile(pid)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    poses = [s for s in SLOTS if (profile_dir(pid) / "poses" / f"c_{s}.png").exists()]
    return {"id": pid, "data": data, "poses": poses}


@app.post("/api/profiles/{pid}/upload")
async def api_upload(pid: str, slot: str = Form(...), file: UploadFile = File(...)):
    if slot not in SLOTS:
        return JSONResponse({"error": "bad slot"}, status_code=400)
    raw = await file.read()
    try:
        img = process_upload(raw)
    except Exception as e:
        return JSONResponse({"error": f"图片处理失败：{type(e).__name__}"}, status_code=400)
    d = profile_dir(pid) / "poses"
    d.mkdir(parents=True, exist_ok=True)
    img.save(d / f"c_{slot}.png")
    return {"ok": True, "slot": slot, "size": img.size,
            "url": f"/pose/{pid}/{slot}?t={int(time.time())}"}


@app.get("/pose/{pid}/{slot}")
async def api_pose(pid: str, slot: str):
    f = profile_dir(pid) / "poses" / f"c_{slot}.png"
    if not f.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(f), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/profiles/{pid}/persona")
async def api_persona(pid: str, payload: dict = Body(...)):
    data = load_profile(pid)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    data["name"] = (payload.get("name") or data.get("name") or "我的 AI").strip()[:20]
    data["persona"] = {
        "identity": payload.get("identity", ""),
        "tone": payload.get("tone", ""),
        "skills": payload.get("skills", ""),
        "limits": payload.get("limits", ""),
        "voice": payload.get("voice", "zh-CN-XiaoyiNeural"),
    }
    data["persona"]["prompt"] = build_prompt(data)
    save_profile(pid, data)
    return {"ok": True, "prompt": data["persona"]["prompt"]}


def build_prompt(data: dict) -> str:
    p = data.get("persona", {})
    name = data.get("name", "AI")
    parts = [f"你是{name}，正在直播。"]
    if p.get("identity"):
        parts.append(f"你的身份：{p['identity']}。")
    if p.get("skills"):
        parts.append(f"你能干的事：{p['skills']}。")
    if p.get("tone"):
        parts.append(f"说话风格：{p['tone']}。")
    parts.append("每次一到两句，口语，不用排比和形容词堆砌。先正面回答，再补一句有用的。")
    parts.append("每三条回复里至少有一条反问观众。有人问你是不是真人，直接说是 AI，声音是合成的。")
    if p.get("limits"):
        parts.append(f"边界：{p['limits']}。")
    parts.append("不聊政治，不评价具体真人，不承诺收益。")
    return "".join(parts)


@app.post("/api/profiles/{pid}/activate")
async def api_activate(pid: str):
    """把某个形象装进引擎：人设写进 config.json，姿态图拷进 assets/poses。"""
    data = load_profile(pid)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    for other in PROFILES.iterdir():
        if other.is_dir() and (other / "profile.json").exists():
            od = load_profile(other.name)
            od["active"] = (other.name == pid)
            save_profile(other.name, od)

    cfg_path = ENGINE / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["persona"]["talos"] = data["persona"].get("prompt") or build_prompt(data)
    voice = data["persona"].get("voice")
    if voice:
        cfg.setdefault("voices", {})["talos"] = voice
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    src = profile_dir(pid) / "poses"
    dst = ENGINE / "assets" / "poses"
    dst.mkdir(parents=True, exist_ok=True)
    for s in SLOTS:
        f = src / f"c_{s}.png"
        if f.exists():
            shutil.copy2(f, dst / f"c_{s}.png")
    if not (dst / "c_type.png").exists() and (dst / "c_idle.png").exists():
        shutil.copy2(dst / "c_idle.png", dst / "c_type.png")
    if not (dst / "c_happy.png").exists() and (dst / "c_idle.png").exists():
        shutil.copy2(dst / "c_idle.png", dst / "c_happy.png")

    # 引擎在跑就重启一下，让人设和形象立刻生效
    if port_open(8321):
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-Process python -ErrorAction SilentlyContinue | Where-Object "
                        "{$_.Path -like '*talos-stream*'} | Stop-Process -Force"], capture_output=True)
        time.sleep(2)
        subprocess.Popen([PYEXE, "server.py"], cwd=str(ENGINE),
                         stdout=open(ENGINE / "run.log", "a", encoding="utf-8"),
                         stderr=subprocess.STDOUT)
        time.sleep(4)
        return {"ok": True, "restarted": True}
    return {"ok": True, "restarted": False}


# ---------------- 一键开播 ----------------
def port_open(port: int) -> bool:
    import socket
    s = socket.socket()
    try:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def run_ps(cmd: str, elevate: bool = False, wait: bool = False) -> None:
    flag = "-Verb RunAs " if elevate else ""
    w = "-Wait " if wait else ""
    subprocess.Popen(["powershell", "-NoProfile", "-Command",
                      f"Start-Process {flag}{w}-FilePath {cmd}"], shell=False)


def preflight() -> dict:
    cfg = json.loads((ENGINE / "config.json").read_text(encoding="utf-8"))
    checks = []

    def add(ok, name, hint=""):
        checks.append({"ok": ok, "name": name, "hint": hint})

    add((ENGINE / "config.json").exists(), "直播引擎配置", str(ENGINE))
    add(bool((cfg.get("brain") or {}).get("api_key")), "对话模型 Key", "在引擎 config.json 的 brain.api_key")
    add(OBS_EXE.exists(), "OBS Studio", "没装 OBS 的话点「重试」旁边的安装")
    if IS_MAC:
        add(False, "弹幕监听（macOS）", "Mac 版采集还在开发，先用 OBS 播测试画面")
    add(BARRAGE.exists(), "弹幕监听程序", "在 talos-live/tools/ 下")
    add(LIVE_MATE.exists(), "抖音直播伴侣", str(LIVE_MATE))
    add(port_open(8321), "直播引擎已在运行", "没运行就点启动")
    add(port_open(8888), "弹幕监听已在运行", "需要管理员权限")
    return {"checks": checks}


@app.get("/api/preflight")
async def api_preflight():
    return preflight()


@app.post("/api/launch")
async def api_launch(payload: dict = Body(default={})):
    steps = []
    # 1. 弹幕监听（要管理员）
    if IS_MAC:
        steps.append("弹幕监听：macOS 版开发中，本场先用固定弹幕")
    elif not port_open(8888):
        run_ps(f"'{BARRAGE}'", elevate=True)
        steps.append("弹幕监听：已请求管理员权限启动")
    else:
        steps.append("弹幕监听：已在运行")

    # 2. OBS
    if not port_open(4455):
        if IS_MAC:
            subprocess.Popen(["open", "-a", "OBS"])
        else:
            run_ps(f"'{OBS_EXE}'")
        steps.append("OBS：启动中")
        time.sleep(6)
    else:
        steps.append("OBS：已在运行")

    # 3. 引擎
    if not port_open(8321):
        subprocess.Popen([PYEXE, "server.py"], cwd=str(ENGINE),
                         stdout=open(ENGINE / "run.log", "a", encoding="utf-8"),
                         stderr=subprocess.STDOUT)
        steps.append("直播引擎：启动中")
        time.sleep(5)
    else:
        steps.append("直播引擎：已在运行")

    # 4. 直播伴侣
    if LIVE_MATE.exists():
        if IS_MAC:
            subprocess.Popen(["open", "-a", "抖音直播伴侣"])
        else:
            run_ps(f"'{LIVE_MATE}'")
        steps.append("直播伴侣：已拉起，去点开播")
    return {"ok": True, "steps": steps}


@app.post("/api/stop")
async def api_stop():
    # 先把这场的数据定稿
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:8321/api/session/finalize", method="POST")
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process python -ErrorAction SilentlyContinue | Where-Object "
                    "{$_.Path -like '*talos-stream*'} | Stop-Process -Force"], capture_output=True)
    return {"ok": True}


def mask_engine_cfg(c: dict) -> dict:
    c = json.loads(json.dumps(c))
    c.setdefault("brain", {})["api_key"] = "***" if c["brain"].get("api_key") else ""
    if c.get("tts", {}).get("fish_api_key"):
        c["tts"]["fish_api_key"] = "***"
    if c.get("images", {}).get("siliconflow_api_key"):
        c["images"]["siliconflow_api_key"] = "***"
    return c


def restart_engine() -> bool:
    if not port_open(8321):
        return False
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process python -ErrorAction SilentlyContinue | Where-Object "
                    "{$_.Path -like '*talos-stream*'} | Stop-Process -Force"], capture_output=True)
    time.sleep(2)
    subprocess.Popen([PYEXE, "server.py"], cwd=str(ENGINE),
                     stdout=open(ENGINE / "run.log", "a", encoding="utf-8"),
                     stderr=subprocess.STDOUT)
    time.sleep(4)
    return True


@app.get("/api/setup")
async def api_setup():
    cfg = json.loads((ENGINE / "config.json").read_text(encoding="utf-8"))
    profs = [d for d in PROFILES.iterdir() if d.is_dir() and (d / "profile.json").exists()]
    has_pose = any((d / "poses" / "c_idle.png").exists() for d in profs)
    return {
        "obs": OBS_EXE.exists(),
        "live_mate": LIVE_MATE.exists(),
        "key": bool((cfg.get("brain") or {}).get("api_key")),
        "profile": bool(profs),
        "poses": has_pose,
        "engine": port_open(8321),
    }


@app.post("/api/setup/install-obs")
async def api_install_obs():
    cmd = ("Start-Process -Verb RunAs -Wait -FilePath winget "
           "-ArgumentList 'install','--id','OBSProject.OBSStudio','-e',"
           "'--accept-package-agreements','--accept-source-agreements'")
    subprocess.Popen(["powershell", "-NoProfile", "-Command", cmd])
    return {"ok": True, "msg": "已弹出安装窗口，装完回来点重新检查"}


@app.post("/api/setup/open-download")
async def api_open_download():
    import webbrowser
    webbrowser.open("https://streamingtool.douyin.com/")
    return {"ok": True, "msg": "已打开官方下载页"}


@app.post("/api/gen")
async def api_gen(payload: dict = Body(...)):
    """用语言描述生成形象图。返回可预览的 URL，用户挑一张再装进形象槽。"""
    desc = (payload.get("desc") or "").strip()
    style = (payload.get("style") or "cute").strip()
    if not desc:
        return JSONResponse({"error": "先描述一下你想要的形象"}, status_code=400)

    style_map = {
        "cute": "cute chibi mascot character, big eyes, soft rounded shapes, pastel mint green and cream palette",
        "cool": "stylish anime character, clean lineart, confident pose, mint green and charcoal palette",
        "simple": "minimal flat vector mascot, geometric shapes, two tone mint green and off white",
    }
    prompt = (f"full body character, {desc}, {style_map.get(style, style_map['cute'])}, "
              "plain pure white background, full figure visible, centered, front view, "
              "high quality illustration, no text, no watermark")

    gen_dir = ROOT / "generated"
    gen_dir.mkdir(exist_ok=True)
    name = f"g{int(time.time()*1000)}.jpg"

    # 1) 画图接口
    try:
        import requests as rq
        url = "https://image.pollinations.ai/prompt/" + rq.utils.quote(prompt)
        r = rq.get(url, params={"width": 768, "height": 1024, "nologo": "true",
                                "seed": int(time.time()) % 99999}, timeout=90,
                   proxies={"http": None, "https": None})
        if r.status_code == 200 and len(r.content) > 2000:
            (gen_dir / name).write_bytes(r.content)
            return {"ok": True, "url": f"/gen/{name}", "kind": "image"}
    except Exception:
        pass

    # 2) 退路：让模型直接写 SVG
    try:
        cfg = json.loads((ENGINE / "config.json").read_text(encoding="utf-8"))
        b = cfg["brain"]
        sys_prompt = ("You are a character illustrator. Output ONE <svg viewBox=\"0 0 400 520\"> "
                      "drawing a full body chibi character. Use only path/circle/ellipse/rect/polygon, "
                      "flat colors, clean shapes, plain background rect, no text, no script, no external links. "
                      "Output only the SVG.")
        import requests as rq
        r = rq.post(b["api"].rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + b["api_key"]},
                    json={"model": b.get("model", "deepseek-chat"),
                          "messages": [{"role": "system", "content": sys_prompt},
                                       {"role": "user", "content": prompt}],
                          "max_tokens": 2000, "temperature": 0.85},
                    timeout=60, proxies={"http": None, "https": None})
        text = (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
        import re as _re
        m = _re.search(r"<svg[\s\S]*?</svg>", text or "", _re.I)
        if m and "href" not in m.group(0).lower():
            svg_name = name.replace(".jpg", ".svg")
            (gen_dir / svg_name).write_text(m.group(0), encoding="utf-8")
            return {"ok": True, "url": f"/gen/{svg_name}", "kind": "svg"}
    except Exception:
        pass

    return JSONResponse({"error": "这次没画出来，换个说法再试"}, status_code=502)


def svg_to_png(svg_path: Path, out: Path) -> bool:
    """把 SVG 渲染成带透明背景的 PNG，借 Edge 无头模式。"""
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if not edge.exists():
        return False
    tmp = svg_path.with_suffix(".html")
    tmp.write_text("<html><body style='margin:0;background:transparent'>"
                   + svg_path.read_text(encoding="utf-8") + "</body></html>", encoding="utf-8")
    try:
        subprocess.run([str(edge), "--headless=new", "--disable-gpu", "--no-first-run",
                        "--default-background-color=00000000", "--hide-scrollbars",
                        f"--screenshot={out}", "--window-size=800,1040",
                        f"file:///{tmp.as_posix()}"], capture_output=True, timeout=60)
    finally:
        tmp.unlink(missing_ok=True)
    return out.exists() and out.stat().st_size > 2000


@app.post("/api/profiles/{pid}/adopt")
async def api_adopt(pid: str, payload: dict = Body(...)):
    """把 AI 生成的图装进某个姿态槽。"""
    url = (payload.get("url") or "").replace("/gen/", "")
    slot = payload.get("slot") or "idle"
    if slot not in SLOTS:
        return JSONResponse({"error": "bad slot"}, status_code=400)
    src = ROOT / "generated" / url
    if not src.exists():
        return JSONResponse({"error": "文件没了，重新生成一次"}, status_code=404)

    d = profile_dir(pid) / "poses"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"c_{slot}.png"

    if src.suffix == ".svg":
        if not svg_to_png(src, dst):
            return JSONResponse({"error": "矢量图转换失败，换一张试试"}, status_code=500)
    else:
        img = process_upload(src.read_bytes())
        img.save(dst)
    return {"ok": True, "slot": slot, "url": f"/pose/{pid}/{slot}?t={int(time.time())}"}


@app.get("/gen/{name}")
async def api_gen_file(name: str):
    f = ROOT / "generated" / name
    if not f.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    mt = "image/svg+xml" if f.suffix == ".svg" else "image/jpeg"
    return FileResponse(str(f), media_type=mt, headers={"Cache-Control": "no-store"})


@app.post("/api/profiles/from-template")
async def api_from_template(payload: dict = Body(...)):
    """复制一个已有形象当模板，让新用户不用先做图也能开播。"""
    name = (payload.get("name") or "模板形象").strip()[:20]
    src = None
    for d in PROFILES.iterdir():
        if d.is_dir() and (d / "poses" / "c_idle.png").exists():
            src = d
            break
    if not src:
        return JSONResponse({"error": "没有可用的模板形象"}, status_code=400)

    pid = "p" + uuid.uuid4().hex[:8]
    dst = PROFILES / pid
    shutil.copytree(src, dst)
    data = load_profile(pid)
    data["name"] = name
    data["active"] = False
    save_profile(pid, data)
    return {"ok": True, "id": pid}


@app.get("/api/settings")
async def api_settings():
    cfg = json.loads((ENGINE / "config.json").read_text(encoding="utf-8"))
    return {"config": mask_engine_cfg(cfg)}


@app.post("/api/settings")
async def api_settings_save(payload: dict = Body(...)):
    path = ENGINE / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    new = payload or {}

    for sec in ("brain", "voices", "tts", "images", "goal", "layout"):
        if isinstance(new.get(sec), dict):
            cfg.setdefault(sec, {}).update(new[sec])
    for k in ("auto_act", "danmaku_can_draw", "danmaku_can_game", "idle_seconds"):
        if k in new:
            cfg[k] = new[k]

    # 回填被掩码的 Key
    if cfg["brain"].get("api_key") in ("***", "", None):
        cfg["brain"]["api_key"] = json.loads(path.read_text(encoding="utf-8"))["brain"].get("api_key", "")
    if (cfg.get("tts") or {}).get("fish_api_key") in ("***", "", None):
        cfg["tts"]["fish_api_key"] = json.loads(path.read_text(encoding="utf-8")).get("tts", {}).get("fish_api_key", "")
    if (cfg.get("images") or {}).get("siliconflow_api_key") in ("***", "", None):
        cfg["images"]["siliconflow_api_key"] = json.loads(path.read_text(encoding="utf-8"))["images"].get("siliconflow_api_key", "")

    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    restarted = restart_engine() if payload.get("restart") else False
    return {"ok": True, "restarted": restarted}


@app.post("/api/chat")
async def api_chat(payload: dict = Body(...)):
    """用选中形象的设定跟用户对话。"""
    pid = payload.get("profile") or ""
    msgs = payload.get("messages") or []
    data = load_profile(pid)
    if not data:
        return JSONResponse({"error": "profile not found"}, status_code=404)
    persona = (data.get("persona") or {}).get("prompt") or build_prompt(data)
    cfg = json.loads((ENGINE / "config.json").read_text(encoding="utf-8"))
    b = cfg.get("brain") or {}
    if not b.get("api_key"):
        return {"reply": "还没配好对话模型的 Key，去引擎配置里填一下。", "tokens": 0}

    import requests as rq
    try:
        r = rq.post(b["api"].rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + b["api_key"]},
                    json={"model": b.get("model", "deepseek-chat"),
                          "messages": [{"role": "system", "content": persona}] + msgs[-12:],
                          "max_tokens": 300, "temperature": 0.9},
                    timeout=40, proxies={"http": None, "https": None})
        d = r.json()
        reply = (d.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        used = int((d.get("usage") or {}).get("total_tokens") or 0)
        return {"reply": reply or "……", "tokens": used}
    except Exception as e:
        return {"reply": f"说不上话：{type(e).__name__}", "tokens": 0}


@app.get("/api/sessions")
async def api_sessions():
    f = ENGINE / "sessions.json"
    if not f.exists():
        return {"sessions": []}
    try:
        return {"sessions": json.loads(f.read_text(encoding="utf-8"))[::-1][:8]}
    except Exception:
        return {"sessions": []}


@app.get("/api/engine/state")
async def engine_state():
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8321/api/state", timeout=2) as r:
            return JSONResponse(json.loads(r.read()))
    except Exception:
        return JSONResponse({"offline": True})


ensure_default_profile()

if __name__ == "__main__":
    import uvicorn
    print("Talos Studio → http://127.0.0.1:8400")
    uvicorn.run(app, host="127.0.0.1", port=8400, log_level="warning")
