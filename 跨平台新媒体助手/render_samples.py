# -*- coding: utf-8 -*-
from pathlib import Path
import media

out = Path("samples")
out.mkdir(exist_ok=True)

# WeChat cover
media.make_wechat_cover("车灯洗牌之后，利润流向了哪里",
                         "汽车与车灯产业观察", out)

# Red cards
red = {
    "cover_title": "车灯整灯国产化后，下一关是什么？",
    "pages": [
        {"heading": "整灯规模大，芯片更集中", "body": "整灯厂背注塑、光学与折旧，上游光芯片更集中。"},
        {"heading": "技术层级决定利润", "body": "ADB、MicroLED、DLP 的层级决定利润归属。"},
        {"heading": "关键器件是分水岭", "body": "高端光源与驱动仍卡在上游。"},
        {"heading": "自制改变分工", "body": "比亚迪、零跑把车灯收进工厂。"},
        {"heading": "出海要算风险账", "body": "自建与收购两种路径风险归属不同。"},
        {"heading": "待跟踪变量", "body": "定点、量产、毛利率是关键信号。"}],
    "brand": "汽车与车灯产业",
}
media.make_xhs_cards(red["cover_title"], red["pages"], out,
                     brand=red["brand"])

# Diagram card (fallback)
media.make_diagram_card(
    "车灯产业分工的关键事实",
    ["现代摩比斯：约4.43亿美元出售照明业务",
     "整灯厂：承担注塑、光学、电子与折旧",
     "高端光源与驱动：仍集中于少数供应商",
     "下一关：关键器件国产化"],
    out)
# bar chart
media.make_bar_chart(
    ["2022", "2023", "2024", "2025"],
    [("整灯国产化率", [12, 18, 27, 36], media.BLUE)],
    "整灯国产化率（示例，仅在有真实数据时绘制）",
    out)

print("files:")
for f in sorted(out.glob("*")):
    print(" ", f.name, f.stat().st_size)
