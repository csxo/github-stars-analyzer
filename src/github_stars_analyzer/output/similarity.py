"""Similarity and aggregation helpers.

We start with a simple, deterministic measure: Jaccard similarity over the
union of `(tags, topics, tech_stack)`. Embedding-based similarity is the
explicit next extension point — see README.md.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..models import AnalysisResult, Repository


@dataclass(frozen=True)
class SimilarityEntry:
    other_id: int
    score: float
    shared: frozenset[str]


def _normalise(items: tuple[str, ...] | list[str]) -> set[str]:
    return {i.strip().lower() for i in items if i and i.strip()}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union) if union else 0.0


def repo_keyset(repo: Repository, analysis: AnalysisResult | None) -> set[str]:
    """Return the bag of strings used for similarity scoring."""
    bits = []
    bits.extend(repo.topics)
    if repo.primary_language:
        bits.append(repo.primary_language)
    if analysis is not None:
        bits.extend(analysis.tags)
        bits.extend(analysis.tech_stack)
    return _normalise(bits)


def find_similar(
    repo: Repository,
    analysis: AnalysisResult | None,
    *,
    pool: dict[int, tuple[Repository, AnalysisResult | None, set[str]]],
    threshold: float,
    top_n: int,
) -> list[SimilarityEntry]:
    base = repo_keyset(repo, analysis)
    if not base:
        return []
    out: list[SimilarityEntry] = []
    for other_id, (_or, _oa, other_set) in pool.items():
        if other_id == repo.id:
            continue
        if not other_set:
            continue
        score = jaccard(base, other_set)
        if score < threshold:
            continue
        shared = frozenset(base & other_set)
        out.append(SimilarityEntry(other_id=other_id, score=score, shared=shared))
    out.sort(key=lambda e: (-e.score, e.other_id))
    return out[:top_n]


def build_similarity_pool(
    repos: list[Repository],
    analyses_by_repo: dict[int, AnalysisResult | None],
) -> dict[int, tuple[Repository, AnalysisResult | None, set[str]]]:
    """Pre-compute keysets so each repo's `find_similar` call is O(N)."""
    pool: dict[int, tuple[Repository, AnalysisResult | None, set[str]]] = {}
    for r in repos:
        key = repo_keyset(r, analyses_by_repo.get(r.id))
        pool[r.id] = (r, analyses_by_repo.get(r.id), key)
    return pool


def tech_frequency(
    repos: list[Repository], analyses: dict[int, AnalysisResult | None],
) -> list[tuple[str, int]]:
    """Return (tech, count) sorted by frequency, for the index page."""
    counts: dict[str, int] = defaultdict(int)
    for r in repos:
        if r.primary_language:
            counts[r.primary_language] += 1
        a = analyses.get(r.id)
        if a is None:
            continue
        for tech in a.tech_stack:
            if tech:
                counts[tech] += 1
    out = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return out