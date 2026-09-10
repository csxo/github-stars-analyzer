"""Storage layer tests — uses a tmp SQLite DB and tmp cache dir."""

from __future__ import annotations

from pathlib import Path

from github_stars_analyzer.models.repository import AnalysisResult, Repository
from github_stars_analyzer.storage import ContentCache, Database


def _repo(rid: int) -> Repository:
    return Repository(
        id=rid, node_id="", name=f"r{rid}", full_name=f"o/r{rid}", owner="o",
        html_url="", description=None, primary_language="Python",
    )


def test_upsert_and_get(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "gsa.db"))
    try:
        r = _repo(1)
        db.upsert_repositories([r])
        got = db.get_repository(1)
        assert got is not None
        assert got.full_name == "o/r1"
    finally:
        db.close()


def test_analysis_cache_invalidation(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "gsa.db"))
    try:
        db.upsert_repositories([_repo(1)])
        analysis = AnalysisResult(
            repo_id=1, analysis_version="1.0", model="m", provider="p",
            summary="hi", value_score=5.0,
        )
        db.upsert_analysis(analysis, readme_hash="hash-a")

        # Same hash + version + model: hit.
        assert db.get_analysis(1, analysis_version="1.0", model="m", readme_hash="hash-a")

        # Hash changed: miss.
        assert db.get_analysis(1, analysis_version="1.0", model="m", readme_hash="hash-b") is None

        # Version changed: miss.
        assert db.get_analysis(1, analysis_version="1.1", model="m", readme_hash="hash-a") is None

        # Model changed: miss.
        assert db.get_analysis(1, analysis_version="1.0", model="m2", readme_hash="hash-a") is None
    finally:
        db.close()


def test_content_cache_dedup(tmp_path: Path) -> None:
    cache = ContentCache(tmp_path / "cache")
    rec1 = cache.store_readme("o/r1", "# Hello")
    rec2 = cache.store_readme("o/r2", "# Hello")
    assert rec1.path == rec2.path
    assert rec1.hash == rec2.hash
    assert cache.read_readme(rec1.hash) is not None


def test_repositories_needing_analysis_filters_by_hash(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "gsa.db"))
    try:
        db.upsert_repositories([_repo(1)])
        db.set_readme(1, readme_hash="hash-a", readme_path="x")
        db.upsert_analysis(
            AnalysisResult(
                repo_id=1, analysis_version="v1", model="m", provider="p",
                summary="", value_score=0.0,
            ),
            readme_hash="hash-a",
        )
        # Same hash, same version+model: nothing to analyze.
        assert db.repositories_needing_analysis(analysis_version="v1", model="m") == []

        # New hash: should re-analyze.
        db.set_readme(1, readme_hash="hash-b", readme_path="y")
        pending = db.repositories_needing_analysis(analysis_version="v1", model="m")
        assert len(pending) == 1
        assert pending[0][1] == "hash-b"
    finally:
        db.close()