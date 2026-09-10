# Prompt 模板：分析 GitHub Stars

> **用法**：把下面 `<>` 替换成实际值，复制整段到 Claude / GPT / Cursor / Aider / WorkBuddy 等任何 chat AI 即可。

---

## 模板 A · 全自动（推荐：≤ 100 stars）

```
分析这个 GitHub 用户的公开 stars：<USER_OR_URL>

具体步骤：
1. 用 GitHub REST API 抓取 https://api.github.com/users/<USER>/starred?per_page=30&page=N
   翻页直到响应为空数组。每页最多 30 个 repo（避免响应被截断）。
2. 对每个 repo 提取：full_name、language、stargazers_count、description、topics。
3. 用启发式分类（见下方分类表）给每个 repo 标一个 primary_category + 2-4 个 tags。
4. 用 star 数 + topics 密度 + description 是否非空 算一个 0-10 的 value_score。
5. 用 tags + topics + language 的 Jaccard 相似度，找每个 repo 最相关的 5 个相似项目。
6. 生成三套 Markdown 文件到 ./data/output/：
   - index.md（分类索引表）
   - categories/<slug>.md（每个分类一个，按 value_score 降序排）
   - repos/<owner>-<repo>.md（每个 repo 一个详情页，含基本信息 + tags + 相似项目）
7. 完成后告诉我：共多少 stars、多少分类、生成多少文件、放在哪个路径。

启发式分类表：
- AI / LLM：chatgpt, llm, gpt, agent, rag, prompt, langchain, ollama, llama
- 代理 / 网络工具：clash, surge, shadowsocks, v2ray, quantumult, proxy, tunnel
- 媒体 / 播放器：player, mpv, vlc, music, spotify, youtube-dl, yt-dlp, download, podcast
- 桌面工具 / Windows 优化：windows, tweak, registry, debloat, powertoys, sysmon
- 浏览器 / 扩展：browser, extension, userscript, tampermonkey, firefox, chrome
- RSS / 阅读：rss, reader, feed, inoreader, netnewswire, feedly
- 输入法 / Rime：rime, squirrel, pinyin, input, keyboard, typing
- Awesome 列表：awesome, awesome-*, list, curated
- 容器 / 编排：docker, kubernetes, k8s, podman
- 编辑器 / IDE：vim, neovim, helix, emacs, ide, vscode, jetbrains
- 游戏 / 开发引擎：game, unity, godot, minecraft
- 区块链 / Web3：blockchain, bitcoin, web3, solidity, ethereum
- 编程语言 / 库：python, rust, go, typescript, swift, kotlin
- 其它：misc

约束：
- 用 WebFetch 而非浏览器自动化（GitHub API 更可靠）
- 单 repo 失败不阻塞，记录到 failed_ids 后续重试
- 中文分类文件名用 slug（保留 Unicode）：s.replace 模式用 [^\w]+ + re.UNICODE
```

---

## 模板 B · 深度分析（带 LLM key，100-1000 stars）

```
深度分析这个 GitHub 用户的公开 stars：<USER_OR_URL>

具体步骤：
1. Phase 1 - 元数据抓取：用 GitHub API 拉所有 stars（按页翻），保存为 stars.json
2. Phase 2 - README 抓取：对每个 repo 用 GITHUB_TOKEN 调 raw.githubusercontent.com 拿 README.md
   （每个 repo 一个 HTTP 请求，需要 5000 req/h 配额，匿名 60 req/h 不够）
3. Phase 3 - LLM 分析：对每个 repo 用如下 prompt 调 LLM（OpenAI 兼容接口）：
   "你是 GitHub 项目分析师。给定 README + topics + description，输出 JSON：
    {
      'summary': '一句话说明项目解决什么问题（≤60字）',
      'features': ['feature 1', 'feature 2', ...],
      'capabilities': ['能做什么 1', '能做什么 2', ...],
      'use_cases': ['场景 1', '场景 2', ...],
      'tech_stack': ['Python', 'FastAPI', ...],
      'tags': ['python', 'cli', 'productivity'],
      'value_score': 7.5  // 0-10
    }"
4. Phase 4 - 分类聚合：用 LLM 返回的 tags 做 Jaccard 相似度聚类，按主标签聚合
5. Phase 5 - 渲染 Markdown：生成 index.md + categories/*.md + repos/*.md

缓存策略：每个 repo 用 (README hash × analysis_version × model) 作为缓存 key
断点续跑：每个 phase 独立 checkpoint，下次可继续
LLM 配置：<LLM_BASE_URL> + <LLM_API_KEY> + <LLM_MODEL>

完成后输出：共多少 stars、哪些分析失败、生成文件路径
```

---

## 模板 C · 一次性快速看（无 LLM，200+ stars）

```
快速看一份 GitHub stars 概览：<USER_OR_URL>

只做元数据分析，不读 README：
1. 用 GitHub API 拉 stars 列表（full_name, language, stars, description, topics）
2. 按 description + topics 关键词做粗分类（约 20 个分类）
3. 渲染一个 index.md（分类索引）+ categories/*.md（每个分类一页）
5. repos/<owner>-<repo>.md 只包含基本信息（不写 features/capabilities）

用途：让我快速浏览 ~700 个 star 项目的整体偏好，精度要求不高

完成后告诉我：
- 用户 star 数量
- 分类分布（哪些分类最多）
- Top 10 star 数最多的 repo
```

---

## 模板 D · 对比两个用户

```
对比这两个 GitHub 用户的 stars 偏好差异：<USER_A> vs <USER_B>

分别对两个用户跑模板 A，得到两份 stars 知识库。
然后：
1. 找出两个用户都 star 的交集（共同喜好）
2. 找出只在 A 里 star 的项目（独有偏好）
3. 找出只在 B 里 star 的项目
4. 按分类对比两个用户在每个分类的 star 数量

输出一份对比报告到 ./data/output/comparison.md
```

---

## 模板 E · 长期自动化（GitHub Actions）

```
我想让这个分析每天自动跑一次：
1. clone https://github.com/csxo/github-stars-analyzer 到你的工作目录
2. 编辑 .github/workflows/analyze-stars.yml，把 cron 改成你想要的频率
3. 在 GitHub repo Settings → Secrets 里加 GITHUB_TOKEN + LLM_API_KEY
4. 每天 workflow 会自动 sync + analyze + report
5. 把生成的 Markdown 通过 git pull 到你本地浏览

如果你想部署成网页，加一个 gh-pages 工作流：
- 跑完后 git push 到 gh-pages 分支
- GitHub Pages 自动发布
```

---

## 常见边界处理

### 匿名 API 限流（403）

```
我遇到了 403 rate limit。处理方式：
1. 等 1 小时重试
2. 或用户配置 GITHUB_TOKEN（提升到 5000 req/h）
3. 或切到只用 description 的简化模式（不需要 token）
```

### 用户有几千个 stars

```
stars 数量超过 1000，匿名 API 60 req/h 不够读 README。
建议：
- 仍然跑元数据抓取（每页 100 个，约 10 页 = 10 次 API 调用）
- README 分析放到本地 CLI 跑（csxo/github-stars-analyzer）
```

### 分类名是中文

```
Markdown 文件名必须用 ASCII slug 但保留中文：
python:
import re
slug = re.sub(r'[^\w]+', '-', name, flags=re.UNICODE).strip('-') or 'misc'

不能用：[^\w]+  # 会把中文全 strip
不能用：[^a-z0-9]+  # 同上
正确：[^\w]+ + flags=re.UNICODE
```

---

把任意一个模板复制粘贴给 chat AI，按提示替换 `<USER_OR_URL>` 等占位符即可启动。不需要装 Python 或 WorkBuddy。