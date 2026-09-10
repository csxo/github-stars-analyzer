# GitHub Stars Analyzer

> A self-hosted, batch-friendly pipeline that turns your GitHub stars into a structured Markdown knowledge base — with caching, resume, retries, and a turnkey GitHub Actions schedule.

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)]() [![License](https://img.shields.io/badge/license-MIT-green)]() [![Stars](https://img.shields.io/github/stars/csxo/github-stars-analyzer)](https://github.com/csxo/github-stars-analyzer)

## 📌 Links

- Source: <https://github.com/csxo/github-stars-analyzer>
- Docs: `ARCHITECTURE.md` · `EXTENDING.md`
- Companion example (analyzed stars of user `csxo`): <https://github.com/csxo/csxo-stars-analysis>

## ✨ What it solves

Once your GitHub stars climb past a few dozen, the browser bookmark list becomes useless. This tool turns stars into a **searchable structured knowledge base**:

- 🔄 **Auto-collect** — `gsa sync` pulls starred repos + READMEs
- 🤖 **LLM deep analysis** — summary, features, capabilities, use cases, tech stack, value score, multi-tag classification
- 🏷️ **Auto-categorize & aggregate** — same-domain repos land on one page; compare side-by-side; quickly decide what to un-star
- 📝 **Markdown output** — index page + category pages + per-repo detail pages; readable in Obsidian / VS Code / GitHub
- 💾 **Three-axis cache** — invalidated by any of `README hash × analysis version × model`; never re-analyses unchanged content
- ⏸️ **Resumable** — each phase writes checkpoints; kill it mid-run, pick up where you left off
- 🔁 **Retry + per-item fault tolerance** — one bad repo never blocks the batch
- 🤖 **GitHub Actions ready** — weekly cron, manual dispatch, artifact upload

## 🏗️ Architecture at a glance

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

Every phase is **independently runnable**, **independently restartable**, and **independently resumable**. See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the design deep-dive.

## 📦 Quick start

### 1. Install

```bash
git clone https://github.com/csxo/github-stars-analyzer.git
cd github-stars-analyzer
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
# Required: GITHUB_TOKEN (scope: public_repo + read:user)

cp config/config.yaml.example config/config.yaml
```

Bare-minimum config — no LLM key needed:

```yaml
llm:
  provider: heuristic   # rule + metadata fallback
  model: offline
github:
  user: YOUR_GITHUB_USERNAME   # replace with your own GitHub handle before running
```

> For a real LLM, set `provider: openai_compatible` and `LLM_API_KEY` (or `OPENAI_API_KEY`). DeepSeek / Qwen / OpenRouter / Ollama all speak the OpenAI protocol.

### 3. Run

```bash
# Full pipeline (first run, batched, interruptible)
gsa sync

# Just regenerate Markdown (no LLM calls)
gsa report

# Progress check
gsa status

# Incremental — run weekly; already-analyzed repos auto-skipped
gsa sync                   # incremental collect
gsa analyze                # only new / README-changed repos
```

Full CLI:

| Command | Purpose |
| --- | --- |
| `gsa sync [--user NAME] [--phases collect,readme]` | Fetch stars + READMEs |
| `gsa analyze [--provider X] [--model Y] [--limit N]` | LLM analysis |
| `gsa report [--output DIR]` | Render Markdown |
| `gsa status` | DB / cache / last job summary |
| `gsa clean [--orphans] [--older-than 30d]` | Drop orphan cache & old jobs |

## 📂 What the output looks like

```
data/output/
├── index.md                       # master index + 26 category panels
├── categories/
│   ├── ai-llm.md                  # 76 AI / LLM projects
│   ├── 代理-网络工具.md            # 65 proxy / rule tools (中文)
│   ├── 媒体-播放器.md             # 55 media / player tools
│   └── ... (26 categories)
└── repos/
    └── <owner>-<repo>.md          # 678 per-repo pages (README digest + score + similar)
```

## 🤖 GitHub Actions automation

The shipped `.github/workflows/analyze-stars.yml`:

- ⏰ Weekly at Mon 04:00 UTC
- 🖱️ `workflow_dispatch` with `--user`, `--provider`, `--model`, `--analyze-limit` overrides
- 💾 Caches `pip` and the `data/` tree so README downloads don't repeat
- 📤 Uploads the report as a workflow artifact; trivial to extend for gh-pages
- 🔒 `concurrency` group serializes runs to avoid SQLite WAL contention

## 🧱 Tech stack

- **Python 3.11+** · SQLite (WAL) · asyncio · httpx
- LLM via **OpenAI-compatible** protocol (swap in Anthropic / Gemini / Ollama trivially)
- Fully async, local-first, zero cloud deps aside from GitHub API + your LLM endpoint

## 📚 Documentation

- 📘 [`ARCHITECTURE.md`](./ARCHITECTURE.md) — system design, data model, phase orchestration, key decisions
- 🛠 [`EXTENDING.md`](./EXTENDING.md) — how to add a new LLM, new fields, change the similarity algorithm, etc.


## 📄 License

MIT
