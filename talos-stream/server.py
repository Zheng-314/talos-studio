# -*- coding: utf-8 -*-
"""Talos 直播间 · 后台

一个进程干完所有事：接弹幕、想台词、出声音、跑活动、往外推状态。
前端 web/index.html 只负责画，不负责想。

启动：
    .venv\\Scripts\\python.exe server.py          # 正式，等真实弹幕
    .venv\\Scripts\\python.exe server.py --demo   # 演示，自己造弹幕
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import time
import uuid
from pathlib import Path

import requests
import uvicorn
import websockets
from edge_tts import Communicate
from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
GAL = WEB / "gallery"
AUD = WEB / "audio"
GAL.mkdir(parents=True, exist_ok=True)
AUD.mkdir(parents=True, exist_ok=True)
for _old in AUD.glob("*.mp3"):      # 每次启动清空旧语音
    try:
        _old.unlink()
    except Exception:
        pass

_CFG = ROOT / "config.json"
if not _CFG.exists():
    _example = ROOT / "config.example.json"
    if _example.exists():
        _CFG.write_bytes(_example.read_bytes())
CFG = json.loads(_CFG.read_text(encoding="utf-8"))
SESSION_FILE = ROOT / "sessions.json"

# 模型和画图请求不走系统代理（弹幕监听会把系统代理指到 8827，绕一手既慢又容易 SSL 出错）
NET = requests.Session()
NET.trust_env = False

POSES = ["idle", "type", "happy", "talk", "think", "surprise"]


def now() -> float:
    return time.time()


class Stream:
    def __init__(self) -> None:
        self.started = now()
        self.last_danmaku = now()
        self.mode = "idle"
        self.pose = "idle"
        self.caption = {"text": "", "who": "talos", "at": 0.0}
        self.activity: dict = {"kind": "idle"}
        self.chat: list[dict] = []
        self.bubbles: list[dict] = []
        self.log: list[dict] = []
        self.audio: list[dict] = []
        self.seq = 0
        self.metrics = {
            "tokens": 0, "gifts": 0, "gift_value": 0,
            "danmaku": 0, "follows": 0, "mood": 60, "acts": 0,
        }
        self.recent: list[str] = []
        self.calls: list[dict] = []
        self._speaking = False
        self._last_act_end = now() - 20
        self._mood_target = 60

    # ---------- 对外状态 ----------
    def snapshot(self) -> dict:
        return {
            "uptime": int(now() - self.started),
            "mode": self.mode,
            "pose": self.pose,
            "caption": self.caption,
            "activity": self.activity,
            "chat": self.chat[-6:],
            "bubbles": [b for b in self.bubbles if now() - b["at"] < 12],
            "log": self.log[-7:],
            "metrics": self.metrics,
            "goal": CFG["goal"],
            "speaking": self._speaking,
            "layout": CFG.get("layout") or {"scale": 1.0, "x": 0, "y": 0},
            "audio_seq": self.seq,
        }

    def note(self, text: str) -> None:
        self.log.append({"t": time.strftime("%H:%M:%S"), "text": text})
        self.log = self.log[-40:]

    # ---------- 心情 ----------
    def bump_mood(self, delta: float) -> None:
        self.metrics["mood"] = max(0, min(100, self.metrics["mood"] + delta))

    def mood_tick(self) -> None:
        # 缓慢回落到 55；长时间没人说话会掉
        target = 55.0
        if now() - self.last_danmaku > 60:
            target = 42.0
        m = self.metrics["mood"]
        self.metrics["mood"] = m + (target - m) * 0.004

    # ---------- 输出：字幕 + 语音 ----------
    _reply_to: str = ""
    _question: str = ""

    async def say(self, text: str, who: str = "talos", pose: str | None = None,
                  reply_to: str = "", question: str = "") -> None:
        text = (text or "").strip()
        if not text:
            return
        if pose:
            self.pose = pose
        voice = CFG["voices"].get(who, CFG["voices"]["talos"])
        self._reply_to, self._question = reply_to, question
        parts = [p for p in re.split(r"(?<=[。！？!?…])\s*", text) if p.strip()]
        self._speaking = True
        self.seq += 1
        seq = self.seq
        self.caption = {"text": text, "who": who, "at": now(),
                        "reply_to": self._reply_to, "question": self._question}
        for part in parts[:4]:
            if who == "talos" and pose is None:
                self.pose = "talk"
            name = f"{seq:05d}_{uuid.uuid4().hex[:6]}.mp3"
            path = AUD / name
            try:
                ok = False
                if who == "talos" and (CFG.get("tts") or {}).get("provider") == "fish":
                    ok = await asyncio.to_thread(tts_fish, part.strip(), path)
                if not ok:
                    await Communicate(part.strip(), voice=voice, rate="+6%").save(str(path))
                self.audio.append({"seq": seq, "who": who, "url": f"/audio/{name}", "text": part.strip()})
                self.audio = self.audio[-40:]
                await asyncio.sleep(min(0.35 + len(part) * 0.012, 1.2))
            except Exception as e:
                self.note(f"配音失败 {type(e).__name__}")
        self._speaking = False
        self.pose = "idle" if pose is None else self.pose
        ST.recent.append(text[:40])
        ST.recent = ST.recent[-14:]


ST = Stream()


def save_session(final: bool = False) -> None:
    """把这一场的数据写下来，每 60 秒更新一次，下播时定稿。"""
    rec = {
        "id": int(ST.started),
        "start": time.strftime("%Y-%m-%d %H:%M", time.localtime(ST.started)),
        "duration": int(now() - ST.started),
        "danmaku": ST.metrics["danmaku"],
        "gifts": ST.metrics["gifts"],
        "follows": ST.metrics["follows"],
        "tokens": ST.metrics["tokens"],
        "acts": ST.metrics["acts"],
        "mood": round(ST.metrics["mood"]),
        "final": final,
    }
    rows = []
    if SESSION_FILE.exists():
        try:
            rows = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            rows = []
    rows = [r for r in rows if r.get("id") != rec["id"]]
    rows.append(rec)
    rows = sorted(rows, key=lambda r: r.get("id", 0))[-60:]
    try:
        SESSION_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


async def session_ticker() -> None:
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(save_session, False)


# ---------------- 大脑 ----------------
def think(system: str, user: str, max_tokens: int | None = None, tag: str = "") -> str:
    b = CFG["brain"]
    try:
        r = NET.post(
            b["api"].rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + b["api_key"], "Content-Type": "application/json"},
            json={
                "model": b["model"],
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": max_tokens or b["max_tokens"],
                "temperature": b["temperature"],
            },
            timeout=30,
        )
        if r.status_code != 200:
            ST.note(f"模型返回 {r.status_code}")
            return ""
        d = r.json()
        used = int((d.get("usage") or {}).get("total_tokens") or 0)
        ST.metrics["tokens"] += used
        ST.calls.append({"t": time.strftime("%H:%M:%S"), "tokens": used, "tag": tag,
                         "q": user.replace(chr(10), " ")[:46]})
        return (d["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        ST.note(f"模型异常 {type(e).__name__}")
        return ""


def tts_fish(text: str, path: Path) -> bool:
    """Fish Audio 合成，失败返回 False 由调用方回退。"""
    t = CFG.get("tts") or {}
    key = (t.get("fish_api_key") or "").strip()
    if not key:
        return False
    body = {
        "text": text, "format": "mp3", "mp3_bitrate": 192,
        "latency": "normal", "normalize": True,
        "prosody": {"speed": float(t.get("fish_speed") or 1.0), "volume": 0,
                    "normalize_loudness": True},
    }
    ref = (t.get("fish_reference_id") or "").strip()
    if ref:
        body["reference_id"] = ref
    try:
        r = NET.post("https://api.fish.audio/v1/tts", json=body, timeout=60,
                          headers={"Authorization": "Bearer " + key,
                                   "Content-Type": "application/json; charset=utf-8",
                                   "model": t.get("fish_model") or "s2.1-pro-free"})
        if r.status_code != 200 or len(r.content) < 1500:
            ST.note(f"Fish 合成失败 {r.status_code}")
            return False
        path.write_bytes(r.content)
        return True
    except Exception as e:
        ST.note(f"Fish 异常 {type(e).__name__}")
        return False


def clean(text: str) -> str:
    return re.sub(r"[\*#`>_\[\]]", "", text or "").strip()


# ---------------- 弹幕 ----------------
async def reply_to_comment(who: str, text: str) -> None:
    NL = chr(10)
    extra = "现在是冷场，主动一点。" if now() - ST.last_danmaku > 25 else ""
    st = CFG["persona"]["talos"]
    if ST.metrics["mood"] < 35:
        st += "你现在有点低落，说话更短、更平静。"
    elif ST.metrics["mood"] > 75:
        st += "你现在心情很好，语速轻快一点。"
    avoid = "，".join(ST.recent[-5:])
    q = ("观众【" + who + "】说：" + text + NL + extra + NL
         + "要求：开口先用「" + who + "」称呼他；必须正面回答他问的那件事，不要反问代替回答；"
           "一到两句，不超过 50 字。"
         + ((NL + "不要重复你刚说过的话：" + avoid) if avoid else ""))
    ans = clean(think(st, q))
    if ans:
        if who not in ans[:14]:
            ans = who + "，" + ans
        ST.bubbles.append({"who": who, "text": text, "at": now()})
        await ST.say(ans, "talos", reply_to=who, question=text)


async def on_gift(who: str, name: str) -> None:
    ST.metrics["gifts"] += 1
    ST.metrics["gift_value"] += 1
    ST.bump_mood(9)
    ST.bubbles.append({"who": who, "text": f"送出 {name}", "at": now()})
    await ST.say(f"谢谢{who}的{name}，收到。", "talos", pose="surprise")
    ST.pose = "idle"


async def on_enter(who: str) -> None:
    if random.random() < 0.34:
        line = clean(think(CFG["persona"]["talos"], f"{who} 刚进直播间。用一句话打个招呼，别热情过头。", 60))
        if line:
            await ST.say(line, "talos")


async def danmaku_loop() -> None:
    url = CFG["danmaku"]["ws"]
    while True:
        try:
            async with websockets.connect(url, ping_interval=None, close_timeout=2, max_size=2**22) as ws:
                ST.note("弹幕已连接")
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        body = json.loads(msg.get("Data") or "{}")
                    except Exception:
                        continue
                    t = msg.get("Type")
                    who = (body.get("User") or {}).get("Nickname") or "观众"
                    if t == 1:
                        text = (body.get("Content") or "").strip()
                        if not text:
                            continue
                        ST.last_danmaku = now()
                        ST.metrics["danmaku"] += 1
                        ST.bump_mood(2)
                        await route_comment(who, text)
                    elif t == 5:
                        ST.last_danmaku = now()
                        await on_gift(who, body.get("GiftName") or "礼物")
                    elif t == 3:
                        await on_enter(who)
                    elif t == 4:
                        ST.metrics["follows"] += 1
                        ST.bump_mood(12)
                        await ST.say(f"谢谢{who}关注。", "talos", pose="happy")
                        ST.pose = "idle"
        except Exception as e:
            ST.note(f"弹幕断开 {type(e).__name__}，2 秒后重连")
            await asyncio.sleep(2)


STOP = re.compile(
    r"(帮我|给我|你能|能不能|可不可以|可以|麻烦|请|来一张|画一张|画一个|画个|画|做一张|做一个|做个|生成|一个|一张|个头像|头像|图片|看看|一下|个|只|条|张|幅|吗|呢|吧|啊|呀|的|了)")


def pick_theme(text: str) -> str | None:
    """从弹幕里抠出到底要画什么。抠不出东西就返回 None，让它自己挑。"""
    t = STOP.sub("", text).strip("，。！？、 ~")
    t = re.sub(r"^[一两三四五几][只条张幅个]?", "", t).strip("，。！？、 ~")
    return t if len(t) >= 1 else None


async def route_comment(who: str, text: str) -> None:
    """弹幕既能聊天，也能点单。"""
    if CFG.get("danmaku_can_draw", True) and any(
            k in text for k in ("画", "头像", "图片", "来一张", "画个", "画一只")):
        if ST.mode != "idle":
            await ST.say(f"{who}，手上这张先画完，马上轮到你。", "talos")
        else:
            asyncio.create_task(activity_avatar(pick_theme(text), who))
        return
    if CFG.get("danmaku_can_order", False) and any(
            k in text for k in ("吵", "辩", "打架", "对聊", "互怼")):
        asyncio.create_task(activity_duel(topic=text, who=who))
        return
    if CFG.get("danmaku_can_game", False) and any(
            k in text for k in ("游戏", "玩", "通关")):
        asyncio.create_task(activity_game(who=who))
        return
    await reply_to_comment(who, text)


# ---------------- 活动：小游戏 ----------------
GAMES = {
    "snake": {"title": "贪吃蛇 · 自己会玩", "line": "写了个贪吃蛇，它自己找吃的，我盯着它别撞墙。"},
    "breakout": {"title": "打砖块 · 自动挡板", "line": "打砖块，挡板会自己追球，我在看它能撑几轮。"},
    "reaction": {"title": "反应力测试 · 机器手", "line": "测反应速度的，机器手平均 0.21 秒，人类大概 0.25。"},
}


async def activity_game(who: str | None = None) -> None:
    if ST.mode != "idle":
        return
    ST.mode = "game"
    ST.metrics["acts"] += 1
    gid = random.choice(list(GAMES))
    g = GAMES[gid]
    ST.activity = {"kind": "game", "game": gid, "title": g["title"], "note": g["line"], "started": now()}
    ST.note(f"开始做 {g['title']}")
    await ST.say(g["line"], "talos", pose="talk")
    t0 = now()
    while now() - t0 < 75 and ST.mode == "game":
        await asyncio.sleep(20)
        if ST.mode != "game":
            break
        avoid = "，".join(ST.recent[-4:])
        line = clean(think(CFG["persona"]["talos"],
                           f"你正在直播玩自己写的{g['title']}，对观众说一句解说，一句就行。"
                           f"不要重复这些：{avoid}", 70))
        if line:
            await ST.say(line, "talos")
    if ST.mode == "game":
        ST.mode = "idle"
        ST.activity = {"kind": "idle"}
        ST.pose = "idle"
        ST._last_act_end = now()


# ---------------- 活动：AI 头像 ----------------
THEMES = [
    ("a jellyfish wearing headphones", "戴耳机的水母"),
    ("a glowing mushroom", "会发光的蘑菇"),
    ("a cat in a business suit", "穿西装的猫"),
    ("a cup of coffee working overtime", "一杯在加班的咖啡"),
    ("a bronze moon", "青铜色的月亮"),
    ("a cactus writing code", "写代码的仙人掌"),
    ("a rocket with a backpack", "背书包的火箭"),
    ("a snowman with glasses", "戴眼镜的雪人"),
]


def to_english(theme: str) -> str:
    """中文题目直接喂给画图模型会跑偏，先让模型翻成一句英文描述。"""
    if all(ord(ch) < 128 for ch in theme):
        return theme
    out = clean(think("Translate the Chinese into a detailed English image description. "
                      "Keep every visual detail the user mentioned (subject, action, setting, objects). "
                      "Output only the English, one sentence, max 30 words, no quotes, no explanation.",
                      theme, 70))
    bad = ("provide", "please", "translate", "sorry", "text to")
    if not out or len(out) > 90 or any(b in out.lower() for b in bad):
        return ""
    return out


STYLE = ("high quality illustration, clean composition, soft lighting, "
         "plain simple background, centered, no text, no watermark")


_last_img = {"t": 0.0}


SVG_SYS = (
    "你是插画师，用纯 SVG 代码画图。规则：\n"
    "1. 只输出一个 <svg> 标签，viewBox=\"0 0 400 400\"，width=400 height=400。\n"
    "2. 只用 path、circle、ellipse、rect、polygon、polyline、g 这些元素，允许 fill、stroke、stroke-width、opacity。\n"
    "3. 扁平插画风格，描边 3-5，主色 #19A26A / #7FE0B4 / #F7F6F2 / #0E7A4E，可用少量暖色点缀。\n"
    "4. 不要 <style>、<script>、滤镜、外链图片、文字。\n"
    "5. 主体居中，占画面 70% 以上，背景透明或纯色圆角底。\n"
    "6. 不要任何解释，直接给 SVG 代码。"
)

_RE_SVG = __import__("re").compile(r"<svg[\s\S]*?</svg>", __import__("re").IGNORECASE)


def make_svg(theme: str, tag: str) -> str | None:
    """让模型直接写 SVG，纯代码画图，零外部依赖。"""
    raw = think(SVG_SYS, "画：" + theme + "。只输出 SVG 代码。", 1400, tag="svg")
    if not raw:
        return None
    raw = raw.replace("```svg", "").replace("```xml", "").replace("```", "")
    m = _RE_SVG.search(raw)
    if not m:
        ST.note("模型没给出 SVG")
        return None
    svg = m.group(0)
    low = svg.lower()
    if len(svg) > 30000 or "<script" in low or "href" in low or "xlink" in low:
        ST.note("SVG 不合规，丢弃")
        return None
    if "viewbox" not in low:
        svg = svg.replace("<svg", '<svg viewBox="0 0 400 400"', 1)
    name = f"{int(now())}_{tag}.svg"
    (GAL / name).write_text(svg, encoding="utf-8")
    return f"/gallery/{name}"


def make_image_siliconflow(prompt: str, tag: str) -> str | None:
    conf = CFG.get("images") or {}
    key = (conf.get("siliconflow_api_key") or "").strip()
    if not key:
        return None
    try:
        r = NET.post("https://api.siliconflow.cn/v1/images/generations",
                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                     json={"model": conf.get("siliconflow_model") or "Kwai-Kolors/Kolors",
                           "prompt": prompt,
                           "image_size": f"{conf.get('size', 1024)}x{conf.get('size', 1024)}",
                           "batch_size": 1, "num_inference_steps": 20,
                           "guidance_scale": 7.5, "seed": random.randint(1, 999999)},
                     timeout=90)
        if r.status_code != 200:
            ST.note(f"硅基流动出图失败 {r.status_code}")
            return None
        url = ((r.json().get("images") or [{}])[0]).get("url")
        if not url:
            ST.note("硅基流动没返回图片地址")
            return None
        img = NET.get(url, timeout=60)
        if img.status_code != 200 or len(img.content) < 2000:
            return None
        name = f"{int(now())}_{tag}.jpg"
        (GAL / name).write_bytes(img.content)
        return f"/gallery/{name}"
    except Exception as e:
        ST.note(f"硅基流动异常 {type(e).__name__}")
        return None


def make_image(prompt: str, tag: str) -> str | None:
    ST.note(f"生成图片：{prompt[:34]}…")
    if (CFG.get("images") or {}).get("provider") == "siliconflow":
        img = make_image_siliconflow(prompt, tag)
        if img:
            return img
        ST.note("硅基流动没出来，退回免费接口")
    # 免费额度对连发很敏感，两次之间至少隔 45 秒
    wait = 45 - (now() - _last_img["t"])
    if wait > 0:
        time.sleep(wait)
    url = "https://image.pollinations.ai/prompt/" + requests.utils.quote(prompt)
    for attempt in range(3):
        try:
            r = NET.get(url, params={"width": 768, "height": 768, "nologo": "true",
                                          "seed": random.randint(1, 99999)}, timeout=90)
            if r.status_code == 200 and len(r.content) > 2000:
                name = f"{int(now())}_{tag}.jpg"
                dst = GAL / name
                dst.write_bytes(r.content)
                # 免费额度右下角有水印，裁掉底部 7%
                try:
                    from PIL import Image
                    im = Image.open(dst)
                    im.crop((0, 0, im.width, int(im.height * 0.93))).save(dst, quality=92)
                except Exception:
                    pass
                _last_img["t"] = now()
                return f"/gallery/{name}"
            ST.note(f"出图重试 {attempt+1} ({r.status_code})")
        except Exception as e:
            ST.note(f"出图异常 {type(e).__name__}")
        time.sleep(6 + attempt * 9)
    ST.note("出图失败，放弃")
    return None


async def activity_avatar(theme: str | None = None, who: str | None = None) -> None:
    if ST.mode != "idle":
        ST.note("手上还有活，等会儿再画")
        return
    ST.mode = "avatar"
    ST.metrics["acts"] += 1
    if theme:
        theme_en = to_english(theme) if (CFG.get("images") or {}).get("provider") != "svg" else theme
        theme_cn = theme
    if not theme or not theme_en:
        theme_en, theme_cn = random.choice(THEMES)
    ST.activity = {"kind": "avatar", "title": f"在画：{theme_cn}", "note": "等出图",
                   "img": None, "theme": theme_cn, "who": who or "", "started": now()}
    ask = f"{who} 要一个{theme_cn}，" if who else "自己挑的题目："
    await ST.say(f"{ask}我画一张试试。", "talos", pose="think")
    provider = (CFG.get("images") or {}).get("provider")
    if provider == "svg":
        img = await asyncio.to_thread(make_svg, theme_cn, "av")
    else:
        prompt = f"{theme_en}, " + STYLE
        img = await asyncio.to_thread(make_image, prompt, "av")
    if img:
        ST.activity["img"] = img
        ST.activity["note"] = "出图完成"
        ST.note("出图完成")
        await ST.say("出来了，看屏幕。", "talos", pose="happy")
        ST.pose = "idle"
        await asyncio.sleep(22)
    else:
        ST.activity["note"] = "这次没画出来"
        await ST.say("这次没画出来，回头再试。", "talos")
    ST.mode = "idle"
    ST.activity = {"kind": "idle", "last": "avatar", "img": img,
                   "theme": theme_cn, "who": who or ""}
    ST.pose = "idle"
    ST._last_act_end = now()


# ---------------- 活动：双 AI 对聊 ----------------
DUEL_TOPICS = ["AI 会不会让人变懒", "先有流量还是先有好产品", "学生该不该现在做副业",
               "一个人能不能靠 AI 做出一家公司", "该不该把所有工作都交给 AI"]


async def activity_duel(topic: str | None = None, who: str | None = None) -> None:
    if not CFG.get("activities", {}).get("duel", True):
        ST.note("双 AI 对聊已关闭")
        return
    if ST.mode != "idle":
        return
    ST.mode = "duel"
    ST.metrics["acts"] += 1
    topic = topic or random.choice(DUEL_TOPICS)
    ST.activity = {"kind": "duel", "title": f"对聊：{topic}", "note": "阿刺 上线", "started": now()}
    ST.chat = []
    ST.note(f"双 AI 对聊：{topic}")
    await ST.say(f"阿刺来了。今天聊{topic}，你先说。", "talos", pose="talk")

    for i in range(6):
        if ST.mode != "duel":
            break
        speaker = "rival" if i % 2 == 0 else "talos"
        history = "\n".join(f"{m['who']}：{m['text']}" for m in ST.chat[-4:])
        line = clean(think(
            CFG["persona"][speaker],
            f"话题：{topic}\n前面的对话：\n{history or '（刚开始）'}\n轮到你说了，一到两句，别重复别人的话。",
            90,
        ))
        if not line:
            break
        label = "阿刺" if speaker == "rival" else "Talos"
        ST.chat.append({"who": speaker, "name": label, "text": line})
        await ST.say(line, "rival" if speaker == "rival" else "talos")
        await asyncio.sleep(1.2)

    ST.mode = "idle"
    ST.activity = {"kind": "idle", "last": "duel", "topic": topic}
    ST.pose = "idle"
    ST._last_act_end = now()


# ---------------- 调度 ----------------
async def scheduler() -> None:
    while True:
        await asyncio.sleep(2)
        ST.mood_tick()
        if ST.mode != "idle":
            continue
        if not CFG.get("auto_act", True):
            continue
        idle_for = now() - max(ST.last_danmaku, ST._last_act_end)
        if idle_for > float(CFG.get("idle_seconds", 90)):
            pick = random.choice(["game", "avatar", "duel"])
            ST.note(f"闲着 {int(idle_for)} 秒，自己找事做：{pick}")
            if pick == "game":
                asyncio.create_task(activity_game())
            elif pick == "avatar":
                asyncio.create_task(activity_avatar())
            else:
                asyncio.create_task(activity_duel())


DEMO_LINES = [
    ("小满", "这个是真人吗"), ("阿树", "你能帮我画个头像吗"), ("coco", "做这个要钱吗"),
    ("柚子", "你现在在干嘛"), ("大熊", "能帮我写作业吗"), ("小满", "声音好好听"),
    ("阿树", "来一张戴帽子的柴犬"), ("cici", "你们俩吵一架看看"),
]


async def demo_loop() -> None:
    while True:
        await asyncio.sleep(random.randint(9, 16))
        who, text = random.choice(DEMO_LINES)
        ST.last_danmaku = now()
        ST.metrics["danmaku"] += 1
        ST.bump_mood(2)
        await route_comment(who, text)


# ---------------- Web ----------------

app = FastAPI()
app.mount("/assets", StaticFiles(directory=str(ROOT / "assets")), name="assets")
app.mount("/audio", StaticFiles(directory=str(AUD)), name="audio")
app.mount("/gallery", StaticFiles(directory=str(GAL)), name="gallery")
app.mount("/games", StaticFiles(directory=str(WEB / "games")), name="games")


@app.get("/")
async def index():
    return FileResponse(str(WEB / "index.html"))


@app.get("/api/state")
async def api_state():
    return JSONResponse(ST.snapshot())


@app.post("/api/session/finalize")
async def api_session_finalize():
    await asyncio.to_thread(save_session, True)
    ST.note("本场数据已存档")
    return {"ok": True}


@app.get("/api/sessions")
async def api_sessions():
    if not SESSION_FILE.exists():
        return {"sessions": []}
    try:
        return {"sessions": json.loads(SESSION_FILE.read_text(encoding="utf-8"))[::-1][:12]}
    except Exception:
        return {"sessions": []}


@app.get("/api/audio")
async def api_audio(since: int = Query(0)):
    return JSONResponse({"items": [a for a in ST.audio if a["seq"] > since]})


@app.get("/api/say")
async def api_say(text: str, who: str = "talos"):
    await ST.say(text, who)
    return {"ok": True}


@app.get("/api/act")
async def api_act(kind: str):
    if kind == "game":
        asyncio.create_task(activity_game())
    elif kind == "avatar":
        asyncio.create_task(activity_avatar())
    elif kind == "duel":
        asyncio.create_task(activity_duel())
    return {"ok": True}


# ---------------- 后台管理 ----------------
from fastapi import Body  # noqa: E402


def public_cfg() -> dict:
    c = json.loads(json.dumps(CFG))
    c["brain"]["api_key"] = "***"
    if c.get("tts") and c["tts"].get("fish_api_key"):
        c["tts"]["fish_api_key"] = "***"
    if c.get("images") and c["images"].get("siliconflow_api_key"):
        c["images"]["siliconflow_api_key"] = "***"
    return c


def save_cfg() -> None:
    (ROOT / "config.json").write_text(
        json.dumps(CFG, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/admin")
async def admin_page():
    return FileResponse(str(WEB / "admin.html"))


@app.get("/api/admin/state")
async def admin_state():
    poses = sorted(p.stem[2:] for p in (ROOT / "assets" / "poses").glob("c_*.png"))
    return JSONResponse({
        "stream": ST.snapshot(),
        "config": public_cfg(),
        "poses": poses,
        "calls": ST.calls[-14:],
        "gallery": sorted((p.name for p in GAL.glob("*.jpg")), reverse=True)[:12],
        "log_all": ST.log[-24:],
    })


@app.post("/api/admin/config")
async def admin_config(payload: dict = Body(...)):
    for key, val in payload.items():
        if isinstance(val, dict) and isinstance(CFG.get(key), dict):
            CFG[key].update(val)
        else:
            CFG[key] = val
    old = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    if CFG["brain"].get("api_key") in ("***", "", None):
        CFG["brain"]["api_key"] = old["brain"]["api_key"]
    if CFG.get("tts", {}).get("fish_api_key") in ("***", "", None):
        CFG.setdefault("tts", {})["fish_api_key"] = old.get("tts", {}).get("fish_api_key", "")
    if CFG.get("images", {}).get("siliconflow_api_key") in ("***", "", None):
        CFG.setdefault("images", {})["siliconflow_api_key"] = old.get("images", {}).get("siliconflow_api_key", "")
    save_cfg()
    ST.note("配置已更新")
    return {"ok": True, "config": public_cfg()}


@app.post("/api/admin/comment")
async def admin_comment(payload: dict = Body(...)):
    who = (payload.get("who") or "测试观众").strip()[:12]
    text = (payload.get("text") or "").strip()
    if not text:
        return {"ok": False}
    ST.last_danmaku = now()
    ST.metrics["danmaku"] += 1
    ST.bump_mood(2)
    asyncio.create_task(route_comment(who, text))
    ST.note(f"模拟弹幕 {who}：{text[:24]}")
    return {"ok": True}


@app.post("/api/admin/say")
async def admin_say(payload: dict = Body(...)):
    text = (payload.get("text") or "").strip()
    if text:
        asyncio.create_task(ST.say(text, payload.get("who") or "talos",
                                   reply_to=payload.get("reply_to") or "",
                                   question=payload.get("question") or ""))
    return {"ok": bool(text)}


@app.post("/api/admin/act")
async def admin_act(payload: dict = Body(...)):
    kind = payload.get("kind")
    ST._last_act_end = now()
    if kind == "game":
        asyncio.create_task(activity_game())
    elif kind == "avatar":
        asyncio.create_task(activity_avatar(payload.get("theme") or None))
    elif kind == "duel":
        if CFG.get("activities", {}).get("duel", True):
            asyncio.create_task(activity_duel(payload.get("topic") or None))
        else:
            ST.note("双 AI 对聊已关闭")
    return {"ok": True}


@app.post("/api/admin/idle")
async def admin_idle():
    ST.mode = "idle"
    ST.activity = {"kind": "idle"}
    ST.pose = "idle"
    ST.note("手动切回待机")
    return {"ok": True}


@app.post("/api/admin/pose")
async def admin_pose(payload: dict = Body(...)):
    p = payload.get("pose")
    if p in POSES:
        ST.pose = p
    return {"ok": True}


@app.post("/api/admin/upload")
async def admin_upload(name: str = Form(...), file: UploadFile = File(...)):
    safe = re.sub(r"[^a-z0-9_]", "", name.lower()) or "custom"
    dst = ROOT / "assets" / "poses" / f"c_{safe}.png"
    dst.write_bytes(await file.read())
    ST.note(f"形象已替换：{safe}")
    return {"ok": True, "pose": safe}




async def main(demo: bool) -> None:
    ST.note("Talos 直播间启动")
    asyncio.create_task(scheduler())
    asyncio.create_task(session_ticker())
    if demo:
        asyncio.create_task(demo_loop())
        ST.note("演示模式：自己造弹幕")
    else:
        asyncio.create_task(danmaku_loop())
    cfg = uvicorn.Config(app, host="127.0.0.1", port=8321, log_level="warning")
    await uvicorn.Server(cfg).serve()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(main(a.demo))
    except KeyboardInterrupt:
        pass
