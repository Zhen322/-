"""Explicit calls to user-configured Coze Coding workflow deployments.

Protocol: GET /graph_parameter, POST /async_run, GET /task/{task_id}.
Preview project IDs are never converted into deployment addresses. Credentials
stay in a separate owner-only file and are never returned or redirected.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import http.client
import json
import os
import re
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit
import urllib.error
import urllib.request

ROLES = ("topics", "writer")
MAX_INPUT = 1024 * 1024
MAX_OUTPUT = 4 * 1024 * 1024
POLL_SECONDS = 5
REMOTE_TERMINAL = {"completed", "failed", "timeout"}
REMOTE_STATUSES = {"pending", "running", *REMOTE_TERMINAL}


class CozeError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise CozeError("输入必须是有效 JSON，不能包含 NaN、Infinity 或过深嵌套。") from None


def validate_base_url(value):
    if not isinstance(value, str) or len(value) > 500:
        raise CozeError("请填写部署页面提供的 API 基址。")
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if (re.search(r"[\x00-\x20\\]", value) or parsed.scheme != "https" or
                parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment or parsed.path not in ("", "/") or
                parsed.port not in (None, 443)):
            raise ValueError()
        host = parsed.hostname or ""
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+coze\.site", host):
            raise ValueError()
        return "https://" + host
    except ValueError:
        raise CozeError("请填写部署页面的 https://子域.coze.site API 基址（不含 /run 或参数）。预览分享链接不是 API 地址；首版暂不支持自定义域名。") from None


def _atomic_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".coze-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(_json(value) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _scrub(value, tokens):
    if isinstance(value, str):
        for token in tokens:
            if token:
                value = value.replace(token, "[令牌已隐藏]")
        return value
    if isinstance(value, list):
        return [_scrub(item, tokens) for item in value]
    if isinstance(value, dict):
        return {_scrub(key, tokens): _scrub(item, tokens) for key, item in value.items()}
    return value


def request_coze(base_url, token, path, inputs=None):
    """One pinned HTTPS request; deliberately never follows any redirect."""
    import engine
    base_url = validate_base_url(base_url)
    is_task = bool(re.fullmatch(r"/task/[A-Za-z0-9_-]{1,200}", path))
    if not base_url or not (path in ("/graph_parameter", "/async_run") or is_task):
        raise CozeError("Coze 请求路径不合法。")
    if (path == "/async_run") != (inputs is not None):
        raise CozeError("Coze 执行与查询方法不匹配。")
    try:
        canonical, addresses = engine._public_url(base_url + path)
        opener = engine._public_opener(addresses)
        payload = _json(inputs).encode("utf-8") if inputs is not None else None
        req = urllib.request.Request(canonical, data=payload, headers={"Authorization": "Bearer " + token, "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "Local-Wechat-Writer/1.0"}, method="POST" if inputs is not None else "GET")
        # Submission only creates the remote task. Long execution is observed
        # through separate idempotent GET requests, never a repeated POST.
        with opener.open(req, timeout=60 if inputs is not None else 30) as response:
            if not 200 <= response.status < 300:
                raise CozeError("Coze 未返回成功的 HTTP 响应，请检查部署状态。", 502)
            content = response.read(MAX_OUTPUT + 1)
        if len(content) > MAX_OUTPUT:
            raise CozeError("Coze 返回超过 4 MiB，未截断或伪造结果。请缩小工作流输出后重试。", 502)
        parsed = json.loads(content.decode("utf-8-sig"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        _json(parsed)
        return _scrub(parsed, [token])
    except CozeError:
        raise
    except engine.EngineError:
        raise CozeError("无法核实 Coze API 的公网地址，已停止请求。请检查部署域名或网络；不允许访问本机、局域网或内部服务。", 502) from None
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        if 300 <= status < 400:
            raise CozeError("Coze 返回了重定向。为保护令牌未跟随跳转，请使用部署页面显示的最终 API 基址。", 502) from None
        if status in (401, 403):
            raise CozeError("Coze 拒绝鉴权，请检查该部署对应的 API Token 及权限。", 502) from None
        if status == 404:
            raise CozeError("未找到 Coze 部署接口，请确认使用的是扣子编程工作流的部署 API 基址。", 502) from None
        if status == 429:
            raise CozeError("Coze 当前请求过多或额度受限，请在 Coze 检查后再试。", 502) from None
        raise CozeError(f"Coze 返回 HTTP {status}，请在部署日志中检查；本地没有自动重试该工作流。", 502) from None
    except (TimeoutError, urllib.error.URLError, OSError, http.client.HTTPException):
        raise CozeError("Coze 网络请求失败或超时。远端可能已经执行，请先检查 Coze 运行记录，避免重复执行；本地不会自动重试。", 502) from None
    except (ValueError, UnicodeError, RecursionError):
        raise CozeError("Coze 未返回有效 JSON。请检查部署接口或远端日志；没有生成替代结果。", 502) from None


class CozeStoreMixin:
    def init_coze(self):
        self.coze_lock = threading.RLock()
        self.coze_active = set()
        self.coze_threads = set()
        self.coze_secrets_path = self.root / "coze_secrets.json"
        if self.coze_secrets_path.exists():
            os.chmod(self.coze_secrets_path, 0o600)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS coze_roles(id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        # app.Store marks previously running jobs as interrupted before this
        # hook. Retain remote identity and require an explicit resume action.
        for job in self.all("jobs"):
            if job.get("type") in ("coze-topics", "coze-writer") and job.get("status") == "error" and job.get("remote_task_id"):
                job["can_resume"] = self._coze_can_resume(job)
                if job.get("type") == "coze-writer" and job.get("remote_status") == "completed" and job.get("draft_import_pending") and not job.get("remote_error") and isinstance(job.get("result"), dict):
                    job["error"] = "Coze 已完成且原始返回已保存，本地草稿导入因服务重启中断；请恢复本地导入，不要重新生成。"
                    job["message"] = "只需将已保存的远端返回存为本地待审草稿，无须再次请求 Coze。"
                elif job["can_resume"]:
                    job["error"] = "服务重启中断了本地查询，远端任务 ID 已保存；请继续查询已有任务，不要重新生成。"
                    job["message"] = "本地查询已中断，远端任务 ID 已保存；可继续查询已有任务，不会再次生成。"
                self.put("jobs", job)

    def _coze_role(self, role):
        if role not in ROLES:
            raise CozeError("Coze 角色只能是 topics 或 writer。", 404)
        with self.connect() as db:
            row = db.execute("SELECT data FROM coze_roles WHERE id=?", (role,)).fetchone()
        return json.loads(row["data"]) if row else {"base_url": "", "schema": None, "schema_fetched_at": None, "last_run_at": None}

    def _coze_secrets(self):
        if not self.coze_secrets_path.exists():
            return {}
        try:
            values = json.loads(self.coze_secrets_path.read_text(encoding="utf-8"))
            if not isinstance(values, dict) or any(not isinstance(value, dict) or not isinstance(value.get("token"), str) or not isinstance(value.get("base_url"), str) for value in values.values()):
                raise ValueError()
            return values
        except (ValueError, OSError):
            raise CozeError("本地 Coze 凭据文件无法读取，请重新保存部署配置。") from None

    @staticmethod
    def _coze_token(secrets, role, base_url):
        entry = secrets.get(role) or {}
        # Binding credentials to their destination also protects against a
        # partial settings write (private file succeeds, SQLite update fails).
        return entry.get("token", "") if entry.get("base_url") == base_url else ""

    def _coze_put(self, role, value, db=None):
        if db is not None:
            db.execute("INSERT INTO coze_roles(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (role, _json(value)))
        else:
            with self.connect() as connection:
                self._coze_put(role, value, connection)

    def coze_settings(self):
        with self.coze_lock:
            secrets = self._coze_secrets()
            roles = {}
            for role in ROLES:
                value = self._coze_role(role)
                token = self._coze_token(secrets, role, value.get("base_url", ""))
                value["token_configured"] = bool(token)
                value["configured"] = bool(value.get("base_url") and token)
                roles[role] = value
            return {"roles": roles}

    def update_coze_settings(self, body):
        roles = body.get("roles")
        if not isinstance(roles, dict) or not roles or any(role not in ROLES for role in roles):
            raise CozeError("请按 roles.topics / roles.writer 提交部署配置。")
        with self.coze_lock:
            secrets = self._coze_secrets()
            updates = {}
            for role, changes in roles.items():
                if not isinstance(changes, dict) or any(key not in ("base_url", "token") for key in changes):
                    raise CozeError("每个 Coze 配置仅支持 base_url 与 token。")
                if role in self.coze_active:
                    raise CozeError("该 Coze 角色正在请求中，请完成后再修改配置。", 409)
                current = self._coze_role(role)
                base_url = validate_base_url(changes.get("base_url", current.get("base_url", "")))
                old_token = self._coze_token(secrets, role, current.get("base_url", ""))
                token = changes.get("token", "" if base_url != current.get("base_url", "") else old_token)
                if not isinstance(token, str) or len(token) > 8192 or re.search(r"[\x00-\x20\x7f]", token):
                    raise CozeError("API Token 必须是不含空格或换行的文本；清空可移除令牌。")
                if token.startswith("Bearer "):
                    raise CozeError("请只填写令牌本身，不要包含 Bearer 前缀。")
                if token != old_token or base_url != current.get("base_url", ""):
                    current.update(schema=None, schema_fetched_at=None, last_run_at=None)
                current.update(base_url=base_url, updated_at=_now())
                secrets[role] = {"base_url": base_url, "token": token}
                updates[role] = current
            # Validate every role before changing either persistence surface.
            _atomic_json(self.coze_secrets_path, secrets)
            with self.connect() as db:
                for role, value in updates.items():
                    self._coze_put(role, value, db)
            return self.coze_settings()

    def _reserve_coze(self, role, require_schema=False):
        with self.coze_lock:
            value = self._coze_role(role)
            token = self._coze_token(self._coze_secrets(), role, value.get("base_url", ""))
            if not value.get("base_url") or not token:
                raise CozeError("请先填写这个 Coze 角色的真实部署 API 基址和 API Token。")
            if require_schema and not value.get("schema_fetched_at"):
                raise CozeError("请先读取该部署的真实输入结构，检查字段后再执行工作流。")
            if role in self.coze_active:
                raise CozeError("这个 Coze 角色已有进行中的请求，请等待完成，避免重复调用。", 409)
            self.coze_active.add(role)
            return copy.deepcopy(value), token

    def _release_coze(self, role):
        with self.coze_lock:
            self.coze_active.discard(role)

    def coze_schema(self, role):
        value, token = self._reserve_coze(role)
        try:
            schema = request_coze(value["base_url"], token, "/graph_parameter")
            if not isinstance(schema, dict) or not isinstance(schema.get("input_schema"), dict) or not isinstance(schema.get("output_schema"), dict):
                raise CozeError("该接口未返回 input_schema / output_schema 对象，请检查部署是否为扣子编程工作流。", 502)
            with self.coze_lock:
                value.update(schema=schema, schema_fetched_at=_now())
                self._coze_put(role, value)
            return {"schema": schema}
        finally:
            self._release_coze(role)

    @staticmethod
    def _coze_time(value):
        if not isinstance(value, str) or len(value) > 100:
            raise CozeError("远端任务截止时间格式无效。", 502)
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                raise ValueError()
            return moment.astimezone(timezone.utc)
        except ValueError:
            raise CozeError("远端任务截止时间必须包含时区。", 502) from None

    @classmethod
    def _coze_can_resume(cls, job):
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", str(job.get("remote_task_id") or "")) or
                (isinstance(job.get("remote_status"), str) and job["remote_status"] in REMOTE_TERMINAL) or job.get("status") == "done"):
            return False
        try:
            return cls._coze_time(job.get("poll_expires_at")) > datetime.now(timezone.utc)
        except CozeError:
            return False

    def _coze_job_patch(self, job_id, **changes):
        with self.job_lock:
            job = self.get("jobs", job_id)
            job.update(changes)
            self.put("jobs", job)
            return job

    def _coze_capacity(self):
        if sum(job.get("status") == "running" for job in self.all("jobs")) >= 8:
            raise CozeError("当前已有 8 个进行中的任务，请等待部分完成后重试。", 429)

    def _coze_observe(self, job_id, value, token):
        """Read an existing remote task; never submits a workflow."""
        while True:
            job = self.get("jobs", job_id)
            if self._coze_time(job["poll_expires_at"]) <= datetime.now(timezone.utc):
                self._coze_job_patch(job_id, can_resume=False, local_poll_expired=True)
                raise CozeError("已到远端任务截止时间或本地最长 24 小时查询上限。停止查询；没有重复生成任务。")
            status = request_coze(value["base_url"], token, "/task/" + job["remote_task_id"])
            if not isinstance(status, dict):
                raise CozeError("Coze 任务状态响应不是对象，已保留远端任务 ID。", 502)
            if status.get("task_id") is not None and status["task_id"] != job["remote_task_id"]:
                raise CozeError("Coze 返回的任务 ID 与查询目标不符，已停止查询。", 502)
            state = status.get("status")
            # Persist observed status before interpreting it. The raw result is
            # retained on completion; pending payloads never become articles.
            changes = {"remote_status": state, "last_polled_at": _now(), "can_resume": False}
            if status.get("deadline"):
                deadline = self._coze_time(status["deadline"])
                existing = self._coze_time(job["poll_expires_at"])
                changes.update(remote_deadline=status["deadline"], poll_expires_at=min(existing, deadline).isoformat())
            self._coze_job_patch(job_id, **changes)
            if not isinstance(state, str) or state not in REMOTE_STATUSES:
                raise CozeError("Coze 返回未知任务状态，未当作执行成功；远端 ID 已保存。", 502)
            if state == "completed":
                if status.get("error"):
                    detail = status["error"] if isinstance(status["error"], str) else _json(status["error"])
                    detail = _scrub(detail, [token])[:1200]
                    self._coze_job_patch(job_id, remote_error=detail, remote_result=status.get("result"))
                    raise CozeError("Coze 返回 completed 但同时报告错误，未标记成功，请检查远端记录：" + detail, 502)
                if "result" not in status or not isinstance(status["result"], (dict, list, str)):
                    raise CozeError("Coze 表示任务完成，但没有返回可用的 result 对象或文字；没有生成替代结果。", 502)
                finished_at = _now()
                output = status["result"]
                result = {"role": job["coze_role"], "base_url": value["base_url"], "output": output, "started_at": job.get("remote_submitted_at") or job["created_at"], "finished_at": finished_at}
                importing = job.get("coze_role") == "writer" and (job.get("input_context") or {}).get("quick_version") == 1
                self._coze_job_patch(job_id, status="running" if importing else "done", result=result, error=None, message="真实返回已保存，正在整理本地待审草稿。" if importing else "Coze 任务已完成，真实返回已保存，等待人工查看。", finished_at=finished_at, can_resume=False, draft_import_pending=importing)
                self._auto_import_coze_draft(job_id)
                if importing:
                    self._coze_job_patch(job_id, status="done", draft_import_pending=False, message="Coze 真实返回已保存，请查看待审草稿或本地导入提示。")
                try:
                    with self.coze_lock:
                        current = self._coze_role(job["coze_role"])
                        if current.get("base_url") == value["base_url"]:
                            current["last_run_at"] = finished_at
                            self._coze_put(job["coze_role"], current)
                except Exception:
                    # The saved result remains authoritative if updating the
                    # optional settings summary fails after completion.
                    pass
                return
            if state in ("failed", "timeout"):
                detail = status.get("error")
                detail = detail if isinstance(detail, str) else _json(detail) if detail else "未提供具体原因"
                detail = _scrub(detail, [token])[:1200]
                self._coze_job_patch(job_id, remote_error=detail)
                raise CozeError(("Coze 远端任务失败：" if state == "failed" else "Coze 远端任务已超时：") + detail, 502)
            message = "Coze 远端任务等待执行；已保存任务 ID，正在持续查询。" if state == "pending" else "Coze 远端任务正在执行；已保存任务 ID，正在持续查询。"
            job = self._coze_job_patch(job_id, message=message)
            remaining = (self._coze_time(job["poll_expires_at"]) - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                time.sleep(min(POLL_SECONDS, remaining))

    def _coze_worker(self, job_id, value, token, inputs=None):
        role = self.get("jobs", job_id)["coze_role"]
        try:
            if inputs is not None:
                submitted_at = _now()
                self._coze_job_patch(job_id, remote_submitted_at=submitted_at, message="正在创建一个 Coze 异步任务；不会自动重复提交。")
                submitted = request_coze(value["base_url"], token, "/async_run", inputs)
                if not isinstance(submitted, dict):
                    raise CozeError("Coze 异步提交未返回任务对象。远端是否执行尚不确定，请先检查部署记录，不要直接重复生成。", 502)
                task_id = submitted.get("task_id")
                if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", task_id):
                    raise CozeError("Coze 异步提交未返回可查询的任务 ID。请先检查部署记录，避免重复执行。", 502)
                cap = self._coze_time(submitted_at) + timedelta(hours=24)
                # Save remote identity immediately, before validating status or
                # deadline. Later parse/network failures must not lose it.
                self._coze_job_patch(job_id, remote_task_id=task_id, remote_base_url=value["base_url"], remote_status=submitted.get("status"), remote_deadline=submitted.get("deadline"), remote_created_at=submitted.get("created_at"), poll_expires_at=cap.isoformat(), can_resume=False)
                if submitted.get("deadline"):
                    cap = min(cap, self._coze_time(submitted["deadline"]))
                    self._coze_job_patch(job_id, poll_expires_at=cap.isoformat())
                if not isinstance(submitted.get("status"), str) or submitted["status"] not in REMOTE_STATUSES:
                    raise CozeError("Coze 异步提交返回未知状态，已保留任务 ID，可人工检查后继续查询。", 502)
            self._coze_observe(job_id, value, token)
        except Exception as exc:
            job = self.get("jobs", job_id)
            resumable = self._coze_can_resume(job)
            message = str(exc) if isinstance(exc, CozeError) else "本地查询遇到异常，执行结果尚未确认。"
            message = _scrub(message, [token])[:1600]
            if resumable:
                message += " 远端任务 ID 已保存，可点击继续查询已有任务；不会再次提交生成。"
            self._coze_job_patch(job_id, status="error", error=message, message="远端任务查询已暂停。" if resumable else "Coze 执行未完成，请查看错误。", finished_at=_now(), can_resume=resumable)
        finally:
            with self.coze_lock:
                self.coze_active.discard(role)
                self.coze_threads.discard(threading.current_thread())

    def _coze_schedule(self, job_id, value, token, inputs=None):
        # Remote polling must not occupy the two local model workers. Role
        # reservation bounds this separate daemon group to at most two threads.
        thread = threading.Thread(target=self._coze_worker, args=(job_id, value, token, inputs), name="coze-" + job_id[:8], daemon=True)
        try:
            with self.coze_lock:
                self.coze_threads.add(thread)
            thread.start()
        except Exception:
            with self.coze_lock:
                self.coze_threads.discard(thread)
            job = self.get("jobs", job_id)
            self._coze_job_patch(job_id, status="error", error="本地查询任务未能排入队列，请重试继续查询。" if job.get("remote_task_id") else "本地执行任务未能排入队列，尚未提交远端。", can_resume=self._coze_can_resume(job))
            self._release_coze(job["coze_role"])
            raise CozeError("本地任务未能排入队列，远端记录已保留。", 503) from None

    def run_coze(self, role, body, *, input_context=None):
        inputs = body.get("inputs")
        if not isinstance(inputs, dict):
            raise CozeError("inputs 必须是按实际输入结构填写的 JSON 对象。")
        if len(_json(inputs).encode("utf-8")) > MAX_INPUT:
            raise CozeError("Coze 输入超过 1 MiB，请缩小后再提交。")
        value, token = self._reserve_coze(role, require_schema=True)
        try:
            if input_context is not None:
                from coze_quick import schema_revision
                if value.get("base_url") != input_context.get("verified_base_url") or schema_revision((value.get("schema") or {}).get("input_schema")) != input_context.get("input_schema_revision"):
                    raise CozeError("快捷调用的部署地址或参数在确认后发生变化，已停止提交，请重新核对。", 409)
            with self.job_lock:
                self._coze_capacity()
                for previous in self.all("jobs"):
                    if previous.get("type") == "coze-" + role and self._coze_can_resume(previous):
                        raise CozeError("这个角色还有未确认完成的远端任务，请先继续查询已有任务，避免重复生成。", 409)
                job_id = uuid.uuid4().hex
                job = {"id": job_id, "type": "coze-" + role, "coze_role": role, "status": "running", "message": "Coze 异步任务已排队，等待提交。", "result": None, "error": None, "created_at": _now(), "remote_task_id": None, "remote_base_url": value["base_url"], "remote_deadline": None, "remote_status": None, "remote_submitted_at": None, "poll_expires_at": None, "can_resume": False}
                job["input_snapshot"] = copy.deepcopy(inputs)
                if input_context is not None:
                    job["input_context"] = copy.deepcopy(input_context)
                self.put("jobs", job)
            self._coze_schedule(job_id, value, token, copy.deepcopy(inputs))
            return {"job_id": job_id}
        except Exception:
            self._release_coze(role)
            raise

    def resume_coze(self, job_id):
        previous = self.get("jobs", job_id)
        role = previous.get("coze_role")
        if previous.get("type") not in ("coze-topics", "coze-writer") or role not in ROLES:
            raise CozeError("这不是可恢复查询的 Coze 异步任务。", 404)
        value, token = self._reserve_coze(role)
        try:
            with self.job_lock:
                previous = self.get("jobs", job_id)
                if previous.get("status") == "running":
                    raise CozeError("该任务正在查询，请等待当前查询完成。", 409)
                if not self._coze_can_resume(previous):
                    raise CozeError("此任务没有可恢复的远端 ID、已结束或已超过查询截止时间。不会新建远端任务。", 409)
                if value["base_url"] != previous.get("remote_base_url"):
                    raise CozeError("当前部署地址与这项远端任务不一致。请恢复原部署地址和对应 Token 后再继续查询。", 409)
                self._coze_capacity()
                previous.update(status="running", error=None, message="正在恢复查询已保存的 Coze 任务 ID，不会再次提交生成。", can_resume=False, resumed_at=_now())
                self.put("jobs", previous)
            self._coze_schedule(job_id, value, token)
            return {"job_id": job_id, "resumed": True}
        except Exception:
            self._release_coze(role)
            raise
