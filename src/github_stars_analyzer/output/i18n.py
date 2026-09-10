"""Localised strings for Markdown rendering.

Keep this file tiny — the rest of the generator uses structural logic, this
just supplies labels for `zh-CN` and `en`.
"""

from __future__ import annotations

I18N: dict[str, dict[str, str]] = {
    "zh-CN": {
        "index_title": "GitHub Stars 分析报告",
        "index_subtitle": "由 GitHub Stars Analyzer 自动生成",
        "index_meta_total": "Star 总数",
        "index_meta_analyzed": "已分析",
        "index_meta_categories": "分类数",
        "index_section_categories": "分类总览",
        "index_section_value": "高价值项目（评分 ≥ 8）",
        "index_section_tech": "热门技术栈",
        "category_header": "分类",
        "category_count": "项目数",
        "category_no_repos": "暂无项目",
        "repo_section_summary": "简介",
        "repo_section_features": "核心功能",
        "repo_section_capabilities": "能力",
        "repo_section_usecases": "使用场景",
        "repo_section_tech": "技术栈",
        "repo_section_tags": "标签",
        "repo_section_similar": "同类项目",
        "repo_section_meta": "元信息",
        "repo_value": "价值评分",
        "repo_stars": "Star 数",
        "repo_language": "主语言",
        "repo_topics": "GitHub 主题",
        "repo_archived": "已归档",
        "repo_no_analysis": "（尚未生成分析）",
        "footer_generated": "生成时间",
    },
    "en": {
        "index_title": "GitHub Stars Analysis Report",
        "index_subtitle": "Generated automatically by GitHub Stars Analyzer",
        "index_meta_total": "Total stars",
        "index_meta_analyzed": "Analyzed",
        "index_meta_categories": "Categories",
        "index_section_categories": "Categories",
        "index_section_value": "Top-value projects (score ≥ 8)",
        "index_section_tech": "Popular tech",
        "category_header": "Category",
        "category_count": "Projects",
        "category_no_repos": "No projects yet",
        "repo_section_summary": "Summary",
        "repo_section_features": "Core features",
        "repo_section_capabilities": "Capabilities",
        "repo_section_usecases": "Use cases",
        "repo_section_tech": "Tech stack",
        "repo_section_tags": "Tags",
        "repo_section_similar": "Similar projects",
        "repo_section_meta": "Meta",
        "repo_value": "Value score",
        "repo_stars": "Stars",
        "repo_language": "Primary language",
        "repo_topics": "GitHub topics",
        "repo_archived": "Archived",
        "repo_no_analysis": "(no analysis yet)",
        "footer_generated": "Generated at",
    },
}


def strings(lang: str) -> dict[str, str]:
    if lang not in I18N:
        return I18N["zh-CN"]
    return I18N[lang]