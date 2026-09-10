# Prompts · GitHub Stars Analyzer

> 这是一份**可移植分发包**。不需要 WorkBuddy、不需要 clone 主项目、不需要 Python——只要你能跟任意一个 chat AI（Claude / GPT / Cursor / Aider / Cline / WorkBuddy）对话，就能跑出同样的结果。

> 🇨🇳 **中文版**：本文 · **🇬🇧 English version**: [`README_EN.md`](./README_EN.md)

## 文件清单

| 文件 | 用途 | 适用对象 |
|------|------|----------|
| [`agent.md`](./agent.md) / [`agent_EN.md`](./agent_EN.md) | 完整 agent 定义：角色、工具、流程、约束、失败处理 | 任何支持 system prompt / agent instructions 的工具（Claude Code、Cursor、Aider 等） |
| [`prompt-template.md`](./prompt-template.md) / [`prompt-template_EN.md`](./prompt-template_EN.md) | 5 个开箱即用的提示词模板（小/中/大批量 + 对比 + 自动化） | 任何 chat interface（直接复制粘贴） |
| [`skill.md`](./skill.md) / [`skill_EN.md`](./skill_EN.md) | WorkBuddy skill 定义（YAML frontmatter + 中文说明） | WorkBuddy 用户 |

## 30 秒上手

### 方案 A · 直接用 prompt 模板（最快）

1. 打开 [`prompt-template.md`](./prompt-template.md)
2. 选一个模板（A/B/C/D/E）
3. 把 `<USER_OR_URL>` 替换成实际值
4. 整段复制到 Claude / GPT / Cursor chat
5. 看结果

### 方案 B · 用 agent 定义（适合反复使用）

把 [`agent.md`](./agent.md) 的内容保存为：
- **Claude Code** → `.claude/agents/github-stars-analyzer.md`
- **Cursor** → Settings → Rules for AI
- **Aider** → `--system-prompt agent.md`
- **WorkBuddy** → 用 `Skill` 工具加载 [`skill.md`](./skill.md)
- **Cline / Continue** → 项目规则文件

之后在该项目中说"分析 https://github.com/xxx?tab=stars"即可触发。

### 方案 C · 完整 Python 实现（生产环境）

```bash
git clone https://github.com/csxo/github-stars-analyzer
cd github-stars-analyzer
pip install -e .

cp .env.example .env  # 填 GITHUB_TOKEN + LLM_API_KEY
cp config/config.yaml.example config/config.yaml

gsa sync
gsa analyze --provider openai_compatible --model gpt-4o-mini
gsa report
```

特性：缓存、断点续跑、失败重试、GitHub Actions 自动化、SQLite 存储。

## 输出示例

跑通后用户会得到：

```
data/output/
├── index.md             # 总索引（分类汇总）
├── categories/          # 每个分类一页（按价值评分降序）
│   ├── ai-llm.md
│   ├── 代理-网络工具.md
│   └── ...
└── repos/               # 每个 repo 一页详情
    ├── openai-gpt-oss.md
    └── ...
```

每页 Markdown 直接在 GitHub / VSCode / Obsidian / 任何 Markdown 浏览器渲染。

## 自定义

需要扩展时：

| 想做的事 | 改哪里 |
|---|---|
| 换 LLM provider（Anthropic / Gemini / Ollama） | `src/github_stars_analyzer/analyzers/llm.py` 加新 Provider 类 |
| 加新分析字段（如 license 健康度） | 改 `models/repository.py::AnalysisResult` + bump `analysis_version` |
| 加嵌入向量相似度 | 加 `embeddings` 表 + 替换 `output/similarity.py` |
| 加 gh-pages 自动发布 | 加 `.github/workflows/deploy.yml` |
| 加 Web UI | 新增 `web/` 子项目 |

完整扩展指南见主项目 [`EXTENDING.md`](../EXTENDING.md)。

## 关联链接

- 主项目实现：<https://github.com/csxo/github-stars-analyzer>
- 演示输出（csxo 的 678 stars）：<https://github.com/csxo/csxo-stars-analysis>
- 架构详解：主项目 [`ARCHITECTURE.md`](../ARCHITECTURE.md)