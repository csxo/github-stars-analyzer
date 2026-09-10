---
name: GitHub Stars Analyzer
version: 1.0.0
description: >
  自动分析指定用户的 GitHub Stars：抓取公开 stars、按分类聚合、生成结构化
  Markdown 知识库，支持缓存、断点续跑、失败重试、CLI 与 GitHub Actions 自动化。
  适用于用户给出 GitHub stars 链接（自己的或他人的）希望批量整理出可浏览的
  Markdown 索引，或希望以 LLM 深度解读每个 star 项目功能/能力/技术栈/价值评分的场景。
  触发词："分析我的 GitHub stars"、"整理 GitHub star 列表"、"star 分类生成
  Markdown"、"批量分析 GitHub starred repos"、"分析某人的 GitHub stars"、
  "github stars analyzer"、"stars knowledge base"。
  不适用：私有仓库（必须登录）、非 GitHub 平台、单 repo 详情（用通用 readme
  分析即可）、需要实时刷新的场景（这是个一次性 + 增量工具）。
description_en: >
  Fetch a GitHub user's public stars, classify them, and render a structured
  Markdown knowledge base with caching, resume, retry, CLI, and GitHub Actions
  automation. Trigger when the user shares a GitHub stars URL and wants a
  browsable categorized Markdown dump, or wants LLM-powered per-repo deep
  analysis. Not for private repos (login required), non-GitHub platforms,
  single-repo detail pages, or real-time requirements.
---

# GitHub Stars Analyzer 技能

把"GitHub stars 列表"变成"结构化的 Markdown 知识库"。本技能既包含一个**可直接运行的命令行实现**（Python 包），也包含**给 agentic tool 使用的提示词模板**。

## 何时使用本技能

触发条件（任一）：

- 用户分享 GitHub stars URL（自己或他人公开账号），希望整理成可浏览文档
- 用户说"分析我的 GitHub stars / 整理 star 列表 / 批量分析 starred 项目"
- 用户希望对一个用户的所有 star 项目做：分类聚合 / 同类对比 / LLM 深度解读 / Markdown 输出
- 用户希望搭建一个长期运行的 GitHub stars 自动化分析流水线

不适用的场景：

- 私有 stars（需登录，本工具用匿名 API）
- 单个 repo 详情深挖（用通用 README 分析即可）
- 实时同步（这是 batch 工具，最小粒度是分钟级）

## 工作模式

技能提供三种使用方式，按用户的工具栈灵活选用：

### 方式 A · WorkBuddy 直接调用（最快）

WorkBuddy 在本会话里可以直接执行：

1. 接收用户的 GitHub stars URL（必须公开）
2. 用 GitHub REST API 匿名抓 stars（每页 100，per_page=30 防截断）
3. 对每个 repo：抓 metadata + topics + description + language
4. 调用内置启发式分类器（无需 LLM key）或可选 LLM 深度分析
5. 渲染 Markdown：1 个 `index.md` + N 个 `categories/*.md` + M 个 `repos/*.md`

输出位置：用户工作区下的 `data/output/` 子目录。

⚠️ **匿名 API 限制**：60 req/h。读 ~700 个 star 的元数据足够（700/100=7 页），但**不能**在一次会话里读完所有 README——README 抓取需要登录态。

### 方式 B · 用户本地 CLI（推荐生产环境）

用户 clone `csxo/github-stars-analyzer` 后：

```bash
pip install -e .
cp .env.example .env  # 填 GITHUB_TOKEN（提升到 5000 req/h）+ 可选 LLM_API_KEY
cp config/config.yaml.example config/config.yaml

# 零密钥跑通（heuristic provider 用 metadata 当 fallback）
gsa sync --provider heuristic
gsa analyze --provider heuristic --model offline
gsa report

# 接 OpenAI / DeepSeek / 任何 OpenAI 兼容服务
gsa sync
gsa analyze --provider openai_compatible --model gpt-4o-mini
gsa report
```

特性：
- ✅ 三层缓存键：`README hash × analysis_version × model`
- ✅ 4 phase 流水线（collect/readme/analyze/report），各 phase 独立 checkpoint
- ✅ 批处理硬上限（`batch_size` + `per_phase_limit`）
- ✅ SQLite + WAL，单文件存储
- ✅ GitHub Actions 工作流（schedule + workflow_dispatch）
- ✅ 失败软处理，单 repo 失败不阻塞 batch

### 方式 C · 复制粘贴提示词（任何 chat agent）

把 `prompts/prompt-template.md` 内容复制到 Claude / GPT / Cursor / Aider 等任意 chat interface，按提示词模板操作。这种方式适用于：用户没装 Python / 没装 WorkBuddy / 只想临时看一次结果。

## 关键设计决策（详见 ARCHITECTURE.md）

| 决策 | 理由 |
|------|------|
| SQLite 而非 Postgres | 单文件、零运行时依赖、Actions cache 友好 |
| 内容寻址缓存（README hash） | README 变了自动失效分析结果 |
| analysis_version 字段 | prompt 模板升级时一次性失效整张缓存 |
| 4 phase 流水线 | collect / readme / analyze / report 互相解耦，断点续跑天然成立 |
| 启发式 fallback | 无 API key 也能跑通端到端，便于调试和零成本使用 |
| Markdown 而非 JSON | 人类可读、GitHub 直接渲染、便于版本管理 |

## 复用建议

别人要复刻这个项目：

1. **最快（30 秒）**：复制 `prompts/prompt-template.md` 到任意 chat AI，按模板提问
2. **生产环境（10 分钟）**：clone `csxo/github-stars-analyzer`，按 README 装依赖、填 token、`gsa sync && gsa analyze && gsa report`
3. **完全自定义**：参考 `prompts/agent.md` 的 agent 定义，写自己的 agent loop

## 文件清单

```
prompts/
├── README.md          # 本目录使用说明
├── agent.md           # 给任何 agentic tool 用的 agent 定义
├── prompt-template.md # 用户可直接复制粘贴的 prompt
└── skill.md           # WorkBuddy skill 定义（与本文件等价）

src/github_stars_analyzer/  # 完整 Python 实现
├── collectors/  # GitHub API + README 抓取
├── analyzers/   # LLM Provider + 启发式分类
├── storage/     # SQLite + 内容缓存
├── pipeline/    # 4 phase 编排 + checkpoint
├── output/      # Markdown 渲染
├── cli.py       # click CLI
└── config.py    # 分层配置

tests/            # 15 个单元测试
.github/workflows/analyze-stars.yml  # GitHub Actions
```

## 已知边界

- 匿名 API 60 req/h → 读 README 必须配 `GITHUB_TOKEN`
- GitHub REST API 单页最多 100 个 repo
- 启发式分类精度 ≈ 70%（基于 description + topics + language），LLM 模式可达 90%+
- 同一用户 star 数 > 5000 时建议分批处理

## 参考链接

- 完整实现：<https://github.com/csxo/github-stars-analyzer>
- 演示输出：<https://github.com/csxo/csxo-stars-analysis>
- 架构详解：本仓库 `ARCHITECTURE.md`
- 扩展指南：本仓库 `EXTENDING.md`