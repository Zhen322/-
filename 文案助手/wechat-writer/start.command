#!/bin/zsh
set -eu
TASK_DIR="${0:A:h}"
cd "$TASK_DIR"
PYTHON_BIN="${WECHAT_WRITER_PYTHON:-python3}"
if [[ -x "$TASK_DIR/.venv/bin/python3" ]]; then
  PYTHON_BIN="$TASK_DIR/.venv/bin/python3"
fi
if curl -fsS --max-time 2 http://127.0.0.1:8765/api/health >/dev/null 2>&1; then
  open http://127.0.0.1:8765
  exit 0
fi
mkdir -p data
nohup "$PYTHON_BIN" "$TASK_DIR/app.py" > "$TASK_DIR/data/server.log" 2>&1 < /dev/null &
TASK_PID=$!
echo "$TASK_PID" > "$TASK_DIR/data/server.pid"
for attempt in {1..40}; do
  if curl -fsS --max-time 1 http://127.0.0.1:8765/api/health >/dev/null 2>&1; then
    open http://127.0.0.1:8765
    exit 0
  fi
  if ! kill -0 "$TASK_PID" 2>/dev/null; then
    echo "启动失败，请查看：$TASK_DIR/data/server.log"
    exit 1
  fi
  sleep 0.25
done
echo "服务尚未就绪，请查看：$TASK_DIR/data/server.log"
exit 1
