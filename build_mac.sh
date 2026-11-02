#!/bin/bash
# 在 macOS 上打包 Talos Studio 成 .app
# 用法：bash build_mac.sh
set -e
cd "$(dirname "$0")"

echo "== 1/4 准备运行环境 =="
if [ ! -d ".venv-build" ]; then
  python3 -m venv .venv-build
fi
source .venv-build/bin/activate
pip install -q --upgrade pip
pip install -q pyinstaller fastapi uvicorn python-multipart pillow numpy requests edge-tts websockets

echo "== 2/4 生成图标 =="
python - <<'PY'
from PIL import Image
im = Image.open("build/character_256.png")
im.save("static/character.icns", format="ICNS")
PY

echo "== 3/4 打包 =="
pyinstaller --noconfirm --windowed --name "Talos Studio" \
  --icon static/character.icns \
  --add-data "static:static" \
  --add-data "default-profile:default-profile" \
  --add-data "talos-stream:talos-stream" \
  app.py

echo "== 4/4 组装 .app =="
APP="dist/Talos Studio.app"
mkdir -p "$APP/Contents/Resources"
cp -R default-profile talos-stream static "$APP/Contents/Resources/"
cat > "$APP/Contents/Resources/启动.command" <<'CMD'
#!/bin/bash
cd "$(dirname "$0")/../Resources"
open "http://127.0.0.1:8400" &
python3 app.py
CMD
chmod +x "$APP/Contents/Resources/启动.command"

echo "完成：$APP"
echo "分发前建议：codesign --deep --force --sign - \"$APP\""
