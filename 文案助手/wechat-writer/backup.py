#!/usr/bin/env python3
"""Create a portable, verified local backup without stopping the writing desk."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("WECHAT_WRITER_DATA", str(ROOT / "data"))).expanduser().resolve()


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_policy_tree(source, target):
    """Copy bounded, plain policy artifacts without following external links."""
    if not source.exists():
        return
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError("写作规范目录异常，备份未完成。")
    entries = list(source.rglob("*"))
    if len(entries) > 10000:
        raise RuntimeError("写作规范目录超过 10,000 个文件或目录，请清理后再备份。")
    total = 0
    for file in entries:
        if file.is_symlink():
            raise RuntimeError("写作规范包含外部符号链接，请改为普通文件后备份。")
        if file.is_file():
            size = file.stat().st_size
            total += size
            if size > 1024 * 1024 or total > 128 * 1024 * 1024:
                raise RuntimeError("写作规范文件超过单文件 1 MiB 或合计 128 MiB 上限，备份未完成。")
    shutil.copytree(source, target)


def create_backup(destination=None):
    destination = Path(destination or ROOT.parent / "wechat-writer-backups").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    os.chmod(destination, 0o700)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    archive = destination / ("文案工作台备份-" + stamp + ".zip")
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=destination) as staging:
        tree = Path(staging) / "wechat-writer"
        tree.mkdir()
        # Only application sources: no virtualenv, credentials, logs or caches.
        for name in ("app.py", "engine.py", "image_engine.py", "performance_store.py", "performance_engine.py", "writing_store.py", "writing_engine.py", "writing_policy.py", "topic_bridge.py", "coze_bridge.py", "coze_quick.py", "backup.py", "requirements.txt",
                     "start.command", "open-notes.command", "backup.command",
                     "使用说明.md", "CONTRACT.md", ".gitignore"):
            path = ROOT / name
            if path.is_file():
                copy_file(path, tree / name)
        shutil.copytree(ROOT / "static", tree / "static")
        copy_policy_tree(ROOT / "writing-skills", tree / "writing-skills")
        target_data = tree / "data"
        target_data.mkdir()
        copy_policy_tree(DATA / "writing-policy", target_data / "writing-policy")
        database = target_data / "workspace.sqlite3"
        source_uri = (DATA / "workspace.sqlite3").as_uri() + "?mode=ro"
        # SQLite's backup API includes committed WAL data in a consistent snapshot.
        with sqlite3.connect(source_uri, uri=True, timeout=30) as source:
            with sqlite3.connect(database) as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("数据库备份校验失败，未生成备份包。")
        for name in ("notes", "backups"):
            if (DATA / name).is_dir():
                shutil.copytree(DATA / name, target_data / name)

        settings = json.loads((DATA / "settings.json").read_text(encoding="utf-8"))
        connected_notes = Path(settings.get("notes_path") or DATA / "notes" / "我的思考.md").expanduser()
        # Always capture the current notebook, including one outside this project.
        latest_notes = connected_notes.read_bytes()
        restored_notes = target_data / "notes" / "我的思考.md"
        restored_notes.parent.mkdir(parents=True, exist_ok=True)
        if restored_notes.exists() and restored_notes.read_bytes() != latest_notes:
            copy_file(restored_notes, restored_notes.with_name("原默认笔记.md"))
        restored_notes.write_bytes(latest_notes)
        # Relative default resolves correctly even after moving to another Mac.
        settings["notes_path"] = ""
        for secret in ("api_key", "tavily_api_key"):
            settings[secret] = ""
        (target_data / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Regenerate human-readable exports from the same database snapshot.
        # The app itself is not needed to read these files.
        counts = {}
        with sqlite3.connect(database) as snapshot:
            for table, folder in (("articles", "articles"), ("cards", "cards"), ("drafts", "drafts")):
                rows = snapshot.execute("SELECT id,data" + (",archived" if table == "articles" else "") + " FROM " + table).fetchall()
                counts[table] = len(rows)
                directory = target_data / folder
                directory.mkdir()
                for row in rows:
                    item = json.loads(row[1])
                    # Derive a safe filename from the stored ID; do not trust titles.
                    item_id = str(row[0])
                    if not item_id or any(c not in "0123456789abcdef" for c in item_id):
                        raise RuntimeError("资料编号异常，备份已停止。")
                    title = str(item.get("title", "未命名")).replace("\n", " ")
                    content = str(item.get("content", ""))
                    body = content if content.split("\n", 1)[0].strip() == "# " + title else "# " + title + "\n\n" + content
                    if table == "articles":
                        body += "\n\n---\n来源：" + str(item.get("url", "本地导入"))
                        body += "\n正文类型：" + str(item.get("content_kind", "full"))
                        body += "\n已归档：" + ("是" if row[2] else "否")
                    (directory / (item_id + ".md")).write_text(body.rstrip() + "\n", encoding="utf-8")
                    if table == "drafts":
                        # Use the same snapshot for provenance/audits as for prose;
                        # live sidecars may have changed after the SQLite backup.
                        metadata = {key: item.get(key) for key in ("created_at", "model", "generation_seconds", "pipeline_model_seconds", "model_cost", "cost_currency", "references", "editor_notes", "usage", "context", "writing_id", "review", "publication_package", "writing_policy_revision", "writing_mode")}
                        metadata["draft_id"] = item_id
                        (directory / (item_id + ".sources.json")).write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                        if item.get("image_suggestions"):
                            # Use the same SQLite snapshot as the prose, preserving
                            # image source links and editorial placement suggestions.
                            (directory / (item_id + ".images.json")).write_text(json.dumps(item["image_suggestions"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    (directory / (item_id + ".json")).write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            # Publication observations and edit history come from this same
            # database snapshot; live sidecars may change during a backup.
            has_performance = snapshot.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='performance_records'").fetchone()
            counts["performance_records"] = 0
            if has_performance:
                from performance_store import record_markdown
                directory = target_data / "performance"
                directory.mkdir()
                rows = snapshot.execute("SELECT id,data FROM performance_records").fetchall()
                counts["performance_records"] = len(rows)
                for item_id, raw in rows:
                    if not item_id or any(c not in "0123456789abcdef" for c in item_id):
                        raise RuntimeError("复盘记录编号异常，备份已停止。")
                    item = json.loads(raw)
                    (directory / (item_id + ".json")).write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    (directory / (item_id + ".md")).write_text(record_markdown(item), encoding="utf-8")

            # Keep the evidence checkpoint, audits and bounded repair history
            # readable even when the original application is not running.
            has_writing = snapshot.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='writing_runs'").fetchone()
            counts["writing_runs"] = 0
            if has_writing:
                from writing_store import writing_markdown
                directory = target_data / "writing"
                directory.mkdir()
                rows = snapshot.execute("SELECT id,data FROM writing_runs").fetchall()
                counts["writing_runs"] = len(rows)
                for item_id, raw in rows:
                    if not item_id or any(c not in "0123456789abcdef" for c in item_id):
                        raise RuntimeError("写作任务编号异常，备份已停止。")
                    item = json.loads(raw)
                    draft_row = snapshot.execute("SELECT data FROM drafts WHERE id=?", (item.get("draft_id", ""),)).fetchone()
                    draft = json.loads(draft_row[0]) if draft_row else None
                    (directory / (item_id + ".json")).write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    (directory / (item_id + ".md")).write_text(writing_markdown(item, draft), encoding="utf-8")

            has_topics = snapshot.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='topic_batches'").fetchone()
            counts["topic_batches"] = 0
            if has_topics:
                from topic_bridge import batch_markdown
                directory = target_data / "topics"
                directory.mkdir()
                rows = snapshot.execute("SELECT id,data FROM topic_batches").fetchall()
                counts["topic_batches"] = len(rows)
                for item_id, raw in rows:
                    if not item_id or any(c not in "0123456789abcdef" for c in item_id):
                        raise RuntimeError("选题批次编号异常，备份已停止。")
                    item = json.loads(raw)
                    (directory / (item_id + ".json")).write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    (directory / (item_id + ".md")).write_text(batch_markdown(item), encoding="utf-8")

            has_coze = snapshot.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coze_roles'").fetchone()
            if has_coze:
                directory = target_data / "coze"
                directory.mkdir()
                roles = {row[0]: json.loads(row[1]) for row in snapshot.execute("SELECT id,data FROM coze_roles")}
                (directory / "public-config.json").write_text(json.dumps(roles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                # Secrets are stored separately and are intentionally excluded.
                for item_id, raw in snapshot.execute("SELECT id,data FROM jobs"):
                    job = json.loads(raw)
                    if str(job.get("type", "")).startswith("coze-") and job.get("result") is not None:
                        if not item_id or any(c not in "0123456789abcdef" for c in item_id):
                            raise RuntimeError("Coze 调用记录编号异常，备份已停止。")
                        (directory / (item_id + ".json")).write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        manifest = {"created_at": datetime.now().astimezone().isoformat(), "records": counts,
                    "database": "SQLite online backup; committed data only",
                    "notes_sha256": hashlib.sha256(latest_notes).hexdigest(),
                    "api_secrets_included": False,
                    "note": "数据库、笔记与当前写作规范分别取快照；每个写作任务仍保留其准备时的规范快照。网页尚未保存的编辑不在备份中。"}
        files = {}
        for file in sorted(tree.rglob("*")):
            if file.is_file():
                files[str(file.relative_to(tree))] = hashlib.sha256(file.read_bytes()).hexdigest()
        manifest["sha256"] = files
        (tree / "backup-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        instructions = """文案工作台备份与恢复

这份 ZIP 含程序源码、使用说明、SQLite 数据快照、当前思考笔记及可直接阅读的 Markdown 资料。
仅包括已经保存的内容。数据库与笔记分别取快照；建议保存编辑、等待生成结束后再备份。
API 密钥、会员登录凭据、Python 虚拟环境和运行日志不在包内。

恢复方式：
1. 解压到一个新文件夹，保留原文件夹，不要覆盖唯一副本。
2. 先用文本编辑器打开 data/notes、data/articles、data/cards、data/drafts、data/performance、data/writing、data/topics 中的 .md 检查内容；复盘 .json 保留修改历史，写作任务 .json 保留事实卡、提纲和审核过程，data/coze 保留已保存的接口结果和公开配置。
3. 确认原工作台服务已停止，再双击 start.command；同一端口运行时启动脚本会打开已有工作台。
4. 新电脑需要 Python 3.11+。PDF 导入另需安装 requirements.txt 中的依赖。
5. 使用 Codex 会员模式需安装官方 Codex 并登录；使用 API 模式需重新填写密钥；Coze 接入也需要重新填写两个部署对应的 API Token。
6. 笔记已收进 data/notes/我的思考.md，恢复后默认使用包内笔记；如需外部笔记，可在设置中重新选择。
7. writing-skills 保留规范种子与来源，data/writing-policy 保留本机可编辑主 Prompt、模式与分阶段规则；每个历史写作任务继续使用其已捕获的规范版本。发布配套内容位于草稿 JSON 和待审包，正文修改后须核对过期标记。

只想读旧资料，无需启动程序，也无需 GPT 模型。
本机备份不能防止整块硬盘损坏；请另存一份到移动硬盘或你自己的备份盘。
"""
        (tree / "备份恢复说明.txt").write_text(instructions, encoding="utf-8")
        pending = archive.with_suffix(".zip.part")
        try:
            with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for path in sorted(tree.rglob("*")):
                    if path.is_file():
                        output.write(path, arcname=str(path.relative_to(Path(staging))))
            os.chmod(pending, 0o600)
            with zipfile.ZipFile(pending) as check:
                bad = check.testzip()
                if bad:
                    raise RuntimeError("压缩包校验失败：" + bad)
            pending.replace(archive)
        finally:
            pending.unlink(missing_ok=True)
    return archive, counts


if __name__ == "__main__":
    try:
        result, counts = create_backup()
        print("备份已创建并校验：\n" + str(result))
        print(f"素材 {counts['articles']} 条，写法卡片 {counts['cards']} 张，草稿 {counts['drafts']} 篇。")
        print(f"发布数据 {counts.get('performance_records', 0)} 条统计快照。")
        print(f"写作任务 {counts.get('writing_runs', 0)} 个，包含证据与审核内容包。")
        print("下次备份会生成新文件，不覆盖已有备份。请另存一份到其他磁盘。")
    except Exception as error:
        print("备份未完成：" + str(error))
        raise SystemExit(1)
