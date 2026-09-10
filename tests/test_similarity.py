"""Similarity helpers — pure-Python, fully testable."""

from __future__ import annotations

from github_stars_analyzer.output.similarity import (
    build_similarity_pool,
    find_similar,
    jaccard,
    repo_keyset,
)
from github_stars_analyzer.models.repository import AnalysisResult, Repository


def _repo(rid: int, full: str, *, lang: str | None = None, topics=()):
    return Repository(
        id=rid, node_id="", name=full.split("/")[-1], full_name=full, owner=full.split("/")[0],
        html_url="", description=None, primary_language=lang, topics=topics,
    )


def test_jaccard_identical() -> None:
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint() -> None:
    assert jaccard({"a"}, {"b"}) == 0.0


def test_find_similar_ranks_by_overlap() -> None:
    r1 = _repo(1, "a/x", lang="Python", topics=("llm", "rag"))
    r2 = _repo(2, "b/y", lang="Python", topics=("llm", "agent"))
    r3 = _repo(3, "c/z", lang="Rust", topics=("compiler",))

    pool = build_similarity_pool([r1, r2, r3], {})
    similar = find_similar(r1, None, pool=pool, threshold=0.3, top_n=5)
    # r2 shares {python, llm} with r1 (Jaccard 2/4 = 0.5); r3 shares nothing.
    assert [e.other_id for e in similar] == [2]


def test_repo_keyset_includes_topics_and_lang() -> None:
    r = _repo(1, "a/x", lang="Python", topics=("LLM", "RAG"))
    analysis = AnalysisResult(
        repo_id=1, analysis_version="1", model="m", provider="p",
        summary="", tags=("agent",), tech_stack=("openai",),
    )
    keys = repo_keyset(r, analysis)
    assert "python" in keys
    assert "llm" in keys and "rag" in keys
    assert "agent" in keys and "openai" in keys