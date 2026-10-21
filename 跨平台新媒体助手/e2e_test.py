# -*- coding: utf-8 -*-
"""End-to-end pipeline test with a stub LLM that mimics DeepSeek's JSON
responses. Validates orchestration, persistence, image rendering and the
publish state machine. Blocked platforms are expected to record "blocked"
(never a fake success). Run in an isolated temp data directory.
"""
import json
import shutil
import tempfile
from pathlib import Path

import llm
from store import Store
from agent import Agent
from platforms import (WECHAT, ZHIHU, RED, X, targets,
                       STATUS_PUBLISHED, STATUS_FAILED, STATUS_UNKNOWN)

# ---------------- stub chat ----------------

_audit_seen = {}

def stub_chat(settings, system, user, max_tokens=2000, temperature=0.3,
               json_mode=True, retries=3, timeout=180):
    def resp(obj):
        return json.dumps(obj, ensure_ascii=False), {"total_tokens": 100}, "stub-model"

    if "内容日历" in user:
        entries = []
        for wk in range(1, 3):
            for p in ["wechat", "zhihu", "red"]:
                entries.append({"week": wk, "date": "2026-10-%02d" % (14 + wk),
                                "platform": p, "language": "zh",
                                "content_type": "文章", "column": "产业观察",
                                "topic": "车灯产业洗牌下的分工变化",
                                "angle": "从并购看利润分配", "goal": "提升影响力",
                                "notes": ""})
        return resp({"entries": entries})
    if "选题卡" in user:
        return resp({"topics": [
            {"title": "摩比斯为何卖掉车灯业务", "core_question": "传统整灯为何估值走低？",
             "angle": "从营收倍数与产业分工切入", "platforms": ["wechat", "zhihu", "red", "x"],
             "outline": ["交易事实", "估值逻辑", "分工变化"], "information_gain": "补充营收倍数分析",
             "sources": [{"title": "src", "url": "https://example.com/news"}],
             "effort": "中", "pros": "时效性强", "cons": "数据待核实", "status": "candidate"}]})
    if "事实卡" in user:
        return resp({"fact_cards": [
            {"fact_id": "F1", "source_id": "M1", "excerpt": "以6000亿韩元出售照明业务",
             "entity": "现代摩比斯", "numbers": "4.43亿美元", "time": "2026-10",
             "event_status": "展示", "uncertainty": "最终完成时间待核实"}]})
    if "小红书" in user:
        return resp({"cover_title": "车灯整灯国产化后，下一关是什么？",
                     "pages": [{"heading": "整灯规模大，芯片更集中", "body": "整灯厂背注塑、光学与折旧，上游光芯片更集中。"},
                               {"heading": "技术层级决定利润", "body": "ADB、MicroLED、DLP 的层级决定利润归属。"},
                               {"heading": "关键器件是分水岭", "body": "高端光源与驱动仍卡在上游。"},
                               {"heading": "自制改变分工", "body": "比亚迪、零跑把车灯收进工厂。"},
                               {"heading": "出海要算风险账", "body": "自建与收购两种路径风险归属不同。"},
                               {"heading": "待跟踪变量", "body": "定点、量产、毛利率是关键信号。"}],
                     "body": "车灯整灯国产化已到全球前十，下一关是关键器件国产化。高端光源、驱动IC能否拿到高端项目定点，是真正的分水岭。",
                     "hashtags": ["车灯", "汽车电子", "产业观察"], "image_queries": []})
    if "技术简报" in user:
        return resp({"summary": "车灯产业分工变化",
                     "posts": [{"text": "传统整灯估值走低，关键器件国产化是下一关。", "image_queries": []},
                               {"text": "高端光源与驱动仍集中在国际供应商。", "image_queries": []}]})
    if "双审核" in user:
        # first audit for a given body reports a fact issue; the re-audit after
        # auto-fix passes (simulating the model confirming the correction).
        key = hash(user)
        if key not in _audit_seen:
            _audit_seen[key] = True
            return resp({"fact": {"issues": [
                {"position": "第2段", "original": "已经完成出售",
                 "reason": "交易仍在推进，不能写成已完成",
                 "suggestion": "改为已签署最终协议"}], "passed": False},
                "language": {"issues": [], "passed": True}})
        return resp({"fact": {"issues": [], "passed": True},
                     "language": {"issues": [], "passed": True}})
    if "定点修改" in user:
        return resp({"title": None, "body": None,
                     "changes": [{"position": "第2段", "before": "已经完成出售",
                                  "after": "已签署最终协议", "reason": "状态准确"}],
                     "unresolved": []})
    if "复盘" in user:
        return resp({"findings": [{"content_id": "x", "platform": "wechat",
                                   "finding": "问题型开头评论更集中", "evidence": "评论区多为提问"}],
                     "adjustments": ["下一轮尝试更具体的问题型开头，并在相近统计窗口观察表现"],
                     "reply_drafts": [{"comment": "如何看待国产替代？", "draft": "建议关注高端定点进展。"}],
                     "notes": "缺失指标留空"})
    # default: article body for wechat/zhihu (platform-appropriate length)
    if "知乎" in user:
        target = 1500
    elif "公众号" in user:
        target = 4500
    else:
        target = 1500
    base = (
        "## 一、交易事实\n\n现代摩比斯以约4.43亿美元出售照明业务 [M1]。"
        "在最终交割完成前，表述上不能把它写成已经完成出售，这是事实卡反复核对的边界。\n\n"
        "## 二、估值逻辑\n\n传统整灯营收倍数走低，关键器件价值更集中。整灯厂承担注塑、光学、电子与产线折旧，"
        "而上游的光芯片、高端光源与驱动器件仍集中在少数供应商手中。\n\n"
        "## 三、产业分工\n\n整灯国产化已到全球前十，下一关是关键器件国产化。ADB、MicroLED、DLP 等技术层级不同，"
        "决定利润在整灯厂与器件厂之间如何分配。自制车灯的主机厂与出海设厂的供应商，正在改变原有的分工关系。\n\n"
        "## 四、待跟踪变量\n\n高端定点、量产时间与毛利率是接下来需要持续跟踪的信号，缺少确证的数据宁可留空。\n\n")
    while len(base) < target:
        base += ("对整灯厂而言，规模并不等于利润，真正决定回报的是关键器件的自主程度与高端项目的定点能力。"
                 "这也解释了为什么传统整灯资产在并购中给出的营收倍数持续走低。\n\n")
    return resp({"title": "车灯洗牌之后，利润流向了哪里",
                 "body": base[:target],
                 "summary": "从摩比斯出售车灯看产业利润分配",
                 "share_text": "推荐给关注车灯产业的朋友",
                 "image_queries": ["汽车 LED 大灯", "车灯 控制器", "车灯 工厂"],
                 "editor_notes": "核对交易金额与完成时间"})

llm.chat = stub_chat

# ---------------- run pipeline ----------------

def main():
    tmp = Path(tempfile.mkdtemp(prefix="media-e2e-"))
    store = Store(tmp)
    ag = Agent(store)

    # 1. plan
    plan = ag.plan_calendar(weeks=2)
    assert len(plan["entries"]) == 6, plan["entries"]
    print("1. plan entries:", len(plan["entries"]))

    # 2. topics (no real web in stub)
    topics = ag.research_topics("摩比斯出售车灯", use_web=False)
    assert len(topics["topics"]) == 1
    print("2. topics:", len(topics["topics"]))

    # 3. material + project + evidence
    store.add_material("note", "摩比斯出售车灯资料",
                       "现代摩比斯以6000亿韩元出售照明业务。")
    p = store.create_project("摩比斯出售车灯", "分析利润分配", "整灯厂从业者")
    ev = ag.build_fact_cards(p["id"])
    assert len(ev["fact_cards"]) == 1
    print("3. fact cards:", len(ev["fact_cards"]))

    # 4. produce all targets
    for platform, language in targets():
        c = ag.produce_target(p["id"], platform, language)
        print("4. produced", platform, language, "images:", len(c["images"]))
    p = store.get("projects", p["id"])
    assert len(p["content_ids"]) == 5, p["content_ids"]

    # 5. audit + fix for each
    for cid in p["content_ids"]:
        c = ag.audit(cid)
        audits = c["audits"]
        has_issues = audits["fact"]["issues"] or audits["language"]["issues"]
        if has_issues:
            c = ag.auto_fix(cid)
    print("5. audit + fix done")

    # 6. approve (red has no issues; approve all)
    for cid in p["content_ids"]:
        c = store.get("contents", cid)
        # force approve to test the binding
        c["force_approve"] = True
        store.save_content(c)
        ag.approve(cid, account="测试账号")
    print("6. all approved")

    # 7. publish -> all blocked without creds (no fake success)
    statuses = {}
    for cid in p["content_ids"]:
        rec = ag.execute_publish(cid)
        statuses[rec["platform"] + ":" + rec["language"]] = rec["status"]
    print("7. publish statuses:", statuses)
    assert all(s == "blocked" for s in statuses.values()), statuses

    # 8. feedback + review
    c0 = store.get("contents", p["content_ids"][0])
    ag.record_feedback(c0["id"], {"reads": 1850, "likes": 42,
                                   "favorites": None, "shares": 18},
                       [{"text": "如何看待国产替代？"}])
    review = ag.review(c0["id"])
    assert review["result"]["adjustments"]
    print("8. review adjustments:", len(review["result"]["adjustments"]))

    shutil.rmtree(tmp, ignore_errors=True)
    print("\nE2E PASSED")

if __name__ == "__main__":
    main()
