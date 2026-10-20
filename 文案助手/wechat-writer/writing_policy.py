"""Editable local editorial rules, captured once for each writing run.

Only fixed files in the policy directory are read. No third-party skill code is
installed or executed. Existing rules are never replaced by a software update.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from datetime import datetime, timezone

SEED = Path(__file__).resolve().parent / "writing-skills" / "automotive-wechat-editor"
MAIN = "主写作提示词.md"
STAGES = ("facts", "outline", "body", "title", "fact_audit", "language_audit", "repair", "package")
MAX_PROMPT = 50000
_LOCK = threading.RLock()


class PolicyError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _atomic(path, text):
    fd, temporary = tempfile.mkstemp(prefix=".policy-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _directory(root):
    root = Path(root).resolve()
    directory = root / "writing-policy"
    if directory.is_symlink():
        raise PolicyError("写作规范目录不能是符号链接。", 400)
    if not directory.exists():
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".writing-policy-", dir=root))
        try:
            shutil.copytree(SEED, staging, dirs_exist_ok=True)
            os.chmod(staging, 0o700)
            if not directory.exists():
                staging.rename(directory)
        except OSError as exc:
            raise PolicyError("无法初始化写作规范，请检查本机写作技能文件与目录权限。", 500) from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return directory


def _read(directory, name, maximum):
    path = directory / name
    try:
        if not path.resolve().is_relative_to(directory.resolve()):
            raise PolicyError("写作规范文件必须保存在写作规范目录中。", 400)
        raw = path.read_bytes()
        if len(raw) > maximum * 4:
            raise PolicyError(f"写作规范文件 {name} 过长，请先缩短内容。")
        text = raw.decode("utf-8")
        if len(text) > maximum:
            raise PolicyError(f"写作规范文件 {name} 超过 {maximum:,} 字符。")
        return text
    except (OSError, UnicodeError) as exc:
        raise PolicyError(f"无法读取写作规范 {name}，请检查文件；原内容不会被默认规则覆盖。", 500) from exc


def _load(root):
    directory = _directory(root)
    files = {MAIN: _read(directory, MAIN, MAX_PROMPT),
             "config.json": _read(directory, "config.json", 2000),
             "sources.json": _read(directory, "sources.json", 32000)}
    for stage in STAGES:
        files[f"stages/{stage}.md"] = _read(directory, f"stages/{stage}.md", 8000)
    files["references/deep.md"] = _read(directory, "references/deep.md", 8000)
    try:
        config = json.loads(files["config.json"])
        sources = json.loads(files["sources.json"])
        mode = config["mode"]
        if mode not in ("lite", "deep") or not isinstance(sources, list) or len(sources) > 40:
            raise ValueError()
        if not all(isinstance(s, dict) and isinstance(s.get("id"), str)
                   and isinstance(s.get("name"), str) and isinstance(s.get("url"), str) for s in sources):
            raise ValueError()
    except (ValueError, KeyError, TypeError) as exc:
        raise PolicyError("写作规范 config.json 或 sources.json 格式不正确，未恢复默认值。", 400) from exc
    if not files[MAIN].strip():
        raise PolicyError("主写作提示词不能为空。")
    revision = hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return directory, files, mode, sources, revision


def read_policy(root):
    with _LOCK:
        directory, files, mode, sources, revision = _load(root)
        return {"revision": revision, "text": files[MAIN], "path": str(directory / MAIN),
                "mode": mode, "sources": sources, "max_characters": MAX_PROMPT,
                "original_path": str(directory / "用户原始提示词.md"),
                "stage_files": {stage: str(directory / "stages" / f"{stage}.md") for stage in STAGES}}


def save_policy(root, body):
    if not isinstance(body, dict):
        raise PolicyError("写作规范必须是一个对象。")
    text, mode = body.get("text"), body.get("mode")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_PROMPT:
        raise PolicyError(f"主写作提示词应为非空文本，最多 {MAX_PROMPT:,} 字符。")
    if mode not in ("lite", "deep"):
        raise PolicyError("请选择日常 Lite 或深度专题。")
    with _LOCK:
        directory, files, old_mode, sources, revision = _load(root)
        if body.get("revision") != revision:
            raise PolicyError("写作规范已在别处修改，请保留当前编辑并重新读取最新版本。", 409)
        if text == files[MAIN] and mode == old_mode:
            return read_policy(root)
        history = directory / "history"
        history.mkdir(exist_ok=True)
        if history.is_symlink():
            raise PolicyError("规范历史目录不能是符号链接。")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        _atomic(history / (stamp + ".json"), json.dumps({"revision": revision, "mode": old_mode,
                "text": files[MAIN]}, ensure_ascii=False, indent=2) + "\n")
        # Catch edits made by Obsidian while the backup was being written.
        if _load(root)[-1] != revision:
            raise PolicyError("文件刚刚被外部编辑器更新，未覆盖，请重新读取。", 409)
        _atomic(directory / MAIN, text)
        _atomic(directory / "config.json", json.dumps({"mode": mode}, ensure_ascii=False, indent=2) + "\n")
        return read_policy(root)


def snapshot_policy(root, mode=None):
    with _LOCK:
        directory, files, saved_mode, sources, revision = _load(root)
        mode = mode or saved_mode
        if mode not in ("lite", "deep"):
            raise PolicyError("写作模式不正确。")
        stages = {stage: files[f"stages/{stage}.md"] for stage in STAGES}
        if mode == "deep":
            for stage in ("outline", "body", "fact_audit", "package"):
                stages[stage] += "\n\n" + files["references/deep.md"]
        return {"revision": revision, "main_prompt": files[MAIN], "mode": mode,
                "stages": stages, "sources": copy.deepcopy(sources),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "source_path": str(directory / MAIN)}
