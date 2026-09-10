# 架构说明

本文档解释**为什么**代码要这样组织。在做非平凡改动之前先读一遍。

## 目标（以及驱动设计的约束）

1. **默认增量**：在 5,000 个 star 的账号上重跑流水线必须便宜——输入未变时大多数操作应当是 no-op。
2. **可恢复**：任何超过几分钟的任务必须能中途被杀后存活下来。"从头再来"不可接受。
3. **Provider 无关**：加一个新 LLM 厂商只需要在 `analyzers/` 加一个新文件。相似度算法同理。
4. **有界批次**：任何 phase 都不应该能跑上几小时。一切都通过 `batch_size` 和 `per_phase_limit` 参数化。
5. **可自托管**：单一二进制入口点、零后台服务、纯标准库 + 3 个小依赖。
6. **可复现**：每条 `job_runs` 记录都带 config snapshot，可以重放历史任务。

## 数据模型

```
repositories ──┐
   │            │
   │            ├─< analyses            (在 repo_id, version, model 上唯一)
   │            │
   │            └─< repo_categories >── categories
   │
   └─< checkpoints >── job_runs
```

* `repositories` 是规范存储。行按 `id` upsert，因此重新同步从不复制——像 `stargazers_count` 和 `topics` 这种字段会被覆盖；只有 `starred_at` 在新 payload 没有时才会保留。
* `analyses` 行按 `(repo_id, analysis_version, model)` 做键。结合 README hash，缓存失效策略可以用一条 SQL 完成：

  ```sql
  SELECT 1 FROM analyses a
   WHERE a.repo_id = :rid
     AND a.analysis_version = :ver
     AND a.model = :model
     AND a.readme_hash = :hash
  ```

* `repo_categories` 是支持"相似项目"视图的多对多表。LLM 返回的 tag 在这里插入，每 tag 一行，`confidence=1.0`。聚合流水线从不直接写这张表——只读。
* `job_runs` + `checkpoints` 提供断点续跑能力。每次 `sync` / `analyze` / `report` 调用写一条 `job_runs`；每个 phase 写一条 `checkpoints` 记录 `(cursor, processed_ids, failed_ids, last_id)`。

## 流水线 Phase

```
sync  →  collect    →  readme
analyze             →  analyze
report             →  report
```

每个 phase 都是 `src/github_stars_analyzer/pipeline/orchestrator.py` 中一个独立的协程。**它们之间内存中什么都不共享**——每个输入来自 DB，每个输出回到 DB 或文件系统。

### `collect`

* 分页拉取 `/users/{name}/starred`。
* 批量 upsert（每 100 行/提交）保证 SQLite 写入效率。
* 尊重 GitHub 的 `x-ratelimit-*` 头并在配额耗尽时主动 sleep。

### `readme`

* 读 `repositories WHERE has_readme=0 OR readme_hash IS NULL`。
* 用 `application/vnd.github.raw` media type 调 `GET /repos/{o}/{r}/readme`——省掉 base64 解码。
* 计算 SHA-256，写到 `data/cache/readme/xx/<hash>.md`，更新 DB 行。

### `analyze`

* 读 `repositories JOIN analyses ... WHERE readme_hash != analyses.readme_hash`。
  这就是完整的缓存失效逻辑——出来的每行都是真正需要新做的工作。
* 并发上限由 `llm_concurrency` 控制。吞吐受最慢的 provider 调用限制；网络快就调高 `GSA_LLM_CONCURRENCY`。
* 每批处理过的 ID 刷新到 `checkpoints` 行。

### `report`

* 全部从 DB 读。
* 生成 `index.md`、`categories/*.md`、`repos/*.md`。
* 幂等——再跑一次只是覆盖文件。

## 缓存失效，一张图说清

```
                 bump analysis_version?
                 │ yes
                 ▼
        ┌────────────────────────┐
        │  every cached analysis │   ← 全部失效；下次跑重分析
        │       is "stale"       │
        └────────────────────────┘

  per-repo:
  ┌──────────────┐   changed?    ┌──────────────┐
  │ README body  │ ────────────► │ analysis row │
  │  (new hash)  │               │  is invalid  │
  └──────────────┘               └──────────────┘
        ▲                                │
        │ fetch                          │ store
        │                                ▼
   data/cache/readme/                SQLite analyses table
```

## 续跑语义

* `sync --no-resume` 是强制重跑的唯一方式；不带它的话，`_load_or_init_checkpoint()` 会继承最近一条相同命令的 `job_runs` 行的 processed IDs，所以一个被日常杀掉的 cron 永远从断点继续。
* Checkpoints 是 append-only——失败 ID 被追踪但不自动重试；想强制重新尝试加 `--no-resume`。
* `report` 故意没有 checkpoint：渲染幂等且快。

## 失败模型

| 失败 | 行为 |
| --- | --- |
| 单 repo 的 README 返回 404 | 存 `readme_hash=""`；`analyze` 跳过 |
| 单 repo 的 LLM 响应无法解析 | 记录日志，原始响应持久化以备取证，任务继续 |
| 单 repo 的 LLM 调用 429 | `OpenAICompatibleProvider` 内部指数退避重试 |
| 网络断 30 秒 | `retry_async` 退避后继续 |
| 进程被杀 | 下次调用从最后一条 `checkpoints` 行恢复 |
| 整个 provider 宕机 | 任务结束，失败 ID 捕获到 `checkpoints.failed_ids` |

## 为什么选 SQLite？

* 单文件 → 备份、复制、检查都简单。
* WAL 模式 → 并发读不阻塞写。
* JSON 列 → 加字段不用 schema 迁移。
* `sqlite3` 在标准库 → 存储层零运行时依赖。

## 我们刻意没做的事

* Web UI。`docs/` 分支上的 Markdown 文件比另一个 React App 更好浏览、对比、搜索。
* daemon / scheduler。Cron + Actions 足够；流水线幂等所以重跑安全。
* 嵌入向量相似度。Tag/topics 上的 Jaccard 是第一刀——够起步；以后换很简单（见 `EXTENDING.md`）。

## 目录结构

```
src/github_stars_analyzer/
├── __init__.py
├── cli.py              # click 命令
├── config.py           # 分层 config loader
├── analyzers/
│   ├── llm.py          # provider 抽象 + 3 个实现
│   └── analyzer.py     # prompt、解析、缓存写
├── collectors/
│   ├── github_api.py   # 异步 REST 客户端
│   └── stars.py        # 高层 collect_stars / collect_readmes
├── models/
│   ├── repository.py
│   ├── category.py
│   └── job_run.py
├── output/
│   ├── markdown.py     # 站点生成器
│   ├── similarity.py   # Jaccard helpers
│   └── i18n.py
├── pipeline/
│   ├── orchestrator.py # Pipeline + phases
│   └── resume.py       # checkpoint merge helpers
├── storage/
│   ├── database.py     # SQLite + schema
│   └── cache.py        # 内容寻址文件系统缓存
└── utils/
    ├── logger.py
    ├── hash.py
    └── retry.py
```