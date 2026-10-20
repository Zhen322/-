#!/bin/zsh
set -eu
TASK_DIR="${0:A:h}"
cd "$TASK_DIR"
PYTHON_BIN="${WECHAT_WRITER_PYTHON:-python3}"
if [[ -x "$TASK_DIR/.venv/bin/python3" ]]; then
  PYTHON_BIN="$TASK_DIR/.venv/bin/python3"
fi
"$PYTHON_BIN" "$TASK_DIR/backup.py"
