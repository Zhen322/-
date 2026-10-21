# -*- coding: utf-8 -*-
"""Generate one complete style-preview sample for every platform,
including X in Chinese and English. Outputs to samples/platform_previews/.

Run:  python render_all_platforms.py
"""
from pathlib import Path
import media

out = Path("samples/platform_previews")
out.mkdir(parents=True, exist_ok=True)

# 1) 公众号 (WeChat): deep-blue industrial magazine cover
media.make_wechat_cover(
    "车灯洗牌之后，利润流向了哪里",
    "汽车与车灯产业观察 · 深度长文", out)
(out / "wechat_cover.png").replace(out / "wechat_preview.png")

# 2) 知乎 (Zhihu): white engineering-notes article card
media.make_post_card(
    "知乎 · 文章", "@创新科学",
    "现代摩比斯车灯业务卖给 OPmobility：0.24 倍收入意味着什么",
    "把交易口径拆开看：标的是照明业务整体，买方要的是产能、客户与技术路线；"
    "单一来源的工厂数量与地区清单需要第二来源核实，正文里逐条标注证据与口径。"
    "这是一篇工程笔记风格的图文，白底、信息密度高。",
    out, filename="zhihu_preview.png", accent=media.NAVY,
    background=media.WHITE, title_size=40, body_size=30)

# 3) 小红书 (Red): vertical 1080x1440 card set
red_pages = [
    {"heading": "先看交易口径", "body": "卖的是照明业务，不是整家公司。"},
    {"heading": "0.24 倍收入说明什么", "body": "定价反映资产组合与未来订单的预期。"},
    {"heading": "工厂位置是关键", "body": "数量与地区分布目前只有单一来源。"},
    {"heading": "中国供应商在哪", "body": "本土整灯厂的位置与客户结构是看点。"},
    {"heading": "哪些是待核实", "body": "交割年份、净债务与汇率基准日。"},
]
paths = media.make_xhs_cards(
    "车灯业务被卖掉，问题出在估值还是位置？",
    red_pages, out, brand="汽车与车灯产业")
# use the cover as the single preview image
Path(paths[0]).replace(out / "red_preview.png")

# 4) X · 中文 (Chinese, single-thread style)
media.make_post_card(
    "X · 中文", "@AutoLightingCN",
    "现代摩比斯车灯业务出售给 OPmobility：交易口径、双方动机、中国供应商位置与交割变量",
    "一图看懂：标的为照明业务整体；买方补齐产能与客户，卖方聚焦核心；"
    "中国整灯厂的位置与四个时间节点（MOU、最终协议、生效、交割）仍需回到原始公告核实。"
    "本条为串帖首条，中文独立组织。",
    out, filename="x_zh_preview.png", accent=media.BLUE,
    background=media.WHITE, title_size=36, body_size=29)

# 5) X · English (separately organized, not a line-by-line translation)
media.make_post_card(
    "X · English", "@AutoLightingEN",
    "OPmobility's planned acquisition of Hyundai Mobis lighting: terms, logic, and what remains open",
    "What is being sold is the lighting business, not the whole company. "
    "The buyer adds capacity, customers and technology; the seller narrows focus. "
    "Four dates (MoU, final agreement, closing conditions, completion) and the "
    "factory footprint still need to be verified against the original announcements.",
    out, filename="x_en_preview.png", accent=media.BLUE,
    background=media.WHITE, title_size=34, body_size=29, body_lang="en")

print("Platform preview samples:")
for f in sorted(out.glob("*.png")):
    print("  ", f.name, f.stat().st_size)
