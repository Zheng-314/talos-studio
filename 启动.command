#!/bin/bash
cd "$(dirname "$0")"
echo "== Talos Studio =="

PY="./runtime/bin/python3"
if [ ! -x "$PY" ]; then
  PY="./talos-stream/.venv/bin/python"
fi
if [ ! -x "$PY" ]; then
  echo "没找到运行环境，尝试用系统 Python 建一个…"
  python3 -m venv ./talos-stream/.venv || { echo "请先安装 Python 3.10+"; read -n 1; exit 1; }
  PY="./talos-stream/.venv/bin/python"
  "$PY" -m pip install -q -r ./talos-stream/requirements.txt
  "$PY" -m pip install -q -r ./requirements.txt
fi

echo "启动中： http://127.0.0.1:8400"
"$PY" desktop.py
