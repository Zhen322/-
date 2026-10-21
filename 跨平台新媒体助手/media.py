# -*- coding: utf-8 -*-
"""Render real image files: Xiaohongshu vertical cards, WeChat covers, simple
structure/comparison diagrams. Uses Pillow with Chinese fonts (Microsoft YaHei /
SimHei on Windows). Every delivered image is an actual file, never a prompt or
a placeholder link. Charts are drawn from the supplied real data only.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def uid():
    return uuid.uuid4().hex


# Brand palette (deep blue + grey-white industrial look)
NAVY = (27, 58, 92)
NAVY_DARK = (18, 39, 63)
BLUE = (43, 96, 145)
CREAM = (247, 245, 240)
LIGHT_GRAY = (239, 239, 237)
WHITE = (255, 255, 255)
INK = (45, 52, 64)
MUTED = (120, 128, 140)
GOLD = (176, 141, 87)


FONT_CANDIDATES = {
    "yahei": r"C:\Windows\Fonts\msyh.ttc",
    "yahei_bold": r"C:\Windows\Fonts\msyhbd.ttc",
    "simhei": r"C:\Windows\Fonts\simhei.ttf",
    "simsun": r"C:\Windows\Fonts\simsun.ttc",
    "deng": r"C:\Windows\Fonts\Deng.ttf",
    "deng_bold": r"C:\Windows\Fonts\Dengb.ttf",
}


def _font(name, size):
    path = FONT_CANDIDATES.get(name)
    if path and os.path.exists(path):
        return ImageFont.truetype(path, size)
    # Fallback to any candidate that exists, then PIL default.
    for p in FONT_CANDIDATES.values():
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _is_cjk(ch):
    return bool(re.match(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", ch))


def wrap_text(draw, text, font, max_width):
    """Greedy wrap that handles CJK per-character and Latin by word."""
    lines = []
    for para in str(text).split("\n"):
        if not para:
            lines.append("")
            continue
        current = ""
        i = 0
        while i < len(para):
            ch = para[i]
            if _is_cjk(ch) or ch in "，。、；：？！…（）【】“”":
                trial = current + ch
                if draw.textlength(trial, font=font) <= max_width:
                    current = trial
                else:
                    if current:
                        lines.append(current)
                    current = ch
                i += 1
            else:
                # Consume a whole latin token/word.
                m = re.match(r"[A-Za-z0-9’'%./\-]+", para[i:])
                token = m.group(0) if m else ch
                trial = current + token
                if draw.textlength(trial, font=font) <= max_width:
                    current = trial
                else:
                    if current:
                        lines.append(current)
                    if draw.textlength(token, font=font) > max_width:
                        # Break a very long token character by character.
                        for c in token:
                            if draw.textlength(current + c, font=font) > max_width:
                                lines.append(current)
                                current = c
                            else:
                                current += c
                    else:
                        current = token
                i += len(token)
        lines.append(current)
    return lines


def _draw_paragraph(draw, xy, text, font, max_width, fill, line_gap=14):
    x, y = xy
    lines = wrap_text(draw, text, font, max_width)
    ascent, descent = font.getmetrics()
    step = ascent + descent + line_gap
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += step
    return y


# ------------------------- Xiaohongshu cards -------------------------

def make_xhs_cards(cover_title, pages, out_dir, brand="创新科学",
                   width=1080, height=1440):
    """Render 6-8 vertical cards. pages: list of {heading, body}.

    Cover states the reader's question; each inner page carries one key point.
    Returns list of image file paths (cover first).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    total = len(pages) + 1
    margin = 80
    text_w = width - margin * 2

    # ---- Cover ----
    img = Image.new("RGB", (width, height), CREAM)
    d = ImageDraw.Draw(img)
    # Top navy band
    d.rectangle([0, 0, width, 34], fill=NAVY)
    # Decorative side bar
    d.rectangle([0, 0, 26, height], fill=NAVY)
    d.rectangle([26, 0, 34, height], fill=GOLD)
    f_kicker = _font("yahei_bold", 40)
    f_cover = _font("yahei_bold", 92)
    f_brand = _font("yahei", 34)
    d.text((margin, 230), "车灯产业笔记", font=f_kicker, fill=BLUE)
    d.rectangle([margin, 296, margin + 120, 306], fill=GOLD)
    y = 340
    y = _draw_paragraph(d, (margin, y), cover_title, f_cover, text_w,
                        NAVY_DARK, line_gap=20)
    d.text((margin, height - 170), brand, font=f_brand, fill=MUTED)
    # page indicator
    f_num = _font("yahei", 32)
    d.text((width - margin - 120, height - 170), "1 / %d" % total,
           font=f_num, fill=MUTED)
    p = out_dir / "xhs_01_cover.png"
    img.save(p, "PNG", optimize=True)
    paths.append(str(p))

    # ---- Inner pages ----
    for idx, item in enumerate(pages, start=2):
        bg = WHITE if idx % 2 == 0 else LIGHT_GRAY
        img = Image.new("RGB", (width, height), bg)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, width, 26], fill=NAVY)
        d.rectangle([0, 26, width, 34], fill=GOLD)
        f_no = _font("yahei_bold", 150)
        f_head = _font("yahei_bold", 66)
        f_body = _font("yahei", 42)
        # big translucent number
        d.text((margin, 120), "%02d" % (idx - 1), font=f_no,
               fill=(220, 226, 235))
        d.rectangle([margin, 300, margin + 90, 310], fill=GOLD)
        y = 340
        y = _draw_paragraph(d, (margin, y), item.get("heading", ""),
                            f_head, text_w, NAVY_DARK, line_gap=16)
        y += 30
        _draw_paragraph(d, (margin, y), item.get("body", ""), f_body,
                        text_w, INK, line_gap=18)
        d.text((margin, height - 150), brand, font=f_brand, fill=MUTED)
        d.text((width - margin - 140, height - 150), "%d / %d" % (idx, total),
               font=f_num, fill=MUTED)
        p = out_dir / f"xhs_{idx:02d}.png"
        img.save(p, "PNG", optimize=True)
        paths.append(str(p))
    return paths


# ------------------------- WeChat cover -------------------------

def make_wechat_cover(title, subtitle, out_dir, width=1242, height=700):
    """Industrial-magazine deep-blue cover image (actual file)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), NAVY_DARK)
    d = ImageDraw.Draw(img)
    # subtle diagonal block
    d.rectangle([0, height - 14, width, height], fill=GOLD)
    d.rectangle([0, 0, 18, height], fill=GOLD)
    f_kicker = _font("yahei_bold", 38)
    f_title = _font("yahei_bold", 86)
    f_sub = _font("yahei", 40)
    d.text((90, 110), "车灯产业研究", font=f_kicker, fill=(160, 190, 220))
    d.rectangle([90, 172, 210, 182], fill=GOLD)
    _draw_paragraph(d, (90, 220), title, f_title, width - 180,
                    WHITE, line_gap=18)
    if subtitle:
        _draw_paragraph(d, (90, height - 210), subtitle, f_sub,
                        width - 180, (190, 205, 220), line_gap=12)
    p = out_dir / "wechat_cover.png"
    img.save(p, "PNG", optimize=True)
    return str(p)


# ------------------------- Generic post card (X / Zhihu) -------------------------

def make_post_card(platform_label, handle, title, body, out_dir,
                   filename="post_card.png", accent=BLUE,
                   background=WHITE, width=1200, height=940,
                   title_size=44, body_size=30, body_lang="zh"):
    """A branded, readable post/article preview card (X zh/en, Zhihu).

    Used for standalone style previews. Body text is illustrative only.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), background)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, 14], fill=accent)
    f_head = _font("yahei_bold", 30)
    f_handle = _font("yahei", 24)
    d.text((54, 46), platform_label, font=f_head, fill=accent)
    d.text((54, 90), handle, font=f_handle, fill=MUTED)
    d.line([54, 140, width - 54, 140], fill=(222, 226, 232), width=2)
    y = 166
    f_title = _font("yahei_bold", title_size)
    y = _draw_paragraph(d, (54, y), title, f_title, width - 108,
                        INK, line_gap=10)
    y += 20
    f_body = _font("yahei" if body_lang == "zh" else "regular", body_size)
    y = _draw_paragraph(d, (54, y), body, f_body, width - 108,
                        INK, line_gap=16)
    f_foot = _font("yahei", 22)
    d.text((54, height - 58),
           "跨平台新媒体助手 · 风格预览样例（正文为示意，正式发布以真实证据为准）",
           font=f_foot, fill=MUTED)
    p = out_dir / filename
    img.save(p, "PNG", optimize=True)
    return str(p)


# ------------------------- Fallback diagram card -------------------------

def make_diagram_card(title, points, out_dir, filename=None,
                      width=1242, height=820, dark=False):
    """A branded diagram/title card used as a real-file substitute when real
    photos are insufficient or their use conditions are unclear. Points are
    short conclusions drawn from the (real) supplied text, not invented data.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bg = NAVY_DARK if dark else WHITE
    fg = WHITE if dark else NAVY_DARK
    img = Image.new("RGB", (width, height), bg)
    d = ImageDraw.Draw(img)
    f_title = _font("yahei_bold", 52)
    f_point = _font("yahei", 40)
    if not dark:
        d.rectangle([0, 0, 18, height], fill=GOLD)
    else:
        d.rectangle([0, height - 14, width, height], fill=GOLD)
    y = 70
    y = _draw_paragraph(d, (60, y), title, f_title, width - 120,
                        fg, line_gap=16)
    y += 24
    for p in points[:6]:
        if not dark:
            d.ellipse([70, y + 18, 92, y + 40], fill=GOLD)
        else:
            d.ellipse([70, y + 18, 92, y + 40], fill=GOLD)
        y = _draw_paragraph(d, (112, y), p, f_point, width - 180,
                            fg, line_gap=12)
        y += 14
    name = filename or ("diagram_%s.png" % uid()[:8])
    p = out_dir / name
    img.save(p, "PNG", optimize=True)
    return str(p)


# ------------------------- Comparison / bar chart -------------------------

def make_bar_chart(categories, series, title, out_dir, filename="chart.png",
                   width=1242, height=820):
    """Simple grouped bar chart drawn only from supplied real numbers.

    series: list of (label, values, color_rgb). Missing values are skipped.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), WHITE)
    d = ImageDraw.Draw(img)
    f_title = _font("yahei_bold", 44)
    f_lab = _font("yahei", 30)
    f_leg = _font("yahei", 30)
    d.text((70, 50), title, font=f_title, fill=NAVY_DARK)
    d.rectangle([70, 112, 190, 120], fill=GOLD)

    plot_l, plot_r = 90, width - 90
    plot_t, plot_b = 180, height - 150
    nums = [v for _, vals, _ in series for v in vals if v is not None]
    vmax = max(nums) if nums else 1
    # axis baseline
    d.line([plot_l, plot_b, plot_r, plot_b], fill=MUTED, width=3)
    n = len(categories)
    group_w = (plot_r - plot_l) / max(1, n)
    nser = len(series)
    bar_w = min(70, (group_w * 0.7) / max(1, nser))
    for i, cat in enumerate(categories):
        gx = plot_l + group_w * i + group_w * 0.15
        for j, (label, vals, color) in enumerate(series):
            v = vals[i] if i < len(vals) else None
            if v is None:
                continue
            bh = (v / vmax) * (plot_b - plot_t)
            x0 = gx + j * (bar_w + 8)
            d.rectangle([x0, plot_b - bh, x0 + bar_w, plot_b], fill=color)
            d.text((x0, plot_b - bh - 38), str(v), font=f_lab, fill=INK)
        # category label (wrap if needed)
        lab_lines = wrap_text(d, cat, f_lab, group_w * 0.95)
        ly = plot_b + 14
        for ln in lab_lines[:2]:
            d.text((gx, ly), ln, font=f_lab, fill=INK)
            ly += 36
    # legend
    lx = plot_l
    ly = plot_b + 110
    for label, vals, color in series:
        d.rectangle([lx, ly, lx + 30, ly + 30], fill=color)
        d.text((lx + 40, ly - 2), label, font=f_leg, fill=INK)
        lx += int(d.textlength(label, font=f_leg)) + 110
    p = out_dir / filename
    img.save(p, "PNG", optimize=True)
    return str(p)
