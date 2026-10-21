# -*- coding: utf-8 -*-
"""SQLite persistence layer (stdlib only).

All domain records are stored as JSON blobs in per-type tables. A project binds
one theme to its four-platform content, approvals, schedules and publishes.
Keys live only in the local settings file, never in exported content or logs.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from llm import DEFAULT_BASE_URL, DEFAULT_MODEL


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid():
    return uuid.uuid4().hex


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


DEFAULT_SETTINGS = {
    "base_url": DEFAULT_BASE_URL,
    "model": DEFAULT_MODEL,
    "api_key": "",
    "tavily_api_key": "",
    "profile": "面向汽车与车灯产业链（整灯厂与上游供应商从业者）的跨平台新媒体运营。",
    "brand": "创新科学",
    "accounts": {},   # platform -> account display name
}


# Tables with a single JSON blob per row.
TABLES = ["materials", "plans", "topics", "projects", "contents",
          "approvals", "schedules", "publishes", "feedback", "conversations"]


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "media").mkdir(exist_ok=True)
        self.db_path = self.root / "workspace.sqlite3"
        self.settings_path = self.root / "settings.json"
        self.lock = threading.RLock()
        self._init_db()
        self._init_settings()

    def _init_db(self):
        with self.connect() as db:
            db.executescript("PRAGMA journal_mode=WAL;")
            for table in TABLES:
                db.execute(
                    f"CREATE TABLE IF NOT EXISTS {table}("
                    "id TEXT PRIMARY KEY, data TEXT NOT NULL)"
                )
            # index content/project lookups
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_contents_project "
                "ON contents(id)"
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # imported below to avoid a top-level import issue
    def _init_settings(self):
        if not self.settings_path.exists():
            self._write_settings(DEFAULT_SETTINGS)
        else:
            self.settings()  # validate

    def _write_settings(self, settings):
        self.settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def settings(self):
        try:
            saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("settings must be an object")
        except (OSError, ValueError) as exc:
            raise ValueError("本地设置文件无法读取，请检查 data/settings.json。") from exc
        merged = {**DEFAULT_SETTINGS, **saved}
        return merged

    def public_settings(self):
        s = self.settings()
        return {
            "base_url": s["base_url"],
            "model": s["model"],
            "profile": s["profile"],
            "brand": s["brand"],
            "accounts": s.get("accounts", {}),
            "api_key_configured": bool(s.get("api_key")),
            "tavily_configured": bool(s.get("tavily_api_key")),
        }

    def update_settings(self, body):
        with self.lock:
            s = self.settings()
            for key in ("base_url", "model", "profile", "brand"):
                if key in body:
                    s[key] = str(body[key])
            if "accounts" in body and isinstance(body["accounts"], dict):
                s["accounts"] = body["accounts"]
            for key in ("api_key", "tavily_api_key"):
                if key in body:
                    secret = str(body[key]).strip()
                    if secret:
                        s[key] = secret
                    elif body[key] == "":
                        s[key] = ""
            self._write_settings(s)
            return self.public_settings()

    # ---------------- generic record helpers ----------------

    def put(self, table, value):
        assert table in TABLES
        with self.lock, self.connect() as db:
            db.execute(
                f"INSERT INTO {table}(id,data) VALUES(?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (value["id"], dump(value)),
            )
        return value

    def get(self, table, item_id):
        with self.connect() as db:
            row = db.execute(
                f"SELECT data FROM {table} WHERE id=?", (item_id,)
            ).fetchone()
        if not row:
            raise KeyError(f"{table} 不存在：{item_id}")
        return json.loads(row["data"])

    def list(self, table):
        with self.connect() as db:
            rows = db.execute(
                f"SELECT data FROM {table} ORDER BY rowid DESC"
            ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def delete(self, table, item_id):
        with self.lock, self.connect() as db:
            db.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
        return {"ok": True}

    # ---------------- typed helpers ----------------

    def add_material(self, kind, title, content, url="", source="",
                     meta=None, item_id=None):
        existing = None
        if item_id:
            try:
                existing = self.get("materials", item_id)
            except KeyError:
                existing = None
        item = existing or {
            "id": uid(), "kind": kind, "title": title, "content": content,
            "url": url, "source": source, "meta": meta or {},
            "created_at": now(),
        }
        item.update(kind=kind, title=title, content=content, url=url,
                    source=source, updated_at=now())
        if meta:
            item["meta"] = {**item.get("meta", {}), **meta}
        return self.put("materials", item)

    def materials(self, kind=None):
        items = self.list("materials")
        if kind:
            items = [m for m in items if m["kind"] == kind]
        return items

    def create_project(self, theme, brief, audience=""):
        item = {
            "id": uid(), "theme": theme, "brief": brief,
            "audience": audience, "status": "created",
            "topic_id": "", "fact_cards": [], "content_ids": [],
            "created_at": now(), "updated_at": now(),
        }
        return self.put("projects", item)

    def update_project(self, project):
        project["updated_at"] = now()
        return self.put("projects", project)

    def save_content(self, item):
        if not item.get("id"):
            item["id"] = uid()
        item["updated_at"] = now()
        return self.put("contents", item)

    def contents_for(self, project_id):
        return [c for c in self.list("contents")
                if c.get("project_id") == project_id]

    def content_by_target(self, project_id, platform, language):
        for c in self.contents_for(project_id):
            if c.get("platform") == platform and c.get("language") == language:
                return c
        return None

    def save_publish(self, item):
        if not item.get("id"):
            item["id"] = uid()
        item["updated_at"] = now()
        return self.put("publishes", item)

    def publishes_for(self, project_id=None):
        items = self.list("publishes")
        if project_id:
            items = [p for p in items if p.get("project_id") == project_id]
        return items

    def schedules_due(self, at=None):
        at = at or now()
        out = []
        for s in self.list("schedules"):
            if s.get("status") == "scheduled" and s.get("execute_at") <= at:
                out.append(s)
        return out

    def save_schedule(self, item):
        if not item.get("id"):
            item["id"] = uid()
        return self.put("schedules", item)

    def feedback_for(self, content_id=None):
        items = self.list("feedback")
        if content_id:
            items = [f for f in items if f.get("content_id") == content_id]
        return items
