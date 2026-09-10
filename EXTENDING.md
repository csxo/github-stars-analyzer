# Extending the analyzer

The codebase is built around a handful of narrow seams. Each section below
shows the smallest possible change that delivers the feature.

## 1. Adding a new LLM provider

Create a class that satisfies the `LLMProvider` protocol in
`src/github_stars_analyzer/analyzers/llm.py`:

```python
class AnthropicProvider:
    name = "anthropic"

    async def __aenter__(self): ...
    async def __aexit__(self, *exc): ...
    async def complete(self, request: LLMRequest) -> str: ...
```

Then register it in `build_provider()` and add the literal to the
`LLMConfig.provider` `Literal[...]` union.

That's it — `Pipeline` and `RepoAnalyzer` need no changes.

## 2. Adding a new analysis schema field

1. Add the field to `models/repository.py::AnalysisResult`.
2. Update `SYSTEM_PROMPT` in `analyzers/analyzer.py` to describe it.
3. Bump `analysis_version` in `config/config.yaml`.

The cache will invalidate every analysis row in one shot (because
`analysis_version` is part of the cache key), and the next `gsa analyze`
will populate the new field everywhere.

## 3. Swapping in embedding-based similarity

`output/similarity.py` defines two functions used by the report
generator:

* `repo_keyset(repo, analysis)` — produce the comparison feature set.
* `find_similar(repo, analysis, pool, threshold, top_n)` — return ranked
  similar repos.

To switch to embeddings:

1. Add an `embeddings` optional dependency to `pyproject.toml`
   (already there: `numpy>=1.26`).
2. Extend the schema with an `embeddings` table keyed by repo_id.
3. Implement an `EmbeddingProvider` (e.g. via `sentence-transformers` or
   the LLM's embeddings endpoint).
4. Replace `repo_keyset` and `find_similar` with vector-based versions.
5. Update `MarkdownGenerator.render_all()` to call the new path.

`MarkdownGenerator` and the report layout don't need to change.

## 4. Pushing Markdown to a GitHub Pages branch

The GitHub Actions workflow already attaches Markdown as an artifact.
To publish automatically:

1. Add a deploy job that checks out `gh-pages`, copies `data/output/`
   into the branch root, and commits.
2. Configure Pages → deploy from branch → `gh-pages` / root.

A reference `deploy` job is in `EXTENDING.md` snippet below.

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

## 5. Adding a new pipeline phase

Phases are just coroutines registered in
`pipeline/orchestrator.py::Pipeline._dispatch`. To add `embed`:

1. Add the coroutine to `Pipeline`.
2. Register it in `_dispatch` and in `VALID_PHASES`.
3. Optionally add it to `PipelineConfig.phases` in `config.py`.
4. Update `cli.py` so users can `--phases collect,readme,analyze,embed`.

Phases get checkpoints for free via `_load_or_init_checkpoint()` and
`_update_checkpoint()`.

## 6. Running an LLM locally

* **Ollama** exposes an OpenAI-compatible endpoint at
  `http://localhost:11434/v1`. Set `LLM_BASE_URL` and use any model
  name (e.g. `qwen2.5-coder:7b`).
* **vLLM** is the same — any open model served via its OpenAI server.
* For very small models (≤3B), expect low-quality tags and
  `value_score`. The heuristic provider is usually more useful.

## 7. Multi-user stars

The DB is keyed on `id` (GitHub's global repo id) which is unique across
the entire GitHub instance — so the same repo starred by two users in
the same database would collide. To support multiple users:

* Add a `user` column to `repositories` and `analyses`.
* Make `repositories.UNIQUE(full_name)` → `UNIQUE(user, full_name)`.
* Update `analyzer.analyze_one` and `find_similar` to filter by user.

This is the most invasive of the listed extensions; the rest are
self-contained.

## 8. Talking to GitHub Enterprise

Set `github.base_url` to your enterprise host
(`https://github.acme.com/api/v3`):

```yaml
github:
  base_url: https://github.acme.com/api/v3
```

The client doesn't assume github.com anywhere except in error messages.

## 9. Cross-repo auto-sync (push `data/output/` → sister repo)

Once `gsa report` runs in CI, you usually want the generated Markdown to
end up in a public, read-only sister repository such as
`csxo/csxo-stars-analysis`. The repository ships with
`.github/workflows/sync-to-repo-b.yml` and `.github/scripts/sync_to_repo_b.py`
that do this via the Contents API — no `git push`, no PAT scope on Repo A.

Enable it in three steps:

1. **Generate a fine-grained PAT**

   Go to <https://github.com/settings/tokens?type=beta> and create a token
   with these settings:

   * Resource owner: **only** the account that owns the target repo
   * Repository access: **only** the target repo (e.g. `csxo/csxo-stars-analysis`)
   * Permissions → Repository → **Contents: Read and write**

   > Classic tokens are fine too, but a fine-grained token limits blast
   > radius if it ever leaks.

2. **Add it as a Secret on Repo A**

   `Repo A → Settings → Secrets and variables → Actions → New repository secret`

   * Name: `SYNC_PAT`
   * Value: the token from step 1

3. **(Optional) Override the target**

   The sync defaults to `csxo/csxo-stars-analysis`. To change it, set a
   *variable* (not a secret) called `SYNC_TARGET_REPO` in the same panel —
   e.g. `yourname/your-stars-analysis`.

That's it. The next time `Analyze Stars` completes, `Sync to Repo B`
runs automatically — and you can also trigger it manually from the
Actions tab.

### How the sync works

* `analyze-stars.yml` runs (now on a 6-hour schedule plus manual).
* On success, `sync-to-repo-b.yml` triggers via `workflow_run`.
* It downloads the `stars-report` artifact, then runs
  `python .github/scripts/sync_to_repo_b.py data/output <target>`.
* The script reads the **remote git tree** in one API call, computes the
  **git blob SHA** for every local file, and only `PUT`/`DELETE`s the
  diff. A typical week (a handful of new stars) produces 5–10 commits,
  not 700.
* The script auto-skips paths whose first segment matches
  `.git,LICENSE,LICENSE_CN.md,README.md,README_CN.md` so the hand-edited
  top-level files on Repo B are preserved.

### What about real-time star-event triggers?

GitHub Actions does not expose `star.created` / `star.deleted` events as
workflow triggers (they only fire on webhooks). Your options:

* **6-hour cron** (what's shipped — best effort / balance).
* **GitHub App** that subscribes to `star` events and calls
  `POST /repos/{o}/{r}/dispatches` → workflow with
  `on: repository_dispatch`.
* **Local cron** that runs `gsa sync && gsa analyze && gsa report` and
  then pushes `data/output/` to Repo B.

For most personal stars dashboards the built-in 6-hour cron is plenty.