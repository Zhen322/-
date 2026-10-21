#!/usr/bin/env bash
# macOS / Linux 启动脚本：在终端执行  bash start.command
cd "$(dirname "$0")"

PY=python3
command -v $PY >/dev/null 2>&1 || PY=python
if ! command -v $PY >/dev/null 2>&1; then
  echo "未找到 python，请先安装 Python 3.9+：https://www.python.org/downloads/"
  exit 1
fi

$PY -c "import PIL" 2>/dev/null || {
  echo "正在安装 Pillow…"; $PY -m pip install Pillow
}

PORT=8766
echo "启动跨平台新媒体助手： http://127.0.0.1:$PORT"
echo "首次使用请在【设置】中填入 DeepSeek API Key；在【发布管理】配置平台凭据。"
exec $PY app.py --port $PORT
