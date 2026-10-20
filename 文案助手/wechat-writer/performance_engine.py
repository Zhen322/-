"""Local descriptive publication review; no model, network, or persistent writes."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone


METRICS = ("reads", "likes", "favorites", "shares", "comments", "wows")
METRIC_LABELS = ("阅读", "点赞", "收藏", "分享", "评论", "在看")


def _date(value):
    """Do not guess a timezone for an incomplete observation timestamp."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _text(value, limit=120):
    return " ".join(str(value or "").split())[:limit]


def _metric(value):
    # bool is an int subclass, but not an observed count. Zero is valid.
    return value if type(value) is int and value >= 0 else None


def _number(value):
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(value) and value >= 0 else None
    except (OverflowError, ValueError):
        return None


def _values(row):
    values = row.get("metrics")
    values = values if isinstance(values, dict) else {}
    return {name: _metric(values.get(name)) for name in METRICS}


def _identity(row, index):
    if _text(row.get("article_id")):
        return ("article", str(row["article_id"]).strip())
    if _text(row.get("draft_id")):
        return ("draft", str(row["draft_id"]).strip())
    # A title is not an article identifier. Never merge two unnamed articles.
    return ("unidentified", index)


def _latest_key(row):
    earliest = datetime.min.replace(tzinfo=timezone.utc)
    observed = _date(row.get("stats_at"))
    updated = _date(row.get("updated_at"))
    return (observed is not None, observed or earliest,
            updated or earliest, str(row.get("id") or ""))


def _hours(row):
    published = _date(row.get("published_at"))
    observed = _date(row.get("stats_at"))
    if published is None or observed is None or observed < published:
        return None
    return (observed - published).total_seconds() / 3600


def _name(row):
    return _text(row.get("adopted_title"), 70) or _text(row.get("article_id")) or "未标识文章"


def _experiment(title, reason, action, measure):
    return {"title": title, "reason": reason, "action": action, "measure": measure}


def review_records(records):
    """Review latest article snapshots, separately by observation-age interval.

    Missing cells are never filled from older snapshots. Edit history, account
    follower deltas and monetary costs are never added to article aggregates.
    """
    if records is None:
        records = []
    if not isinstance(records, (list, tuple)):
        raise ValueError("复盘数据需为记录列表。")
    if any(not isinstance(row, dict) for row in records):
        raise ValueError("复盘记录格式不正确，请重新读取已保存的数据。")

    by_article = defaultdict(list)
    for index, row in enumerate(records):
        by_article[_identity(row, index)].append(row)
    latest = [max(rows, key=_latest_key) for rows in by_article.values()]
    latest.sort(key=_latest_key, reverse=True)
    metric_values = [_values(row) for row in latest]
    missing_cells = sum(value is None for values in metric_values for value in values.values())
    missing_ids = sum(not _text(row.get("article_id")) for row in records)

    buckets = defaultdict(list)
    ungrouped = []
    for row in latest:
        hours = _hours(row)
        if hours is None:
            ungrouped.append(row)
        else:
            buckets[math.floor(hours)].append((row, hours))
    groups = []
    for hour, members in sorted(buckets.items()):
        aggregate = {}
        for metric in METRICS:
            observed = [_values(row)[metric] for row, _ in members]
            known = [value for value in observed if value is not None]
            total = sum(known) if known else None
            aggregate[metric] = {
                "known": len(known), "missing": len(members) - len(known),
                "total": total, "mean": round(total / len(known), 2) if known else None,
            }
        groups.append({
            "label": f"发布后{hour}—{hour + 1}小时（不含{hour + 1}小时）",
            "article_count": len(members),
            "observation_hours_min": min(hours for _, hours in members),
            "observation_hours_max": max(hours for _, hours in members),
            "metrics": aggregate,
            "title_structures": sorted({_text(row.get("title_structure")) for row, _ in members
                                        if _text(row.get("title_structure"))}),
            "topic_types": sorted({_text(row.get("topic_type")) for row, _ in members
                                   if _text(row.get("topic_type"))}),
        })

    facts = []
    for row in latest[:8]:
        values = _values(row)
        counts = "，".join(f"{label}{values[metric] if values[metric] is not None else '未知'}"
                           for metric, label in zip(METRICS, METRIC_LABELS))
        hours = _hours(row)
        observed_at = _date(row.get("stats_at"))
        # Keep the supplied offset visible; UTC is only for comparisons above.
        observed_text = _text(row.get("stats_at")) if observed_at else "未提供有效统计时间"
        window_text = f"发布后{hours:.2f}小时" if hours is not None else "观察时长未知或无效"
        fact = f"《{_name(row)}》：{counts}；统计点{observed_text}，{window_text}。"
        duration = _number(row.get("generation_seconds"))
        cost = _number(row.get("model_cost"))
        if duration is not None:
            fact += f"记录的生成耗时{duration:g}秒。"
        if cost is not None:
            currency = row.get("cost_currency")
            currency = currency if currency in ("CNY", "USD") else "币种未填"
            fact += f"记录的模型费用{cost:g} {currency}（仅该条记录，不合计）。"
        facts.append(fact)

    limitations = [
        "这是已录入数据的描述统计；未进行随机对照，不把阅读或互动变化归因于标题、封面、模型或单次修改。",
        "同一文章只取最新有效统计时间的快照；时间相同时按更新时间、记录ID选取，不累计多次快照，不用旧值补齐最新空缺。",
        "各组仅在同一小时观察时长区间内汇总；仍可能存在选题、发布时间、推广和受众差异，不跨观察窗计算均值或排行。",
        "未知指标与真实0分开；总数和均值只使用该指标的已知值。不同指标的已知样本数可能不同，不能直接据此比较互动率。",
        "关注净变化属于记录的账号统计窗口，不是单篇文章涨粉；不跨文章相加，也不据此推断某篇文章带来的关注。",
    ]
    if not latest:
        limitations.append("尚无发布数据；当前没有可复盘样本。")
    if missing_ids:
        limitations.append(f"有{missing_ids}条记录缺少文章ID；仅在草稿ID相同时合并，其余独立保留，无法保证可靠去重。")
    if ungrouped:
        limitations.append(f"{len(ungrouped)}篇文章缺少有效带时区时间，或统计早于发布，未纳入可比观察窗；原始指标仍保留在逐文事实中。")
    sparse = sum(group["article_count"] < 3 for group in groups)
    if sparse:
        limitations.append(f"{sparse}个观察窗的文章样本不足3篇，只能描述，不能判断哪种写法更优。")
    if len(latest) > 8:
        limitations.append(f"逐文事实仅列出最近8篇；摘要包含全部{len(latest)}篇去重后的文章，分组只纳入时间有效的文章。")

    decreases = []
    for rows in by_article.values():
        current = max(rows, key=_latest_key)
        at = _date(current.get("stats_at"))
        # Compare only genuinely earlier observations, not edit history or ties.
        previous = [row for row in rows if at is not None and _date(row.get("stats_at")) is not None
                    and _date(row.get("stats_at")) < at]
        if not previous:
            continue
        before = _values(max(previous, key=_latest_key))
        after = _values(current)
        reduced = [label for metric, label in zip(METRICS, METRIC_LABELS)
                   if before[metric] is not None and after[metric] is not None
                   and after[metric] < before[metric]]
        if reduced:
            decreases.append(f"《{_name(current)}》的{'、'.join(reduced)}")
    if decreases:
        examples = "；".join(decreases[:3])
        limitations.append(f"最新统计中有指标低于较早快照：{examples}。可能涉及口径修正或录入变更，应核对来源；不能解释为修改产生了负面效果。")

    account_rows = [row for row in records if type(row.get("account_followers_change")) is int]
    if account_rows:
        account_windows = defaultdict(set)
        incomplete_windows = 0
        for row in account_rows:
            start, end = _date(row.get("account_window_start")), _date(row.get("account_window_end"))
            if start is None or end is None or end < start:
                incomplete_windows += 1
                continue
            account_windows[(start, end)].add(row["account_followers_change"])
        conflicting = sum(len(values) > 1 for values in account_windows.values())
        limitations.append(f"账号净关注变化涉及{len(account_windows)}个有效统计窗口；同窗多篇记录不重复相加，不同窗口也不合计。")
        if incomplete_windows or conflicting:
            limitations.append(f"账号数据有{incomplete_windows}条窗口缺失或无效、{conflicting}个窗口存在不同净变化值，需核对账号原始报表。")
    costs = [row for row in latest if _number(row.get("model_cost")) is not None]
    if costs:
        currencies = {row.get("cost_currency") for row in costs}
        limitations.append("费用仅展示记录值，未补算会员调用费用；不同币种、不同计费来源不混加，不从阅读变化推断费用收益。")
        if any(currency not in ("CNY", "USD") for currency in currencies):
            limitations.append("部分费用未注明支持的币种，仅作原始记录，不进行金额比较。")

    experiments = []
    if not latest or missing_cells or ungrouped:
        experiments.append(_experiment(
            "先统一下一轮记录口径",
            "存在空缺指标或不可比统计时间。" if latest else "还没有发布后的真实观察数据。",
            "在发布时保存文章ID、标题结构与封面模板；选择统一观察点（例如发布后24小时），按后台原值补录六项指标和来源，未知继续留空。",
            "核对每篇的发布时间、统计时间、六项指标已知数；同一文章不同统计点分别保存。"))
    if latest:
        experiments.append(_experiment(
            "积累同一观察窗的样本",
            "观察窗样本不足3篇，当前不能判断写法优劣。" if sparse or not groups else "相近观察时长仍不能排除选题和发布条件差异。",
            "下一轮在相近选题、受众和发布条件下继续记录，优先补齐同一观察窗；先把差异作为待验证线索。",
            "逐组检查样本数和各指标空缺，分别观察阅读、收藏、分享等真实记录；不跨时窗排行，也不因样本达到3篇就作因果结论。"))
        experiments.append(_experiment(
            "每轮只验证一个写作调整",
            "当前数据没有随机对照，标题、封面、正文与分发变化可能同时发生。",
            "从标题结构、开头或封面中选一个变量，写下假设和修改原因；其他条件尽量保持接近，完成一轮后人工复核，暂不自动改能力卡或提示词。",
            "在同一观察窗记录阅读和已知互动指标，同时注明选题、发布时间与推广差异；结果只作为下一轮验证依据。"))
    if any(_text(row.get("manual_changes")) or _text(row.get("change_reason")) for row in latest):
        experiments.append(_experiment(
            "保存人工修改前后版本",
            "已经有人工修改记录，但单次发布结果不能隔离修改带来的影响。",
            "把实际采用的标题、具体改动与原因一起保存；下一轮只重复验证一项改法，并记录未采用建议的原因。",
            "在相同观察窗查看多篇记录的原始指标与修改理由，不把同一文章的累积快照当作多个独立样本。"))
    if costs or any(_number(row.get("generation_seconds")) is not None for row in latest):
        experiments.append(_experiment(
            "把制作耗时和费用单独记录",
            "部分记录已有生成耗时或费用，尚不能据此推断内容表现。",
            "下一轮同时记录生成耗时、人工修改时间及有凭据的模型费用；费用注明币种与来源，未知保持为空。",
            "仅在同币种、同计费口径下人工比较已知费用；分别查看耗时与修改量，不把账号涨粉归因到单篇成本。"))

    return {
        "method": "local_rules",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "snapshot_count": len(records), "article_count": len(latest),
            "articles_with_reads": sum(values["reads"] is not None for values in metric_values),
            "missing_metrics_count": missing_cells,
        },
        "facts": facts, "limitations": limitations, "experiments": experiments, "groups": groups,
    }
