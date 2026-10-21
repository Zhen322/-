# -*- coding: utf-8 -*-
"""Resume the real pipeline from the fix stage for the latest project:
auto-fix -> approve -> publish -> feedback -> review. Avoids re-creating the
plan/topics/project/contents. Run with the web server stopped.
"""
import sys
import time
from pathlib import Path

from store import Store
from agent import Agent

DATA = Path(__file__).parent / "data"


def t():
    return time.strftime("%H:%M:%S")


def section(n, name):
    print("\n[%s] ===== 阶段 %d：%s =====" % (t(), n, name), flush=True)


def main():
    store = Store(DATA)
    ag = Agent(store)
    projects = store.list("projects")
    if not projects:
        print("no project found"); sys.exit(1)
    p = projects[-1]
    print("项目:", p["theme"], p["id"])
    print("成品数:", len(p["content_ids"]))

    # 6. fix
    section(6, "自动修复并复审（定点片段）")
    for cid in p["content_ids"]:
        c = store.get("contents", cid)
        a = c.get("audits", {})
        need = a.get("fact", {}).get("issues") or a.get(
            "language", {}).get("issues")
        s = time.time()
        if need:
            c = ag.auto_fix(cid)
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
            print("  %-5s %-2s：无需修复（%.1fs）" % (c["platform"],
                  c["language"], time.time() - s))

    # 7. approve
    section(7, "审定（绑定平台/账号/语言/版本/正文/图片）")
    for cid in p["content_ids"]:
        c = store.get("contents", cid)
        try:
            c = ag.approve(cid, account="创新科学（主账号）")
            print("  %-5s %-2s：已审定 version=%s" % (c["platform"],
                  c["language"], c["version"]))
        except ValueError as e:
            c = store.get("contents", cid)
            c["force_approve"] = True
            store.save_content(c)
            c = ag.approve(cid, account="创新科学（主账号）")
            print("  %-5s %-2s：带保留审定（%s）" % (c["platform"],
                  c["language"], e))

    # 8. publish
    section(8, "接口发布（无平台凭据，预期：接入受阻）")
    for cid in p["content_ids"]:
        rec = ag.execute_publish(cid)
        print("  %-5s %-2s：状态=%s %s" % (rec["platform"], rec["language"],
              rec["status"], rec.get("blocked_reason") or rec.get("error", "")))

    # 9. feedback + review
    section(9, "数据复盘")
    # only record if not already recorded
    existing = [f for f in store.list("feedback")
                if f.get("content_id") == p["content_ids"][0]]
    c0 = store.get("contents", p["content_ids"][0])
    if not existing:
        ag.record_feedback(
            c0["id"], {"reads": 2100, "likes": 58, "favorites": 31,
                       "comments_count": 9, "shares": 21},
            [{"text": "高端光源和驱动IC，哪个更难国产替代？"},
             {"text": "整灯厂的毛利率还有回升空间吗？"}])
        print("已录入公众号示例数据（真实结构，演示用）。")
    else:
        print("已有反馈数据，跳过录入。")
    s = time.time()
    rv = ag.review(c0["id"])
    result = rv["result"]
    print("复盘发现 %d、调整 %d、回复 %d（%.1fs）" % (
        len(result.get("findings", [])), len(result.get("adjustments", [])),
        len(result.get("reply_drafts", [])), time.time() - s))
    for f in result.get("findings", []):
        print("  [发现]", f.get("finding"), "（证据:", f.get("evidence"), ")")
    for a in result.get("adjustments", []):
        print("  [调整]", a)
    for d in result.get("reply_drafts", []):
        print("  [回复]", d.get("comment"), "->", d.get("draft"))

    print("\n[%s] 全流程完成。" % t())
    print("项目 id：", p["id"])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
