# -*- coding: utf-8 -*-
"""Conversational agent (multi-turn).

Each conversation is a persistent Brain session. A user message triggers an
agentic turn: the model may reason and call tools (both are shown to the user
in natural language), and when it replies without tool calls that is the
final natural-language answer.
"""
from __future__ import annotations

import json
import time

import llm
from brain import (Brain, human_action, human_result, human_icon, _j)
from store import uid, now


CHAT_SYSTEM = """你是「跨平台新媒体助手」，一个对话式智能体，服务汽车与车灯产业链。你通过调用工具帮用户完成：联网采集、选题、建项目、整理事实卡、四平台图文制作、审核、自动修复、审定、排期、复盘。

回复规则（必须遵守）：
1. 用自然、口语化的中文和用户交流。**绝对不要输出代码块、JSON、工具名或原始参数**——你调用工具的过程系统会自动用中文展示。
2. 每次准备调用工具前，先用一句简短的中文说明你在做什么、为什么。
3. 完成操作后，用自然语言清楚地告诉用户：做了什么、结果如何、还差什么、需要用户做什么。
4. 证据先行：没有素材或事实卡就不写正文；工具没返回的事实不假设、不编造。
5. 已经存在的成果要复用，一个动作只做一次，不要反复重审或重复建卡。
6. 用户只是闲聊或提问就直接回答；需要调用工具才能完成时再调用工具。
7. 这一轮事情做完，就用自然语言收尾，不要再调用工具。
8. 凡是要判断“有没有 / 缺不缺 / 做没做 / 几个”，必须先用 get_state 或 list_contents 核实，不能凭记忆或假设下结论。
四平台覆盖：公众号(wechat)、知乎(zhihu)、小红书(red)、X(x)；其中 X 要分别产出中文(zh)和英文(en)，分别组织、不逐句直译。"""


class ChatBrain:
    def __init__(self, agent):
        self.agent = agent
        self.store = agent.store
        b = Brain(agent)
        self.tools = b.schemas
        self._dispatch = b._dispatch
        self.total_tokens = 0
        self.max_iter = 16

    # ---------------- conversation persistence ----------------
    def new_conversation(self, title=None):
        conv = {
            "id": uid(),
            "title": title or "新对话",
            "created_at": now(),
            "updated_at": now(),
            "messages": [{"role": "system", "content": CHAT_SYSTEM}],
            "events": [],
        }
        self.store.put("conversations", conv)
        return conv

    def get_conversation(self, conv_id):
        return self.store.get("conversations", conv_id)

    def list_conversations(self):
        return self.store.list("conversations")

    def delete_conversation(self, conv_id):
        return self.store.delete("conversations", conv_id)

    # ---------------- one turn ----------------
    def respond(self, conv_id, user_text, on_progress=None):
        conv = self.get_conversation(conv_id)
        conv["events"].append({"type": "user", "text": user_text})
        conv["messages"].append({"role": "user", "content": user_text})
        if conv.get("title") == "新对话":
            conv["title"] = user_text[:24]
        conv["updated_at"] = now()
        self.store.put("conversations", conv)

        steps = []
        final_text = ""
        start = time.time()
        for it in range(self.max_iter):
            message, usage, model = llm.chat_tools(
                self.store.settings(), conv["messages"], self.tools,
                max_tokens=1000, temperature=0.1,
                retries=2, timeout=90)
            self.total_tokens += int(usage.get("total_tokens") or 0)
            conv["messages"].append(message)
            content = (message.get("content") or "").strip()
            tool_calls = message.get("tool_calls") or []

            if tool_calls:
                if content:
                    steps.append({"kind": "thought", "text": content})
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or ""
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    steps.append({"kind": "action",
                                  "text": human_action(name, args),
                                  "icon": human_icon(name)})
                    obs, _terminal = self._dispatch(name, args)
                    steps.append({"kind": "result",
                                  "text": human_result(name, obs)})
                    obs_limit = 3200 if name in ("list_contents", "get_state",
                                                 "get_project") else 1300
                    conv["messages"].append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "name": name,
                        "content": _j(obs, obs_limit),
                    })
                conv["updated_at"] = now()
                self.store.put("conversations", conv)
                if on_progress:
                    on_progress(list(steps))
                continue

            # No tool calls: this content is the final answer.
            final_text = content or "（已完成）"
            break

        if not final_text:
            final_text = ("这一轮操作较多，我没能在限定步数内给出总结，"
                          "请让我“继续”或直接查看相关页面的结果。")
        elapsed = round(time.time() - start)
        conv["events"].append(
            {"type": "assistant", "text": final_text,
             "steps": list(steps), "elapsed": elapsed})
        conv["updated_at"] = now()
        self.store.put("conversations", conv)
        return conv
