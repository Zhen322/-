"""Explicit shortcuts for the two verified user-owned Coze workflows.

This adapter never adds local writing policy or personal notes to inputs. It
retains submitted inputs and selected-topic provenance, and imports returned
Markdown locally without fetching the remote download link.
"""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
import threading
from urllib.parse import unquote, urlsplit

from coze_bridge import CozeError

TOPICS_INPUT = {"type": "object", "properties": {
    "topic": {"type": "string", "default": ""},
    "keywords": {"type": "string", "default": ""},
    "links": {"type": "array", "items": {"type": "string"}, "default": []},
    "auto_mode": {"type": "boolean", "default": True},
    "confirmed_topics": {"type": "array", "items": {"type": "string"}, "default": []},
}}
# Populated only from the actual deployed workflow schema, never project IDs.
WRITER_INPUT = {'properties': {'task_mode': {'default': 'write_article', 'type': 'string'},
                'topic': {'default': '', 'type': 'string'},
                'materials': {'default': '', 'type': 'string'},
                'target_readers': {'default': '', 'type': 'string'},
                'word_count': {'default': '约1500字', 'type': 'string'},
                'author_view': {'default': '', 'type': 'string'},
                'style_sample': {'default': '', 'type': 'string'},
                'extra_req': {'default': '', 'type': 'string'},
                'search_keywords': {'default': '', 'type': 'string'},
                'days_back': {'default': 30, 'type': 'integer'},
                'max_results': {'default': 10, 'type': 'integer'},
                'auto_search': {'default': True, 'type': 'boolean'},
                'library_file': {'default': None},
                'article_library_file': {'default': None}},
 'type': 'object'}


def _semantic(value):
    if isinstance(value, list):
        return [_semantic(item) for item in value]
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            if key in ("title", "description", "examples", "$id", "$schema"):
                continue
            if key in ("properties", "$defs", "definitions", "patternProperties") and isinstance(item, dict):
                output[key] = {name: _semantic(spec) for name, spec in item.items()}
            elif key in ("default", "enum", "const"):
                output[key] = copy.deepcopy(item)
            else:
                output[key] = _semantic(item)
        return output
    return value


def schema_revision(schema):
    raw = json.dumps(_semantic(schema), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_quick_schema(role, schema):
    expected = TOPICS_INPUT if role == "topics" else WRITER_INPUT
    if expected is None:
        raise CozeError("文案助手的实际字段合同尚未确认，快捷调用暂不可用；可在高级参数页查看已读取的真实参数。", 409)
    if not isinstance(schema, dict) or _semantic(schema.get("input_schema")) != _semantic(expected):
        raise CozeError("Coze 工作流参数已与快捷接入合同不一致。已停止执行，请重新核对真实参数后更新接入映射；没有提交生成。", 409)
    return schema_revision(schema["input_schema"])


def _text(value, label, maximum, allow_blank=True):
    if not isinstance(value, str) or len(value) > maximum or (not allow_blank and not value.strip()):
        raise CozeError(f"{label}须为{'可为空的' if allow_blank else '非空'}文本，最多 {maximum:,} 字符。")
    return value


def safe_external_url(value):
    """Validate link syntax only. Importing a draft never fetches any URL."""
    try:
        value = value.strip().strip("<>")
        decoded = unquote(value)
        if re.search(r"[\x00-\x20\x7f\\]", decoded):
            return False
        parsed = urlsplit(decoded)
        if parsed.scheme.lower() not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global or ip.is_multicast or ip.is_reserved:
                return False
        except ValueError:
            if "." not in host:
                return False
        return True
    except (ValueError, TypeError):
        return False


def safe_draft_markdown(markdown):
    """Strip unsafe destinations/active HTML while preserving the raw job output."""
    removed = []
    def inline(match):
        label, destination = match.group(1), match.group(2)
        target = destination.split(' "', 1)[0].strip()
        if safe_external_url(target):
            return match.group(0)
        removed.append("移除了非公开 HTTP(S) 的文稿链接，原始返回仍保留在任务中。")
        return label
    markdown = re.sub(r"!?\[([^\]\n]*)\]\(([^\n]*?)\)", inline, markdown)
    def definition(match):
        if safe_external_url(match.group(2)):
            return match.group(0)
        removed.append("移除了不安全的引用式链接。")
        return ""
    markdown = re.sub(r"^\s*\[([^\]\n]+)\]:\s*(\S+).*?$", definition, markdown, flags=re.M)
    # HTML is not needed for imported article prose. Drop executable blocks,
    # then remove remaining raw HTML tags; ordinary Markdown stays intact.
    markdown, count = re.subn(r"<(script|style|iframe|object|embed)\b[^>]*>.*?</\1\s*>", "", markdown, flags=re.I|re.S)
    if count:
        removed.append("移除了原始 HTML 活动内容。")
    def html_or_autolink(match):
        inner = match.group(0)[1:-1]
        return match.group(0) if safe_external_url(inner) else ""
    markdown = re.sub(r"</?[A-Za-z][^>]*>", html_or_autolink, markdown)
    return markdown, list(dict.fromkeys(removed))


class CozeQuickMixin:
    def init_coze_quick(self):
        self.coze_draft_lock = threading.RLock()

    def _quick_schema(self, role):
        # Refresh immediately before an explicitly requested execution. Reading
        # schema does not generate content, and changed contracts stop here.
        schema = self.coze_schema(role)["schema"]
        fingerprint = verify_quick_schema(role, schema)
        with self.coze_lock:
            current = self._coze_role(role)
            if current.get("schema") != schema:
                raise CozeError("读取参数后配置又发生变化，请重新发起快捷操作。", 409)
            return schema, fingerprint, current["base_url"]

    def quick_coze(self, role, body):
        if not isinstance(body, dict):
            raise CozeError("快捷请求必须是对象。")
        if role == "topics":
            if any(key not in ("topic",) for key in body):
                raise CozeError("快捷选题只支持可选的研究方向 topic。")
            direction = _text(body.get("topic", ""), "研究方向", 2000)
            schema, fingerprint, base_url = self._quick_schema(role)
            inputs = {name: copy.deepcopy(spec["default"]) for name, spec in TOPICS_INPUT["properties"].items()}
            inputs["topic"] = direction
            context = {"quick_version": 1, "role": role, "input_schema_revision": fingerprint, "verified_base_url": base_url, "direction": direction}
            return self.run_coze(role, {"inputs": inputs}, input_context=context)
        if role == "writer":
            return self._quick_writer(body)
        raise CozeError("不存在这个 Coze 角色。", 404)

    def _quick_writer(self, body):
        allowed = {"batch_id", "topic_id", "title", "brief", "reason", "word_count", "target_readers", "author_view", "materials"}
        if any(key not in allowed for key in body):
            raise CozeError("快捷写作只支持已确认的选题及写作选项，不能修改任务模式或传入额外参数。")
        batch_id = _text(body.get("batch_id"), "选题批次", 100, False)
        topic_id = _text(body.get("topic_id"), "选题编号", 100, False)
        batch = next((item for item in self.topic_batches() if item["id"] == batch_id), None)
        if batch is None:
            raise CozeError("所选选题批次不存在，请重新选择。", 404)
        original = next((item for item in batch.get("topics", []) if item["id"] == topic_id), None)
        if original is None:
            raise CozeError("这个选题不属于所选批次，请重新选择。", 404)
        title = _text(body.get("title", original["title"]), "选题标题", 500, False)
        brief = _text(body.get("brief", original.get("brief", "")), "写作方向", 6000)
        reason = _text(body.get("reason", original.get("reason", "")), "选题理由", 3000)
        word_count = body.get("word_count", 1500)
        if isinstance(word_count, bool) or not isinstance(word_count, int) or not 500 <= word_count <= 5000:
            raise CozeError("目标字数须为 500 至 5,000 的整数。")
        overrides = {}
        for field, label, maximum in (("target_readers", "目标读者", 1000), ("author_view", "作者观点", 6000), ("materials", "补充材料", 24000)):
            if field in body:
                overrides[field] = _text(body[field], label, maximum)
        schema, fingerprint, base_url = self._quick_schema("writer")
        inputs = {name: copy.deepcopy(spec["default"]) for name, spec in WRITER_INPUT["properties"].items() if name not in ("library_file", "article_library_file")}
        inputs.update(task_mode="write_article", topic=title, word_count=f"约{word_count}字")
        # These are the user's selected/edited directions, not local policy or
        # notes. Explicit materials/author_view fields alone populate those slots.
        directions = ["以下为用户选中的选题说明；其中事实与热度仍需核实。"]
        if brief:
            directions.append("写作方向：\n" + brief)
        if reason:
            directions.append("选题理由（待核实）：\n" + reason)
        inputs["extra_req"] = "\n\n".join(directions)
        inputs.update(overrides)
        selected = {"batch_id": batch_id, "topic_id": topic_id, "title": title, "brief": brief, "reason": reason, "source": batch.get("source"), "source_url": batch.get("source_url"), "source_job_id": batch.get("coze_job_id"), "original_topic": copy.deepcopy(original)}
        context = {"quick_version": 1, "role": "writer", "input_schema_revision": fingerprint, "verified_base_url": base_url, "selection": selected, "explicit_fields": list(overrides), "local_policy_attached": False, "personal_notes_attached": False}
        return self.run_coze("writer", {"inputs": inputs}, input_context=context)

    def import_coze_draft(self, job_id, *, _allow_pending=False):
        """Idempotent local import. Never calls Coze or a download URL."""
        with self.coze_draft_lock:
            job = self.get("jobs", job_id)
            result = job.get("result")
            recovering_local = bool(job.get("draft_import_pending") and job.get("remote_status") == "completed" and not job.get("remote_error") and (_allow_pending or job.get("status") == "error"))
            if job.get("type") != "coze-writer" or not (recovering_local or job.get("status") == "done") or (job.get("error") and not recovering_local) or not isinstance(result, dict) or result.get("role") != "writer":
                raise CozeError("只能导入已成功完成的 Coze 文案任务。", 409)
            existing = None
            if result.get("draft_id"):
                existing = self.get("drafts", result["draft_id"])
            if existing is None:
                existing = next((draft for draft in self.all("drafts") if (draft.get("context") or {}).get("coze_job_id") == job_id), None)
            if existing is not None:
                return self._link_coze_draft(job, existing)
            output = result.get("output")
            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except (ValueError, RecursionError):
                    raise CozeError("Coze 原始返回没有已核实的 Markdown 字段，已保留原始结果；未抓取外部下载链接。") from None
            if not isinstance(output, dict) or output.get("task_mode", "write_article") != "write_article":
                raise CozeError("Coze 返回不是文章写作结果，已保留原始返回，不会当作草稿导入。")
            content = output.get("markdown") if isinstance(output.get("markdown"), str) and output["markdown"].strip() else output.get("article")
            if not isinstance(content, str) or not content.strip():
                raise CozeError("Coze 返回中没有非空 article 或 markdown 正文，已保留原始结果；不会抓取下载链接来替代。")
            markdown, warnings = safe_draft_markdown(content)
            if not markdown.strip():
                raise CozeError("清理不安全 HTML 后没有可保存的 Markdown 正文，原始返回仍在任务中。")
            selected = (job.get("input_context") or {}).get("selection") or {}
            heading = re.search(r"^#\s+(.+?)\s*$", markdown, re.M)
            returned_title = output.get("title") if isinstance(output.get("title"), str) and output["title"].strip() else None
            title = (returned_title or (heading.group(1) if heading else selected.get("title") or "Coze 返回的公众号草稿"))[:500]
            note = "此稿来自已接入的 Coze 文案工作流，尚未执行本地事实与语言审核。来源、数据与措辞需人工确认后再发布。"
            sources = output.get("sources") or []
            if not isinstance(sources, list) or any(not isinstance(item, str) for item in sources):
                raise CozeError("Coze 返回的 sources 来源格式与已确认接口不一致，原始结果已保留。")
            references = [{"title": item, "source_text": item, "url": item if safe_external_url(item) else "", "verification": "not_locally_verified"} for item in sources]
            if sources:
                safe_sources = [re.sub(r"([\\`*_{}\[\]()#+.!<>])", r"\\\1", item).replace("\n", " ") for item in sources]
                markdown += "\n\n## Coze 返回的资料来源（尚未核实）\n\n" + "\n".join("- " + item for item in safe_sources)
            package = {"version": 1, "status": "needs_attention", "reason": "Coze 返回的发布配套内容，尚未经过本地事实与语言独立审核。", "alternative_titles": copy.deepcopy(output.get("alt_titles") or []), "summary": output.get("summary", ""), "cover_text": output.get("cover_copy", ""), "cover_prompt": output.get("cover_prompt", ""), "share_text": output.get("share_reco", ""), "review": {"method": "external_coze_output", "status": "needs_attention", "independent_review": False, "issues": [], "note": "仅保存外部工作流结果，没有运行本地正文或配套审核。"}}
            if not isinstance(package["alternative_titles"], list) or any(not isinstance(value, str) for value in package["alternative_titles"]) or any(not isinstance(package[field], str) for field in ("summary", "cover_text", "cover_prompt", "share_text")):
                raise CozeError("Coze 发布配套字段格式与已确认接口不一致，原始结果已保留。")
            external_notes = output.get("editor_notes") if isinstance(output.get("editor_notes"), str) else ""
            context = {"source": "coze_writer", "coze_sources": copy.deepcopy(sources), "coze_job_id": job_id, "remote_task_id": job.get("remote_task_id"), "source_url": result.get("base_url") or job.get("remote_base_url"), "locally_reviewed": False, "input_context": copy.deepcopy(job.get("input_context") or {}), "import_warnings": warnings}
            draft = self.save_draft({"title": title, "content": markdown, "model": "Coze 工作流（模型未提供）", "coze_job_id": job_id, "publication_package": package, "references": references, "generation_seconds": None, "editor_notes": note + ("\n\nCoze 编辑备注：\n" + external_notes if external_notes else "") + ("\n" + "\n".join(warnings) if warnings else ""), "review": {"status": "needs_attention", "scope": "not_locally_reviewed", "checks": {"fact": False, "language": False}, "issues": [], "rounds": [], "repair_rounds": 0, "note": note}}, context)
            return self._link_coze_draft(job, draft)

    def _link_coze_draft(self, job, draft):
        result = {**job["result"], "draft_id": draft["id"], "draft": draft}
        result.pop("draft_import_error", None)
        changes = {"result": result}
        if job.get("draft_import_pending") and job.get("status") == "error":
            changes.update(status="done", error=None, draft_import_pending=False, message="已从保存的远端返回恢复本地草稿，没有重复请求 Coze。")
        self._coze_job_patch(job["id"], **changes)
        return {"job_id": job["id"], "draft_id": draft["id"], "draft": draft}

    def _auto_import_coze_draft(self, job_id):
        job = self.get("jobs", job_id)
        if job.get("coze_role") != "writer" or (job.get("input_context") or {}).get("quick_version") != 1:
            return
        try:
            self.import_coze_draft(job_id, _allow_pending=True)
        except Exception as exc:
            job = self.get("jobs", job_id)
            message = str(exc) if isinstance(exc, CozeError) else "远端原始返回已保存，本地草稿暂未导入；可重试本地导入，不会重新运行 Coze。"
            self._coze_job_patch(job_id, result={**job["result"], "draft_import_error": message})
