#!/usr/bin/env python3
"""Local-only writing workspace. Run with Python 3.11 or later."""
from __future__ import annotations

import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote, unquote, urlparse
import uuid
import xml.etree.ElementTree as ET
import zipfile
from performance_store import PerformanceError, PerformanceStoreMixin
from writing_store import WritingError, WritingStoreMixin
from topic_bridge import TopicStoreMixin
from coze_bridge import CozeError, CozeStoreMixin
from coze_quick import CozeQuickMixin
from writing_policy import PolicyError, read_policy, save_policy

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("WECHAT_WRITER_DATA", str(APP_DIR / "data"))).expanduser().resolve()
MAX_BODY = 12 * 1024 * 1024
MAX_UPLOAD = 8 * 1024 * 1024
MAX_CONTENT = 300_000
MAX_NOTES = 1_000_000
NOTES_PROMPT_BUDGET = 24_000
DEFAULT_PROFILE = "为汽车与车灯上下游公众号创作文案，面向整灯厂和上游供应商从业者。"
DEFAULT_SETTINGS = {
    "provider": "ollama", "base_url": "http://127.0.0.1:11434",
    "model": "qwen2.5:latest", "search_provider": "tavily", "api_key": "",
    "tavily_api_key": "", "notes_path": "", "profile": DEFAULT_PROFILE,
}


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid():
    return uuid.uuid4().hex


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def revision(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def draft_markdown(draft):
    title = re.sub(r"[\r\n]+", " ", draft["title"]).strip()
    content = draft["content"].strip()
    if content.split("\n", 1)[0].strip() == "# " + title:
        return content + "\n"
    return "# " + title + "\n\n" + content + "\n"


def draft_revision(draft):
    return revision(dump({"title": draft.get("title", ""), "content": draft.get("content", "")}))


def mark_image_staleness(draft):
    suggestions = draft.get("image_suggestions")
    if isinstance(suggestions, dict):
        suggestions["stale"] = suggestions.get("revision") != draft_revision(draft)
    review = draft.get("review")
    if isinstance(review, dict):
        review["stale"] = review.get("content_revision") != draft_revision(draft)
    package = draft.get("publication_package")
    if isinstance(package, dict):
        package["stale"] = package.get("content_revision") != draft_revision(draft)
    return draft


def image_count(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 8:
        raise APIError("配图数量须为 5 至 8 的整数。")
    return value


def image_markdown(draft):
    suggestions = mark_image_staleness(draft).get("image_suggestions")
    if not suggestions:
        raise APIError("这篇草稿还没有配图建议，请先查找配图。", 404)
    label = lambda text: re.sub(r"[\r\n\[\]<>]", " ", str(text or "")).strip()
    url = lambda value: quote(value, safe=":/?&=%#@!+$,;~*-._")
    lines = ["# " + label(draft["title"]) + " · 配图清单", "", "以下为联网找到的候选图片与来源，发布前请核实图像内容及使用授权。", ""]
    if suggestions.get("stale"):
        lines.extend(["> 正文或标题已修改：这组配图基于旧版草稿，请重新核对位置与相关性。", ""])
    for index, item in enumerate(suggestions.get("items", []), 1):
        lines.extend([f"## {index}. {label(item.get('description') or item.get('source_title') or '候选图片')}", "", f"- 建议位置：{label(item.get('placement'))}", f"- 建议图注：{label(item.get('caption'))}", f"- [图片预览](<{url(item['image_url'])}>)", f"- [出处：{label(item.get('source_title') or '来源页面')}](<{url(item['source_url'])}>)", f"- 使用说明：{label(item.get('license_note'))}", ""])
    if suggestions.get("queries"):
        lines.extend(["## 搜索词", "", *["- " + label(value) for value in suggestions["queries"]], ""])
    if suggestions.get("warnings"):
        lines.extend(["## 需要核对", "", *["- " + label(value) for value in suggestions["warnings"]], ""])
    return "\n".join(lines)


def atomic_write(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".writer-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def string(value, label, limit, required=False):
    if not isinstance(value, str):
        raise APIError(f"{label}必须是文本。")
    if required and not value.strip():
        raise APIError(f"请填写{label}。")
    if len(value) > limit:
        raise APIError(f"{label}最多 {limit:,} 个字符，当前为 {len(value):,} 个。")
    return value


def ids(value, label, maximum):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise APIError(f"{label}格式不正确。")
    result = list(dict.fromkeys(value))
    if len(result) > maximum:
        raise APIError(f"每次最多选择 {maximum} 个{label}，请减少选择。")
    return result


class Store(PerformanceStoreMixin, WritingStoreMixin, TopicStoreMixin, CozeStoreMixin, CozeQuickMixin):
    def __init__(self, root=DATA_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "workspace.sqlite3"
        self.settings_path = self.root / "settings.json"
        self.settings_lock = threading.RLock()
        self.notes_lock = threading.RLock()
        self.job_lock = threading.Lock()
        self.drafts_lock = threading.RLock()
        self.images_lock = threading.Lock()
        self.images_inflight = set()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="writer")
        for name in ("notes", "drafts", "cards", "backups"):
            (self.root / name).mkdir(exist_ok=True)
        self.default_notes = self.root / "notes" / "我的思考.md"
        if not self.default_notes.exists():
            atomic_write(self.default_notes, "# 我的思考\n\n## 写作定位\n\n" + DEFAULT_PROFILE + "\n\n## 近期观察\n\n## 我对行业的思考\n\n## 写作偏好\n\n## 不希望使用的表达\n")
        with self.connect() as db:
            db.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS articles(id TEXT PRIMARY KEY, data TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS cards(id TEXT PRIMARY KEY, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            """)
            for row in db.execute("SELECT id,data FROM jobs"):
                job = json.loads(row["data"])
                if job["status"] == "running":
                    job.update(status="error", error="服务重启中断了此任务，请重新发起。", message="任务已中断")
                    db.execute("UPDATE jobs SET data=? WHERE id=?", (dump(job), job["id"]))
        self.init_performance()
        self.init_writing()
        self.init_topics()
        self.init_coze()
        self.init_coze_quick()
        os.chmod(self.db_path, 0o600)
        with self.settings_lock:
            if not self.settings_path.exists():
                atomic_write(self.settings_path, dump(DEFAULT_SETTINGS))
            else:
                os.chmod(self.settings_path, 0o600)
        # Fail early for malformed settings instead of discarding the user's configuration.
        self.settings()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        with self.settings_lock:
            try:
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    raise ValueError("settings must be an object")
            except (OSError, ValueError) as exc:
                raise APIError("本地设置文件无法读取，请检查 data/settings.json。", 500) from exc
            return {**DEFAULT_SETTINGS, **saved}

    def public_settings(self):
        settings = self.settings()
        return {key: settings[key] for key in ("provider", "base_url", "model", "search_provider", "profile")} | {
            "notes_path": str(self.notes_path(settings)),
            "tavily_configured": bool(settings.get("tavily_api_key")),
            "api_key_configured": bool(settings.get("api_key")),
            "search_mode": "api_key" if settings.get("tavily_api_key") else "keyless",
        }

    def update_settings(self, body):
        with self.settings_lock:
            settings = self.settings()
            for key in ("provider", "base_url", "model", "profile", "notes_path", "search_provider"):
                if key in body:
                    settings[key] = string(body[key], key, 12_000 if key == "profile" else 2000)
            if settings["provider"] not in ("ollama", "openai", "codex"):
                raise APIError("请选择 Codex 会员、本地 Ollama 或 OpenAI 兼容 API。")
            parsed = urlparse(settings["base_url"])
            if settings["provider"] != "codex" and (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise APIError("模型地址须为有效的 http(s) 地址，不能包含账号、密码或查询参数。")
            settings["base_url"] = settings["base_url"].rstrip("/")
            string(settings["model"], "模型名称", 200, required=True)
            if settings["search_provider"] != "tavily":
                raise APIError("当前版本支持 Tavily 搜索。")
            for key in ("api_key", "tavily_api_key"):
                if key in body:
                    secret = string(body[key], "API 密钥", 2000).strip()
                    if secret:
                        settings[key] = secret
            if "notes_path" in body:
                raw = settings["notes_path"].strip()
                if raw:
                    path = Path(raw).expanduser()
                    if not path.is_absolute() or path.suffix.lower() != ".md":
                        raise APIError("请选择已存在的 Markdown 文件，填写以 .md 结尾的绝对路径。")
                    path = path.resolve()
                    if not path.is_file():
                        raise APIError("笔记文件不存在；选择路径不会创建或覆盖你的文档。")
                    self.read_notes_file(path)
                    settings["notes_path"] = str(path)
                else:
                    settings["notes_path"] = ""
            atomic_write(self.settings_path, dump(settings))
        return self.public_settings()

    def notes_path(self, settings=None):
        settings = settings or self.settings()
        return Path(settings.get("notes_path") or self.default_notes)

    @staticmethod
    def read_notes_file(path):
        try:
            if path.stat().st_size > MAX_NOTES * 4:
                raise APIError("笔记文件过大；编辑器最多读取 1,000,000 个字符。")
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise APIError("笔记无法读取，请确认文件存在、可读取，且编码为 UTF-8。", 409) from exc
        string(text, "笔记", MAX_NOTES)
        return {"text": text, "path": str(path), "revision": revision(text)}

    def read_notes(self):
        with self.notes_lock:
            return self.read_notes_file(self.notes_path())

    def save_notes(self, body):
        text = string(body.get("text"), "笔记", MAX_NOTES)
        expected = string(body.get("revision"), "笔记版本", 128, required=True)
        with self.notes_lock:
            path = self.notes_path()
            current = self.read_notes_file(path)
            if current["revision"] != expected:
                raise APIError("笔记已在其他窗口或外部编辑器中修改。请先复制当前未保存内容，再重新载入最新笔记后合并。", 409)
            if text != current["text"]:
                backup = self.root / "backups" / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uid()[:8] + ".md")
                atomic_write(backup, current["text"])
                # Check again immediately before replace to catch concurrent external saves.
                if self.read_notes_file(path)["revision"] != expected:
                    raise APIError("保存期间检测到外部修改，请重新载入笔记。", 409)
                atomic_write(path, text, path.stat().st_mode & 0o777)
            return self.read_notes_file(path)

    def all(self, table):
        assert table in ("articles", "cards", "drafts", "jobs")
        query = f"SELECT data FROM {table}" + (" WHERE archived=0" if table == "articles" else "") + " ORDER BY rowid DESC"
        with self.connect() as db:
            values = [json.loads(row["data"]) for row in db.execute(query)]
        return [mark_image_staleness(item) for item in values] if table == "drafts" else values

    def get(self, table, item_id):
        assert table in ("articles", "cards", "drafts", "jobs")
        with self.connect() as db:
            row = db.execute(f"SELECT data FROM {table} WHERE id=?" + (" AND archived=0" if table == "articles" else ""), (item_id,)).fetchone()
        if not row:
            raise APIError("内容不存在或已归档。", 404)
        value = json.loads(row["data"])
        return mark_image_staleness(value) if table == "drafts" else value

    def put(self, table, value):
        assert table in ("articles", "cards", "drafts", "jobs")
        with self.connect() as db:
            db.execute(f"INSERT INTO {table}(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (value["id"], dump(value)))
        return value

    def article(self, body, existing=None, source="manual"):
        item = dict(existing) if existing else {
            "id": uid(), "title": "未命名文章", "content": "", "url": "", "author": "", "tags": [],
            "metrics": {key: "" for key in ("reads", "likes", "favorites", "source", "observed_at")},
            "source_type": source, "content_kind": "full", "created_at": now(),
        }
        for key, limit in (("title", 500), ("content", MAX_CONTENT), ("url", 3000), ("author", 300)):
            if key in body:
                item[key] = string(body[key], {"title": "标题", "content": "正文", "url": "来源链接", "author": "作者"}[key], limit, required=key in ("title", "content"))
        if not item["content"].strip():
            raise APIError("请提供文章正文。")
        if item["url"] and urlparse(item["url"]).scheme not in ("http", "https"):
            raise APIError("来源链接须以 http:// 或 https:// 开头。")
        if "tags" in body:
            if not isinstance(body["tags"], list) or len(body["tags"]) > 30:
                raise APIError("标签须为列表，最多 30 个。")
            item["tags"] = [string(tag, "标签", 60) for tag in body["tags"]]
        if "metrics" in body:
            if not isinstance(body["metrics"], dict):
                raise APIError("阅读、点赞、收藏数据格式不正确。")
            item["metrics"] = {**item["metrics"], **{key: string(str(body["metrics"][key]), "传播数据", 1000 if key == "source" else 200) for key in item["metrics"] if key in body["metrics"]}}
        if not existing and source == "web":
            item["content_kind"] = body.get("content_kind", "full")
            if item["content_kind"] not in ("full", "snippet"):
                raise APIError("文章完整度标记无效。")
        if existing and item["content"] != existing["content"]:
            item.pop("analysis", None)
            # A user replacing the search excerpt with their supplied body can analyze it.
            item["content_kind"] = "full"
            # Existing learned techniques need another review after their source changes.
            with self.connect() as db:
                for row in db.execute("SELECT id,data FROM cards"):
                    card = json.loads(row["data"])
                    if card.get("source_article_id") == item["id"]:
                        card["approved"] = False
                        self.write_card_file(card)
                        db.execute("UPDATE cards SET data=? WHERE id=?", (dump(card), card["id"]))
            item["updated_at"] = now()
        return self.put("articles", item)

    def archive_article(self, article_id):
        self.get("articles", article_id)
        with self.connect() as db:
            db.execute("UPDATE articles SET archived=1 WHERE id=?", (article_id,))
        return {"ok": True}

    def update_card(self, card_id, body):
        card = self.get("cards", card_id)
        for key in ("title", "content", "category"):
            if key in body:
                card[key] = string(body[key], "能力卡" + key, 12_000 if key == "content" else 300, required=True)
        if "approved" in body:
            if not isinstance(body["approved"], bool):
                raise APIError("审核状态须为 true 或 false。")
            card["approved"] = body["approved"]
        self.write_card_file(card)
        return self.put("cards", card)

    def write_card_file(self, card):
        """Human-readable copies keep reviewed techniques portable outside SQLite."""
        path = self.root / "cards" / (card["id"] + ".md")
        card["file_path"] = str(path)
        source_url = ""
        try:
            source_url = self.get("articles", card["source_article_id"]).get("url", "")
        except APIError:
            pass
        text = f"# {card['title']}\n\n分类：{card['category']}\n\n审核：{'已确认可复用' if card['approved'] else '待审核'}\n\n来源文章：{card['source_title']}\n"
        if source_url:
            text += f"\n来源链接：{source_url}\n"
        text += "\n---\n\n" + card["content"] + "\n"
        atomic_write(path, text)

    def save_draft(self, result, context=None):
        title = string(result.get("title", "公众号草稿"), "草稿标题", 500, required=True)
        content = string(result.get("content"), "草稿正文", MAX_CONTENT, required=True)
        item = {"id": uid(), "title": title, "content": content, "references": result.get("references", []), "created_at": now(), "model": str(result.get("model", ""))}
        item["editor_notes"] = result.get("editor_notes", "")
        item["usage"] = result.get("usage", [])
        item["generation_seconds"] = result.get("generation_seconds")
        item["model_cost"] = None
        item["cost_currency"] = None
        if result.get("writing_id"):
            item["writing_id"] = str(result["writing_id"])
        if result.get("coze_job_id"):
            item["coze_job_id"] = str(result["coze_job_id"])
        if isinstance(result.get("review"), dict):
            item["review"] = {**result["review"], "content_revision": draft_revision(item), "stale": False}
        if "pipeline_model_seconds" in result:
            item["pipeline_model_seconds"] = result["pipeline_model_seconds"]
        if isinstance(result.get("publication_package"), dict):
            package_text = json.dumps(result["publication_package"], ensure_ascii=False, allow_nan=False)
            if len(package_text.encode("utf-8")) > 100_000:
                raise APIError("配套文案超过 100 KB，当前生成记录已保留，请检查后缩小配套输出。")
            item["publication_package"] = {**json.loads(package_text), "content_revision": draft_revision(item), "stale": False}
        if isinstance(result.get("writing_policy_revision"), str):
            item["writing_policy_revision"] = result["writing_policy_revision"]
        if result.get("writing_mode") in ("lite", "deep"):
            item["writing_mode"] = result["writing_mode"]
        if context:
            item["context"] = context
        item["file_path"] = str(self.root / "drafts" / (item["id"] + ".md"))
        atomic_write(item["file_path"], draft_markdown(item))
        # Provenance belongs beside the public-facing prose, never inside the draft.
        self.write_draft_sources(item)
        return self.put("drafts", item)

    def write_draft_sources(self, draft):
        metadata = {key: draft.get(key) for key in ("created_at", "model", "generation_seconds", "pipeline_model_seconds", "model_cost", "cost_currency", "references", "editor_notes", "usage", "context", "writing_id", "review", "publication_package", "writing_policy_revision", "writing_mode")}
        metadata["draft_id"] = draft["id"]
        atomic_write(self.root / "drafts" / (draft["id"] + ".sources.json"), json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")

    def update_draft(self, draft_id, body):
        with self.drafts_lock:
            draft = self.get("drafts", draft_id)
            draft["content"] = string(body.get("content"), "草稿正文", MAX_CONTENT, required=True)
            if "title" in body:
                draft["title"] = string(body["title"], "标题", 500, required=True)
            draft["updated_at"] = now()
            # Restored databases may contain an absolute path from another computer.
            # Always write editable draft exports inside the current data directory.
            draft["file_path"] = str(self.root / "drafts" / (draft["id"] + ".md"))
            atomic_write(draft["file_path"], draft_markdown(draft))
            mark_image_staleness(draft)
            if draft.get("image_suggestions"):
                self.write_images_file(draft)
            self.put("drafts", draft)
            if draft.get("review") or draft.get("publication_package"):
                self.write_draft_sources(draft)
                self.mark_writing_review_stale(draft)
            return draft

    def write_images_file(self, draft):
        atomic_write(self.root / "drafts" / (draft["id"] + ".images.json"), json.dumps(draft["image_suggestions"], ensure_ascii=False, indent=2) + "\n")

    def reserve_images(self, draft_id):
        with self.images_lock:
            if draft_id in self.images_inflight:
                raise APIError("这篇草稿正在查找配图，请等待当前任务完成。", 409)
            self.images_inflight.add(draft_id)

    def release_images(self, draft_id):
        with self.images_lock:
            self.images_inflight.discard(draft_id)

    def find_images_result(self, captured_draft, count, settings):
        import image_engine
        result = image_engine.find_draft_images(captured_draft, count, settings)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise APIError("配图服务返回格式不完整，原有配图已保留，请重试。")
        if not result["items"]:
            raise APIError("暂未找到可用配图，原有配图已保留。可以调整文章主题或稍后重试。")
        if len(result["items"]) > count:
            raise APIError("配图服务返回数量超出本次选择，原有配图已保留，请重试。")
        items = []
        for entry in result["items"]:
            if not isinstance(entry, dict):
                raise APIError("配图条目格式异常，原有配图已保留。")
            item = {"id": string(entry.get("id") or uid(), "图片编号", 200)}
            for field in ("image_url", "source_url"):
                value = string(entry.get(field), "图片链接或出处", 8192, True)
                parsed = urlparse(value)
                if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or re.search(r"[\x00-\x20]", value):
                    raise APIError("配图服务返回了无效链接，原有配图已保留。")
                item[field] = value
            for field in ("source_title", "description", "placement", "caption", "query", "license_note"):
                item[field] = string(entry.get(field, ""), "配图说明", 3000)
            items.append(item)
        suggestions = {"items": items, "requested_count": count, "model": string(result.get("model", ""), "配图模型", 300), "created_at": now(), "revision": draft_revision(captured_draft)}
        for field in ("queries", "warnings"):
            values = result.get(field, [])
            if not isinstance(values, list) or len(values) > 40:
                raise APIError("配图服务返回的搜索说明格式异常，原有配图已保留。")
            suggestions[field] = [string(value, "配图搜索说明", 3000) for value in values]
        if len(items) < count:
            suggestions["warnings"].append(f"本次找到 {len(items)} 张可用候选，少于请求的 {count} 张；未使用无出处图片补足数量。")
        # Merge only image metadata into the latest row; a user may have edited
        # their title/body while the network search was running.
        with self.drafts_lock:
            latest = self.get("drafts", captured_draft["id"])
            # An image-only action can be the first save after moving a workspace.
            # Rebase the stored export path without altering the author's prose.
            current_markdown = self.root / "drafts" / (latest["id"] + ".md")
            latest["file_path"] = str(current_markdown)
            if not current_markdown.exists():
                atomic_write(current_markdown, draft_markdown(latest))
            latest["image_suggestions"] = suggestions
            mark_image_staleness(latest)
            self.write_images_file(latest)
            self.put("drafts", latest)
            return {"draft_id": latest["id"], "draft": latest, "image_suggestions": latest["image_suggestions"]}

    def draft_images(self, draft_id, body):
        count = image_count(body.get("count", 6))
        captured = self.get("drafts", draft_id)
        self.reserve_images(draft_id)

        def work():
            try:
                return self.find_images_result(captured, count, self.settings())
            finally:
                self.release_images(draft_id)

        try:
            return self.job("draft-images", work, draft_id=draft_id)
        except Exception:
            self.release_images(draft_id)
            raise

    def job(self, kind, work, draft_id=None, writing_id=None):
        with self.job_lock:
            if sum(item["status"] == "running" for item in self.all("jobs")) >= 8:
                raise APIError("当前已有 8 个进行中的任务，请等待部分完成后重试。", 429)
            job = {"id": uid(), "type": kind, "status": "running", "message": "任务已排队，本地模型首次运行可能需要几分钟。", "result": None, "error": None, "created_at": now()}
            if draft_id:
                job["draft_id"] = draft_id
            if writing_id:
                job["writing_id"] = writing_id
            self.put("jobs", job)

        def run():
            try:
                job["message"] = "正在处理，请保持本地服务运行。"
                self.put("jobs", job)
                result = work()
                job.update(status="done", result=result, message="已完成", finished_at=now())
            except Exception as exc:
                # Engine exceptions contain useful user-facing errors; scrub known secrets.
                message = str(exc) or "任务失败，请检查配置后重试。"
                try:
                    settings = self.settings()
                    for key in ("api_key", "tavily_api_key"):
                        if settings.get(key):
                            message = message.replace(settings[key], "[密钥已隐藏]")
                except Exception:
                    pass
                job.update(status="error", error=message[:1500], message="任务失败", finished_at=now())
            self.put("jobs", job)

        self.pool.submit(run)
        return {"job_id": job["id"]}

    def analyze(self, article_id):
        article = self.get("articles", article_id)
        if article.get("content_kind") == "snippet":
            raise APIError("这条素材只有搜索摘要，不能据此拆解文章。请打开原文，将完整正文粘贴或上传后再拆解。")

        def work():
            import engine
            result = engine.analyze_article(article, self.settings())
            latest = self.get("articles", article_id)
            if latest["content"] != article["content"]:
                raise APIError("拆解期间文章正文已修改。此次结果未写入，请重新拆解。", 409)
            latest["analysis"] = string(result.get("analysis"), "拆解结果", MAX_CONTENT, required=True)
            new_cards = []
            for entry in result.get("cards", [])[:20]:
                card = {"id": uid(), "title": string(entry.get("title"), "能力卡标题", 500, True), "category": string(entry.get("category", "写作方法"), "能力卡分类", 300), "content": string(entry.get("content"), "能力卡正文", 12_000, True), "source_article_id": article_id, "source_title": latest["title"], "approved": False, "created_at": now()}
                self.write_card_file(card)
                new_cards.append(card)
            # Commit the analysis and all cards together.
            with self.connect() as db:
                db.execute("UPDATE articles SET data=? WHERE id=? AND archived=0", (dump(latest), article_id))
                for card in new_cards:
                    db.execute("INSERT INTO cards(id,data) VALUES(?,?)", (card["id"], dump(card)))
            return {"article_id": article_id, "article": latest, "cards": new_cards}

        return self.job("analyze", work)

    def research(self, body):
        query = string(body.get("query"), "搜索关键词", 600, True)
        scope = body.get("scope", "wechat")
        if scope not in ("wechat", "web"):
            raise APIError("搜索范围须为 wechat 或 web。")
        count = body.get("max_results", 5)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
            raise APIError("搜索结果数须为 1 至 10 的整数。")

        def work():
            import engine
            found = engine.search_articles(query, scope, count, self.settings())
            articles = [self.article(entry, source="web") for entry in found]
            return {"article_ids": [item["id"] for item in articles], "articles": articles}

        return self.job("research", work)

    def generate(self, body):
        request = {"brief": string(body.get("brief"), "写作要求", 6000, True), "audience": string(body.get("audience", "整灯厂和上游供应商从业者"), "读者", 1000), "tone": string(body.get("tone", "专业、清晰、有依据"), "语气", 1000), "length": string(str(body.get("length", "1200—1800字")), "篇幅", 300)}
        article_ids = ids(body.get("article_ids", []), "参考文章", 8)
        card_ids = ids(body.get("card_ids", []), "能力卡", 12)
        if not isinstance(body.get("find_images", False), bool):
            raise APIError("自动配图选项须为 true 或 false。")
        find_images = body.get("find_images", False)
        count = image_count(body.get("image_count", 6))
        if not isinstance(body.get("use_web", False), bool):
            raise APIError("联网选项须为 true 或 false。")
        request["use_web"] = body.get("use_web", False)
        search_query = string(body.get("search_query", ""), "联网搜索词", 600).strip()
        if not search_query:
            search_query = re.sub(r"\s+", " ", request["brief"]).strip()[:240]
        request["search_query"] = search_query
        if request["use_web"] and len(article_ids) > 5:
            raise APIError("联网写作会补充最多 3 条资料，请最多勾选 5 篇已有素材。")
        # Validate now to avoid creating a doomed job; re-read again when it executes.
        for item_id in article_ids:
            self.get("articles", item_id)
        for item_id in card_ids:
            if not self.get("cards", item_id).get("approved"):
                raise APIError("所选能力卡尚未审核，请先在能力库确认可复用。")
        if len(self.read_notes()["text"]) > NOTES_PROMPT_BUDGET:
            raise APIError(f"当前笔记超过本次写作的 {NOTES_PROMPT_BUDGET:,} 字符预算。请在笔记设置中选择一份聚焦本次写作的 .md 文件，原文不会被截断或覆盖。")

        def work():
            import engine
            settings = self.settings()
            articles = [self.get("articles", item_id) for item_id in article_ids]
            if request["use_web"]:
                found = engine.search_articles(search_query, "web", 3, settings)
                articles.extend(self.article(entry, source="web") for entry in found[:3])
            approved = [card for card in self.all("cards") if card.get("approved")]
            if card_ids:
                cards = [self.get("cards", item_id) for item_id in card_ids]
                if any(not card.get("approved") for card in cards):
                    raise APIError("任务排队期间能力卡审核状态发生变化，请重新选择。")
                omitted_cards = 0
            else:
                cards = approved[:12]
                omitted_cards = max(0, len(approved) - 12)
            # Always read the physical notebook immediately before the model call.
            notes = self.read_notes()
            if len(notes["text"]) > NOTES_PROMPT_BUDGET:
                raise APIError(f"最新笔记超过 {NOTES_PROMPT_BUDGET:,} 字符预算，请选择篇幅更短的写作笔记后重试。")
            context = {"notes_path": notes["path"], "notes_revision": notes["revision"], "notes_characters": len(notes["text"]), "article_ids": [item["id"] for item in articles], "card_ids": [item["id"] for item in cards], "articles": [{"id": item["id"], "title": item["title"], "url": item.get("url", ""), "content_kind": item.get("content_kind", "full")} for item in articles], "cards": [{"id": item["id"], "title": item["title"], "approved": True} for item in cards], "omitted_approved_cards": omitted_cards, "captured_at": now()}
            context["search_query"] = search_query if request["use_web"] else ""
            request["article_ids"] = context["article_ids"]
            request["card_ids"] = context["card_ids"]
            generation_started = time.monotonic()
            result = engine.generate_draft(request, articles, cards, notes, settings)
            result["generation_seconds"] = round(time.monotonic() - generation_started, 6)
            for field in ("context", "usage", "editor_notes"):
                if result.get(field):
                    context["model_" + field] = result[field]
            draft = self.save_draft(result, context)
            if find_images:
                reserved = False
                try:
                    self.reserve_images(draft["id"])
                    reserved = True
                    return self.find_images_result(draft, count, settings)
                except Exception as exc:
                    message = str(exc) or "配图暂时不可用，请在草稿中重新查找。"
                    for key in ("api_key", "tavily_api_key"):
                        if settings.get(key):
                            message = message.replace(settings[key], "[密钥已隐藏]")
                    return {"draft_id": draft["id"], "draft": self.get("drafts", draft["id"]), "images_error": message[:1500]}
                finally:
                    if reserved:
                        self.release_images(draft["id"])
            return {"draft_id": draft["id"], "draft": draft}

        return self.job("draft", work)

    def state(self):
        articles, cards, drafts = (self.all(table) for table in ("articles", "cards", "drafts"))
        return {"articles": articles, "cards": cards, "drafts": drafts, "performance_records": self.performance_records(), "writing_runs": self.writing_runs(), "topic_batches": self.topic_batches(), "notes": self.read_notes(), "settings": self.public_settings(), "jobs": self.all("jobs")[:40], "stats": {"articles": len(articles), "cards": len(cards), "drafts": len(drafts)}}


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = []
        self.in_title = False
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag in ("p", "div", "article", "section", "br", "h1", "h2", "h3", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = max(0, self.skip - 1)
        if tag == "title":
            self.in_title = False
        if tag in ("p", "div", "article", "section", "h1", "h2", "h3", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        elif not self.skip:
            self.parts.append(data)


def decode_text(data):
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    raise APIError("无法识别文本编码，请另存为 UTF-8 的 .md 或 .txt 文件。")


def upload(body, store):
    filename = string(body.get("filename"), "文件名", 500, True)
    encoded = string(body.get("data"), "文件内容", MAX_BODY, True)
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise APIError("上传内容的 base64 编码无效。") from exc
    if len(data) > MAX_UPLOAD:
        raise APIError("文件最大为 8 MB，请先缩小文件。", 413)
    extension = Path(filename).suffix.lower()
    title = Path(filename).stem
    if extension in (".md", ".txt"):
        content = decode_text(data)
        heading = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if heading:
            title = heading.group(1).strip()
    elif extension in (".html", ".htm"):
        parser = HTMLText()
        parser.feed(decode_text(data))
        content = re.sub(r"\n[ \t]*\n+", "\n\n", "".join(parser.parts)).strip()
        title = "".join(parser.title).strip() or title
    elif extension == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > 20 * 1024 * 1024:
                    raise APIError("Word 正文解压后超过 20 MB，请缩小文档。")
                root = ET.fromstring(archive.read(info))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            content = "\n\n".join("".join(paragraph.itertext()) for paragraph in root.findall(".//w:p", ns))
        except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError) as exc:
            raise APIError("无法读取此 Word 文件，请使用未加密的 .docx 或另存为 Markdown。") from exc
    elif extension == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise APIError("当前未安装 PDF 文字提取组件。可先导出为 .txt/.md，或安装可选依赖 pypdf 后重启服务。") from exc
        try:
            reader = PdfReader(io.BytesIO(data))
            if len(reader.pages) > 100:
                raise APIError("单次最多导入 100 页 PDF，请拆分文件。")
            content = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except APIError:
            raise
        except Exception as exc:
            raise APIError("PDF 文字提取失败，请提供可复制文本的 PDF 或导出为 Markdown。") from exc
        if len(content.strip()) < 20:
            raise APIError("此 PDF 没有足够的可提取文字，可能是扫描件，请先 OCR 转为文本。")
    else:
        raise APIError("支持 .md、.txt、.html、.docx；安装可选组件后支持文本 PDF。")
    return store.article({"title": title[:500], "content": content}, source="upload")


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalWriter/1.0"

    @property
    def store(self):
        return self.server.store

    def log_message(self, format, *args):
        # No request bodies, query strings, credentials, or full source URLs in logs.
        if len(args) > 1 and str(args[1]).startswith("5"):
            sys.stderr.write("LocalWriter: server request failed\n")

    def guard(self, mutation=False):
        port = self.server.server_port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        if host.lower() not in allowed_hosts:
            raise APIError("此服务仅允许通过本机 localhost 地址访问。", 403)
        if mutation:
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{host}" for host in allowed_hosts}:
                raise APIError("拒绝跨站请求，请从本地工作台操作。", 403)
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                raise APIError("拒绝跨站请求，请从本地工作台操作。", 403)

    def body(self):
        if self.headers.get("Transfer-Encoding"):
            raise APIError("不支持分块请求。", 400)
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise APIError("请求长度无效。") from exc
        if size < 0 or size > MAX_BODY:
            raise APIError("请求过大，单次最多 12 MB。", 413)
        if not size:
            return {}
        if self.headers.get_content_type() != "application/json":
            raise APIError("请使用 application/json 请求。", 415)
        try:
            result = json.loads(self.rfile.read(size).decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise APIError("请求 JSON 格式错误。") from exc
        if not isinstance(result, dict):
            raise APIError("请求必须是 JSON 对象。")
        return result

    def send(self, value, status=200):
        data = dump(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def send_download(self, content, filename, content_type, encoding="utf-8"):
        data = content.encode(encoding)
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename=\"{filename}\"")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self, method):
        try:
            self.guard(method != "GET")
            path = unquote(urlparse(self.path).path)
            body = self.body() if method != "GET" else {}
            store = self.store
            if method == "GET":
                if path == "/api/health":
                    return self.send({"ok": True})
                if path == "/api/state":
                    return self.send(store.state())
                if path == "/api/notes":
                    return self.send(store.read_notes())
                if path == "/api/writing/policy":
                    return self.send(read_policy(store.root))
                if path == "/api/writing":
                    return self.send({"runs": store.writing_runs()})
                if path == "/api/coze/settings":
                    return self.send(store.coze_settings())
                if path == "/api/topics":
                    return self.send({"batches": store.topic_batches()})
                if match := re.fullmatch(r"/api/writing/([a-f0-9]+)", path):
                    return self.send({"run": store.get_writing(match[1])})
                if match := re.fullmatch(r"/api/export/writing/([a-f0-9]+)(\.json)?", path):
                    as_json = bool(match[2])
                    suffix = ".json" if as_json else ".md"
                    content_type = "application/json" if as_json else "text/markdown"
                    return self.send_download(store.export_writing(match[1], as_json), "writing-" + match[1] + suffix, content_type)
                if path == "/api/performance":
                    return self.send({"records": store.performance_records()})
                if path == "/api/performance/review":
                    return self.send(store.review_performance())
                if path == "/api/performance/template.csv":
                    return self.send_download(store.performance_template_csv(), "performance-template.csv", "text/csv", "utf-8-sig")
                if path == "/api/export/performance.csv":
                    return self.send_download(store.export_performance_csv(), "performance.csv", "text/csv", "utf-8-sig")
                if path == "/api/export/performance.json":
                    return self.send_download(store.export_performance_json(), "performance.json", "application/json")
                if match := re.fullmatch(r"/api/jobs/([a-f0-9]+)", path):
                    return self.send(store.get("jobs", match[1]))
                if match := re.fullmatch(r"/api/export/drafts/([a-f0-9]+)", path):
                    draft = store.get("drafts", match[1])
                    data = draft_markdown(draft).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Content-Disposition", f"attachment; filename=\"draft-{draft['id']}.md\"; filename*=UTF-8''{quote(draft['title'] + '.md', safe='')}")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    return self.wfile.write(data)
                if match := re.fullmatch(r"/api/export/draft-images/([a-f0-9]+)", path):
                    draft = store.get("drafts", match[1])
                    data = image_markdown(draft).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Content-Disposition", f"attachment; filename=\"images-{draft['id']}.md\"; filename*=UTF-8''{quote(draft['title'] + '-配图清单.md', safe='')}")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return self.wfile.write(data)
                return self.static(path)
            if method == "PUT" and path == "/api/writing/policy":
                return self.send(save_policy(store.root, body))
            if method == "PUT" and path == "/api/coze/settings":
                return self.send(store.update_coze_settings(body))
            if method == "PUT" and path == "/api/settings":
                return self.send(store.update_settings(body))
            if method == "PUT" and path == "/api/notes":
                return self.send(store.save_notes(body))
            if method == "POST":
                if match := re.fullmatch(r"/api/coze/(topics|writer)/quick-run", path):
                    return self.send(store.quick_coze(match[1], body), 202)
                if match := re.fullmatch(r"/api/coze/jobs/([a-f0-9]+)/import-draft", path):
                    return self.send(store.import_coze_draft(match[1]))
                if match := re.fullmatch(r"/api/coze/jobs/([a-f0-9]+)/resume", path):
                    return self.send(store.resume_coze(match[1]), 202)
                if match := re.fullmatch(r"/api/coze/(topics|writer)/schema", path):
                    return self.send(store.coze_schema(match[1]))
                if match := re.fullmatch(r"/api/coze/(topics|writer)/run", path):
                    return self.send(store.run_coze(match[1], body), 202)
                if path == "/api/topics/import":
                    return self.send({"batch": store.import_topics(body)}, 201)
                if path == "/api/writing/prepare":
                    return self.send(store.prepare_writing(body), 202)
                if match := re.fullmatch(r"/api/writing/([a-f0-9]+)/continue", path):
                    return self.send(store.continue_writing(match[1], body), 202)
                if path == "/api/performance":
                    return self.send({"record": store.create_performance(body)}, 201)
                if path == "/api/performance/import":
                    return self.send(store.import_performance(body), 201)
                if path == "/api/articles":
                    return self.send({"article": store.article(body)}, 201)
                if path == "/api/import":
                    import engine
                    url = string(body.get("url"), "文章链接", 3000, True)
                    return self.send({"article": store.article(engine.fetch_article(url), source="web")}, 201)
                if path == "/api/upload":
                    return self.send({"article": upload(body, store)}, 201)
                if path == "/api/research":
                    return self.send(store.research(body), 202)
                if path == "/api/drafts":
                    return self.send(store.generate(body), 202)
                if match := re.fullmatch(r"/api/drafts/([a-f0-9]+)/images", path):
                    return self.send(store.draft_images(match[1], body), 202)
                if path == "/api/test-model":
                    import engine
                    return self.send(store.job("test-model", lambda: {"message": engine.test_model(store.settings())}), 202)
                if path == "/api/open-notes":
                    note = store.read_notes()
                    if sys.platform == "darwin":
                        command = ["open", "-a", "Typora", note["path"]] if Path("/Applications/Typora.app").exists() else ["open", note["path"]]
                        subprocess.run(command, check=True, timeout=15, capture_output=True)
                    elif sys.platform.startswith("linux"):
                        subprocess.Popen(["xdg-open", note["path"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        raise APIError("当前系统请直接使用笔记路径打开 Markdown 文件。")
                    return self.send({"ok": True})
                if match := re.fullmatch(r"/api/articles/([a-f0-9]+)/analyze", path):
                    return self.send(store.analyze(match[1]), 202)
            if match := re.fullmatch(r"/api/articles/([a-f0-9]+)", path):
                if method == "PATCH":
                    return self.send({"article": store.article(body, store.get("articles", match[1]))})
                if method == "DELETE":
                    return self.send(store.archive_article(match[1]))
            if method == "PATCH" and (match := re.fullmatch(r"/api/cards/([a-f0-9]+)", path)):
                return self.send({"card": store.update_card(match[1], body)})
            if method == "PATCH" and (match := re.fullmatch(r"/api/drafts/([a-f0-9]+)", path)):
                return self.send({"draft": store.update_draft(match[1], body)})
            if method == "PATCH" and (match := re.fullmatch(r"/api/performance/([a-f0-9]+)", path)):
                return self.send({"record": store.update_performance(match[1], body)})
            raise APIError("接口不存在。", 404)
        except (APIError, PerformanceError, WritingError, CozeError, PolicyError) as exc:
            self.send({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            message = str(exc)[:1200] or "操作失败，请检查配置。"
            try:
                settings = self.store.settings()
                for key in ("api_key", "tavily_api_key"):
                    if settings.get(key):
                        message = message.replace(settings[key], "[密钥已隐藏]")
            except Exception:
                pass
            self.send({"error": message}, 400)
        except Exception:
            self.send({"error": "本地服务处理失败，请检查配置并重试。"}, 500)

    def static(self, path):
        if path.startswith("/api/"):
            raise APIError("接口不存在。", 404)
        relative = "index.html" if path == "/" else path.lstrip("/")
        if relative.startswith("static/"):
            relative = relative[len("static/"):]
        target = (APP_DIR / "static" / relative).resolve()
        if not target.is_relative_to(APP_DIR / "static") or not target.is_file():
            raise APIError("页面不存在。", 404)
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(str(target))[0] or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: http: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")


def create_server(port=8765, data_dir=DATA_DIR):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.store = Store(data_dir)
    return server


def main():
    import argparse
    parser = argparse.ArgumentParser(description="本地公众号写作工作台")
    parser.add_argument("--port", type=int, default=int(os.environ.get("WECHAT_WRITER_PORT", "8765")))
    args = parser.parse_args()
    try:
        server = create_server(args.port)
    except OSError:
        print(f"端口 {args.port} 已被占用；如果工作台已启动，请直接打开 http://127.0.0.1:{args.port}", file=sys.stderr)
        return 1
    print(f"字里 · 本地公众号写作工作台：http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        server.store.pool.shutdown(wait=False, cancel_futures=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
