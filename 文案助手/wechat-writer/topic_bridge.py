"""Import user-supplied topic recommendations without invoking a model.

Preview links are provenance only. Importing a result does not call Coze,
verify its claims or automatically submit a writing request.
"""
from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
import re
import urllib.parse
import uuid


TOPICS_URL = "https://code.coze.cn/p/7695223338066608166/preview?link_share=true"
WRITER_URL = "https://code.coze.cn/p/7695549955188195391/preview?link_share=true"
MAX_CONTENT = 200_000
MAX_TOPICS = 50
FIELD_LABELS = {"title": "标题", "topic": "选题", "name": "名称", "category": "选题类型",
                "source_date": "来源时间", "audience_pain": "读者痛点", "core": "核心问题",
                "angle": "切入角度", "reason": "理由", "why": "理由", "rationale": "理由",
                "source_url": "来源链接", "url": "链接", "sources": "来源资料", "evidence": "证据",
                "metrics": "指标", "reads": "阅读", "likes": "点赞", "favorites": "收藏"}


def _string(value):
    return value.strip() if isinstance(value, str) else ""


def _first(item, keys):
    return next((_string(item[key]) for key in keys if _string(item.get(key))), "")


def _title(text):
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    first = re.sub(r"^\s*#{1,6}\s*", "", first)
    first = re.sub(r"^\s*(?:[-*]\s+|(?:\d+|[一二三四五六七八九十]+)[.)）、．]\s*|[（(](?:\d+|[一二三四五六七八九十]+)[)）]\s*)", "", first)
    return first.strip(" *\t")


def _urls(text):
    result = []
    for found in re.findall(r"https?://[^\s<>\[\]\"'，。；、！]+", text):
        url = found.rstrip("。，；：、.,;:")
        for closing, opening in ((")", "("), ("）", "（")):
            while url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
        try:
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
                    or re.search(r"[\x00-\x20\x7f\\]", url)):
                continue
            parsed.port
        except ValueError:
            continue
        if url not in result:
            result.append(url)
        if len(result) == 20:
            break
    return result


def _texts(value):
    """Read raw string fields, avoiding JSON-escaped newlines inside URLs."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item)


def _json_loads(text):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    def invalid_constant(value):
        raise ValueError("non-JSON numeric constant")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, RecursionError):
        raise ValueError("JSON 格式不完整或含重复字段、非标准数值，请复制完整且有效的结果；不会猜测修复。") from None


def _unwrap_fence(content):
    normalized = content.strip().lstrip("\ufeff").strip()
    match = re.fullmatch(r"(`{3,}|~{3,})(json|markdown|md|text)?[^\S\n]*\n?(.*?)\n?\1", normalized, re.S | re.I)
    if match:
        return match.group(3).strip(), (match.group(2) or "").lower() == "json"
    # An explicitly labelled but incomplete JSON fence is not plain topic text.
    if re.match(r"^(?:`{3,}|~{3,})json(?:\s|$)", normalized, re.I):
        raise ValueError("JSON 代码块不完整，请复制含开始和结束标记的完整结果。")
    return normalized, False


def _render_value(value, indent=0):
    """Human-readable complete fields; raw_item separately retains exact types."""
    prefix = "  " * indent
    if isinstance(value, dict):
        if not value:
            return [prefix + "{}（空对象）"]
        lines = []
        for key, item in value.items():
            label = FIELD_LABELS.get(key, key)
            if isinstance(item, (dict, list)):
                lines.append(prefix + label + "：")
                lines.extend(_render_value(item, indent + 1))
            else:
                rendered = _render_value(item, 0)
                lines.append(prefix + label + "：" + rendered[0])
                lines.extend(prefix + "  " + line for line in rendered[1:])
        return lines
    if isinstance(value, list):
        if not value:
            return [prefix + "[]（空数组）"]
        lines = []
        for item in value:
            rendered = _render_value(item, indent + 1)
            lines.append(prefix + "- " + rendered[0][len(prefix) + 2:])
            lines.extend(rendered[1:])
        return lines
    if value is None:
        return [prefix + "未提供（null）"]
    if isinstance(value, str):
        # split('\n') keeps a trailing empty line and all field text.
        return [prefix + line for line in value.split("\n")]
    return [prefix + json.dumps(value, ensure_ascii=False, allow_nan=False)]


def _topic(item):
    if isinstance(item, str):
        brief = item.strip()
        title, reason = _title(brief), ""
    elif isinstance(item, dict):
        title = _first(item, ("title", "topic", "name", "标题", "选题", "选题标题", "选题名称"))
        reason = _first(item, ("reason", "why", "rationale", "推荐理由", "理由", "选题理由"))
        # Preserve every supplied field in the selected writing input. Unknown
        # fields are not interpreted as metrics or transformed into facts.
        brief = "\n".join(_render_value(item))
        if not title:
            title = _title(_first(item, ("brief", "content", "text", "description", "内容", "描述")))
    else:
        raise ValueError("每个选题需要是文字或包含标题的对象。")
    if not title or not brief:
        raise ValueError("有选题缺少标题或内容，请补充后重新导入。")
    if len(title) > 500:
        raise ValueError("未能识别简短选题标题。请给每个选题增加一行标题，再保留原文说明。")
    return {"id": uuid.uuid4().hex, "title": title, "brief": brief,
            "reason": reason, "source_urls": _urls("\n".join(_texts(item))), "raw_item": copy.deepcopy(item)}


def _split_text(content):
    lines = content.splitlines()
    visible, fence = [], None
    for index, line in enumerate(lines):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker.group(1)[0]
            elif marker.group(1)[0] == fence:
                fence = None
            continue
        if fence is None:
            visible.append((index, line))
    # Prefer explicit topic headings, otherwise the shallowest Markdown level.
    headings = [(i, len(m.group(1))) for i, line in visible
                if (m := re.match(r"^\s*(#{1,6})\s+\S", line))]
    explicit = [i for i, line in visible if re.match(r"^\s*(?:#{1,6}\s+)?(?:\*\*)?(?:选题\s*[一二三四五六七八九十\d]+|Topic\s*\d+)(?:[：:.、\s*]|$)", line, re.I)]
    numbered = [(i, len(m.group(1))) for i, line in visible
                if (m := re.match(r"^(\s*)(?:(?:\d+|[一二三四五六七八九十]+)[.)）、．]|[（(](?:\d+|[一二三四五六七八九十]+)[)）])\s*\S", line))]
    number_starts = [i for i, depth in numbered if depth == min(d for _, d in numbered)] if numbered else []
    heading_starts = []
    if headings:
        counts = {level: sum(l == level for _, l in headings) for _, level in headings}
        eligible = [level for level, count in counts.items() if count > 1]
        if eligible:
            heading_starts = [i for i, level in headings if level == min(eligible)]
    if len(explicit) > 1:
        starts = explicit
    elif len(number_starts) > 1 and (not heading_starts or number_starts[0] < heading_starts[0]):
        starts = number_starts
    elif heading_starts:
        starts = heading_starts
    elif headings:
        starts = [headings[0][0]]
    elif number_starts:
        starts = number_starts
    else:
        starts = [0]
    # A leading report heading/preamble remains in raw; selectable cards retain
    # their own original sections. Nothing is sent to a model during parsing.
    return ["\n".join(lines[start:end]).strip()
            for start, end in zip(starts, starts[1:] + [len(lines)])]


def _items(value, depth=0):
    if depth > 5:
        raise ValueError("选题 JSON 嵌套过深，请只复制选题结果部分。")
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped, json_fence = _unwrap_fence(value)
        if json_fence or stripped.startswith(("[", "{", '"')):
            return _items(_json_loads(stripped), depth + 1)
        return _split_text(stripped)
    if isinstance(value, dict):
        # Explicit result collections win over a report-level title/name, while
        # generic data/output fields inside a titled candidate remain evidence.
        for key in ("topics", "topic_list", "recommendations", "candidates", "选题列表", "推荐选题", "selection"):
            if key in value and isinstance(value[key], (list, dict, str)):
                return _items(value[key], depth + 1)
        if _first(value, ("title", "topic", "name", "标题", "选题标题", "选题名称")) or (isinstance(value.get("选题"), str) and len(value) > 1):
            return [value]
        for key in ("选题", "output", "result", "data"):
            if key in value and isinstance(value[key], (list, dict, str)):
                return _items(value[key], depth + 1)
        return [value]
    raise ValueError("无法识别选题结果，请粘贴文字、选题数组或包含 topics 的 JSON。")


def _parse_topics(content, max_bytes=None):
    if not isinstance(content, str) or not content.strip():
        raise ValueError("请粘贴 Coze 实际返回的选题内容。")
    if max_bytes is None and len(content) > MAX_CONTENT:
        raise ValueError("本次选题内容超过 200,000 字符，请分批导入。")
    if max_bytes is not None:
        try:
            encoded_size = len(content.encode("utf-8"))
        except UnicodeError:
            raise ValueError("保存的 Coze 任务输出包含无效 Unicode，不能导入。") from None
        if encoded_size > max_bytes:
            raise ValueError("Coze API 选题输出超过 4 MiB，未截断原文，请缩小工作流输出后重试。")
    items = _items(content)
    if not items or len(items) > MAX_TOPICS:
        raise ValueError("每批支持 1—50 个选题，请检查分段或分批导入。")
    return [_topic(item) for item in items]


def parse_topics(content):
    """Manual paste parser; the larger API budget is server-provenance-only."""
    return _parse_topics(content)


def _folded_text(text, label):
    # A delimiter longer than any supplied backtick run keeps raw HTML/Markdown
    # inert without dropping or altering a byte of the preserved source string.
    fence = "`" * max(3, 1 + max((len(match.group()) for match in re.finditer(r"`+", text)), default=0))
    return ["<details>", "<summary>" + label + "</summary>", "", fence + "text", text, fence, "", "</details>", ""]


def batch_markdown(batch):
    api_source = batch.get("source") == "coze_api"
    lines = ["# Coze 选题结果（" + ("API 返回导入" if api_source else "手动导入") + "）", "",
             "导入时间：" + batch.get("created_at", ""),
             "来源：" + (batch.get("source_url") or "该任务未记录部署地址"), ""]
    if api_source:
        lines.extend(["服务器任务 ID：" + str(batch.get("coze_job_id") or batch.get("job_id") or ""),
                      "任务完成时间：" + str(batch.get("coze_job_finished_at") or "未记录"),
                      "内容来自本地已成功完成的 Coze 选题调用，未采用客户端另传的文本。", ""])
    lines.extend(["选题中的主张与热度未经本平台核实；选择后仍需整理事实。", ""])
    for topic in batch.get("topics", []):
        lines.extend(["## " + topic["title"], ""])
        if len(topic["brief"]) > 6000:
            lines.extend(_folded_text(topic["brief"], "展开完整选题字段（内容较长，未截断）"))
        else:
            lines.extend([topic["brief"], ""])
    lines.extend(["---", "## 导入原文", ""])
    lines.extend(_folded_text(batch.get("raw", ""), "展开完整导入原文"))
    return "\n".join(lines)


class TopicStoreMixin:
    def init_topics(self):
        (self.root / "topics").mkdir(exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS topic_batches(id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    def topic_batches(self):
        with self.connect() as db:
            return [json.loads(row["data"]) for row in db.execute("SELECT data FROM topic_batches ORDER BY rowid DESC")]

    def import_topics(self, body):
        from app import atomic_write, dump
        if not isinstance(body, dict):
            raise ValueError("导入请求需要是对象。")
        source = body.get("source", "coze")
        lineage = {}
        if source == "coze_api":
            job_id = body.get("coze_job_id")
            if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 100:
                raise ValueError("请提供已成功完成的 Coze 选题任务 ID。")
            job = self.get("jobs", job_id)
            result = job.get("result")
            if (job.get("type") != "coze-topics" or job.get("status") != "done" or job.get("error")
                    or not isinstance(result, dict) or result.get("role") != "topics" or "output" not in result):
                raise ValueError("只能导入已成功完成的 Coze 选题任务；文案任务、未完成或失败的任务不能作为选题来源。")
            output = result["output"]
            if not isinstance(output, (str, dict, list)):
                raise ValueError("这个 Coze 选题任务没有可解析的文字或 JSON 选题输出。")
            try:
                # Compact JSON avoids inflating an already size-checked API
                # response merely by adding export indentation. Values stay whole.
                content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            except (ValueError, TypeError, RecursionError):
                raise ValueError("保存的 Coze 任务输出不是有效 JSON，不能导入。") from None
            base_url = result.get("base_url") or ""
            if base_url:
                from coze_bridge import validate_base_url
                base_url = validate_base_url(base_url)
            lineage = {"coze_job_id": job_id, "job_id": job_id, "base_url": base_url,
                       "coze_job_finished_at": job.get("finished_at") or result.get("finished_at") or ""}
            batch_source, source_url = "coze_api", base_url
        elif source == "coze":
            content = body.get("content")
            batch_source, source_url = "coze_manual", TOPICS_URL
        else:
            raise ValueError("导入来源只能是手动 Coze 结果或本地已完成的 Coze API 任务。")
        if source == "coze_api":
            from coze_bridge import MAX_OUTPUT
            topics = _parse_topics(content, max_bytes=MAX_OUTPUT)
        else:
            topics = parse_topics(content)
        batch = {"id": uuid.uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "source": batch_source, "source_url": source_url, "writer_url": WRITER_URL,
                 "raw": content, "topics": topics, **lineage}
        json_path = self.root / "topics" / (batch["id"] + ".json")
        md_path = json_path.with_suffix(".md")
        try:
            with self.connect() as db:
                if source == "coze_api":
                    # Serialize read+insert across even concurrent requests so a
                    # retry of the same job returns the existing selection cards.
                    db.execute("BEGIN IMMEDIATE")
                    for row in db.execute("SELECT data FROM topic_batches"):
                        previous = json.loads(row["data"])
                        if previous.get("source") == "coze_api" and previous.get("coze_job_id") == lineage["coze_job_id"]:
                            return previous
                db.execute("INSERT INTO topic_batches(id,data) VALUES(?,?)", (batch["id"], dump(batch)))
                atomic_write(json_path, json.dumps(batch, ensure_ascii=False, indent=2) + "\n")
                atomic_write(md_path, batch_markdown(batch))
        except Exception:
            json_path.unlink(missing_ok=True)
            md_path.unlink(missing_ok=True)
            raise
        return batch
