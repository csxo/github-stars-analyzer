# Architecture

This document explains *why* the codebase is shaped the way it is.
Read it once before making non-trivial changes.

## Goals (and the constraints that drove the design)

1. **Incremental by default.** Re-running the pipeline on a 5,000-star
   account should be cheap — most operations must be no-ops when their
   inputs haven't changed.
2. **Resumable.** Anything longer than a few minutes must survive being
   killed mid-flight. Nothing is acceptable as "start over".
3. **Provider-agnostic.** Adding a new LLM vendor is one new file in
   `analyzers/`. Same for similarity algorithms.
4. **Bounded batches.** No phase should be capable of running for hours.
   Everything is parameterised by `batch_size` and `per_phase_limit`.
5. **Self-hostable.** A single binary entry point, zero background
   services, pure stdlib + 3 small deps.
6. **Reproducible.** Each `job_runs` row carries a config snapshot so a
   historical run can be replayed.

## Data model

```
repositories ──┐
   │            │
   │            ├─< analyses            (unique on repo_id, version, model)
   │            │
   │            └─< repo_categories >── categories
   │
   └─< checkpoints >── job_runs
```

* `repositories` is the canonical store. Rows are upserted by `id`, so a
  re-sync never duplicates — fields like `stargazers_count` and `topics`
  are overwritten; only `starred_at` is preserved if the new payload
  doesn't include one.
* `analyses` rows are keyed on `(repo_id, analysis_version, model)`.
  Together with the README hash carried alongside, this gives the cache
  invalidation policy in a single SQL query:

  ```sql
  SELECT 1 FROM analyses a
   WHERE a.repo_id = :rid
     AND a.analysis_version = :ver
     AND a.model = :model
     AND a.readme_hash = :hash
  ```

* `repo_categories` is the many-to-many table feeding the
  "similar projects" view. Tags from the LLM are inserted here, one row
  per tag, with `confidence=1.0`. The aggregation pipeline never writes
  this table directly — it only reads it.
* `job_runs` + `checkpoints` give us resume. Each `sync` / `analyze` /
  `report` invocation writes one `job_runs` row; every phase writes one
  `checkpoints` row capturing `(cursor, processed_ids, failed_ids,
  last_id)`.

## Pipeline phases

```
sync  →  collect    →  readme
analyze             →  analyze
report             →  report
```

Each phase is a self-contained coroutine in
`src/github_stars_analyzer/pipeline/orchestrator.py`. They share
*nothing* in-memory — every input comes from the DB, every output goes
back to the DB or the filesystem.

### `collect`

* Fetches `/users/{name}/starred` paginated.
* Batches upserts (100 rows / commit) for SQLite write efficiency.
* Honours GitHub's `x-ratelimit-*` headers and pre-emptively sleeps when
  the budget is empty.

### `readme`

* Reads `repositories WHERE has_readme=0 OR readme_hash IS NULL`.
* Fetches `GET /repos/{o}/{r}/readme` with the `application/vnd.github.raw`
  media type — saves us from base64 decoding.
* Computes SHA-256, writes to `data/cache/readme/xx/<hash>.md`, updates
  the DB row.

### `analyze`

* Reads `repositories JOIN analyses ... WHERE readme_hash != analyses.readme_hash`.
  That's the entire cache-invalidation logic — every row that comes out
  is one that genuinely needs new work.
* Concurrency-capped by `llm_concurrency`. Throughput is bounded by the
  slowest provider call; raise `GSA_LLM_CONCURRENCY` for faster networks.
* Each batch's processed IDs are flushed to a `checkpoints` row.

### `report`

* Reads everything from the DB.
* Generates `index.md`, `categories/*.md`, `repos/*.md`.
* Idempotent — running it again just overwrites files.

## Cache invalidation, in one diagram

```
                 bump analysis_version?
                 │ yes
                 ▼
        ┌────────────────────────┐
        │  every cached analysis │   ← invalidated; next run re-analyzes all
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

## Resume semantics

* `sync --no-resume` is the only way to force a re-run; without it,
  `_load_or_init_checkpoint()` will inherit the processed IDs from the
  most recent `job_runs` row of the same command, so a daily cron that's
  killed daily always continues from where it died.
* Checkpoints are append-only — failed IDs are tracked but not retried
  automatically; pass `--no-resume` to force a fresh attempt.
* `report` deliberately has no checkpoint: rendering is idempotent and
  fast.

## Failure model

| Failure | Behavior |
| --- | --- |
| Single repo's README returns 404 | stored with `readme_hash=""`; `analyze` skips it |
| Single repo's LLM response unparseable | logged, raw response persisted for forensics, run continues |
| Single repo's LLM call 429s | retried with backoff inside `OpenAICompatibleProvider` |
| Network down for 30 s | `retry_async` backs off, then continues |
| Process killed | next invocation resumes from the last `checkpoints` row |
| Whole-provider outage | run finishes, failed IDs captured in `checkpoints.failed_ids` |

## Why SQLite?

* Single file → trivial to back up, copy, inspect.
* WAL mode → concurrent readers don't block the writer.
* JSON columns → no schema migrations when we add a field.
* `sqlite3` is in stdlib → zero runtime deps for the storage layer.

## What we deliberately didn't build

* A web UI. Markdown files in a `docs/` branch are easier to browse,
  diff, and search than another React app.
* A daemon / scheduler. Cron + Actions is good enough; the pipeline is
  idempotent so re-runs are safe.
* Embedding-based similarity. Jaccard on tags/topics is the first cut —
  good enough to start; trivial to swap later (see `EXTENDING.md`).

## Directory layout

```
src/github_stars_analyzer/
├── __init__.py
├── cli.py              # click commands
├── config.py           # layered config loader
├── analyzers/
│   ├── llm.py          # provider abstraction + 3 implementations
│   └── analyzer.py     # prompt, parsing, cache write
├── collectors/
│   ├── github_api.py   # async REST client
│   └── stars.py        # high-level collect_stars / collect_readmes
├── models/
│   ├── repository.py
│   ├── category.py
│   └── job_run.py
├── output/
│   ├── markdown.py     # the site generator
│   ├── similarity.py   # Jaccard helpers
│   └── i18n.py
├── pipeline/
│   ├── orchestrator.py # Pipeline + phases
│   └── resume.py       # checkpoint merge helpers
├── storage/
│   ├── database.py     # SQLite + schema
│   └── cache.py        # content-addressed filesystem cache
└── utils/
    ├── logger.py
    ├── hash.py
    └── retry.py
```