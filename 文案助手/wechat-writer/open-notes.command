#!/bin/zsh
set -eu
TASK_DIR="${0:A:h}"
TASK_NOTES="$TASK_DIR/data/notes/我的思考.md"
if [[ -f "$TASK_DIR/data/settings.json" ]]; then
  TASK_CUSTOM=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("notes_path", ""))' "$TASK_DIR/data/settings.json")
  if [[ -n "$TASK_CUSTOM" && -f "$TASK_CUSTOM" ]]; then
    TASK_NOTES="$TASK_CUSTOM"
  fi
fi
if [[ -d /Applications/Typora.app ]]; then
  open -a Typora "$TASK_NOTES"
else
  open "$TASK_NOTES"
fi
