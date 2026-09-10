# Prompts · GitHub Stars Analyzer

> A **portable distribution bundle**. No WorkBuddy, no cloning the main project, no Python required — anyone who can talk to any chat AI (Claude / GPT / Cursor / Aider / Cline / WorkBuddy) can produce the same result.

> **中文版本**：[`README.md`](./README.md) · **English version**: this file.

## Files

| File | Purpose | Audience |
|------|---------|----------|
| [`agent.md`](./agent.md) / [`agent_EN.md`](./agent_EN.md) | Full agent definition: role, tools, workflow, constraints, failure handling | Any tool that supports system prompt / agent instructions (Claude Code, Cursor, Aider, etc.) |
| [`prompt-template.md`](./prompt-template.md) / [`prompt-template_EN.md`](./prompt-template_EN.md) | 5 ready-to-use prompt templates (small / medium / large batch + comparison + automation) | Any chat interface (copy & paste) |
| [`skill.md`](./skill.md) / [`skill_EN.md`](./skill_EN.md) | WorkBuddy skill definition (YAML frontmatter + body) | WorkBuddy users |

## 30-second quickstart

### Option A · Prompt template (fastest)

1. Open [`prompt-template.md`](./prompt-template.md) (or the EN version)
2. Pick a template (A / B / C / D / E)
3. Replace `<USER_OR_URL>` with a real value
4. Paste the whole block into Claude / GPT / Cursor chat
5. Done

### Option B · Agent definition (for repeated use)

Save [`agent.md`](./agent.md) content as:
- **Claude Code** → `.claude/agents/github-stars-analyzer.md`
- **Cursor** → Settings → Rules for AI
- **Aider** → `--system-prompt agent.md`
- **WorkBuddy** → use the `Skill` tool to load [`skill.md`](./skill.md)
- **Cline / Continue** → project rules file

After that, in that project, just say "analyze https://github.com/xxx?tab=stars" and it will trigger.

### Option C · Full Python implementation (production)

```bash
git clone https://github.com/csxo/github-stars-analyzer
cd github-stars-analyzer
pip install -e .

cp .env.example .env  # fill in GITHUB_TOKEN + LLM_API_KEY
cp config/config.yaml.example config/config.yaml

gsa sync
gsa analyze --provider openai_compatible --model gpt-4o-mini
gsa report
```

Features: caching, resume, retry, GitHub Actions automation, SQLite storage.

## Output example

After a successful run the user gets:

```
data/output/
├── index.md             # master index (category overview)
├── categories/          # one page per category (sorted by value score)
│   ├── ai-llm.md
│   ├── 代理-网络工具.md
│   └── ...
└── repos/               # one detail page per repo
    ├── openai-gpt-oss.md
    └── ...
```

Every Markdown page works in GitHub / VSCode / Obsidian / any Markdown browser.

## Customization

When you need to extend:

| What you want to do | Where to change |
|---|---|
| Switch LLM provider (Anthropic / Gemini / Ollama) | Add a new Provider class in `src/github_stars_analyzer/analyzers/llm.py` |
| Add new analysis fields (e.g. license health) | Modify `models/repository.py::AnalysisResult` + bump `analysis_version` |
| Add embedding-based similarity | Add `embeddings` table + replace `output/similarity.py` |
| Add gh-pages auto-publish | Add `.github/workflows/deploy.yml` |
| Add a web UI | New `web/` subproject |

Full extension guide: [`EXTENDING.md`](../EXTENDING.md) in the main project.

## Related links

- Main project implementation: <https://github.com/csxo/github-stars-analyzer>
- Demo output (csxo's 678 stars): <https://github.com/csxo/csxo-stars-analysis>
- Architecture deep dive: [`ARCHITECTURE.md`](../ARCHITECTURE.md) in the main project

---

## 中英文版本对照

- 🇨🇳 中文版入口：[`README.md`](./README.md)
- 🇬🇧 English entry: this file (you are here)
- Both files share the same content, only the natural language differs