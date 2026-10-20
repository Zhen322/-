"""Persistent, explicitly confirmed two-stage writing runs.

Evidence is captured before preparation and is never refreshed on continuation.
Revision limits survive failures and restarts; generated prose is retained even
when later review fails. This module never calls a model without a user action.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from datetime import datetime, timezone
import uuid


REPAIR_LIMIT = 2
ACTIVE_STATUSES = {"preparing", "generating"}
FINAL_STATUSES = {"awaiting_review", "needs_attention"}


class WritingError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def _draft_revision(draft):
    text = json.dumps({"title": draft.get("title", ""), "content": draft.get("content", "")}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic(path, text):
    descriptor, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _text(value, label, maximum, required=False):
    if not isinstance(value, str):
        raise WritingError(label + "必须是文本。")
    if required and not value.strip():
        raise WritingError("请填写" + label + "。")
    if len(value) > maximum:
        raise WritingError(f"{label}最多 {maximum:,} 个字符。")
    return value


def _ids(value, label, maximum):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise WritingError(label + "格式不正确。")
    values = list(dict.fromkeys(value))
    if len(values) > maximum:
        raise WritingError(f"本次最多选择 {maximum} 个{label}。")
    return values


def _seconds(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise WritingError("写作引擎返回了无效的实测耗时，未将它记作有效耗时。")
    return value


def _plain(value):
    return str(value or "").strip()


def _label(value):
    return re.sub(r"[\r\n\[\]<>]", " ", _plain(value))


def _quote(value):
    return "\n".join("> " + line for line in (_plain(value) or "未提供原句").splitlines())


def _items(value):
    if isinstance(value, list):
        return value
    return [value] if value else []


def _source_label(source):
    title = _label(source.get("title") or "来源")
    url = _plain(source.get("url"))
    if url.startswith(("https://", "http://")) and not any(char in url for char in "<>\r\n"):
        return f"[{title}](<{url}>)"
    return title


def _issue_lines(issues):
    lines = []
    for index, issue in enumerate(issues, 1):
        if not isinstance(issue, dict):
            lines.extend([f"{index}. {_plain(issue)}", ""])
            continue
        severity = {"critical": "重要问题", "warning": "需留意"}.get(issue.get("severity"), "待核对")
        location = issue.get("display_location") or {"title": "标题", "content": "正文"}.get(issue.get("location"), issue.get("location") or "未定位")
        lines.extend([f"### {index}. {_label(location)} · {severity}", ""])
        if issue.get("quote"):
            lines.extend(["原句：", "", _quote(issue["quote"]), ""])
        lines.extend(["理由：" + _plain(issue.get("reason") or "未提供具体理由，请人工检查。"), "", "建议：" + _plain(issue.get("suggestion") or "人工核对后决定是否修改。"), ""])
        if issue.get("source_ids"):
            lines.extend(["对应来源：" + "、".join(map(str, issue["source_ids"])), ""])
    return lines


def publication_markdown(package, draft=None):
    """Portable editorial copy; never contains prompts or personal notes."""
    if not isinstance(package, dict):
        return ""
    stale = package.get("stale", False)
    if draft:
        stale = package.get("content_revision") != _draft_revision(draft)
    status = {"generated": "已生成，等待人工核对", "skipped": "未生成", "needs_attention": "存在待处理问题"}.get(package.get("status"), "待人工核对")
    lines = ["## 发布配套内容 · 待人工确认", "", "状态：" + status, "", "配套内容只经过生成自查与本地规则检查，没有经过正文的独立事实、语言双审。", ""]
    if stale:
        lines.extend(["> 当前正文或标题已修改；以下配套内容依据旧版本生成，不代表与当前文章一致，请人工重新核对。", ""])
    if package.get("reason"):
        lines.extend([_plain(package["reason"]), ""])
    titles = package.get("alternative_titles") or []
    if titles:
        lines.extend(["### 备选标题", "", *[f"{index}. {_plain(title)}" for index, title in enumerate(titles, 1)], ""])
    for field, label in (("summary", "文章摘要"), ("cover_text", "封面文案"), ("cover_prompt", "封面提示词"), ("share_text", "转发文案")):
        if package.get(field):
            lines.extend(["### " + label, "", _plain(package[field]), ""])
    review = package.get("review") or {}
    if review.get("note"):
        lines.extend(["配套自查说明：" + _plain(review["note"]), ""])
    if review.get("issues"):
        lines.extend(["### 配套内容需要核对的问题", "", *_issue_lines(review["issues"])])
    return "\n".join(lines)


def writing_markdown(run, draft=None):
    """Render an editor-facing package from a snapshot, without live file reads."""
    prepared = run.get("prepared") or {}
    candidate = draft or run.get("partial_result") or {}
    title = _label(candidate.get("title") or "写作事实与提纲")
    status_labels = {"preparing": "正在整理事实与提纲", "awaiting_confirmation": "等待确认提纲，尚未生成正文", "generating": "正在写作与审核", "awaiting_review": "待人工发布前审核", "needs_attention": "存在待处理问题，不能视为审核通过", "error": "任务未完成，已保存可恢复资料"}
    review = copy.deepcopy((draft or {}).get("review") or run.get("review") or {})
    if draft and review:
        review["stale"] = review.get("content_revision") != _draft_revision(draft)
    lines = ["# " + title + " · 待审包", "", "状态：" + status_labels.get(run.get("status"), str(run.get("status", "未知"))), "", "本包用于人工检查事实、结构、措辞与问题位置；系统不会自动发布文章。", ""]
    policy = prepared.get("writing_policy") or ((run.get("capture") or {}).get("request") or {}).get("_writing_policy") or {}
    policy_revision = policy.get("revision") or (draft or {}).get("writing_policy_revision") or run.get("writing_policy_revision")
    mode = policy.get("mode") or (draft or {}).get("writing_mode") or run.get("writing_mode")
    if policy_revision or mode:
        lines.extend(["## 本次写作规范", "", "主 Prompt 版本：" + _plain(policy_revision or "未记录"), "", "执行模式：" + {"lite": "轻量模式", "deep": "深度模式"}.get(mode, _plain(mode or "未记录")), "", "本次继续写作沿用准备时捕获的规范版本；后续规范修改不会悄悄替换该版本。", ""])
    if review.get("stale"):
        lines.extend(["> 正文或标题在审核后已被修改：以下审核结论针对旧版本，当前文章尚未重新核查。", ""])
    if run.get("error"):
        lines.extend(["## 尚未完成的环节", "", str(run["error"]), ""])
    source_map = {source.get("source_id"): source for source in prepared.get("sources", []) if isinstance(source, dict)}
    lines.extend(["## 事实卡片与依据", "", "“原文支持”仅表示捕获的原句支持该说法，不等于已经独立核实。个人笔记与写法卡不作为事实证据。", ""])
    facts = prepared.get("fact_cards") or []
    if not facts:
        lines.extend(["尚无原文事实卡，请核对证据缺口。", ""])
    for index, fact in enumerate(facts, 1):
        if not isinstance(fact, dict):
            continue
        fact_id = _label(fact.get("id") or f"F{index}")
        source_id = fact.get("source_id", "")
        source = source_map.get(source_id, {})
        confidence = "原文支持，仍需人工核对" if fact.get("confidence") == "supported" else "依据不充分，待核实"
        lines.extend([f"### {fact_id} · {_label(fact.get('claim'))}", "", f"- 出处：{_label(source_id)} {_source_label(source) if source else '来源信息未提供'}", f"- 核实状态：{confidence}"])
        for key, label in (("entity", "涉及主体"), ("time", "时间"), ("event_status", "事件状态")):
            if fact.get(key):
                lines.append(f"- {label}：{_plain(fact[key])}")
        if fact.get("numbers"):
            lines.append("- 原文数值：" + "；".join(map(str, fact["numbers"])))
        lines.extend(["", "原文摘录：", "", _quote(fact.get("source_quote")), ""])
        if fact.get("caveat"):
            lines.extend(["待核实或适用边界：" + _plain(fact["caveat"]), ""])
    lines.extend(["## 本地写法案例", ""])
    cases = prepared.get("cases") or []
    if not cases:
        lines.extend(["未找到可用的相关写法案例。", ""])
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            continue
        lines.extend([f"### {index}. {_label(case.get('title') or '写法参考')}", "", "来源：" + _label(case.get("source_id")), "", "参考理由：" + _plain(case.get("reason") or "未提供"), "", "可借鉴写法：", "", _plain(case.get("method") or case.get("content") or "未提供"), ""])
    lines.extend(["## 写作角度", "", _plain(prepared.get("angle")) or "尚未生成", "", "## 已确认或待确认的提纲", ""])
    outline = prepared.get("outline") or []
    if not outline:
        lines.extend(["尚未生成提纲。", ""])
    for index, item in enumerate(outline, 1):
        if isinstance(item, dict):
            lines.extend([f"### {index}. {_label(item.get('heading') or '待定段落')}", "", _plain(item.get("point")), "", "事实依据：" + ("、".join(map(str, item.get("fact_ids") or [])) or "无事实卡；应按观点、问题或假设处理"), ""])
        else:
            lines.extend([f"{index}. {_plain(item)}", ""])
    lines.extend(["## 证据缺口", "", *["- " + _plain(item) for item in _items(prepared.get("gaps"))], ""])
    if not prepared.get("gaps"):
        lines.extend(["尚未记录缺口；这不表示已经独立核实全部事实。", ""])
    if run.get("editor_direction"):
        lines.extend(["## 用户确认时的编辑要求", "", run["editor_direction"], ""])
    if candidate.get("content"):
        lines.extend(["## 当前正文", "", str(candidate["content"]), ""])
    package = candidate.get("publication_package") or run.get("publication_package")
    if package:
        lines.extend([publication_markdown(package, draft), ""])
    lines.extend(["## 审核结论、问题位置与理由", ""])
    if not review:
        lines.extend(["尚未完成正文审核。", ""])
    else:
        lines.extend(["审核状态：" + ("存在待处理问题，需人工检查。" if review.get("status") == "needs_attention" else "等待人工发布前审核；不是自动发布许可。"), ""])
        if review.get("note"):
            lines.extend([_plain(review["note"]), ""])
        checks = review.get("checks") or {}
        if checks:
            lines.extend(["- 事实审查：" + ("已完成自动检查" if checks.get("fact") is True else "未完整完成或存在问题"), "- 语言审查：" + ("已完成自动检查" if checks.get("language") is True else "未完整完成或存在问题"), ""])
        issues = review.get("issues") or []
        for kind, label in (("fact", "事实问题"), ("language", "语言问题"), ("other", "其他待处理问题")):
            group = [issue for issue in issues if (issue.get("kind") if isinstance(issue, dict) else None) == kind] if kind != "other" else [issue for issue in issues if not isinstance(issue, dict) or issue.get("kind") not in ("fact", "language")]
            if group:
                lines.extend(["## " + label, "", *_issue_lines(group)])
        if not issues:
            lines.extend(["本次自动审查没有列出具体问题；发布前仍需人工核对来源与措辞。", ""])
    lines.extend(["## 修订记录", "", f"本轮写作管线累计使用 {run.get('repair_rounds', 0)} / {REPAIR_LIMIT} 轮自动修订；失败或重启不会重置限额。", ""])
    rounds = review.get("rounds") or []
    if not rounds:
        lines.extend(["尚无完整的自动修订记录。", ""])
    for entry in rounds:
        if not isinstance(entry, dict):
            continue
        lines.extend([f"### 第 {entry.get('round', '?')} 轮", ""])
        changes = entry.get("changes") or []
        if not changes:
            lines.extend(["该轮没有记录已执行的文字替换。", ""])
        for change in changes:
            if not isinstance(change, dict):
                continue
            location = {"title": "标题", "content": "正文"}.get(change.get("location"), change.get("location") or "未定位")
            lines.extend([f"**{_label(location)} · {'已应用' if change.get('applied') else '未应用'}**", "", "修改前：", "", _quote(change.get("quote")), "", "修改后：", "", _quote(change.get("replacement")), "", "修改理由：" + _plain(change.get("reason") or "未提供"), ""])
        if entry.get("issues"):
            lines.extend(["该轮记录的问题：", "", *_issue_lines(entry["issues"])])
    if run.get("repair_log"):
        lines.extend(["自动修订启动记录：", "", *[f"- 第 {entry.get('round', '?')} 轮：{_plain(entry.get('message'))}（{_plain(entry.get('started_at'))}）" for entry in run["repair_log"] if isinstance(entry, dict)], ""])
    lines.extend(["## 资料来源与使用说明", ""])
    for ref in prepared.get("references") or []:
        if isinstance(ref, dict):
            kind = {"snippet": "仅搜索摘要", "full": "原文", "writing_card": "写法卡"}.get(ref.get("content_kind"), "资料")
            lines.append(f"- {_label(ref.get('source_id'))} {_source_label(ref)}（{kind}{'，已节选' if ref.get('excerpted') else ''}）")
    lines.extend(["", *["- " + _plain(item) for item in _items(prepared.get("usage"))], ""])
    suggestions = (draft or {}).get("image_suggestions") or {}
    if suggestions:
        lines.extend(["## 配图建议", ""])
        if suggestions.get("stale"):
            lines.extend(["> 配图依据的正文已修改，请重新核对相关性与位置。", ""])
        for index, item in enumerate(suggestions.get("items") or [], 1):
            lines.extend([f"### {index}. {_label(item.get('description') or '候选配图')}", "", "位置：" + _plain(item.get("placement")), "", "图注：" + _plain(item.get("caption")), "", "出处：" + _source_label({"title": item.get("source_title"), "url": item.get("source_url")}), "", "图片：" + _source_label({"title": "预览原图", "url": item.get("image_url")}), "", "使用说明：" + _plain(item.get("license_note")), ""])
        lines.extend(["- " + _plain(item) for item in suggestions.get("warnings") or []])
    if run.get("images_error"):
        lines.extend(["## 配图尚未完成", "", run["images_error"], ""])
    return "\n".join(lines)


class WritingStoreMixin:
    def init_writing(self):
        self.writing_lock = threading.RLock()
        self.writing_directory = self.root / "writing"
        self.writing_directory.mkdir(exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS writing_runs(id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            interrupted = [json.loads(row["data"]) for row in db.execute("SELECT data FROM writing_runs") if json.loads(row["data"]).get("status") in ACTIVE_STATUSES]
        for run in interrupted:
            run["stage"] = "interrupted"
            run["message"] = "服务重启中断了任务，已保留捕获资料与已生成内容。"
            run["error"] = "此任务被服务重启中断。自动修订次数没有重置。"
            if run.get("partial_result") and not run.get("draft_id"):
                try:
                    self._writing_save_candidate(run, run["partial_result"], interrupted=True)
                except Exception:
                    run["error"] += " 正文仍保存在待审包中，请导出检查。"
            run["status"] = "needs_attention" if run.get("draft_id") or run.get("partial_result") else "error"
            self._persist_writing(run)

    def _raw_writing(self, writing_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM writing_runs WHERE id=?", (writing_id,)).fetchone()
        if not row:
            raise WritingError("写作任务不存在。", 404)
        return json.loads(row["data"])

    def _decorate_writing(self, run):
        if run.get("draft_id"):
            try:
                draft = self.get("drafts", run["draft_id"])
                if draft.get("publication_package"):
                    run["publication_package"] = copy.deepcopy(draft["publication_package"])
                if draft.get("review"):
                    run["review"] = copy.deepcopy(draft["review"])
                elif run.get("review"):
                    run["review"]["stale"] = run["review"].get("content_revision") != _draft_revision(draft)
            except Exception:
                run["message"] = "已关联的草稿暂不可读取，写作证据仍保留在本记录中。"
        return run

    def get_writing(self, writing_id):
        return self._decorate_writing(self._raw_writing(writing_id))

    def writing_runs(self):
        with self.connect() as db:
            runs = [json.loads(row["data"]) for row in db.execute("SELECT data FROM writing_runs ORDER BY rowid DESC")]
        return [self._decorate_writing(run) for run in runs]

    def _persist_writing(self, run):
        run["updated_at"] = _now()
        run["revision"] = uuid.uuid4().hex
        draft = None
        if run.get("draft_id"):
            try:
                draft = self.get("drafts", run["draft_id"])
            except Exception:
                pass
        _atomic(self.writing_directory / (run["id"] + ".json"), _json(run))
        _atomic(self.writing_directory / (run["id"] + ".md"), writing_markdown(run, draft))
        with self.connect() as db:
            db.execute("INSERT INTO writing_runs(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (run["id"], _json(run)))
        return run

    def _change_writing(self, writing_id, **changes):
        with self.writing_lock:
            run = self._raw_writing(writing_id)
            run.update(changes)
            return self._persist_writing(run)

    def _writing_capture(self, body):
        request = {"brief": _text(body.get("brief"), "写作要求", 6000, True), "audience": _text(body.get("audience", "整灯厂和上游供应商从业者"), "读者", 1000), "tone": _text(body.get("tone", "专业、清晰、有依据"), "语气", 1000), "length": _text(str(body.get("length", "约1500字")), "篇幅", 300)}
        for field in ("use_web", "find_images"):
            if not isinstance(body.get(field, False), bool):
                raise WritingError("联网与配图选项须为 true 或 false。")
            request[field] = body.get(field, False)
        count = body.get("image_count", 6)
        if isinstance(count, bool) or not isinstance(count, int) or not 5 <= count <= 8:
            raise WritingError("配图数量须为 5 至 8 的整数。")
        request["image_count"] = count
        article_ids = _ids(body.get("article_ids", []), "参考文章", 5 if request["use_web"] else 8)
        card_ids = _ids(body.get("card_ids", []), "写法卡片", 12)
        articles = [self.get("articles", item_id) for item_id in article_ids]
        approved = [card for card in self.all("cards") if card.get("approved")]
        cards = [self.get("cards", item_id) for item_id in card_ids] if card_ids else approved[:12]
        if any(card.get("approved") is not True for card in cards):
            raise WritingError("所选写法卡片尚未审核，请先启用再用于准备写作。")
        notes = self.read_notes()
        if len(notes["text"]) > 24000:
            raise WritingError("当前笔记超过完整使用的 24,000 字符预算，请选择聚焦本次主题的工作笔记；本次没有截断笔记。")
        query = _text(body.get("search_query", ""), "联网搜索词", 600).strip()
        request["search_query"] = query or re.sub(r"\s+", " ", request["brief"]).strip()[:240]
        request["article_ids"] = article_ids
        request["card_ids"] = [card["id"] for card in cards]
        from writing_policy import snapshot_policy
        request["_writing_policy"] = snapshot_policy(self.root, mode=body.get("writing_mode"))
        settings = self.settings()
        capture = {"request": request, "articles": articles, "cards": cards, "notes": notes, "profile": settings.get("profile", ""), "captured_at": _now(), "omitted_approved_cards": max(0, len(approved) - len(cards)) if not card_ids else 0}
        return copy.deepcopy(capture)

    def _safe_writing_error(self, error):
        message = str(error) or "写作任务未能完成，请检查配置与已保存的待审包。"
        try:
            settings = self.settings()
            for key in ("api_key", "tavily_api_key"):
                if settings.get(key):
                    message = message.replace(settings[key], "[密钥已隐藏]")
        except Exception:
            pass
        return message[:2000]

    def _writing_progress(self, writing_id, phase, repair_start=0, repair_budget=0):
        def progress(update):
            if not isinstance(update, dict):
                raise WritingError("写作引擎进度格式不正确。")
            with self.writing_lock:
                run = self._raw_writing(writing_id)
                if run.get("status") not in ACTIVE_STATUSES:
                    raise WritingError("此写作任务已暂停或结束，不能继续后台改写。", 409)
                if "stage" in update:
                    run["stage"] = _text(update["stage"], "写作阶段", 100)
                if "message" in update:
                    run["message"] = _text(update["message"], "写作进度", 1000)
                if "repair_rounds" in update:
                    local_rounds = update["repair_rounds"]
                    if phase != "continue" or isinstance(local_rounds, bool) or not isinstance(local_rounds, int) or not 0 <= local_rounds <= repair_budget:
                        raise WritingError("自动修订已达到本次写作的两轮上限，需人工处理。")
                    total_rounds = repair_start + local_rounds
                    if total_rounds > REPAIR_LIMIT:
                        raise WritingError("自动修订已达到两轮上限，需人工处理。")
                    if total_rounds > run.get("repair_rounds", 0):
                        run["repair_rounds"] = total_rounds
                        run.setdefault("repair_log", []).append({"round": total_rounds, "started_at": _now(), "message": str(update.get("message", "开始自动修订"))})
                candidate = update.get("draft_candidate") or update.get("partial_result")
                if candidate is not None:
                    if not isinstance(candidate, dict) or not isinstance(candidate.get("content"), str) or not candidate["content"].strip():
                        raise WritingError("生成正文的阶段记录不完整。")
                    if len(candidate["content"]) > 300000:
                        raise WritingError("生成正文超过可保存长度，请减少目标篇幅。")
                    run["partial_result"] = copy.deepcopy(candidate)
                self._persist_writing(run)
        return progress

    def prepare_writing(self, body):
        capture = self._writing_capture(body)
        writing_id = uuid.uuid4().hex
        moment = _now()
        run = {"id": writing_id, "revision": uuid.uuid4().hex, "status": "preparing", "stage": "capture", "message": "资料已捕获，正在整理事实与提纲；本步骤不会生成正文。", "created_at": moment, "updated_at": moment, "capture": capture, "prepared": None, "draft_id": None, "editor_direction": "", "review": None, "error": None, "repair_rounds": 0, "repair_log": [], "generation_seconds": None, "pipeline_model_seconds": None, "model_cost": None, "cost_currency": None}
        with self.writing_lock:
            self._persist_writing(run)

        def work():
            try:
                import writing_engine
                settings = self.settings()
                settings["profile"] = capture["profile"]
                request = capture["request"]
                if request["use_web"]:
                    import engine
                    self._change_writing(writing_id, stage="research", message="正在根据主题关键词补充公开资料，不会发送个人笔记全文。")
                    found = engine.search_articles(request["search_query"], "web", 3, settings)
                    capture["articles"].extend(self.article(entry, source="web") for entry in found[:3])
                    request["article_ids"] = [item["id"] for item in capture["articles"]]
                    self._change_writing(writing_id, capture=copy.deepcopy(capture))
                prepared = writing_engine.prepare_writing(request, capture["articles"], capture["cards"], capture["notes"], settings, progress=self._writing_progress(writing_id, "prepare"))
                if not isinstance(prepared, dict) or prepared.get("version") != 1:
                    raise WritingError("写作引擎未返回完整的事实与提纲准备包。")
                # Verify serializability before declaring the manual checkpoint ready.
                _json(prepared)
                prepared = copy.deepcopy(prepared)
                # The captured disk snapshot is authoritative even if a model
                # or adapter omits this bookkeeping field in its response.
                prepared["writing_policy"] = copy.deepcopy(request["_writing_policy"])
                if isinstance(prepared.get("request"), dict):
                    prepared["request"].pop("_writing_policy", None)
                ready = self._change_writing(writing_id, prepared=prepared, preparation_model_seconds=_seconds(prepared.get("preparation_model_seconds")), status="awaiting_confirmation", stage="awaiting_confirmation", message="事实与提纲已准备好。请检查后手动确认，系统此时不会继续生成正文。", error=None)
                return {"writing_id": writing_id, "run": self._decorate_writing(ready)}
            except Exception as exc:
                self._change_writing(writing_id, status="error", stage="error", error=self._safe_writing_error(exc), message="准备未完成，已保留捕获资料；没有生成正文。")
                raise

        try:
            job = self.job("writing-prepare", work, writing_id=writing_id)
        except Exception as exc:
            self._change_writing(writing_id, status="error", stage="error", error=self._safe_writing_error(exc), message="准备任务未能排入队列。")
            raise
        return {**job, "writing_id": writing_id}

    def _writing_draft_context(self, run):
        capture = run.get("capture") or {}
        notes = capture.get("notes") or {}
        prepared = run.get("prepared") or {}
        return {"writing_id": run["id"], "notes_path": notes.get("path", ""), "notes_revision": notes.get("revision", prepared.get("notes_revision", "")), "notes_characters": len(notes.get("text", "")), "captured_at": capture.get("captured_at", run["created_at"]), "article_ids": [article["id"] for article in capture.get("articles", [])], "card_ids": [card["id"] for card in capture.get("cards", [])], "omitted_approved_cards": capture.get("omitted_approved_cards", 0), "search_query": (capture.get("request") or {}).get("search_query", "") if (capture.get("request") or {}).get("use_web") else "", "prepared_version": prepared.get("version"), "model_usage": prepared.get("usage", [])}

    @staticmethod
    def _attention_review(message, repair_rounds):
        return {"status": "needs_attention", "passed": False, "issues": [{"location": "未完成审核的正文", "reason": message, "suggestion": "根据已保存事实、提纲与原始来源人工检查后再发布。"}], "repair_rounds": repair_rounds}

    def _writing_save_candidate(self, run, result, interrupted=False):
        candidate = copy.deepcopy(result)
        if not isinstance(candidate, dict) or not isinstance(candidate.get("content"), str) or not candidate["content"].strip():
            raise WritingError("没有可保存的完整正文，已保留事实与提纲。")
        candidate["title"] = candidate.get("title") or "待人工审阅的公众号草稿"
        if interrupted or not isinstance(candidate.get("review"), dict):
            attention = self._attention_review(run.get("error") or "自动审核没有完整完成，当前正文不能视为已通过。", run.get("repair_rounds", 0))
            previous = candidate.get("review") if isinstance(candidate.get("review"), dict) else {}
            candidate["review"] = {**previous, **attention, "issues": list(previous.get("issues") or []) + attention["issues"], "checks": {"fact": False, "language": False}}
        candidate["review"]["repair_rounds"] = run.get("repair_rounds", 0)
        candidate["writing_id"] = run["id"]
        policy = (run.get("prepared") or {}).get("writing_policy") or ((run.get("capture") or {}).get("request") or {}).get("_writing_policy") or {}
        if policy:
            candidate["writing_policy_revision"] = policy.get("revision", "")
            candidate["writing_mode"] = policy.get("mode", "lite")
        candidate["generation_seconds"] = _seconds(candidate.get("generation_seconds"))
        candidate["pipeline_model_seconds"] = _seconds(candidate.get("pipeline_model_seconds"))
        draft = self.save_draft(candidate, self._writing_draft_context(run))
        run["draft_id"] = draft["id"]
        run["review"] = copy.deepcopy(draft["review"])
        if draft.get("publication_package"):
            run["publication_package"] = copy.deepcopy(draft["publication_package"])
        if draft.get("writing_policy_revision"):
            run["writing_policy_revision"] = draft["writing_policy_revision"]
            run["writing_mode"] = draft.get("writing_mode")
        run["generation_seconds"] = draft.get("generation_seconds")
        run["pipeline_model_seconds"] = draft.get("pipeline_model_seconds")
        return draft

    @staticmethod
    def _review_needs_attention(review):
        status = str(review.get("status") or review.get("verdict") or "").lower()
        if status in ("needs_attention", "failed", "fail", "blocked", "error", "reject") or review.get("passed") is False:
            return True
        return not (status in ("passed", "pass", "awaiting_review", "ready", "ready_for_review", "approved") or review.get("passed") is True)

    def continue_writing(self, writing_id, body):
        direction = _text(body.get("editor_direction", ""), "确认后的编辑要求", 4000)
        with self.writing_lock:
            run = self._raw_writing(writing_id)
            if not isinstance(body.get("revision"), str) or body["revision"] != run["revision"]:
                raise WritingError("写作包已更新。请保留编辑要求，重新查看最新事实与提纲后再确认。", 409)
            if run["status"] in FINAL_STATUSES or run.get("draft_id") or run.get("partial_result"):
                raise WritingError("这轮写作已产生成稿，请在待审包中人工处理；不能重复确认来重置自动修订次数。", 409)
            if run["status"] not in ("awaiting_confirmation", "error") or not isinstance(run.get("prepared"), dict):
                raise WritingError("当前写作任务尚未到达可确认阶段，或已经在运行。", 409)
            repair_start = run.get("repair_rounds", 0)
            if isinstance(repair_start, bool) or not isinstance(repair_start, int) or not 0 <= repair_start <= REPAIR_LIMIT:
                raise WritingError("写作包中的修订计数异常，请人工检查。", 409)
            repair_budget = REPAIR_LIMIT - repair_start
            prepared = copy.deepcopy(run["prepared"])
            run.update(status="generating", stage="body", message="已确认捕获的事实与提纲，正在生成正文和标题。", editor_direction=direction, error=None)
            self._persist_writing(run)

        def work():
            draft = None
            try:
                import writing_engine
                result = writing_engine.complete_writing(prepared, self.settings(), editor_direction=direction, progress=self._writing_progress(writing_id, "continue", repair_start, repair_budget), repair_budget=repair_budget)
                if not isinstance(result, dict):
                    raise WritingError("写作引擎没有返回有效成稿。")
                with self.writing_lock:
                    latest = self._raw_writing(writing_id)
                    result_review = result.get("review") if isinstance(result.get("review"), dict) else {}
                    latest["partial_result"] = copy.deepcopy(result)
                    self._persist_writing(latest)
                    reported = result.get("repair_rounds", result_review.get("repair_rounds", 0))
                    if isinstance(reported, bool) or not isinstance(reported, int) or not 0 <= reported <= repair_budget:
                        raise WritingError("写作引擎返回的修订次数超出两轮上限，已生成正文留待人工处理。")
                    latest["repair_rounds"] = max(latest.get("repair_rounds", 0), repair_start + reported)
                    draft = self._writing_save_candidate(latest, result)
                    attention = self._review_needs_attention(draft["review"])
                    latest.update(status="needs_attention" if attention else "awaiting_review", stage="awaiting_review", message="正文已保存，仍有审核问题需要人工处理。" if attention else "正文及审核记录已保存，请进行发布前人工确认。", error=None)
                    self._persist_writing(latest)
                request = (latest.get("capture") or {}).get("request") or {}
                if request.get("find_images"):
                    reserved = False
                    try:
                        self.reserve_images(draft["id"])
                        reserved = True
                        images = self.find_images_result(draft, request.get("image_count", 6), self.settings())
                        draft = images["draft"]
                    except Exception as exc:
                        self._change_writing(writing_id, images_error=self._safe_writing_error(exc))
                    finally:
                        if reserved:
                            self.release_images(draft["id"])
                final_run = self.get_writing(writing_id)
                # Include image suggestions in the portable waiting-for-review package.
                with self.writing_lock:
                    self._persist_writing(self._raw_writing(writing_id))
                response = {"writing_id": writing_id, "run": self.get_writing(writing_id), "draft_id": draft["id"], "draft": self.get("drafts", draft["id"])}
                if final_run.get("images_error"):
                    response["images_error"] = final_run["images_error"]
                return response
            except Exception as exc:
                with self.writing_lock:
                    latest = self._raw_writing(writing_id)
                    latest["error"] = self._safe_writing_error(exc)
                    partial = getattr(exc, "partial_result", None) or latest.get("partial_result")
                    if partial and not latest.get("draft_id"):
                        latest["partial_result"] = copy.deepcopy(partial)
                        try:
                            draft = self._writing_save_candidate(latest, partial, interrupted=True)
                        except Exception:
                            latest["error"] += " 已有正文仍保存在待审包中。"
                    latest.update(status="needs_attention" if latest.get("draft_id") or latest.get("partial_result") else "error", stage="error", message="写作未完整通过审核，已保留事实、提纲及已有正文；请人工处理。")
                    self._persist_writing(latest)
                    if latest.get("draft_id"):
                        return {"writing_id": writing_id, "run": self._decorate_writing(latest), "draft_id": latest["draft_id"], "draft": self.get("drafts", latest["draft_id"]), "writing_error": latest["error"]}
                raise

        try:
            job = self.job("writing-continue", work, writing_id=writing_id)
        except Exception as exc:
            self._change_writing(writing_id, status="error", stage="error", error=self._safe_writing_error(exc), message="正文任务未能排入队列，事实与提纲仍已保留。")
            raise
        return {**job, "writing_id": writing_id}

    def mark_writing_review_stale(self, draft):
        writing_id = draft.get("writing_id")
        if not writing_id or not (draft.get("review") or draft.get("publication_package")):
            return
        with self.writing_lock:
            try:
                run = self._raw_writing(writing_id)
            except WritingError:
                return
            if draft.get("review"):
                run["review"] = copy.deepcopy(draft["review"])
            if draft.get("publication_package"):
                run["publication_package"] = copy.deepcopy(draft["publication_package"])
            if (draft.get("review") or {}).get("stale") or (draft.get("publication_package") or {}).get("stale"):
                run["message"] = "正文或标题已在生成后修改，旧审核与配套内容已标记过期，请人工重新核查。"
            self._persist_writing(run)

    def export_writing(self, writing_id, as_json=False):
        run = self.get_writing(writing_id)
        draft = self.get("drafts", run["draft_id"]) if run.get("draft_id") else None
        return _json({"run": run, "draft": draft}) if as_json else writing_markdown(run, draft)
