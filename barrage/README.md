# macOS 弹幕采集

Windows 上用的是 `DouyinBarrageGrab`（.NET，靠系统代理抓包）。macOS 上没有对应实现，
这里用 mitmproxy 截同一路 WebSocket 流量，纯 Python 解析 protobuf。

## 装

```bash
pip3 install mitmproxy websockets
```

## 装证书（只需一次）

1. 起一次代理：`mitmdump --listen-port 8899`
2. 浏览器访问 `http://mitm.it`，下载 macOS 证书，双击装进「钥匙串」
3. 钥匙串里找到 mitmproxy 证书 → 右键「显示简介」→ 信任 → 始终信任
4. 关掉 mitmdump

## 跑

```bash
mitmdump -s barrage/mitm_addon.py --listen-port 8899
```

它会：
- 截获抖音直播伴侣的 WebSocket 帧，解出弹幕/礼物/进场
- 在本机 `8888` 起一个 WebSocket，直播引擎连上就能收到

## 让直播伴侣走代理

系统设置 → 网络 → 详细信息 → 代理 → 网页代理(HTTP) 和 安全网页代理(HTTPS)
都填 `127.0.0.1` `8899`。然后重启直播伴侣。

## 注意

- 字段号来自公开的逆向资料，抖音改版就会失效，表现是「连上了但没消息」
- 解析失败的帧只打日志，不会中断
- 播完记得把系统代理关掉

## 自测

```bash
python -m pytest tests/test_barrage.py -q
```

用手工构造的 protobuf 验证解码链路，不依赖真实直播间。
