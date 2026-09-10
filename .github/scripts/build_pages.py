#!/usr/bin/env python3
"""Build a static GitHub Pages site from prompts/ + top-level README files.

Produces:
    site/
    ├── index.html              # project landing (CN)
    ├── index_en.html           # project landing (EN)
    ├── prompts/
    │   ├── index.html          # prompts directory listing
    │   ├── README.html         # prompts/README.md rendered
    │   ├── README_EN.html
    │   ├── agent.html
    │   ├── agent_EN.html
    │   └── ...

The deployed URL (after GitHub Pages is enabled) will be:
    https://<user>.github.io/github-stars-analyzer/

Run from repo root:
    python .github/scripts/build_pages.py
"""
from __future__ import annotations

import shutil
import sys
from html import escape
from pathlib import Path
import re
import textwrap

try:
    import markdown
    from markdown.extensions.codehilite import CodeHiliteExtension
    from markdown.extensions.toc import TocExtension
except ImportError:
    print("ERROR: this script requires `markdown` and `pygments` packages.", file=sys.stderr)
    print("Install with: pip install markdown pygments", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_PROM = REPO_ROOT / "prompts"
OUT = REPO_ROOT / "site"

LANGS = [
    ("zh", "中文", "🇨🇳"),
    ("en", "English", "🇬🇧"),
]


# ----------------------------------------------------------------------------
# Markdown → HTML
# ----------------------------------------------------------------------------

_md = markdown.Markdown(
    extensions=[
        "fenced_code",
        "tables",
        "toc",
        CodeHiliteExtension(guess_lang=False, css_class="highlight"),
        "nl2br",
    ],
    extension_configs={"toc": {"permalink": False}},
)


def render_md(md_text: str) -> str:
    """Convert Markdown to HTML body fragment (no <html> wrapper)."""
    return _md.convert(md_text)


# ----------------------------------------------------------------------------
# HTML template
# ----------------------------------------------------------------------------

PAGE_TEMPLATE = textwrap.dedent("""\
<!DOCTYPE html>
<html lang="{lang_code}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/[email protected]/dist/css/github-markdown.min.css">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; margin: 0; }}
  .page {{ max-width: 980px; margin: 0 auto; padding: 24px; }}
  .topbar {{
    background: #f6f8fa; border-bottom: 1px solid #d0d7de;
    padding: 8px 16px; display: flex; justify-content: space-between; align-items: center;
    font-size: 14px;
  }}
  .topbar a {{ color: #0969da; text-decoration: none; }}
  .topbar a:hover {{ text-decoration: underline; }}
  .lang-switch a {{ margin-left: 12px; }}
  .lang-switch a.active {{ font-weight: 600; color: #24292f; }}
  .markdown-body {{ background: #fff; padding: 32px; border: 1px solid #d0d7de; border-radius: 6px; }}
  .markdown-body h1 {{ border-bottom: 1px solid #d0d7de; padding-bottom: 8px; }}
  .markdown-body pre {{ background: #f6f8fa; padding: 16px; border-radius: 6px; overflow: auto; }}
  .markdown-body code {{ background: rgba(175,184,193,0.2); padding: 0.2em 0.4em; border-radius: 3px; }}
  .markdown-body table {{ border-collapse: collapse; }}
  .markdown-body table th, .markdown-body table td {{ border: 1px solid #d0d7de; padding: 6px 13px; }}
  .markdown-body table tr:nth-child(2n) {{ background: #f6f8fa; }}
  footer {{ text-align: center; color: 0; color: #6e7781; padding: 24px; font-size: 12px; }}
  footer a {{ color: #0969da; }}
</style>
</head>
<body>
  <div class="topbar">
    <div>📚 <a href="{home_href}">{home_label}</a></div>
    <div class="lang-switch">{lang_switch_html}</div>
  </div>
  <div class="page">
    <article class="markdown-body">
{body}
    </article>
  </div>
  <footer>
    {footer_html}
  </footer>
</body>
</html>
""")


def lang_switch(current_code: str, items: list[tuple[str, str, str, str | None]]) -> str:
    """Render language switcher.

    items: list of (code, label, flag, href) tuples; href may be None for current.
    """
    parts = []
    for code, label, flag, href in items:
        active = " active" if code == current_code else ""
        if href:
            parts.append(f'<a class="{active.strip()}" href="{escape(href)}">{flag} {label}</a>')
        else:
            parts.append(f'<span class="active">{flag} {label}</span>')
    return " · ".join(parts)


def make_page(
    *,
    title: str,
    body_html: str,
    lang_code: str,
    lang_switch_items,
    home_href: str = "../",
    home_label: str = "GitHub Stars Analyzer",
    footer_label: str = "Project",
) -> str:
    return PAGE_TEMPLATE.format(
        lang_code=lang_code,
        title=escape(title),
        body=body_html,
        lang_switch_html=lang_switch(lang_code, lang_switch_items),
        home_href=home_href,
        home_label=escape(home_label),
        footer_html=escape(footer_label),
    )


# ----------------------------------------------------------------------------
# Content mapping
# ----------------------------------------------------------------------------

def build_lang_switch(items: list[tuple[str, str]]) -> list[tuple[str, str, str, str | None]]:
    """items: list of (code, href) pairs"""
    out = []
    for code, href in items:
        label = next(l for c, l, _ in LANGS if c == code)
        flag = next(f for c, _, f in LANGS if c == code)
        out.append((code, label, flag, href))
    return out


def pair_href(local_path: str) -> list[tuple[str, str]]:
    """Given local path like 'README.md', return [(zh, 'README.md'), (en, 'README_EN.md')]."""
    base = Path(local_path).stem
    suffix = Path(local_path).suffix
    zh = f"{base}{suffix}"
    en = f"{base}_EN{suffix}"
    # Adjust for README (the EN version is the default at GitHub top-level, so at prompts/ level it's README_EN.md)
    return [("zh", zh), ("en", en)]


# ----------------------------------------------------------------------------
# Build steps
# ----------------------------------------------------------------------------

def resolve_pair(md_path: Path) -> tuple[Path | None, Path | None]:
    """Given a .md file, return (zh_path, en_path). One may be None.

    Supports both naming conventions used in this repo:
      - prompts/:   X.md is CN,   X_EN.md is EN
      - top-level:  X.md is EN,   X_CN.md is CN
    """
    base = md_path.stem
    suffix = md_path.suffix
    if base.endswith("_EN"):
        return (md_path.with_name(f"{base[:-3]}{suffix}"), md_path)
    elif base.endswith("_CN"):
        return (md_path, md_path.with_name(f"{base[:-3]}{suffix}"))
    else:
        en_twin = md_path.with_name(f"{base}_EN{suffix}")
        cn_twin = md_path.with_name(f"{base}_CN{suffix}")
        if en_twin.exists():
            return (md_path, en_twin)
        elif cn_twin.exists():
            return (cn_twin, md_path)
        else:
            return (md_path, None)


def _to_html(md_name: str) -> str:
    return Path(md_name).stem + ".html"


def render_pair_to_files(zh_path: Path | None, en_path: Path | None, *, depth: int) -> None:
    """Render a (zh, en) pair to OUT/. Either side may be None for one-language files.

    Output filenames are derived from the actual zh_path / en_path stems
    (so X_CN.md -> X_CN.html, X.md -> X.html, etc.). Language switch
    links point at the .html siblings, not back to the .md source.
    """
    if not zh_path and not en_path:
        return
    src = zh_path or en_path
    title = src.stem.replace("_", " ").title()
    rel_prefix = "../" * depth

    zh_html_name = _to_html(zh_path.name) if zh_path and zh_path.exists() else None
    en_html_name = _to_html(en_path.name) if en_path and en_path.exists() else None

    if zh_path and zh_path.exists():
        zh_text = zh_path.read_text(encoding="utf-8")
        items = [("zh", zh_html_name)]
        if en_html_name:
            items.append(("en", en_html_name))
        zh_html = make_page(
            title=title,
            body_html=render_md(zh_text),
            lang_code="zh",
            lang_switch_items=build_lang_switch(items),
            home_href=rel_prefix if rel_prefix else "./",
        )
        out = OUT / zh_html_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(zh_html, encoding="utf-8")
        print(f"  wrote site/{zh_html_name}")

    if en_path and en_path.exists():
        en_text = en_path.read_text(encoding="utf-8")
        items = []
        if zh_html_name:
            items.append(("zh", zh_html_name))
        items.append(("en", en_html_name))
        en_html = make_page(
            title=title,
            body_html=render_md(en_text),
            lang_code="en",
            lang_switch_items=build_lang_switch(items),
            home_href=rel_prefix if rel_prefix else "./",
        )
        out = OUT / en_html_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(en_html, encoding="utf-8")
        print(f"  wrote site/{en_html_name}")


def build_landing() -> tuple[str, str]:
    """Render top-level README / README_CN as landing pages."""
    readme_cn = REPO_ROOT / "README_CN.md"
    readme = REPO_ROOT / "README.md"
    zh_html = en_html = ""

    if readme_cn.exists():
        zh_html = make_page(
            title="GitHub Stars Analyzer — 中文",
            body_html=render_md(readme_cn.read_text(encoding="utf-8")),
            lang_code="zh",
            lang_switch_items=build_lang_switch([("zh", "index.html"), ("en", "index_en.html")]),
            home_href="./",
            home_label="GitHub Stars Analyzer",
        )

    if readme.exists():
        en_html = make_page(
            title="GitHub Stars Analyzer — English",
            body_html=render_md(readme.read_text(encoding="utf-8")),
            lang_code="en",
            lang_switch_items=build_lang_switch([("zh", "index.html"), ("en", "index_en.html")]),
            home_href="./",
            home_label="GitHub Stars Analyzer",
        )

    return zh_html, en_html


def build_prompts_index() -> tuple[str, str]:
    """Render the prompts/index.html directory listing."""
    if not SRC_PROM.exists():
        return "", ""

    files = sorted(
        p for p in SRC_PROM.iterdir()
        if p.is_file() and p.suffix == ".md"
    )

    # Filter out EN twins — list each pair only once
    seen = set()
    pairs = []
    for f in files:
        base = f.stem
        if base.endswith("_EN"):
            base = base[:-3]
            twin = SRC_PROM / f"{base}.md"
            if twin.exists() and twin.resolve() in [p.resolve() for p in files]:
                if base not in seen:
                    pairs.append((base, twin, f))
                    seen.add(base)
        else:
            en_twin = SRC_PROM / f"{base}_EN.md"
            if en_twin.exists():
                if base not in seen:
                    pairs.append((base, f, en_twin))
                    seen.add(base)
            else:
                if base not in seen:
                    pairs.append((base, f, None))
                    seen.add(base)

    # Build directory listing HTML
    rows = []
    for base, zh_path, en_path in pairs:
        zh_link = f'<a href="{zh_path.stem}.html">中文</a>'
        # If en_path is None, this file is CN-only — link to the same file for English
        if en_path is None:
            en_link = f'<a href="{zh_path.stem}.html">English</a>'
        else:
            en_link = f'<a href="{en_path.stem}.html">English</a>'
        rows.append(f"<tr><td><code>{escape(base)}</code></td><td>{zh_link}</td><td>{en_link}</td></tr>")

    table_html = f"""
<table>
<thead><tr><th>File</th><th>中文</th><th>English</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
<p><a href="https://github.com/csxo/github-stars-analyzer/tree/main/prompts">View source on GitHub →</a></p>
"""

    body_zh = f"<h1>📚 prompts/ 目录</h1><p>把 <code>prompts/</code> 里的 Markdown 文件渲染成网页。每一行点进去是完整可读的版本。</p>{table_html}"
    body_en = f"<h1>📚 prompts/ directory</h1><p>The Markdown files under <code>prompts/</code> rendered as web pages. Click any row for the full readable version.</p>{table_html}"

    zh_html = make_page(
        title="prompts/ — GitHub Stars Analyzer",
        body_html=body_zh,
        lang_code="zh",
        lang_switch_items=build_lang_switch([("zh", "index.html"), ("en", "index_en.html")]),
        home_href="../",
        home_label="GitHub Stars Analyzer",
    )
    en_html = make_page(
        title="prompts/ — GitHub Stars Analyzer",
        body_html=body_en,
        lang_code="en",
        lang_switch_items=build_lang_switch([("zh", "index.html"), ("en", "index_en.html")]),
        home_href="../",
        home_label="GitHub Stars Analyzer",
    )
    return zh_html, en_html


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # 1. Top-level landing
    landing_zh, landing_en = build_landing()
    if landing_zh:
        (OUT / "index.html").write_text(landing_zh, encoding="utf-8")
        print("  wrote site/index.html")
    if landing_en:
        (OUT / "index_en.html").write_text(landing_en, encoding="utf-8")
        print("  wrote site/index_en.html")

    # 2. prompts/ directory
    prompts_out = OUT / "prompts"
    prompts_out.mkdir()
    if SRC_PROM.exists():
        idx_zh, idx_en = build_prompts_index()
        (prompts_out / "index.html").write_text(idx_zh, encoding="utf-8")
        (prompts_out / "index_en.html").write_text(idx_en, encoding="utf-8")
        print("  wrote site/prompts/index.html + index_en.html")

        # Process each .md file (or its pair) once.
        seen = set()
        for md in sorted(SRC_PROM.iterdir()):
            if not md.is_file() or md.suffix != ".md":
                continue
            if md.resolve() in seen:
                continue
            zh, en = resolve_pair(md)
            if zh: seen.add(zh.resolve())
            if en: seen.add(en.resolve())
            render_pair_to_files(zh, en, depth=2)

    # 3. Top-level *.md files (ARCHITECTURE, EXTENDING, etc.)
    top_level_md = [
        p for p in REPO_ROOT.iterdir()
        if p.is_file() and p.suffix == ".md" and p.stem not in {"README", "README_CN"}
    ]
    seen = set()
    for md in sorted(top_level_md):
        if md.resolve() in seen:
            continue
        zh, en = resolve_pair(md)
        if zh: seen.add(zh.resolve())
        if en: seen.add(en.resolve())
        render_pair_to_files(zh, en, depth=1)

    print("\nDone.")


if __name__ == "__main__":
    main()