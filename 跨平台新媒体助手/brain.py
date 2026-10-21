# -*- coding: utf-8 -*-
"""Autonomous agent brain (tool-calling / ReAct-style).

The difference between this and the fixed pipeline in ``agent.py``:

* ``Agent`` exposes the *capabilities* (plan, research, fact cards, produce,
  audit, fix, approve, ...) but a fixed caller decides when they run.
* ``Brain`` is given a high-level goal and uses the model's own tool-calling
  to decide *which* capability to invoke next, passes the real result back as
  an observation, and loops until the goal is met (``finish``).

The brain never writes the deliverable text itself: every content-producing
step is a capability whose internal, evidence-first model does the work. All
observations are real results; on failure the brain gets the error and can
change parameters or take another route rather than the run silently dying.
"""
from __future__ import annotations

import json
import traceback

import llm
import search as search_mod
from platforms import (
    PLATFORMS, SPECS, WECHAT, ZHIHU, RED, X,
)


BRAIN_SYSTEM = """你是「跨平台新媒体助手」的自主调度大脑（Agent），服务汽车与车灯产业链。你的任务不是自己写正文，而是**通过调用工具**，自主把一个高层目标拆解、推进、闭环。

你可以调用的工具（能力）：
- get_state：查看当前工作区全部现状（项目/选题/素材/成品/发布/排期）。
- collect_materials：联网搜索并把结果保存为素材（证据）。
- search_web：仅联网核实事实（不保存）。
- generate_calendar：生成跨平台内容日历。
- research_topics：联网研究并产出选题卡。
- create_project：创建项目。
- build_fact_cards：把素材整理成事实卡。
- produce_content：为一个平台（wechat/zhihu/red/x）和语言（zh/en）产出成品。
- audit_content：事实+语言双审核。
- auto_fix_content：定点自动修复。
- approve_content：审定版本（绑定平台/账号/语言/版本）。
- mark_manual_required：把无法自动闭环的问题记录为人工处理。
- get_project / list_contents：查看项目与成品详情。
- finish：目标完成或无法继续时，提交交付报告并结束。

工作铁律（必须遵守）：
1. 证据先行：先有素材、事实卡，再产成品；正文里的每个数字都要有来源，工具没返回的事实绝不假设、绝不编造。
2. 每一步只依据工具返回的真实结果推进；一个工具失败就换参数或换工具。**每个动作只做一次，禁止反复 build_fact_cards、反复 audit 同一条内容**——单轮推进、不回头。
3. 完整覆盖四个平台：wechat、zhihu、red，以及 x 的中文和英文（x 必须 zh、en 各一份，分别组织、不是逐句直译）。
4. 每个成品只经历一次 produce → audit；有问题 auto_fix 一次；修复后仍残留且无法在不编造前提下消除的，用 mark_manual_required 记录，随后直接 approve_content 并 accept_residual=true 带保留审定，**不要反复重审**。
5. 已经存在的成果要复用，不要重做。
6. 每一步先在 content 里用一句话说明你当前的判断和下一步意图（中文，简短），然后再调用工具。
7. 为节省上下文：每个成品先 produce，紧接着 audit/fix/approve，完成一个再做下一个，不要先全部 produce 再回头。
8. 当目标全部闭环，或无法完成的部分都已如实说明，必须调用 finish 结束；不要在 finish 之外空转。

推荐的推进路径（不是硬性顺序，依据 get_state 的真实结果决定）：
get_state → （素材不足时）collect_materials → research_topics → create_project →
build_fact_cards → 逐个 produce_content（wechat/zhihu/red/x:zh/x:en）→
逐个 audit_content → 有问题 auto_fix_content → approve_content → finish。"""


def _j(value, limit=1200):
    """Compact JSON observation (truncates, never raises)."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(text) > limit:
        text = text[:limit] + "…(已截断)"
    return text


# ---------------------------------------------------------------
# Human-readable descriptions (natural language; no code/JSON)
# ---------------------------------------------------------------
PLATFORM_CN = {"wechat": "公众号", "zhihu": "知乎", "red": "小红书", "x": "X"}
LANG_CN = {"zh": "中文", "en": "英文"}


def human_action(tool, args):
    """Natural-language sentence for what the agent is about to do."""
    a = args or {}
    pl = PLATFORM_CN.get(a.get("platform"), "")
    lg = LANG_CN.get(a.get("language", "zh"), "中文")
    table = {
        "get_state": "正在查看当前工作区的状态",
        "collect_materials": f"正在联网搜集「{a.get('query', '')}」的资料",
        "search_web": f"正在联网核实「{a.get('query', '')}」",
        "generate_calendar": f"正在生成 {a.get('weeks', 2)} 周的内容日历",
        "research_topics": f"正在围绕「{a.get('theme', '')}」研究选题",
        "create_project": f"正在创建项目「{a.get('theme', '')}」",
        "build_fact_cards": "正在把素材整理成事实卡",
        "produce_content": f"正在制作{pl}{lg}成品",
        "audit_content": "正在进行事实与语言双审核",
        "auto_fix_content": "正在对问题做定点自动修复",
        "approve_content": "正在审定版本",
        "mark_manual_required": "正在记录需人工处理的问题",
        "list_contents": "正在查看项目的成品清单",
        "get_project": "正在查看项目详情",
        "finish": "正在整理交付报告",
    }
    return table.get(tool, f"正在执行 {tool}")


def human_icon(tool):
    """Icon key for a tool action (frontend maps it to an SVG icon)."""
    table = {
        "get_state": "list", "list_contents": "list", "get_project": "list",
        "collect_materials": "search", "search_web": "search",
        "research_topics": "search",
        "generate_calendar": "edit", "create_project": "edit",
        "produce_content": "edit", "auto_fix_content": "edit",
        "build_fact_cards": "doc",
        "audit_content": "shield",
        "approve_content": "check", "mark_manual_required": "check",
        "finish": "check",
    }
    return table.get(tool, "terminal")


def human_result(tool, obs):
    """Natural-language sentence summarizing the real outcome."""
    if isinstance(obs, dict) and obs.get("error"):
        return f"这一步没有成功：{obs['error']}，我会调整方式后再试。"
    if tool == "get_state":
        c = obs.get("counts", {})
        return (f"当前工作区有 {c.get('projects', 0)} 个项目、"
                f"{c.get('contents', 0)} 个成品、{c.get('materials', 0)} 份素材、"
                f"{c.get('topics', 0)} 个选题。")
    if tool == "collect_materials":
        return f"已保存 {obs.get('count', 0)} 份资料。"
    if tool == "search_web":
        return f"联网找到 {len(obs)} 条相关结果。"
    if tool == "generate_calendar":
        return f"已生成内容日历，共 {obs.get('count', 0)} 条安排。"
    if tool == "research_topics":
        titles = "；".join(t.get("title", "") for t in obs.get("topics", []))
        return f"形成 {obs.get('count', 0)} 个选题：{titles}"
    if tool == "create_project":
        return f"已创建项目「{obs.get('theme', '')}」。"
    if tool == "build_fact_cards":
        return f"已整理出 {obs.get('fact_cards', 0)} 张事实卡。"
    if tool == "produce_content":
        pl = PLATFORM_CN.get(obs.get("platform"), "")
        lg = LANG_CN.get(obs.get("language"), "中文")
        return (f"已完成{pl}{lg}成品「{obs.get('title', '')}」，"
                f"含 {obs.get('images', 0)} 张图。")
    if tool == "audit_content":
        f = len(obs.get("fact_issues", []))
        l = len(obs.get("language_issues", []))
        return f"审核发现 {f} 个事实问题、{l} 个语言问题。"
    if tool == "auto_fix_content":
        return (f"修复后剩余 {obs.get('fact_issues', 0)} 个事实问题、"
                f"{obs.get('language_issues', 0)} 个语言问题。")
    if tool == "approve_content":
        return f"已完成审定（{obs.get('status', '')}）。"
    if tool == "mark_manual_required":
        return "已记录为需人工处理。"
    if tool == "list_contents":
        return f"项目下共有 {len(obs)} 个成品。"
    if tool == "get_project":
        return f"项目「{obs.get('theme', '')}」，{len(obs.get('fact_cards', []))} 张事实卡。"
    return "该步骤已完成。"


class Brain:
    def __init__(self, agent):
        self.agent = agent
        self.store = agent.store
        self.tool_map = {}
        self.schemas = []
        self.trace = []
        self.total_tokens = 0
        self.report = None
        self.max_steps = 40
        self._register()

    # ---------------------------------------------------------------
    # trace
    # ---------------------------------------------------------------
    def _log(self, kind, text, extra=None):
        entry = {"t": kind, "text": text}
        if extra is not None:
            entry["extra"] = extra
        self.trace.append(entry)
        if self.on_progress:
            try:
                self.on_progress(self.trace)
            except Exception:
                pass

    # ---------------------------------------------------------------
    # tool registration
    # ---------------------------------------------------------------
    def _register(self):
        def tool(name, description, properties, required, fn):
            self.schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            })
            self.tool_map[name] = fn

        _STR = {"type": "string"}
        _INT = {"type": "integer"}
        _PLATFORM = {"type": "string", "enum": ["wechat", "zhihu", "red", "x"]}
        _LANG = {"type": "string", "enum": ["zh", "en"], "default": "zh"}

        tool("get_state",
             "查看工作区现状：各表计数、项目、选题、素材、成品（含每篇图片数/审核问题数/审定状态）、发布记录。",
             {}, [], self.t_state)
        tool("collect_materials",
             "联网搜索并把前若干条真实结果保存为素材（证据）。素材不足时优先调用。",
             {"query": _STR, "count": {"type": "integer", "default": 6}},
             ["query"], self.t_collect)
        tool("search_web",
             "仅联网核实事实（不保存），用于确认某个数字或说法。",
             {"query": _STR}, ["query"], self.t_search)
        tool("generate_calendar",
             "生成跨平台内容日历。",
             {"weeks": {"type": "integer", "default": 2},
              "goals": _STR,
              "platforms": {"type": "array",
                            "items": _PLATFORM}},
             ["goals"], self.t_calendar)
        tool("research_topics",
             "联网研究并产出若干张选题卡（含标题/角度/大纲/来源/工作量）。",
             {"theme": _STR, "count": {"type": "integer", "default": 4}},
             ["theme"], self.t_research)
        tool("create_project",
             "创建一个项目（一个主题下的四平台成品都挂在项目下）。",
             {"theme": _STR, "brief": _STR, "audience": _STR},
             ["theme", "brief"], self.t_create_project)
        tool("build_fact_cards",
             "把项目素材整理成事实卡（写正文前必须先完成）。",
             {"project_id": _STR}, ["project_id"], self.t_fact_cards)
        tool("produce_content",
             "为指定平台/语言产出成品（含真实配图）。x 需分别产出 zh 与 en。",
             {"project_id": _STR, "platform": _PLATFORM,
              "language": _LANG},
             ["project_id", "platform"], self.t_produce)
        tool("audit_content",
             "对成品做事实+语言双审核，返回两类问题数量与要点。",
             {"content_id": _STR}, ["content_id"], self.t_audit)
        tool("auto_fix_content",
             "对审核问题做定点自动修复并自动复审，返回剩余问题。",
             {"content_id": _STR}, ["content_id"], self.t_fix)
        tool("approve_content",
             "审定成品版本。accept_residual=true 表示已知悉残留问题并带保留审定。",
             {"content_id": _STR, "account": _STR,
              "accept_residual": {"type": "boolean", "default": False}},
             ["content_id", "account"], self.t_approve)
        tool("mark_manual_required",
             "把无法自动闭环的问题记录为需人工处理并写明原因。",
             {"content_id": _STR, "reason": _STR},
             ["content_id", "reason"], self.t_manual)
        tool("get_project",
             "查看项目详情（主题/说明/事实卡/成品ID）。不填 project_id 时默认查看当前项目。",
             {"project_id": _STR}, [], self.t_get_project)
        tool("list_contents",
             "列出项目下全部成品的紧凑信息。不填 project_id 时默认查看当前项目。",
             {"project_id": _STR}, [], self.t_list_contents)
        tool("finish",
             "提交最终交付报告并结束本次任务。",
             {"report": _STR,
              "status": {"type": "string",
                         "enum": ["done", "partial", "blocked"],
                         "default": "done"}},
             ["report"], self.t_finish)

    # ---------------------------------------------------------------
    # observations
    # ---------------------------------------------------------------
    def _compact_content(self, c):
        audits = c.get("audits") or {}
        return {
            "id": c.get("id"),
            "platform": c.get("platform"),
            "language": c.get("language"),
            "title": c.get("title"),
            "images": len(c.get("images") or []),
            "fact_issues": len((audits.get("fact") or {}).get("issues") or []),
            "language_issues": len((audits.get("language") or {}).get("issues") or []),
            "approved": bool(c.get("approval")),
            "needs_human": bool(c.get("needs_human")),
            "unresolved_count": len(c.get("unresolved") or []),
        }

    def t_state(self):
        contents = self.store.list("contents")
        projects = self.store.list("projects")
        return {
            "counts": {
                "plans": len(self.store.list("plans")),
                "topics": len(self.store.list("topics")),
                "materials": len(self.store.materials()),
                "projects": len(projects),
                "contents": len(contents),
                "publishes": len(self.store.list("publishes")),
            },
            "topics": [{"id": t.get("id"), "title": t.get("title")}
                       for t in self.store.list("topics")],
            "materials": [{"id": m.get("id"), "title": m.get("title")}
                          for m in self.store.materials()],
            "projects": [
                {"id": p.get("id"), "theme": p.get("theme"),
                 "status": p.get("status"),
                 "fact_cards": len(p.get("fact_cards") or []),
                 "contents": len(p.get("content_ids") or [])}
                for p in projects],
            "contents": [self._compact_content(c) for c in contents],
        }

    def t_collect(self, query, count=6):
        found = search_mod.web_search(
            query, max_results=count or 6, settings=self.store.settings())
        saved = []
        for item in found:
            m = self.store.add_material(
                "web", item.get("title", ""),
                item.get("snippet", ""),
                url=item.get("url", ""), source=item.get("source", "web"))
            saved.append({"id": m["id"], "title": m["title"], "url": m["url"]})
        return {"query": query, "saved": saved, "count": len(saved)}

    def t_search(self, query):
        found = search_mod.web_search(
            query, max_results=8, settings=self.store.settings())
        return [{"title": i.get("title"), "url": i.get("url"),
                 "snippet": (i.get("snippet") or "")[:160]} for i in found]

    def t_calendar(self, goals, weeks=2, platforms=None):
        plan = self.agent.plan_calendar(
            weeks=int(weeks or 2), goals=goals,
            platforms=platforms or list(PLATFORMS))
        entries = [{"week": e.get("week"), "platform": e.get("platform"),
                    "language": e.get("language"),
                    "content_type": e.get("content_type"),
                    "topic": e.get("topic")} for e in plan.get("entries", [])]
        return {"plan_id": plan["id"], "entries": entries, "count": len(entries)}

    def t_research(self, theme, count=4):
        res = self.agent.research_topics(theme, int(count or 4), True)
        return {"topics": [{"id": t.get("id"), "title": t.get("title"),
                            "effort": t.get("effort"),
                            "angle": t.get("angle")}
                           for t in res["topics"]],
                "count": len(res["topics"])}

    def t_create_project(self, theme, brief, audience=""):
        p = self.store.create_project(theme, brief, audience or "")
        return {"project_id": p["id"], "theme": p["theme"]}

    def t_fact_cards(self, project_id):
        res = self.agent.build_fact_cards(project_id)
        cards = res["fact_cards"]
        return {"project_id": project_id,
                "fact_cards": len(cards),
                "summary": [{"entity": c.get("entity", ""),
                             "numbers": c.get("numbers", ""),
                             "status": c.get("event_status", ""),
                             "uncertainty": c.get("uncertainty", "")}
                            for c in cards[:12]]}

    def t_produce(self, project_id, platform, language="zh"):
        if platform not in ("wechat", "zhihu", "red", "x"):
            return {"error": "platform 必须是 wechat/zhihu/red/x"}
        language = language if language in ("zh", "en") else "zh"
        c = self.agent.produce_target(project_id, platform, language)
        return self._compact_content(c)

    def t_audit(self, content_id):
        c = self.agent.audit(content_id)
        audits = c.get("audits") or {}
        return {
            "content_id": content_id,
            "fact_issues": [
                {"position": i.get("position", ""),
                 "original": (i.get("original") or "")[:60],
                 "reason": i.get("reason", "")}
                for i in (audits.get("fact") or {}).get("issues") or []],
            "language_issues": [
                {"position": i.get("position", ""),
                 "original": (i.get("original") or "")[:60],
                 "reason": i.get("reason", "")}
                for i in (audits.get("language") or {}).get("issues") or []],
        }

    def t_fix(self, content_id):
        c = self.agent.auto_fix(content_id)
        compact = self._compact_content(c)
        compact["unresolved_full"] = c.get("unresolved") or []
        return compact

    def t_approve(self, content_id, account, accept_residual=False):
        c = self.store.get("contents", content_id)
        audits = c.get("audits") or {}
        issues = ((audits.get("fact") or {}).get("issues") or []) + \
                 ((audits.get("language") or {}).get("issues") or [])
        if issues and not accept_residual:
            return {"blocked": True,
                    "reason": "仍有 %d 条审核问题，请先 auto_fix_content；"
                              "确需带保留审定时 accept_residual=true。"
                              % len(issues)}
        if accept_residual:
            c["force_approve"] = True
            self.store.save_content(c)
        c = self.agent.approve(content_id, account=account or self.store.settings().get("brand", ""))
        return {"approved": True, "content_id": content_id,
                "status": c.get("status"), "version": c.get("version")}

    def t_manual(self, content_id, reason):
        c = self.store.get("contents", content_id)
        c["needs_human"] = True
        c["manual_reason"] = reason
        self.store.save_content(c)
        return {"content_id": content_id, "needs_human": True, "reason": reason}

    def _resolve_project(self, project_id):
        """Return the named project if it exists, else the active project."""
        if project_id:
            try:
                return self.store.get("projects", project_id)
            except KeyError:
                pass
        return self._active_project()

    def t_get_project(self, project_id=""):
        p = self._resolve_project(project_id)
        if not p:
            return {"error": "当前没有项目，请先创建项目。"}
        return {"id": p["id"], "theme": p["theme"], "brief": p.get("brief"),
                "status": p.get("status"),
                "fact_cards": [{"entity": c.get("entity", ""),
                                "numbers": c.get("numbers", ""),
                                "uncertainty": c.get("uncertainty", "")}
                               for c in (p.get("fact_cards") or [])],
                "content_ids": p.get("content_ids", [])}

    def t_list_contents(self, project_id=""):
        p = self._resolve_project(project_id)
        if not p:
            return []
        return [self._compact_content(c)
                for c in self.store.contents_for(p["id"])]

    def t_finish(self, report, status="done"):
        self.report = {"report": report, "status": status,
                       "total_tokens": self.total_tokens}
        return self.report

    # ---------------------------------------------------------------
    # main agentic loop
    # ---------------------------------------------------------------
    def run(self, goal, on_progress=None):
        self.on_progress = on_progress
        self._log("goal", goal)
        messages = [
            {"role": "system", "content": BRAIN_SYSTEM},
            {"role": "user", "content": goal},
        ]
        terminal = False
        for step in range(self.max_steps):
            message, usage, model = llm.chat_tools(
                self.store.settings(), messages, self.schemas,
                max_tokens=1000, temperature=0.1,
                retries=2, timeout=90)
            self.total_tokens += int(usage.get("total_tokens") or 0)
            messages.append(message)

            thought = (message.get("content") or "").strip()
            tool_calls = message.get("tool_calls") or []
            if thought:
                self._log("thought", thought)

            if not tool_calls:
                # No tool call: gently steer once; if it keeps happening, stop.
                self._log("note", "模型未调用工具，提示其继续或结束。")
                messages.append({
                    "role": "system",
                    "content": "请继续调用工具推进；若目标已闭环，必须调用 finish 并给出交付报告。"})
                continue

            for tc in tool_calls:
                fn = (tc.get("function") or {})
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                self._log("action", name, args)
                observation, is_terminal = self._dispatch(name, args)
                self._log("observation", observation)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": name,
                    "content": _j(observation, limit=1300),
                })
                if is_terminal:
                    terminal = True
            if terminal:
                break
            # Deterministic completion: once every expected target is approved,
            # stop the loop and produce the report in a fresh, small call.
            if self._goal_complete():
                self._log("note", "检测到四平台（含 X 中英）成品均已审定，目标闭环。")
                self._final_report(goal)
                terminal = True
                break

        if not terminal:
            # Ran out of steps without finish: synthesize an honest close.
            self._log("finish", "达到步数上限，未能在限定步数内调用 finish；"
                                "请查看跟踪记录与工作区现状。",
                      {"status": "partial"})
            self.report = {"report": "达到步数上限，未完成闭环。",
                           "status": "partial",
                           "total_tokens": self.total_tokens}
        return {
            "trace": self.trace,
            "report": self.report,
            "total_tokens": self.total_tokens,
            "steps": step + 1,
        }

    def _active_project(self):
        projects = self.store.list("projects")
        return projects[-1] if projects else None

    def _goal_complete(self):
        p = self._active_project()
        if not p:
            return False
        expected = {
            ("wechat", "zh"), ("zhihu", "zh"),
            ("red", "zh"), ("x", "zh"), ("x", "en"),
        }
        got = {
            (c["platform"], c["language"])
            for c in self.store.contents_for(p["id"])
            if c.get("approval")
        }
        return expected.issubset(got)

    def _report_data(self):
        p = self._active_project()
        if not p:
            return None
        return {
            "project": {"theme": p.get("theme"), "status": p.get("status")},
            "materials_count": len(self.store.materials()),
            "topics_count": len(self.store.list("topics")),
            "contents": [
                {
                    "platform": c.get("platform"),
                    "language": c.get("language"),
                    "title": c.get("title"),
                    "images": len(c.get("images") or []),
                    "approved": bool(c.get("approval")),
                    "needs_human": bool(c.get("needs_human")),
                    "residual": (c.get("unresolved") or [])[:3],
                }
                for c in self.store.contents_for(p["id"])
            ],
        }

    def _final_report(self, goal):
        data = self._report_data()
        system = "你是交付报告助手。只能依据给定的真实工作状态写中文报告，不要编造状态之外的信息。"
        user = ("目标：" + goal
                + "\n\n真实工作状态（JSON）：" + _j(data, 4000)
                + "\n\n请用小标题输出：①已完成的各平台成品（逐一列出标题/图片数/审定状态）；"
                  "②仍需人工处理的问题及原因；③接口发布状态。简明、如实。")
        text, usage, model = llm.chat(
            self.store.settings(), system, user,
            max_tokens=900, json_mode=False, temperature=0.2,
            retries=2, timeout=90)
        self.total_tokens += int(usage.get("total_tokens") or 0)
        self._log("finish", text)
        self.report = {"report": text, "status": "done",
                       "total_tokens": self.total_tokens}

    def _dispatch(self, name, args):
        fn = self.tool_map.get(name)
        if fn is None:
            return {"error": f"未知工具：{name}"}, False
        try:
            return fn(**args), name == "finish"
        except TypeError as exc:
            return {"error": f"工具参数错误：{exc}"}, False
        except Exception as exc:
            # Hand the real error back so the brain can adapt; never crash.
            return {"error": str(exc),
                    "type": type(exc).__name__,
                    "trace": traceback.format_exc(limit=1)}, False
