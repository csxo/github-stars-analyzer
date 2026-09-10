# 扩展指南

代码围绕几个狭窄的 seam 组织。下面每一节展示交付某个功能所需的最小改动。

## 1. 加新 LLM Provider

在 `src/github_stars_analyzer/analyzers/llm.py` 里写一个满足 `LLMProvider` 协议的类：

```python
class AnthropicProvider:
    name = "anthropic"

    async def __aenter__(self): ...
    async def __aexit__(self, *exc): ...
    async def complete(self, request: LLMRequest) -> str: ...
```

然后在 `build_provider()` 里注册，并把字面量加到 `LLMConfig.provider` 的 `Literal[...]` 联合。

就这些——`Pipeline` 和 `RepoAnalyzer` 不需要改。

## 2. 加新分析 schema 字段

1. 在 `models/repository.py::AnalysisResult` 加字段。
2. 更新 `analyzers/analyzer.py` 的 `SYSTEM_PROMPT` 来描述它。
3. 在 `config/config.yaml` 里 bump `analysis_version`。

缓存会一次性失效所有分析行（因为 `analysis_version` 是 cache key 的一部分），下次 `gsa analyze` 会在所有 repo 上填新字段。

## 3. 换成嵌入向量相似度

`output/similarity.py` 定义了 report 生成器用的两个函数：

* `repo_keyset(repo, analysis)` — 生成对比特征集。
* `find_similar(repo, analysis, pool, threshold, top_n)` — 返回排序后的相似 repos。

要切到嵌入向量：

1. 在 `pyproject.toml` 加 `embeddings` 可选依赖（已有：`numpy>=1.26`）。
2. 扩展 schema 加 `embeddings` 表，键为 repo_id。
3. 实现 `EmbeddingProvider`（例如 `sentence-transformers` 或 LLM 的 embeddings endpoint）。
4. 用向量版替换 `repo_keyset` 和 `find_similar`。
5. 更新 `MarkdownGenerator.render_all()` 调用新路径。

`MarkdownGenerator` 和报告布局不需要改。

## 4. 把 Markdown 推到 GitHub Pages 分支

GitHub Actions workflow 已经把 Markdown 作为 artifact 上传。要自动发布：

1. 加一个 deploy job：checkout `gh-pages`、把 `data/output/` 拷到分支根目录、提交。
2. 配置 Pages → deploy from branch → `gh-pages` / root。

下面 `EXTENDING.md` 里有一个参考 `deploy` job。

```yaml
deploy:
  needs: analyze
  runs-on: ubuntu-latest
  permissions:
    contents: write
  steps:
    - uses: actions/checkout@v4
      with: { ref: gh-pages }
    - uses: actions/download-artifact@v4
      with: { name: stars-report, path: . }
    - run: |
        git config user.name "github-actions[bot]"
        git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
        git add -A
        git diff --cached --quiet || git commit -m "Update stars report"
        git push
```

## 5. 加新流水线 Phase

Phase 只是注册在 `pipeline/orchestrator.py::Pipeline._dispatch` 里的协程。要加 `embed`：

1. 把协程加到 `Pipeline`。
2. 在 `_dispatch` 和 `VALID_PHASES` 注册。
3. 可选：加到 `config.py` 的 `PipelineConfig.phases`。
4. 更新 `cli.py` 让用户可以 `--phases collect,readme,analyze,embed`。

Phase 通过 `_load_or_init_checkpoint()` 和 `_update_checkpoint()` 自动获得 checkpoint。

## 6. 本地跑 LLM

* **Ollama** 在 `http://localhost:11434/v1` 暴露 OpenAI 兼容 endpoint。设 `LLM_BASE_URL` 并用任意模型名（如 `qwen2.5-coder:7b`）。
* **vLLM** 同理——任何通过它 OpenAI server 服务的开放模型。
* 对很小（≤3B）的模型，期望 tags + `value_score` 质量偏低。启发式 provider 通常更有用。

## 7. 多用户 Stars

DB 按 `id`（GitHub 的全局 repo id）做键，在整个 GitHub 实例中唯一——所以同一 repo 被两个用户 star 放在同一数据库会冲突。要支持多用户：

* 在 `repositories` 和 `analyses` 加 `user` 列。
* `repositories.UNIQUE(full_name)` → `UNIQUE(user, full_name)`。
* 更新 `analyzer.analyze_one` 和 `find_similar` 按 user 过滤。

这是列出的扩展里侵入性最大的；其他都是自包含的。

## 8. 接 GitHub Enterprise

把 `github.base_url` 设成你的 enterprise 主机（`https://github.acme.com/api/v3`）：

```yaml
github:
  base_url: https://github.acme.com/api/v3
```

除了错误信息，客户端不在任何地方假设 github.com。

## 9. 跨仓库自动同步（`data/output/` → 兄弟仓库）

`gsa report` 在 CI 里跑完之后，你大概率希望把生成的 Markdown 同步到
一个对外只读的兄弟仓库，比如 `csxo/csxo-stars-analysis`。仓库自带的
`.github/workflows/sync-to-repo-b.yml` + `.github/scripts/sync_to_repo_b.py`
就是用 Contents API 做这件事的——不需要 `git push`，也不需要给 Repo A
申请跨仓权限。

三步启用：

1. **生成 fine-grained PAT**

   去 <https://github.com/settings/tokens?type=beta>，新建一个 token：

   * Resource owner：**只选**目标仓库所属的账号
   * Repository access：**只勾**目标仓库（例如 `csxo/csxo-stars-analysis`）
   * Permissions → Repository → **Contents: Read and write**

   > Classic PAT 也可以，但 fine-grained 一旦泄露影响面更小。

2. **把它作为 Secret 加到 Repo A**

   `Repo A → Settings → Secrets and variables → Actions → New repository secret`

   * Name: `SYNC_PAT`
   * Value: 第 1 步拿到的 token

3. **（可选）覆盖目标仓库**

   默认同步到 `csxo/csxo-stars-analysis`。想改的话，在同一个面板里加一个
   *变量*（不是 secret），名字 `SYNC_TARGET_REPO`，例如
   `yourname/your-stars-analysis`。

之后 `Analyze Stars` 跑完，`Sync to Repo B` 会自动接上——当然你也可以在
Actions 标签页里手动触发一次。

### 同步原理

* `analyze-stars.yml` 跑（现在是每 6 小时一次 + 手动触发）。
* 成功后，`sync-to-repo-b.yml` 通过 `workflow_run` 触发。
* 下载 `stars-report` artifact，然后跑
  `python .github/scripts/sync_to_repo_b.py data/output <target>`。
* 脚本一次 API 调用读出**远程 git tree**，再算每个本地文件的
  **git blob SHA**，只 PUT / DELETE 差异部分。典型一周（新增 stars
  个位数）只会产生 5–10 个提交，而不是 700+。
* 脚本会自动跳过首层路径匹配 `.git,LICENSE,LICENSE_CN.md,README.md,README_CN.md`
  的文件，所以 Repo B 上手工维护的顶层文件会被原样保留。

### 关于"star 事件实时触发"

GitHub Actions 不把 `star.created` / `star.deleted` 当 workflow 触发器
（它们只在 webhook 里）。可选方案：

* **6 小时 cron**（当前自带——已经够大部分人用）。
* **GitHub App**：订阅 `star` 事件，调
  `POST /repos/{o}/{r}/dispatches` → `on: repository_dispatch` 的 workflow。
* **本地 cron**：跑 `gsa sync && gsa analyze && gsa report`，再把
  `data/output/` 推到 Repo B。

个人 stars 仪表盘这种规模，自带的 6 小时 cron 已经足够。