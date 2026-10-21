#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-platform new-media assistant — local web server (stdlib only).

Run:  python app.py [--port 8766]
Binds to the loopback address. Long operations run in a thread pool and are
tracked as jobs; the SPA polls job status. Generated images are served from
/data/media.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import mimetypes
import os
import re
import sys
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, quote

import publisher
from agent import Agent
from brain import Brain
from chat import ChatBrain
from platforms import PLATFORMS, SPECS, PLATFORM_NAMES
from store import Store, now, uid

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MEDIA_AGENT_DATA", str(APP_DIR / "data")))
MAX_BODY = 16 * 1024 * 1024
DEFAULT_PORT = 8766


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _s(value, label, limit, required=False):
    if not isinstance(value, str):
        raise APIError(f"{label}必须是文本。")
    if required and not value.strip():
        raise APIError(f"请填写{label}。")
    if len(value) > limit:
        raise APIError(f"{label}超长（最多 {limit}）。")
    return value


class Server:
    def __init__(self, root=DATA_DIR):
        self.store = Store(root)
        self.agent = Agent(self.store)
        self.chat = ChatBrain(self.agent)
        self.pool = ThreadPoolExecutor(max_workers=3,
                                       thread_name_prefix="agent")
        self.jobs_lock = threading.Lock()
        self.jobs = {}
        self.scheduler_thread = threading.Thread(
            target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()

    # ---------------- job manager ----------------
    def submit_job(self, kind, work, context=None, job_id=None):
        job = {
            "id": job_id or uid(), "kind": kind, "status": "running",
            "message": "处理中，请保持本服务运行。", "result": None,
            "error": None, "context": context, "progress": None,
            "created_at": now(),
        }
        with self.jobs_lock:
            self.jobs[job["id"]] = job

        def run():
            try:
                job["result"] = work()
                job["status"] = "done"
                job["message"] = "已完成"
                job["finished_at"] = now()
            except Exception as exc:
                msg = str(exc) or "任务失败。"
                s = self.store.settings()
                for key in ("api_key", "tavily_api_key"):
                    if s.get(key):
                        msg = msg.replace(s[key], "[密钥已隐藏]")
                job["status"] = "error"
                job["error"] = msg[:2000]
                job["message"] = "任务失败"
                job["finished_at"] = now()
        self.pool.submit(run)
        return {"job_id": job["id"]}

    def get_job(self, job_id):
        with self.jobs_lock:
            job = self.jobs.get(job_id)
        if not job:
            raise APIError("任务不存在。", 404)
        return job

    def _set_progress(self, job_id, progress):
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            if job is not None:
                job["progress"] = progress

    # ---------------- background scheduler ----------------
    def _scheduler_loop(self):
        while True:
            try:
                due = self.store.schedules_due(now())
                for sch in due:
                    sch["status"] = "executing"
                    self.store.save_schedule(sch)
                    def _do(sid=sch["id"], cid=sch["content_id"]):
                        self.agent.execute_publish(cid)
                    self.submit_job("scheduled-publish", _do)
            except Exception:
                pass
            time.sleep(20)

    # ---------------- full state ----------------
    def state(self):
        return {
            "settings": self.store.public_settings(),
            "materials": self.store.materials(),
            "plans": self.store.list("plans"),
            "topics": self.store.list("topics"),
            "projects": self.store.list("projects"),
            "contents": self.store.list("contents"),
            "publishes": self.store.list("publishes"),
            "schedules": self.store.list("schedules"),
            "feedback": self.store.list("feedback"),
            "capabilities": {
                p: publisher.capability(self.store.settings(), p)
                for p in PLATFORMS
            },
        }


# ----------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "MediaAgent/1.0"

    @property
    def srv(self):
        return self.server.srv

    def log_message(self, fmt, *args):
        if len(args) > 1 and str(args[1]).startswith("5"):
            sys.stderr.write("MediaAgent: request failed\n")

    def guard(self, mutation=False):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        if host.lower() not in allowed:
            raise APIError("仅允许通过本机 localhost 访问。", 403)
        if mutation:
            if self.headers.get("Sec-Fetch-Site") not in (
                    None, "same-origin", "none"):
                raise APIError("拒绝跨站请求。", 403)

    def read_body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError("请求长度无效。")
        if size < 0 or size > MAX_BODY:
            raise APIError("请求过大。", 413)
        if not size:
            return {}
        if self.headers.get_content_type() != "application/json":
            raise APIError("请使用 application/json。", 415)
        try:
            data = json.loads(self.rfile.read(size).decode("utf-8"))
        except (ValueError, UnicodeError):
            raise APIError("JSON 格式错误。")
        if not isinstance(data, dict):
            raise APIError("请求须为 JSON 对象。")
        return data

    def send_json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self, method):
        try:
            self.guard(method != "GET")
            path = unquote(urlparse(self.path).path)
            body = self.read_body() if method != "GET" else {}
            srv = self.srv
            ag = srv.agent

            if method == "GET":
                if path == "/api/health":
                    return self.send_json({"ok": True})
                if path == "/api/state":
                    return self.send_json(srv.state())
                if m := re.fullmatch(r"/api/jobs/([a-f0-9]+)", path):
                    return self.send_json(srv.get_job(m[1]))
                if path == "/api/capabilities":
                    return self.send_json({
                        p: publisher.capability(
                            srv.store.settings(), p)
                        for p in PLATFORMS})
                if path == "/api/contents":
                    return self.send_json(
                        {"contents": srv.store.list("contents")})
                if m := re.fullmatch(r"/api/contents/([a-f0-9]+)", path):
                    return self.send_json(
                        srv.store.get("contents", m[1]))
                if path == "/api/feedback":
                    return self.send_json(
                        {"items": srv.store.list("feedback")})
                # ---- chat ----
                if path == "/api/chat/conversations":
                    convs = [
                        {"id": c["id"], "title": c["title"],
                         "updated_at": c["updated_at"],
                         "created_at": c["created_at"]}
                        for c in srv.chat.list_conversations()]
                    return self.send_json({"conversations": convs})
                if m := re.fullmatch(r"/api/chat/conversations/([a-f0-9]+)",
                                     path):
                    conv = srv.chat.get_conversation(m[1])
                    # don't expose internal tool-call messages to the frontend
                    return self.send_json(
                        {"id": conv["id"], "title": conv["title"],
                         "events": conv["events"],
                         "created_at": conv["created_at"],
                         "updated_at": conv["updated_at"]})
                return self.serve_static(path)

            if method == "PUT" and path == "/api/settings":
                return self.send_json(srv.store.update_settings(body))
            if method == "PUT" and path == "/api/platform-creds":
                return self._save_platform_creds(body)

            if method == "DELETE":
                if m := re.fullmatch(r"/api/materials/([a-f0-9]+)", path):
                    srv.store.delete("materials", m[1])
                    return self.send_json({"ok": True})
                if m := re.fullmatch(r"/api/chat/conversations/([a-f0-9]+)",
                                     path):
                    srv.chat.delete_conversation(m[1])
                    return self.send_json({"ok": True})

            if method == "POST":
                # ---- chat ----
                if path == "/api/chat/new":
                    conv = srv.chat.new_conversation()
                    return self.send_json({"conversation_id": conv["id"]}, 201)
                if m := re.fullmatch(
                        r"/api/chat/conversations/([a-f0-9]+)/send", path):
                    conv_id = m[1]
                    text = _s(body.get("message"), "消息", 8000, True)
                    def _run():
                        return srv.chat.respond(
                            conv_id, text,
                            on_progress=lambda steps:
                                srv._set_progress(job_id, {"steps": steps}))
                    job_id = uid()
                    return self.send_json(
                        srv.submit_job("chat", _run, job_id=job_id), 202)
                # ---- autonomous brain ----
                if path == "/api/brain/run":
                    goal = _s(body.get("goal"), "目标", 4000, True)
                    job_id = uid()
                    def _run():
                        b = Brain(ag)
                        return b.run(
                            goal,
                            on_progress=lambda trace:
                                srv._set_progress(job_id, trace))
                    return self.send_json(
                        srv.submit_job("brain", _run, job_id=job_id), 202)
                # ---- LLM test ----
                if path == "/api/test-llm":
                    import llm as llm_mod
                    return self.send_json(srv.submit_job(
                        "test-llm",
                        lambda: {"message": llm_mod.test(
                            srv.store.settings())}), 202)
                # ---- materials ----
                if path == "/api/materials":
                    kind = body.get("kind", "note")
                    item = srv.store.add_material(
                        kind, _s(body.get("title"), "标题", 500, True),
                        _s(body.get("content"), "内容", 300_000, True),
                        body.get("url", ""), body.get("source", "手动"),
                        body.get("meta"))
                    return self.send_json({"material": item}, 201)
                if path == "/api/upload":
                    return self._upload(body, srv)
                if path == "/api/import-url":
                    return self._import_url(body, srv)
                # ---- plan ----
                if path == "/api/plan":
                    return self.send_json(srv.submit_job(
                        "plan",
                        lambda: ag.plan_calendar(
                            int(body.get("weeks", 4)),
                            body.get("goals", ""),
                            body.get("platforms") or PLATFORMS,
                            body.get("notes", ""))), 202)
                # ---- topics ----
                if path == "/api/research-topics":
                    return self.send_json(srv.submit_job(
                        "research",
                        lambda: ag.research_topics(
                            _s(body.get("theme"), "主题", 600, True),
                            int(body.get("count", 5)),
                            body.get("use_web", True))), 202)
                # ---- projects ----
                if path == "/api/projects":
                    p = srv.store.create_project(
                        _s(body.get("theme"), "主题", 600, True),
                        _s(body.get("brief"), "任务说明", 6000, True),
                        body.get("audience", ""))
                    if body.get("topic_id"):
                        p["topic_id"] = body["topic_id"]
                        srv.store.update_project(p)
                    return self.send_json({"project": p}, 201)
                if m := re.fullmatch(
                        r"/api/projects/([a-f0-9]+)/evidence", path):
                    pid = m[1]
                    return self.send_json(srv.submit_job(
                        "evidence",
                        lambda: ag.build_fact_cards(pid)), 202)
                if m := re.fullmatch(
                        r"/api/projects/([a-f0-9]+)/produce", path):
                    pid = m[1]
                    target = body.get("target")
                    return self.send_json(srv.submit_job(
                        "produce",
                        lambda: self._produce(pid, target, ag)), 202)
                if m := re.fullmatch(
                        r"/api/projects/([a-f0-9]+)/full-auto", path):
                    pid = m[1]
                    return self.send_json(srv.submit_job(
                        "full-auto",
                        lambda: self._full_auto(pid, ag, body)), 202)
                # ---- content actions ----
                if m := re.fullmatch(
                        r"/api/contents/([a-f0-9]+)/audit", path):
                    cid = m[1]
                    return self.send_json(srv.submit_job(
                        "audit", lambda: ag.audit(cid)), 202)
                if m := re.fullmatch(
                        r"/api/contents/([a-f0-9]+)/fix", path):
                    cid = m[1]
                    return self.send_json(srv.submit_job(
                        "fix", lambda: ag.auto_fix(cid)), 202)
                if m := re.fullmatch(
                        r"/api/contents/([a-f0-9]+)/approve", path):
                    cid = m[1]
                    content = ag.approve(
                        cid, body.get("account", ""),
                        body.get("execute_at"), body.get("timezone",
                                                         "Asia/Shanghai"))
                    return self.send_json({"content": content})
                if m := re.fullmatch(
                        r"/api/contents/([a-f0-9]+)/publish", path):
                    cid = m[1]
                    return self.send_json(srv.submit_job(
                        "publish",
                        lambda: ag.execute_publish(
                            cid, body.get("force_run", False))), 202)
                if m := re.fullmatch(
                        r"/api/contents/([a-f0-9]+)/verify", path):
                    cid = m[1]
                    rid = body.get("record_id")
                    return self.send_json(srv.submit_job(
                        "verify",
                        lambda: ag.verify_publish(rid)), 202)
                # ---- schedules ----
                if m := re.fullmatch(
                        r"/api/schedules/([a-f0-9]+)/cancel", path):
                    return self.send_json(
                        ag.cancel_schedule(m[1]))
                # ---- feedback ----
                if path == "/api/feedback":
                    item = ag.record_feedback(
                        _s(body.get("content_id"), "内容ID", 64, True),
                        body.get("metrics"), body.get("comments"),
                        body.get("source", "手动录入"),
                        body.get("observed_at"))
                    return self.send_json({"item": item}, 201)
                if path == "/api/review":
                    return self.send_json(srv.submit_job(
                        "review",
                        lambda: ag.review(body.get("content_id"))), 202)
            raise APIError("接口不存在。", 404)
        except APIError as exc:
            self.send_json({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (KeyError, ValueError) as exc:
            self.send_json({"error": str(exc)[:1500]}, 400)
        except Exception:
            self.send_json({"error": "本地服务处理失败。"}, 500)

    # ---------------- helpers used by routes ----------------
    def _produce(self, pid, target, ag):
        if target:
            platform, language = target.split(":")
            return ag.produce_target(pid, platform, language)
        # produce all targets
        out = {}
        for platform, language in self._project_targets(pid, ag):
            res = ag.produce_target(pid, platform, language)
            out[target_key(platform, language)] = res["id"]
        return {"content_ids": out}

    def _project_targets(self, pid, ag):
        from platforms import targets as ptargets
        return ptargets()

    def _full_auto(self, pid, ag, body):
        """Evidence -> produce all -> audit -> fix -> (approve/publish if
        requested). Stops and returns blockers clearly."""
        ag.build_fact_cards(pid)
        ids = {}
        from platforms import targets as ptargets
        for platform, language in ptargets():
            c = ag.produce_target(pid, platform, language)
            c = ag.audit(c["id"])
            audits = c.get("audits", {})
            issues = audits.get("fact", {}).get("issues", []) + \
                audits.get("language", {}).get("issues", [])
            if issues:
                c = ag.auto_fix(c["id"])
            ids[target_key(platform, language)] = c["id"]
        return {"content_ids": ids, "note":
                "内容已生成并完成审核/自动修复；请在界面逐项审定，再按需发布。"}

    def _save_platform_creds(self, body):
        s = self.srv.store
        settings = s.settings()
        creds = settings.get("platform_creds") or {}
        platform = body.get("platform")
        if platform not in PLATFORMS:
            raise APIError("平台无效。")
        data = body.get("credentials")
        if not isinstance(data, dict):
            raise APIError("凭据格式无效。")
        creds[platform] = data
        settings["platform_creds"] = creds
        s.update_settings({"platform_creds": creds})
        return self.send_json_local(platform)

    def send_json_local(self, platform):
        return publisher.capability(self.srv.store.settings(), platform)

    def _upload(self, body, srv):
        filename = _s(body.get("filename"), "文件名", 500, True)
        encoded = _s(body.get("data"), "文件内容", MAX_BODY, True)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise APIError("base64 编码无效。")
        ext = Path(filename).suffix.lower()
        title = Path(filename).stem
        if ext in (".md", ".txt"):
            content = _decode_text(data)
            hm = re.search(r"^#\s+(.+)$", content, re.M)
            if hm:
                title = hm.group(1).strip()
        elif ext == ".docx":
            content = _read_docx(data)
        elif ext in (".html", ".htm"):
            content = _read_html(data)
        else:
            raise APIError("支持 .md/.txt/.docx/.html。")
        item = srv.store.add_material("upload", title[:500], content,
                                      source=filename)
        return self.send_json({"material": item}, 201)

    def _import_url(self, body, srv):
        import engine_compat
        url = _s(body.get("url"), "链接", 3000, True)
        article = engine_compat.fetch_article(url)
        item = srv.store.add_material(
            "web", article["title"], article["content"],
            url=url, source=article.get("author", ""))
        return self.send_json({"material": item}, 201)

    # ---------------- static & media ----------------
    def serve_static(self, path):
        if path.startswith("/media/"):
            return self._serve_media(path[len("/media/"):])
        relative = "index.html" if path == "/" else path.lstrip("/")
        if relative.startswith("static/"):
            relative = relative[len("static/"):]
        target = (APP_DIR / "static" / relative).resolve()
        if not str(target).startswith(str(APP_DIR / "static")) or \
                not target.is_file():
            raise APIError("页面不存在。", 404)
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         (mimetypes.guess_type(str(target))[0]
                          or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data: http: https:; "
                         "connect-src 'self';")
        self.end_headers()
        self.wfile.write(data)

    def _serve_media(self, rel):
        store_root = self.srv.store.root.resolve()
        target = (store_root / "media" / rel).resolve()
        if not str(target).startswith(str(store_root / "media")) or \
                not target.is_file():
            raise APIError("媒体文件不存在。", 404)
        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "image/png"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")


# ---------------- upload text helpers ----------------

def _decode_text(data):
    for enc in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    raise APIError("无法识别文本编码，请另存为 UTF-8。")


def _read_docx(data):
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise APIError("无法读取 Word，请使用未加密 .docx。")
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return "\n\n".join("".join(p.itertext())
                       for p in root.findall(".//w:p", ns))


def _read_html(data):
    text = _decode_text(data)
    text = re.sub(r"(?is)<script.*?</script>", "", text)
    text = re.sub(r"(?is)<style.*?</style>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "\n", text)
    import html as h
    return h.unescape(text).strip()


def create_server(port=DEFAULT_PORT):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.srv = Server(DATA_DIR)
    return server


def main():
    import argparse
    parser = argparse.ArgumentParser(description="跨平台新媒体助手")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("MEDIA_AGENT_PORT",
                                                   DEFAULT_PORT)))
    args = parser.parse_args()
    try:
        server = create_server(args.port)
    except OSError:
        print(f"端口 {args.port} 已被占用，请直接访问或更换端口。",
              file=sys.stderr)
        return 1
    print(f"跨平台新媒体助手：http://127.0.0.1:{server.server_port}",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
