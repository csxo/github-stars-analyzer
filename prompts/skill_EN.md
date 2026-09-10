---
name: GitHub Stars Analyzer
version: 1.0.0
description: >
  Auto-analyze a given GitHub user's stars: fetch public stars, aggregate by category, generate
  a structured Markdown knowledge base with caching, resume, retry, CLI, and GitHub Actions
  automation. Trigger when the user shares a GitHub stars URL (their own or someone else's)
  and wants a browsable categorized Markdown dump, or wants LLM-powered per-repo deep analysis.
  Triggers: "analyze my GitHub stars", "organize GitHub star list", "star classification into
  Markdown", "batch-analyze GitHub starred repos", "analyze someone's GitHub stars",
  "github stars analyzer", "stars knowledge base".
  Not for: private repos (login required), non-GitHub platforms, single-repo detail pages
  (use generic README analysis), real-time refresh requirements (this is a batch tool).
description_en: >
  Fetch a GitHub user's public stars, classify them, and render a structured Markdown
  knowledge base with caching, resume, retry, CLI, and GitHub Actions automation. Trigger
  when the user shares a GitHub stars URL and wants a browsable categorized Markdown dump,
  or wants LLM-powered per-repo deep analysis. Not for private repos (login required),
  non-GitHub platforms, single-repo detail pages, or real-time requirements.
---

# GitHub Stars Analyzer Skill (English)

This Skill turns "GitHub stars lists" into "structured Markdown knowledge bases". It includes a **directly runnable CLI implementation** (Python package) and **prompt templates for any agentic tool**.

## When to use this skill

Triggers (any one):

- User shares a GitHub stars URL (own or someone else's public account), wants to organize it into browsable documents
- User says "analyze my GitHub stars / organize my star list / batch-analyze starred projects"
- User wants to do category aggregation / same-domain comparison / LLM deep analysis / Markdown output for all starred projects
- User wants to set up a long-running GitHub stars automation pipeline

Not applicable:

- Private stars (login required; this tool uses anonymous API)
- Single-repo detail deep dive (use generic README analysis instead)
- Real-time sync (this is a batch tool, min granularity is minutes)

## Usage modes

Three ways to use this Skill, choose based on the user's tooling stack:

### Mode A · Direct WorkBuddy invocation (fastest)

WorkBuddy can execute directly in the current session:

1. Receive the user's GitHub stars URL (must be public)
2. Use GitHub REST API anonymously to fetch stars (per_page=100, but 30 to avoid truncation)
3. For each repo: fetch metadata + topics + description + language
4. Call the built-in heuristic classifier (no LLM key required) or optional LLM deep analysis
5. Render Markdown: 1 `index.md` + N `categories/*.md` + M `repos/*.md`

Output location: `data/output/` subdirectory under the user's workspace.

⚠️ **Anonymous API limit**: 60 req/h. Metadata for ~700 stars fits (700/100=7 pages), but **cannot** read all READMEs in one session — README fetch requires authenticated access.

### Mode B · User's local CLI (recommended for production)

User clones `csxo/github-stars-analyzer` then:

```bash
pip install -e .
cp .env.example .env  # fill in GITHUB_TOKEN (raises to 5000 req/h) + optional LLM_API_KEY
cp config/config.yaml.example config/config.yaml

# Zero-key run-through (heuristic provider uses metadata as fallback)
gsa sync --provider heuristic
gsa analyze --provider heuristic --model offline
gsa report

# With OpenAI / DeepSeek / any OpenAI-compatible service
gsa sync
gsa analyze --provider openai_compatible --model gpt-4o-mini
gsa report
```

Features:
- ✅ Three-axis cache key: `README hash × analysis_version × model`
- ✅ 4-phase pipeline (collect/readme/analyze/report), each phase has its own checkpoint
- ✅ Hard batch limits (`batch_size` + `per_phase_limit`)
- ✅ SQLite + WAL, single-file storage
- ✅ GitHub Actions workflow (schedule + workflow_dispatch)
- ✅ Soft failure: a single repo failure does not block the batch

### Mode C · Copy-paste prompts (any chat agent)

Copy `prompts/prompt-template.md` content into Claude / GPT / Cursor / Aider — any chat interface. This mode is for: users without Python / without WorkBuddy / one-time casual use.

## Key design decisions (see ARCHITECTURE.md for details)

| Decision | Rationale |
|---|---|
| SQLite over Postgres | Single file, zero runtime deps, Actions cache friendly |
| Content-addressable cache (README hash) | Auto-invalidate analysis when README changes |
| `analysis_version` field | Prompt template upgrade invalidates the entire cache in one go |
| 4-phase pipeline | collect / readme / analyze / report decoupled; resume is natural |
| Heuristic fallback | End-to-end works without API key, easy to debug and zero-cost |
| Markdown over JSON | Human-readable, GitHub renders directly, version-control friendly |

## Reuse guide

To fork this project for others:

1. **Fastest (30 seconds)**: copy `prompts/prompt-template.md` into any chat AI, ask per the template
2. **Production (10 minutes)**: clone `csxo/github-stars-analyzer`, install deps, fill token, `gsa sync && gsa analyze && gsa report`
3. **Fully customized**: reference `prompts/agent.md` and write your own agent loop

## File inventory

```
prompts/
├── README.md          # this directory's guide (CN)
├── README_EN.md       # this directory's guide (EN)
├── agent.md           # agent definition for any agentic tool (CN)
├── agent_EN.md        # agent definition (EN)
├── prompt-template.md # copy-paste prompt templates (CN)
├── prompt-template_EN.md # copy-paste templates (EN)
├── skill.md           # WorkBuddy skill definition (CN)
└── skill_EN.md        # WorkBuddy skill definition (EN)

src/github_stars_analyzer/  # full Python implementation
├── collectors/  # GitHub API + README fetch
├── analyzers/   # LLM Provider + heuristic classification
├── storage/     # SQLite + content cache
├── pipeline/    # 4-phase orchestration + checkpoint
├── output/      # Markdown rendering
├── cli.py       # click CLI
└── config.py    # layered configuration

tests/            # 15 unit tests
.github/workflows/analyze-stars.yml  # GitHub Actions
```

## Known boundaries

- Anonymous API 60 req/h → reading READMEs requires `GITHUB_TOKEN`
- GitHub REST API: max 100 repos per page
- Heuristic classification accuracy ~70% (based on description + topics + language), LLM mode reaches 90%+
- Same user stars > 5000 recommend batched processing

## Reference links

- Full implementation: <https://github.com/csxo/github-stars-analyzer>
- Demo output: <https://github.com/csxo/csxo-stars-analysis>
- Architecture deep dive: `ARCHITECTURE.md` in this repo
- Extension guide: `EXTENDING.md` in this repo