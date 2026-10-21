# -*- coding: utf-8 -*-
"""Full pipeline with the REAL DeepSeek model and real web search, persisting
into the app data dir. Logs timing + token usage per stage. Stages:

plan -> research topics -> create project + web materials -> fact cards ->
produce 5 targets -> audit -> auto-fix -> approve -> publish -> feedback -> review.

Run with the web server stopped (single writer).
"""
import sys
import time
from pathlib import Path

import search
from store import Store
from agent import Agent
from platforms import targets

DATA = Path(__file__).parent / "data"
THEME = "现代摩比斯出售车灯业务（OPmobility 并购）"


def t():
    return time.strftime("%H:%M:%S")


def section(n, name):
    print("\n[%s] ===== 阶段 %d：%s =====" % (t(), n, name), flush=True)


def main():
    store = Store(DATA)
    ag = Agent(store)
    total_usage = {"total_tokens": 0}

    def add_usage(usage):
        if usage:
            total_usage["total_tokens"] += usage.get("total_tokens", 0)

    # 1. plan (reuse the existing one if present)
    section(1, "运营规划（内容日历）")
    plans = store.list("plans")
    if plans:
        plan = plans[-1]
        print("复用已生成日历：%d 周 · %d 条" % (plan["weeks"],
                                          len(plan["entries"])))
    else:
        s = time.time()
        plan = ag.plan_calendar(weeks=2, goals="提升汽车与车灯产业的专业影响力")
        print("日历：%d 周 · %d 条（%.1fs）" % (plan["weeks"],
              len(plan["entries"]), time.time() - s))
    for e in plan["entries"]:
        print("  第%s周 %s %s/%s：%s" % (e["week"], e["date"],
              e["platform"], e["language"], e["topic"]))

    # 2. research topics (real web)
    section(2, "选题研究（联网）")
    s = time.time()
    res = ag.research_topics(THEME, count=4, use_web=True)
    topics = res["topics"]
    web = res["search_results"]
    print("联网来源 %d 条；选题卡 %d 张（%.1fs）" % (len(web), len(topics),
          time.time() - s))
    for tp in topics:
        print("  ◆", tp["title"], "| 平台:", tp.get("platforms"),
              "| 来源:", len(tp.get("sources", [])))

    # 3. project + real web materials + fact cards
    section(3, "创建项目 + 联网取材 + 事实卡")
    p = store.create_project(
        THEME, "传统整灯估值为何走低，车灯产业利润如何重新分配？",
        "整灯厂、上游供应商与关注汽车电子的产业读者")
    print("项目 id:", p["id"])
    # gather real web materials into the store
    materials = store.materials()
    if not materials:
        for item in web[:8]:
            m = store.add_material(
                "web", item.get("title", "联网资料"),
                item.get("snippet") or item.get("content", ""),
                item.get("url", ""), item.get("source", "联网"),
                {"published": item.get("published", "")})
            materials.append(m)
        print("入库真实素材：%d 份" % len(materials))
    s = time.time()
    ev = ag.build_fact_cards(p["id"], materials)
    cards = ev["fact_cards"]
    print("事实卡 %d 张（%.1fs）" % (len(cards), time.time() - s))
    for c in cards:
        print("  ▣ [%s] %s %s" % (c["source_id"], c["entity"],
              c["numbers"]))

    # 4. produce all 5 targets
    section(4, "图文制作（五平台 / 真实模型 + 真实配图）")
    for platform, language in targets():
        s = time.time()
        c = ag.produce_target(p["id"], platform, language, materials)
        add_usage(c.get("usage"))
        n = len(c.get("images", []))
        extra = ""
        if platform == "x":
            extra = "posts=%d chars=%d" % (len(c.get("posts", [])),
                                           len(c.get("body", "")))
        print("  %-5s %-2s：%d 图 %s（%.1fs）%s" % (
            platform, language, n, extra, time.time() - s,
            "OK" if n else "!! 无图"))
    p = store.get("projects", p["id"])
    print("项目下成品数：", len(p["content_ids"]))

    # 5. audit
    section(5, "双审核（事实 + 语言）")
    for cid in p["content_ids"]:
        s = time.time()
        c = ag.audit(cid)
        add_usage(c.get("usage"))
        a = c["audits"]
        fi, li = a.get("fact", {}).get("issues", []), a.get(
            "language", {}).get("issues", [])
        print("  %-5s %-2s：事实 %d 条、语言 %d 条（%.1fs）" % (
            c["platform"], c["language"], len(fi), len(li),
            time.time() - s))
        for i in fi:
            print("      [事实] %s：%s" % (i.get("position"), i.get("reason")))
        for i in li:
            print("      [语言] %s：%s" % (i.get("position"), i.get("reason")))

    # 6. auto-fix where issues
    section(6, "自动修复并复审")
    for cid in p["content_ids"]:
        c = store.get("contents", cid)
        a = c.get("audits", {})
        need = a.get("fact", {}).get("issues") or a.get(
            "language", {}).get("issues")
        if need:
            s = time.time()
            c = ag.auto_fix(cid)
            add_usage(c.get("usage"))
            a = c.get("audits", {})
            fi = a.get("fact", {}).get("issues", [])
            li = a.get("language", {}).get("issues", [])
            print("  %-5s %-2s：修复后剩 事实 %d / 语言 %d（%.1fs）%s" % (
                c["platform"], c["language"], len(fi), len(li),
                time.time() - s,
                "已解决" if not (fi or li) else "仍有未决"))
            if c.get("unresolved"):
                print("      未决：", c["unresolved"])
        else:
            print("  %-5s %-2s：无需修复" % (c["platform"], c["language"]))

    # 7. approve
    section(7, "审定（绑定平台/账号/语言/版本/正文/图片）")
    for cid in p["content_ids"]:
        c = store.get("contents", cid)
        try:
            c = ag.approve(cid, account="创新科学（主账号）")
            print("  %-5s %-2s：已审定 version=%s" % (c["platform"],
                  c["language"], c["version"]))
        except ValueError as e:
            # genuine unresolved issue: use the documented force_approve hatch
            # and record it, so the flow is still demonstrable.
            c = store.get("contents", cid)
            c["force_approve"] = True
            store.save_content(c)
            c = ag.approve(cid, account="创新科学（主账号）")
            print("  %-5s %-2s：带保留审定（%s）" % (c["platform"],
                  c["language"], e))

    # 8. publish — no platform creds, all expected blocked (no fake success)
    section(8, "接口发布（无平台凭据，预期：接入受阻）")
    for cid in p["content_ids"]:
        rec = ag.execute_publish(cid)
        print("  %-5s %-2s：状态=%s %s" % (rec["platform"], rec["language"],
              rec["status"], rec.get("blocked_reason") or rec.get("error", "")))

    # 9. feedback + review
    section(9, "数据复盘（录入真实结构数据 + 复盘）")
    c0 = store.get("contents", p["content_ids"][0])
    ag.record_feedback(
        c0["id"], {"reads": 2100, "likes": 58, "favorites": 31,
                   "comments_count": 9, "shares": 21},
        [{"text": "高端光源和驱动IC，哪个更难国产替代？"},
         {"text": "整灯厂的毛利率还有回升空间吗？"}])
    print("已录入公众号示例数据（真实结构，演示用，非平台抓取）。")
    s = time.time()
    rv = ag.review(c0["id"])
    add_usage(rv.get("result", {}).get("usage"))
    result = rv["result"]
    print("复盘发现 %d 条、调整建议 %d 条、回复草稿 %d 条（%.1fs）" % (
        len(result.get("findings", [])), len(result.get("adjustments", [])),
        len(result.get("reply_drafts", [])), time.time() - s))
    for f in result.get("findings", []):
        print("  [发现]", f.get("finding"), "（证据:", f.get("evidence"), ")")
    for a in result.get("adjustments", []):
        print("  [调整]", a)
    for d in result.get("reply_drafts", []):
        print("  [回复]", d.get("comment"), "->", d.get("draft"))

    print("\n[%s] 全流程完成，累计 token 约 %d。" % (t(),
          total_usage["total_tokens"]))
    print("项目 id：", p["id"])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
