"""Publication performance snapshots, validated imports and portable exports.

The SQLite rows are authoritative. Counts are observations supplied by the user;
missing values stay null, and account follower changes remain account-level data.
"""
from __future__ import annotations

from contextlib import contextmanager
import csv
from datetime import datetime
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid
from zoneinfo import ZoneInfo


LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
MAX_IMPORT_BYTES = 8 * 1024 * 1024
MAX_IMPORT_ROWS = 1000
MAX_SAFE_INTEGER = 2 ** 53 - 1
METRICS = ("reads", "likes", "favorites", "shares", "comments", "wows")
METRIC_LABELS = dict(zip(METRICS, ("阅读量", "点赞量", "收藏量", "分享量", "评论量", "在看量")))
TEXT_LIMITS = {
    "article_id": 200, "draft_id": 200, "topic_type": 300,
    "adopted_title": 1000, "title_structure": 2000, "cover_template": 2000,
    "manual_changes": 12000, "change_reason": 6000, "cost_source": 2000,
    "data_source": 2000, "notes": 12000,
}
COLUMNS = [
    ("article_id", "文章ID"), ("draft_id", "关联草稿ID"),
    ("published_at", "发布时间"), ("stats_at", "统计时间"),
    ("topic_type", "选题类型"), ("adopted_title", "采用标题"),
    ("title_structure", "标题结构"), ("cover_template", "封面模板"),
    *[("metrics." + key, METRIC_LABELS[key]) for key in METRICS],
    ("manual_changes", "人工修改"), ("change_reason", "修改原因"),
    ("generation_seconds", "生成耗时秒"), ("model_cost", "模型费用"),
    ("cost_currency", "费用币种"), ("cost_source", "费用来源"),
    ("account_followers_change", "账号关注净变化"),
    ("account_window_start", "账号窗口开始"), ("account_window_end", "账号窗口结束"),
    ("data_source", "数据来源"), ("notes", "备注"),
]
LABELS = {field: label for field, label in COLUMNS}
CORE_FIELDS = [field for field in LABELS if not field.startswith("metrics.")] + ["metrics"]
INTERNAL_FIELDS = {"id", "created_at", "updated_at", "revision", "history"}
NUMERIC_FIELDS = {"generation_seconds", "model_cost", "account_followers_change", *["metrics." + key for key in METRICS]}


class PerformanceError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def timestamp():
    return datetime.now(LOCAL_ZONE).isoformat(timespec="microseconds")


def json_text(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def text_value(value, field, limit):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PerformanceError(f"{LABELS.get(field, field)}必须是文本。")
    value = value.strip()
    if len(value) > limit:
        raise PerformanceError(f"{LABELS.get(field, field)}最多 {limit:,} 个字符。")
    if "\x00" in value:
        raise PerformanceError(f"{LABELS.get(field, field)}不能包含空字符。")
    return value


def observation_time(value, field):
    value = text_value(value, field, 100)
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=LOCAL_ZONE)
        parsed = parsed.astimezone(LOCAL_ZONE)
        return parsed.isoformat(timespec="microseconds" if parsed.microsecond else "seconds")
    except (ValueError, OverflowError):
        raise PerformanceError(f"{LABELS[field]}格式无效，请使用 ISO 日期时间，例如 2026-10-13T18:00:00+08:00。") from None


def integer_value(value, field, signed=False):
    if value is None or isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, bool):
        valid = False
    elif isinstance(value, int):
        valid = True
    elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        try:
            value = int(value.strip())
            valid = True
        except ValueError:
            valid = False
    else:
        valid = False
    if not valid or not signed and value < 0:
        kind = "整数（可为负数）" if signed else "非负整数"
        raise PerformanceError(f"{LABELS.get(field, field)}须为{kind}或留空；请把“10万+”等不明确数值保留在备注中，不要猜测。")
    if abs(value) > MAX_SAFE_INTEGER:
        raise PerformanceError(f"{LABELS.get(field, field)}超出可精确保存的整数范围（最大 {MAX_SAFE_INTEGER:,}），请核对原始数据。")
    return value


def number_value(value, field):
    if value is None or isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise PerformanceError(f"{LABELS[field]}须为非负有限数字或留空。")
    try:
        if isinstance(value, str) and not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value.strip()):
            raise ValueError()
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError()
        return number
    except (ValueError, OverflowError):
        raise PerformanceError(f"{LABELS[field]}须为非负有限数字或留空，不能填写 NaN、无穷大或带单位的文本。") from None


def validate_record(body, current=None, draft_exists=None):
    if not isinstance(body, dict):
        raise PerformanceError("每条发布记录必须是 JSON 对象。")
    unknown = set(body) - set(CORE_FIELDS) - INTERNAL_FIELDS
    if unknown:
        raise PerformanceError("不识别的数据字段：" + "、".join(sorted(map(str, unknown)))[:300] + "。请使用导入模板中的字段。")
    base = {field: current.get(field) for field in CORE_FIELDS} if current else {}
    base.update({field: value for field, value in body.items() if field in CORE_FIELDS and field != "metrics"})
    record = {field: text_value(base.get(field), field, limit) for field, limit in TEXT_LIMITS.items()}
    if not record["article_id"]:
        raise PerformanceError("文章ID必填，请使用公众号文章ID、原文链接或你自行维护的唯一文章编号。")
    if record["draft_id"] and draft_exists and not draft_exists(record["draft_id"]):
        raise PerformanceError("关联草稿不存在，请选择当前工作台已有的草稿，或将关联草稿ID留空。")
    metrics = dict(current.get("metrics", {})) if current else {}
    if "metrics" in body:
        if not isinstance(body["metrics"], dict):
            raise PerformanceError("metrics 必须是包含六项指标的对象；未知指标请留空或填 null。")
        unknown_metrics = set(body["metrics"]) - set(METRICS)
        if unknown_metrics:
            raise PerformanceError("不识别的传播指标：" + "、".join(sorted(map(str, unknown_metrics)))[:200] + "。")
        metrics.update(body["metrics"])
    record["metrics"] = {key: integer_value(metrics.get(key), "metrics." + key) for key in METRICS}
    record["published_at"] = observation_time(base.get("published_at"), "published_at")
    record["stats_at"] = observation_time(base.get("stats_at"), "stats_at")
    if any(value is not None for value in record["metrics"].values()) and not record["stats_at"]:
        raise PerformanceError("有阅读、点赞、收藏等指标时，统计时间必填；0 也是一条有效观测。")
    if record["published_at"] and record["stats_at"] and datetime.fromisoformat(record["stats_at"]) < datetime.fromisoformat(record["published_at"]):
        raise PerformanceError("统计时间不能早于发布时间。")
    for field in ("generation_seconds", "model_cost"):
        record[field] = number_value(base.get(field), field)
    record["cost_currency"] = text_value(base.get("cost_currency"), "cost_currency", 10).upper()
    if record["cost_currency"] not in ("", "CNY", "USD"):
        raise PerformanceError("费用币种须为空、CNY 或 USD。")
    if record["model_cost"] is not None and not record["cost_currency"]:
        raise PerformanceError("录入模型费用时必须选择 CNY 或 USD，不能把不同币种混合统计。")
    record["account_followers_change"] = integer_value(base.get("account_followers_change"), "account_followers_change", signed=True)
    for field in ("account_window_start", "account_window_end"):
        record[field] = observation_time(base.get(field), field)
    window_start, window_end = record["account_window_start"], record["account_window_end"]
    if bool(window_start) != bool(window_end):
        raise PerformanceError("账号关注统计窗口的开始与结束日期须同时填写或同时留空。")
    if record["account_followers_change"] is not None and not (window_start and window_end):
        raise PerformanceError("录入账号关注净变化时，必须填写完整账号统计窗口；它不会归因于单篇文章。")
    if window_start and datetime.fromisoformat(window_end) < datetime.fromisoformat(window_start):
        raise PerformanceError("账号统计窗口结束日期不能早于开始日期。")
    return record


def record_markdown(record):
    def display(value):
        return "未知" if value is None or value == "" else str(value)

    title = re.sub(r"[\r\n]+", " ", str(record.get("adopted_title") or record.get("article_id") or "发布数据快照"))
    lines = ["# " + title, "", "本记录是用户录入的发布数据快照，选题或写法关联不等于效果因果。", ""]
    for field, label in COLUMNS:
        value = (record.get("metrics") or {}).get(field.split(".", 1)[1]) if field.startswith("metrics.") else record.get(field)
        lines.extend(["## " + label, "", display(value), ""])
    lines.extend(["---", "账号关注净变化只描述账号在指定窗口的变化，不能视为这篇文章带来的关注。", "", "修订历史保存在同名 JSON 的 history 字段中。", ""])
    return "\n".join(lines)


def csv_safe_text(value):
    value = "" if value is None else str(value)
    if value and (ord(value[0]) < 32 or ord(value[0]) == 127 or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value


def records_csv(records):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow([label for _, label in COLUMNS])
    for record in records:
        row = []
        for field, _ in COLUMNS:
            value = (record.get("metrics") or {}).get(field.split(".", 1)[1]) if field.startswith("metrics.") else record.get(field)
            row.append("" if value is None else value if field in NUMERIC_FIELDS else csv_safe_text(value))
        writer.writerow(row)
    return output.getvalue()


def _header_key(value):
    return re.sub(r"[\s_\-.（）()]", "", str(value or "").strip("\ufeff")).casefold()


ALIASES = {}
for _field, _label in COLUMNS:
    ALIASES[_header_key(_field)] = _field
    ALIASES[_header_key(_label)] = _field
    if _field.startswith("metrics."):
        ALIASES[_header_key(_field.split(".", 1)[1])] = _field
for _alias, _field in {
    "文章编号": "article_id", "公众号文章ID": "article_id", "文章链接": "article_id",
    "draft": "draft_id", "草稿ID": "draft_id", "主题类型": "topic_type",
    "文章标题": "adopted_title", "标题": "adopted_title", "title": "adopted_title",
    "阅读": "metrics.reads", "点赞": "metrics.likes", "收藏": "metrics.favorites",
    "转发": "metrics.shares", "转发量": "metrics.shares", "分享": "metrics.shares",
    "评论": "metrics.comments", "在看": "metrics.wows", "read_count": "metrics.reads",
    "like_count": "metrics.likes", "favorite_count": "metrics.favorites",
    "share_count": "metrics.shares", "comment_count": "metrics.comments", "wow_count": "metrics.wows",
    "生成耗时(秒)": "generation_seconds", "生成耗时": "generation_seconds",
    "模型成本": "model_cost", "币种": "cost_currency", "成本来源": "cost_source",
    "人工修改内容": "manual_changes", "账号关注变化": "account_followers_change",
    "source": "data_source", "数据备注": "notes",
}.items():
    ALIASES[_header_key(_alias)] = _field


def parse_import(body):
    format_name = body.get("format")
    content = body.get("content")
    if format_name not in ("csv", "json"):
        raise PerformanceError("导入格式须为 csv 或 json。")
    if not isinstance(content, str) or not content.strip():
        raise PerformanceError("请提供需要导入的 CSV 或 JSON 文本。")
    if len(content.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise PerformanceError("单次导入最多 8 MiB 文本，请分批导入。", 413)
    content = content.lstrip("\ufeff")
    if format_name == "json":
        try:
            records = json.loads(content)
        except (ValueError, RecursionError):
            raise PerformanceError("JSON 格式无效，请提供记录数组或包含 records 数组的对象。") from None
        if isinstance(records, dict):
            records = records.get("records")
        if not isinstance(records, list):
            raise PerformanceError("JSON 必须是记录数组，或 {\"records\":[...]} 对象。")
        result = [(index, record) for index, record in enumerate(records, 1)]
    else:
        try:
            reader = csv.reader(io.StringIO(content, newline=""), strict=True)
            headers = next(reader, None)
            if not headers:
                raise PerformanceError("CSV 缺少表头，请下载导入模板。")
            fields = []
            for header in headers:
                field = ALIASES.get(_header_key(header))
                if not field:
                    raise PerformanceError(f"不识别 CSV 列“{str(header)[:80]}”，请使用模板中的中英字段。")
                if field in fields:
                    raise PerformanceError(f"CSV 中“{LABELS[field]}”重复出现，请只保留一列。")
                fields.append(field)
            if "article_id" not in fields:
                raise PerformanceError("CSV 缺少“文章ID”列，请使用导入模板。")
            result = []
            for row in reader:
                if not row or not any(cell.strip() for cell in row):
                    continue
                if len(row) != len(fields):
                    raise PerformanceError(f"CSV 第 {reader.line_num} 行列数与表头不一致，请检查引号与逗号。")
                item = {}
                for field, value in zip(fields, row):
                    if field.startswith("metrics."):
                        item.setdefault("metrics", {})[field.split(".", 1)[1]] = value
                    else:
                        item[field] = value
                result.append((reader.line_num, item))
                if len(result) > MAX_IMPORT_ROWS:
                    raise PerformanceError(f"单次最多导入 {MAX_IMPORT_ROWS} 条记录，请分批导入。")
        except csv.Error:
            raise PerformanceError("CSV 格式无效或单个字段过长，请检查引号、分隔符与文本长度。") from None
    if not result:
        raise PerformanceError("导入内容没有数据行；模板不包含示例或虚构发布数据。")
    if len(result) > MAX_IMPORT_ROWS:
        raise PerformanceError(f"单次最多导入 {MAX_IMPORT_ROWS} 条记录，请分批导入。")
    return result


def _atomic_bytes(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix=".performance-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class PerformanceStoreMixin:
    def init_performance(self):
        self.performance_lock = threading.RLock()
        self.performance_directory = self.root / "performance"
        self.performance_directory.mkdir(exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS performance_records(id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    def performance_records(self):
        with self.connect() as db:
            return [json.loads(row["data"]) for row in db.execute("SELECT data FROM performance_records ORDER BY rowid DESC")]

    @contextmanager
    def _performance_transaction(self):
        with self.performance_lock:
            written = []
            try:
                with self.connect() as db:
                    # Serialize writers even if another local server process is running.
                    db.execute("BEGIN IMMEDIATE")
                    yield db, written
            except Exception:
                for path, previous in reversed(written):
                    try:
                        if previous is None:
                            path.unlink(missing_ok=True)
                        else:
                            _atomic_bytes(path, previous)
                    except OSError:
                        # SQLite rolls back; its committed records remain authoritative.
                        pass
                raise

    @staticmethod
    def _existing_keys(db, exclude_id=None):
        keys = set()
        for row in db.execute("SELECT id,data FROM performance_records"):
            if row["id"] != exclude_id:
                record = json.loads(row["data"])
                keys.add((record["article_id"], record.get("stats_at", "")))
        return keys

    @staticmethod
    def _check_unique(record, existing_keys):
        key = (record["article_id"], record["stats_at"])
        if key in existing_keys:
            moment = record["stats_at"] or "未填写统计时间"
            raise PerformanceError(f"文章“{record['article_id']}”在“{moment}”已有快照，不能重复导入。请编辑原记录，或填写新的统计时间。", 409)
        existing_keys.add(key)

    @staticmethod
    def _new_record(payload):
        moment = timestamp()
        return {**payload, "id": uuid.uuid4().hex, "created_at": moment, "updated_at": moment, "revision": uuid.uuid4().hex, "history": []}

    def _save_performance_records(self, db, written, records):
        # Validate and stage the entire batch before changing any visible record.
        with tempfile.TemporaryDirectory(prefix=".pending-", dir=self.performance_directory) as directory:
            prepared = []
            for record in records:
                for suffix, content in ((".json", json_text(record)), (".md", record_markdown(record))):
                    destination = self.performance_directory / (record["id"] + suffix)
                    staged = Path(directory) / destination.name
                    _atomic_bytes(staged, content.encode("utf-8"))
                    prepared.append((staged, destination))
            for record in records:
                db.execute("INSERT INTO performance_records(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (record["id"], json_text(record)))
            for staged, destination in prepared:
                previous = destination.read_bytes() if destination.exists() else None
                written.append((destination, previous))
                os.replace(staged, destination)

    def create_performance(self, body):
        with self._performance_transaction() as (db, written):
            draft_exists = lambda draft_id: bool(db.execute("SELECT 1 FROM drafts WHERE id=?", (draft_id,)).fetchone())
            payload = validate_record(body, draft_exists=draft_exists)
            self._check_unique(payload, self._existing_keys(db))
            record = self._new_record(payload)
            self._save_performance_records(db, written, [record])
        return record

    def update_performance(self, record_id, body):
        with self._performance_transaction() as (db, written):
            row = db.execute("SELECT data FROM performance_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                raise PerformanceError("发布数据记录不存在。", 404)
            current = json.loads(row["data"])
            if not isinstance(body.get("revision"), str) or body["revision"] != current["revision"]:
                raise PerformanceError("这条数据已更新或缺少有效版本。请保留当前输入，重新载入记录后合并；未覆盖已有数据。", 409)
            draft_exists = lambda draft_id: bool(db.execute("SELECT 1 FROM drafts WHERE id=?", (draft_id,)).fetchone())
            payload = validate_record(body, current, draft_exists=draft_exists)
            self._check_unique(payload, self._existing_keys(db, exclude_id=record_id))
            if all(payload[field] == current.get(field) for field in CORE_FIELDS):
                return current
            record = {**current, **payload, "updated_at": timestamp(), "revision": uuid.uuid4().hex}
            previous = {key: value for key, value in current.items() if key != "history"}
            record["history"] = [*current.get("history", []), previous]
            self._save_performance_records(db, written, [record])
        return record

    def import_performance(self, body):
        raw_records = parse_import(body)
        with self._performance_transaction() as (db, written):
            existing = self._existing_keys(db)
            draft_exists = lambda draft_id: bool(db.execute("SELECT 1 FROM drafts WHERE id=?", (draft_id,)).fetchone())
            records = []
            for row_number, raw in raw_records:
                try:
                    payload = validate_record(raw, draft_exists=draft_exists)
                    self._check_unique(payload, existing)
                    records.append(self._new_record(payload))
                except PerformanceError as exc:
                    label = "CSV 第" if body["format"] == "csv" else "JSON 第"
                    unit = "行" if body["format"] == "csv" else "条"
                    raise PerformanceError(f"{label} {row_number} {unit}：{exc} 本批未写入任何数据。", exc.status) from None
            self._save_performance_records(db, written, records)
        return {"imported": len(records), "records": records}

    def export_performance_json(self):
        return json_text({"format": "wechat-writer-performance-v1", "exported_at": timestamp(), "records": self.performance_records()})

    def export_performance_csv(self):
        return records_csv(self.performance_records())

    def performance_template_csv(self):
        return records_csv([])

    def review_performance(self):
        import performance_engine
        return performance_engine.review_records(self.performance_records())
