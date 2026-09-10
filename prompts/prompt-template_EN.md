# Prompt Template: Analyze GitHub Stars

> **Usage**: replace `<>` placeholders with real values, copy the whole block into Claude / GPT / Cursor / Aider / WorkBuddy — any chat AI.

---

## Template A · Fully automatic (recommended: ≤ 100 stars)

```
Analyze this GitHub user's public stars: <USER_OR_URL>

Steps:
1. Use GitHub REST API to fetch https://api.github.com/users/<USER>/starred?per_page=30&page=N
   Paginate until the response is an empty array. Max 30 per page (avoid response truncation).
2. For each repo, extract: full_name, language, stargazers_count, description, topics.
3. Use heuristic classification (see table below) to assign each repo a primary_category + 2-4 tags.
4. Compute a 0-10 value_score from stars count + topics density + whether description is non-empty.
5. Use tags + topics + language Jaccard similarity to find the top 5 most similar projects per repo.
6. Generate three sets of Markdown files under ./data/output/:
   - index.md (category index table)
   - categories/<slug>.md (one per category, sorted by value_score desc)
   - repos/<owner>-<repo>.md (one detail page per repo, with basic info + tags + similar projects)
7. When done, report: total stars, total categories, total files generated, and the output path.

Heuristic classification table:
- AI / LLM: chatgpt, llm, gpt, agent, rag, prompt, langchain, ollama, llama
- Proxy / Network Tools: clash, surge, shadowsocks, v2ray, quantumult, proxy, tunnel
- Media / Player: player, mpv, vlc, music, spotify, youtube-dl, yt-dlp, download, podcast
- Desktop Tools / Windows Optimization: windows, tweak, registry, debloat, powertoys, sysmon
- Browser / Extensions: browser, extension, userscript, tampermonkey, firefox, chrome
- RSS / Reader: rss, reader, feed, inoreader, netnewswire, feedly
- Input Method / Rime: rime, squirrel, pinyin, input, keyboard, typing
- Awesome Lists: awesome, awesome-*, list, curated
- Containers / Orchestration: docker, kubernetes, k8s, podman
- Editor / IDE: vim, neovim, helix, emacs, ide, vscode, jetbrains
- Game / Dev Engine: game, unity, godot, minecraft
- Blockchain / Web3: blockchain, bitcoin, web3, solidity, ethereum
- Language / Library: python, rust, go, typescript, swift, kotlin
- Other: misc

Constraints:
- Use WebFetch rather than browser automation (GitHub API is more reliable)
- Single repo failure does not block; record to failed_ids for later retry
- CJK category filenames must use slug (preserve Unicode): pattern [^\w]+ with flags=re.UNICODE
```

---

## Template B · Deep analysis (with LLM key, 100-1000 stars)

```
Deep-analyze this GitHub user's public stars: <USER_OR_URL>

Steps:
1. Phase 1 - Metadata fetch: pull all stars via GitHub API (paginated), save as stars.json
2. Phase 2 - README fetch: for each repo, use GITHUB_TOKEN to fetch README.md from raw.githubusercontent.com
   (one HTTP request per repo; needs the 5000 req/h quota, anonymous 60 req/h is insufficient)
3. Phase 3 - LLM analysis: for each repo, call the LLM with this prompt (OpenAI-compatible interface):
   "You are a GitHub project analyst. Given README + topics + description, output JSON:
    {
      'summary': 'one-sentence description of what the project solves (≤60 chars)',
      'features': ['feature 1', 'feature 2', ...],
      'capabilities': ['capability 1', 'capability 2', ...],
      'use_cases': ['scenario 1', 'scenario 2', ...],
      'tech_stack': ['Python', 'FastAPI', ...],
      'tags': ['python', 'cli', 'productivity'],
      'value_score': 7.5  // 0-10
    }"
4. Phase 4 - Categorize & aggregate: use LLM-returned tags to compute Jaccard similarity and cluster by primary tag
5. Phase 5 - Render Markdown: generate index.md + categories/*.md + repos/*.md

Cache strategy: each repo uses (README hash × analysis_version × model) as cache key
Resumable: each phase has its own checkpoint, can resume next time
LLM config: <LLM_BASE_URL> + <LLM_API_KEY> + <LLM_MODEL>

When done, report: total stars, which analyses failed, output file paths
```

---

## Template C · Quick look (no LLM, 200+ stars)

```
Quick overview of a GitHub stars list: <USER_OR_URL>

Metadata-only analysis, do not read READMEs:
1. Pull stars list via GitHub API (full_name, language, stars, description, topics)
2. Coarse classification by description + topics keywords (~20 categories)
3. Render one index.md (category index) + categories/*.md (one page per category)
4. repos/<owner>-<repo>.md contains only basic info (no features/capabilities)

Purpose: let me quickly browse the overall preference profile of ~700 starred projects; precision is not critical

When done, tell me:
- Total star count
- Category distribution (which categories have the most)
- Top 10 by star count
```

---

## Template D · Compare two users

```
Compare stars preference differences between these two GitHub users: <USER_A> vs <USER_B>

Run template A on each user to produce two stars knowledge bases.
Then:
1. Find the intersection (shared interests)
2. Find repos only A has starred (unique to A)
3. Find repos only B has starred
4. For each category, compare the star counts between the two users

Output a comparison report to ./data/output/comparison.md
```

---

## Template E · Long-term automation (GitHub Actions)

```
I want this analysis to run automatically every day:
1. clone https://github.com/csxo/github-stars-analyzer into your working directory
2. Edit .github/workflows/analyze-stars.yml, change the cron to your preferred frequency
3. In GitHub repo Settings → Secrets, add GITHUB_TOKEN + LLM_API_KEY
4. Each day the workflow will auto-run sync + analyze + report
5. Pull the generated Markdown to your local machine to browse

If you want to deploy as a website, add a gh-pages workflow:
- After the run, git push to the gh-pages branch
- GitHub Pages will auto-publish
```

---

## Common boundary handling

### Anonymous API rate limit (403)

```
I hit a 403 rate limit. How to handle:
1. Wait 1 hour and retry
2. Or have the user configure GITHUB_TOKEN (raises limit to 5000 req/h)
3. Or switch to a simplified mode that uses only description (no token required)
```

### User has thousands of stars

```
Stars count exceeds 1000; anonymous API at 60 req/h can't read READMEs.
Suggestion:
- Still run the metadata fetch (each page 100, about 10 pages = 10 API calls)
- README analysis goes to the local CLI (csxo/github-stars-analyzer)
```

### Category names are CJK

```
Markdown filenames must use ASCII slug but preserve CJK characters:
python:
import re
slug = re.sub(r'[^\w]+', '-', name, flags=re.UNICODE).strip('-') or 'misc'

Do NOT use: [^\w]+  # strips all CJK
Do NOT use: [^a-z0-9]+  # same problem
Correct:    [^\w]+ with flags=re.UNICODE
```

---

Copy any template, replace `<USER_OR_URL>` placeholders, and start. No Python or WorkBuddy installation needed.