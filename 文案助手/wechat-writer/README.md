# 字里 · 公众号写作工作台

面向汽车与车灯上下游公众号的本地写作工作台，服务于整灯厂和上游供应商从业者。程序和资料保存在本机，可通过本机已登录的 Codex 调用 GPT-6 Astra，也支持 Ollama 本地模型和云端 API。

完整功能说明见 [使用说明.md](使用说明.md)；服务端与前端的数据契约见 [CONTRACT.md](CONTRACT.md)。

## 运行

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # 仅可选的 PDF 导入需要 pypdf
./start.command                             # 浏览器打开 http://127.0.0.1:8765
```

服务端只使用 Python 3.11+ 标准库，无需 Docker 与 Node 构建。工作台绑定本机回环地址。

## 目录

| 路径 | 说明 |
| --- | --- |
| `app.py` | HTTP 服务端与 API 路由 |
| `engine.py` | 通用模型调用与素材处理引擎 |
| `writing_engine.py` | B 流程写作引擎（事实卡、提纲、正文、标题、双审核、定点修改、发布配套） |
| `writing_store.py` / `writing_policy.py` | 写作任务、待审包与规范快照持久化 |
| `image_engine.py` | 联网配图检索、筛选与图注生成 |
| `coze_bridge.py` / `coze_quick.py` / `topic_bridge.py` | Coze 工作流接入与选题导入 |
| `performance_store.py` / `performance_engine.py` | 发布数据记录与本地规则复盘 |
| `backup.py` | 在线 SQLite 快照备份 |
| `static/` | 网页界面与选题看板 |
| `data/` | 笔记、素材、写法卡片、草稿、待审包、写作规范、发布数据 |
| `writing-skills/` | 程序内置的写作规范模板 |

## 哪些内容不进版本库

`.gitignore` 有意排除三类本地内容，请不要提交：

| 排除项 | 原因 | 如何保管 |
| --- | --- | --- |
| `data/coze_secrets.json`、`data/settings.json` | 含 Coze Token 与模型 API 密钥 | 单独保管，不随程序外发 |
| `data/workspace.sqlite3` | 二进制数据库，每次写入都会变化 | 双击 `backup.command` 生成 ZIP 快照 |
| `.venv/`、`__pycache__/`、`*.log`、`*.pid` | 运行环境与运行时状态 | 克隆后按上文重建 |

**分工：版本库负责代码与文本资料（笔记、草稿、待审包、写作规范）的可回溯历史；完整资料、数据库和外部笔记仍由 `backup.command` 生成的 ZIP 承担。** 两者互补，不能相互替代。

## 同步到 GitHub

远程仓库：<https://github.com/Zhen322/wechat-writer>（**私有**）

**日常同步（推荐）**：在 Finder 里双击 `同步到GitHub.command`。它会自动判断有没有改动，然后提交、推送，失败时告诉你原因。

**手动命令**：

```sh
git add -A
git commit -m "说明这次改了什么"
git push
```

注意事项：

- 推送需要能访问 GitHub。本机是通过 ClashX Pro 代理访问的，**请保持 ClashX Pro 开启**，否则 `git push` 会失败。提交已保存在本机，不会丢，重试即可。
- 本仓库的 Git 凭证来自 `gh` 的 keyring，并已配置为仓库级 `credential.helper`。若提示需要登录，执行 `~/.local/bin/gh auth login`。
- **不要**用 `git add -f` 强制添加 `data/coze_secrets.json` 或 `data/settings.json`。
- 免费计划的私有仓库**不包含** GitHub secret scanning，密钥防护依赖 `.gitignore` 和 `同步到GitHub.command` 的前置检查，不要绕过它们。

## 提交前自检

```sh
git status                  # 确认列表中没有 data/coze_secrets.json 与 data/settings.json
git diff --cached --stat
```
