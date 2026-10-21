# -*- coding: utf-8 -*-
"""Platform specifications: editorial defaults, image specs and visual style.

These are the adjustable defaults from the requirement doc (section 2). Actual
hard limits must be validated against each platform's live API and account
capability. Reorganising per platform means re-thinking expression, structure
and visual style, not just shortening the same text or resizing an image.
"""
from __future__ import annotations

# Canonical platform ids
WECHAT = "wechat"          # 公众号
ZHIHU = "zhihu"            # 知乎
RED = "red"                # 小红书
X = "x"                     # X

PLATFORMS = [WECHAT, ZHIHU, RED, X]

# X has two language versions that must be produced, reviewed and scheduled
# independently. Each version is a distinct "target" (platform + language).
LANGUAGES = {
    WECHAT: ["zh"],
    ZHIHU: ["zh"],
    RED: ["zh"],
    X: ["zh", "en"],
}

PLATFORM_NAMES = {
    WECHAT: "公众号",
    ZHIHU: "知乎",
    RED: "小红书",
    X: "X",
}

SPECS = {
    WECHAT: {
        "id": WECHAT,
        "name": "公众号",
        "column": "产业观察与技术解读",
        "words_min": 4000,
        "words_max": 5000,
        "words_default": 4500,
        "image_min": 5,
        "image_max": 8,
        "image_default": 6,
        "cover": True,
        "image_kind": "封面 + 正文配图（技术图/实物图支持相邻论点）",
        "text_rules": [
            "从具体变化、读者痛点或技术问题切入，解释事实、机制和行业影响，保留作者判断。",
            "标题有明确的信息点、利益点或疑问，不超出正文证据。",
            "交付封面、5-8 张配图、插入位置、图注、摘要和分享语。",
        ],
        "visual": {"style": "工业杂志风", "colors": ["深蓝", "灰白"]},
        "deliverables": ["封面", "配图(5-8)", "插入位置", "图注", "摘要", "分享语", "主标题+2备选"],
        "content_types": ["文章", "群发"],  # 发布文章与向粉丝群发分别定义、授权、验收
    },
    ZHIHU: {
        "id": ZHIHU,
        "name": "知乎",
        "column": "问题解答与工程说明",
        "words_min": 1200,
        "words_max": 2200,
        "words_default": 1700,
        "image_min": 3,
        "image_max": 5,
        "image_default": 4,
        "cover": False,
        "image_kind": "结构图、对比图或实物细节图，图中参数与文字一致",
        "text_rules": [
            "先给判断，再说明依据、适用条件和例外。",
            "解释术语和推理过程，避免把宣传语当作结论。",
            "文章与回答的发布能力分别核验。",
        ],
        "visual": {"style": "工程笔记风", "colors": ["白底", "清晰标注"]},
        "deliverables": ["正文", "配图(3-5)", "参考依据", "图注"],
        "content_types": ["文章", "回答"],  # 文章与回答分开记录
    },
    RED: {
        "id": RED,
        "name": "小红书",
        "column": "专业视觉笔记",
        "words_min": 300,
        "words_max": 600,
        "words_default": 450,
        "image_min": 6,
        "image_max": 8,
        "image_default": 7,
        "cover": True,
        "image_kind": "6-8 页竖版卡片，一页一个重点，封面明确读者问题",
        "text_rules": [
            "问题具体、句子简短，图片承载主要内容。",
            "正文补充背景和边界，约 300-600 字，配相关话题。",
            "保持面向行业从业者的表达，不做泛流量口吻。",
        ],
        "visual": {"style": "专业视觉笔记", "colors": ["米白", "浅灰", "深蓝标题"]},
        "deliverables": ["竖版卡片(6-8)", "正文(300-600)", "话题标签"],
        "card_canvas": {"width": 1080, "height": 1440},  # 3:4 竖版
        "content_types": ["笔记"],
    },
    X: {
        "id": X,
        "name": "X",
        "column": "中英文技术简报",
        "words_min": 120,
        "words_max": 520,
        "words_default": 280,
        "image_min": 1,
        "image_max": 3,
        "image_default": 2,
        "cover": False,
        "image_kind": "1-3 张关键实物图或清晰图解，图片文字随语言版本适配",
        "text_rules": [
            "简洁行业观点；简单主题单帖，复杂主题串帖。",
            "中文与英文分别组织、审核和排期，不逐句直译。",
            "按目标读者解释必要背景；串帖须展示顺序、各帖配图与完整预览。",
        ],
        "visual": {"style": "技术简报风", "colors": ["深色", "高对比"]},
        "deliverables": ["单帖或串帖", "配图(1-3)", "完整预览"],
        "post_char_limit": 280,
        "content_types": ["单帖", "串帖"],
    },
}

# Publish statuses (requirement doc section 4) — the only authoritative statuses.
STATUS_PENDING = "pending"            # 待发布
STATUS_EXECUTING = "executing"        # 执行中
STATUS_PROCESSING = "processing"      # 平台处理中
STATUS_PUBLISHED = "published"        # 已发布
STATUS_FAILED = "failed"              # 失败
STATUS_UNKNOWN = "unknown"            # 结果未知

PUBLISH_STATUSES = [
    STATUS_PENDING, STATUS_EXECUTING, STATUS_PROCESSING,
    STATUS_PUBLISHED, STATUS_FAILED, STATUS_UNKNOWN,
]

STATUS_NAMES = {
    STATUS_PENDING: "待发布",
    STATUS_EXECUTING: "执行中",
    STATUS_PROCESSING: "平台处理中",
    STATUS_PUBLISHED: "已发布",
    STATUS_FAILED: "失败",
    STATUS_UNKNOWN: "结果未知",
}

# Connection readiness for a platform (requirement doc section 3/6).
READY = "ready"              # 可接入
UNVERIFIED = "unverified"    # 待验证
BLOCKED = "blocked"          # 接入受阻

def targets(platforms=None):
    """Return all (platform, language) targets, optionally filtered."""
    out = []
    for p in (platforms or PLATFORMS):
        for lang in LANGUAGES[p]:
            out.append((p, lang))
    return out

def target_key(platform, language):
    return f"{platform}:{language}"

def spec_for(platform):
    return SPECS[platform]
