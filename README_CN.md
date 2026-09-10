# GitHub Stars Analyzer

> 自动拉取 GitHub Stars、抓 README、用 LLM 分析、按分类聚合，生成分类清晰的结构化 Markdown 知识库。带缓存、断点续跑、失败重试、GitHub Actions 定时任务。

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)



![License](https://img.shields.io/badge/license-MIT-green)

![Stars](https://img.shields.io/github/stars/csxo/github-stars-analyzer)

## 📌 项目链接

- 源码仓库：<https://github.com/csxo/github-stars-analyzer>
- 在线文档：`ARCHITECTURE.md` · `EXTENDING.md`
- 配套示例（csxo 用户 678 个 stars 的分析结果）：<https://github.com/csxo/csxo-stars-analysis>

## ✨ 这能解决什么

当你 GitHub 上的 stars 攒到几十上百个，浏览器收藏夹就废了。这个工具把 stars 变成**可搜索的结构化知识库**：

- 🔄 **自动抓取** — `gsa sync` 拉取所有 starred repos 元数据 + README
- 🤖 **LLM 深度分析** — 总结功能、核心能力、使用场景、技术栈、价值评分、多标签分类
- 🏷️ **自动分类与聚合** — 同类项目归一页，可对比、可快速决定要不要"解 star"
- 📝 **Markdown 输出** — 索引页 + 分类页 + 单 repo 详情页，可直接用 Obsidian / VS Code / GitHub 阅读
- 💾 **三层缓存** — `README hash × 分析版本 × 模型` 任一变化即失效；增量同步不重复调 LLM
- ⏸️ **断点续跑** — 每个 phase 完成后写 checkpoint，跑到一半断了下次接着来
- 🔁 **失败重试 + 单点容错** — 单 repo 失败不阻塞整批，重跑自动跳过已完成项
- 🤖 **GitHub Actions 全自动** — 每周一 UTC 04:00 自动跑、可手动触发、上传产物

> 💡 **不想装 Python？** 看 [`prompts/`](./prompts/) 目录——里面打包了三种可复用形式，让任何人都能用 chat AI（Claude / GPT / Cursor / Aider）跑同样的分析，或直接复制一份 prompt 模板。详见 [`prompts/README.md`](./prompts/README.md) 的 30 秒上手指南。

## 🏗️ 架构一览

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ Collect  │ →  │  README  │ →  │ Analyze  │ →  │  Report  │
│  stars   │    │  fetch   │    │   LLM    │    │ Markdown │
└────┬─────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘
     ↓               ↓               ↓               ↓
              SQLite + WAL  +  Content-addressed cache
                       ↑
                       │ checkpoint + phase_stats
                       │
                  GitHub Actions (cron / dispatch)
```

每个 phase **独立可跑**、**独立可重跑**、**独立可恢复**。详细设计见 [`ARCHITECTURE.md`](./ARCHITECTURE.md)。

## 📦 快速上手

### 1. 安装

```bash
git clone https://github.com/csxo/github-stars-analyzer.git
cd github-stars-analyzer
pip install -e .
```

### 2. 配置

```bash
cp .env.example .env
# 必填：GITHUB_TOKEN（个人 access token，权限只需 public_repo + read:user）

cp config/config.yaml.example config/config.yaml
```

最小可用配置（无需 LLM key）：

```yaml
llm:
  provider: heuristic   # 用规则 + 元数据当 fallback
  model: offline
github:
  user: YOUR_GITHUB_USERNAME   # 替换成你自己的 GitHub 用户名后再跑
```

> 想用真 LLM：把 `provider` 改成 `openai_compatible`，设置环境变量 `LLM_API_KEY`（或 `OPENAI_API_KEY`）。DeepSeek / 通义千问 / OpenRouter / Ollama 全部走 OpenAI 兼容协议。

### 3. 跑起来

```bash
# 全流水线（首次跑，按 batch_size 分批，可中断）
gsa sync

# 只生成报告（重跑只 rewrite Markdown，不调 LLM）
gsa report

# 看进度
gsa status

# 增量同步：每周跑就行，已分析过的自动跳过
gsa sync                   # 增量收集
gsa analyze                # 只分析新增 / README 变化过的
```

CLI 子命令全集：

| 命令                                                   | 作用                   |
| ---------------------------------------------------- | -------------------- |
| `gsa sync [--user NAME] [--phases collect,readme]`   | 拉 stars + 抓 README   |
| `gsa analyze [--provider X] [--model Y] [--limit N]` | LLM 分析               |
| `gsa report [--output DIR]`                          | 生成 Markdown          |
| `gsa status`                                         | 数据库 / 缓存 / 最近 job 概览 |
| `gsa clean [--orphans] [--older-than 30d]`           | 清理孤儿缓存与历史 job        |

## 📂 输出长什么样

```
data/output/
├── index.md                       # 总索引 + 26 个分类面板
├── categories/
│   ├── ai-llm.md                  # 76 个 AI/LLM 相关项目
│   ├── 代理-网络工具.md            # 65 个代理 / 规则工具
│   ├── 媒体-播放器.md             # 55 个媒体类工具
│   └── ... (共 26 个分类)
└── repos/
    └── <owner>-<repo>.md          # 678 个 repo 详情页（含 README 摘要 + 评分 + 相似项目）
```

## 🤖 GitHub Actions 自动化

默认的 `.github/workflows/analyze-stars.yml`：

- ⏰ 每周一 04:00 UTC 自动跑
- 🖱️ `workflow_dispatch` 手动触发，可指定 user / provider / model / analyze_limit
- 💾 缓存 pip + `data/` 目录避免每次重新抓 README
- 📤 产物作为 artifact 上传；可扩展为 gh-pages 自动发布
- 🔒 串行（`concurrency` group）避免 SQLite WAL 冲突

## 🧱 技术栈

- **Python 3.11+** · SQLite (WAL) · asyncio · httpx
- LLM 接口走 **OpenAI 兼容协议**（也可换 Anthropic / Gemini / Ollama）
- 全异步、本地优先、零云依赖（除 GitHub API 和你的 LLM endpoint）

## 📚 文档导航

- 📘 [`ARCHITECTURE.md`](./ARCHITECTURE.md) / [English](./ARCHITECTURE.md) — 架构设计、数据模型、Phase 编排、关键决策
- 🛠 [`EXTENDING.md`](./EXTENDING.md) / [English](./EXTENDING.md) — 怎么加新 LLM、加新字段、改相似度算法等
- 🧩 [`prompts/`](./prompts/) — 可复用的 skill / agent / prompt 分发包，让其他人不用装 Python 也能跑同样分析（详见 [`prompts/README.md`](./prompts/README.md)）
- 🌐 **在线站点**：<https://csxo.github.io/github-stars-analyzer/prompts/> — 把 prompts 渲染成网页（push 时自动部署）

## 📄 License

MIT
