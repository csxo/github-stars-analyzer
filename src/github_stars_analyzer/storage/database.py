"""SQLite-backed persistence.

This module is intentionally framework-free — pure stdlib `sqlite3`. The
schema is created lazily on first use and is fully forward-compatible (uses
`IF NOT EXISTS` and additive columns only).

Tables:

  repositories       — canonical row per starred repo (keyed by GitHub `id`)
  analyses           — LLM analysis rows, keyed by (repo_id, version, model)
  categories         — canonical category names
  repo_categories    — many-to-many between repos and categories
  job_runs           — one row per top-level CLI invocation
  checkpoints        — one row per (job_run_id, phase) for resume

Cache invalidation:

  * A repo's stored analysis is reusable iff
        stored.analysis_version == current.analysis_version
    AND stored.model           == current.model
    AND stored.readme_hash     == current.readme_hash

  * Bumping `analysis_version` in config invalidates every row in `analyses`
    on the next read — no migration needed.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..models import AnalysisResult, Checkpoint, JobRun, Repository

SCHEMA_VERSION = 1

# DDL is split into one statement per table for clarity. Adding a column
# requires a migration in `_migrate()` below — never edit the CREATE TABLE
# after the schema has shipped.
_DDL = [
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS repositories (
        id              INTEGER PRIMARY KEY,
        node_id         TEXT,
        name            TEXT NOT NULL,
        full_name       TEXT NOT NULL UNIQUE,
        owner           TEXT NOT NULL,
        html_url        TEXT,
        description     TEXT,
        primary_language TEXT,
        languages       TEXT,           -- JSON array
        topics          TEXT,           -- JSON array
        stargazers_count INTEGER DEFAULT 0,
        forks_count     INTEGER DEFAULT 0,
        archived        INTEGER DEFAULT 0,
        disabled        INTEGER DEFAULT 0,
        fork            INTEGER DEFAULT 0,
        created_at      TEXT,
        updated_at      TEXT,
        pushed_at       TEXT,
        starred_at      TEXT,
        fetched_at      TEXT,
        readme_hash     TEXT,
        readme_path     TEXT,
        has_readme      INTEGER DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_repositories_pushed ON repositories(pushed_at)",
    "CREATE INDEX IF NOT EXISTS idx_repositories_starred ON repositories(starred_at)",
    """
    CREATE TABLE IF NOT EXISTS analyses (
        repo_id          INTEGER NOT NULL,
        analysis_version TEXT NOT NULL,
        model            TEXT NOT NULL,
        provider         TEXT NOT NULL,
        summary          TEXT,
        features         TEXT,           -- JSON array
        capabilities     TEXT,           -- JSON array
        use_cases        TEXT,           -- JSON array
        tech_stack       TEXT,           -- JSON array
        tags             TEXT,           -- JSON array
        value_score      REAL DEFAULT 0,
        primary_category TEXT,
        raw_response     TEXT,           -- JSON
        analyzed_at      TEXT,
        readme_hash      TEXT,           -- copied at write time for invalidation
        PRIMARY KEY (repo_id, analysis_version, model),
        FOREIGN KEY (repo_id) REFERENCES repositories(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_analyses_version ON analyses(analysis_version)",
    """
    CREATE TABLE IF NOT EXISTS categories (
        name        TEXT PRIMARY KEY,
        description TEXT,
        parent      TEXT,
        repo_count  INTEGER DEFAULT 0,
        created_at  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS repo_categories (
        repo_id    INTEGER NOT NULL,
        category   TEXT NOT NULL,
        confidence REAL DEFAULT 1.0,
        source     TEXT DEFAULT 'llm',
        assigned_at TEXT,
        PRIMARY KEY (repo_id, category),
        FOREIGN KEY (repo_id) REFERENCES repositories(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_repo_cat_cat ON repo_categories(category)",
    """
    CREATE TABLE IF NOT EXISTS job_runs (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at       TEXT,
        finished_at      TEXT,
        status           TEXT,
        command          TEXT,
        config_snapshot  TEXT,
        phase_stats      TEXT,
        error_message    TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
        job_run_id     INTEGER NOT NULL,
        phase          TEXT NOT NULL,
        cursor         TEXT,
        processed_ids  TEXT,
        failed_ids     TEXT,
        last_id        INTEGER DEFAULT 0,
        updated_at     TEXT,
        PRIMARY KEY (job_run_id, phase),
        FOREIGN KEY (job_run_id) REFERENCES job_runs(id) ON DELETE CASCADE
    )
    """,
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply additive migrations. Keep this short — schema evolution should be
    a rare event driven by `SCHEMA_VERSION` bumping."""
    cur = conn.execute("SELECT value FROM schema_meta WHERE key='version'")
    row = cur.fetchone()
    current = int(row[0]) if row else 0
    if current >= SCHEMA_VERSION:
        return
    # No structural migrations yet — DDL above is idempotent.
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
        ("version", str(SCHEMA_VERSION)),
    )


class Database:
    """Thin wrapper around `sqlite3.Connection`.

    The connection lives for the lifetime of the Database object and uses
    WAL mode for concurrent reads while a write is in progress.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
            isolation_level=None,   # autocommit; we manage transactions explicitly
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript("BEGIN;\n" + ";\n".join(_DDL) + ";\nCOMMIT;")
        _migrate(self._conn)

    # ---- lifecycle -----------------------------------------------------

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        cur = self._conn
        cur.execute("BEGIN")
        try:
            yield cur
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    # ---- repository CRUD ----------------------------------------------

    def upsert_repositories(self, repos: Iterable[Repository]) -> int:
        rows = []
        for r in repos:
            d = r.to_row()
            rows.append(
                (
                    d["id"], d["node_id"], d["name"], d["full_name"], d["owner"],
                    d["html_url"], d["description"], d["primary_language"],
                    json.dumps(d["languages"]), json.dumps(d["topics"]),
                    d["stargazers_count"], d["forks_count"],
                    int(d["archived"]), int(d["disabled"]), int(d["fork"]),
                    d["created_at"], d["updated_at"], d["pushed_at"], d["starred_at"],
                    d["fetched_at"], d["readme_hash"], d["readme_path"],
                    int(d["has_readme"]),
                )
            )
        if not rows:
            return 0
        sql = """
        INSERT INTO repositories (
            id, node_id, name, full_name, owner, html_url, description,
            primary_language, languages, topics, stargazers_count, forks_count,
            archived, disabled, fork, created_at, updated_at, pushed_at,
            starred_at, fetched_at, readme_hash, readme_path, has_readme
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            node_id=excluded.node_id,
            name=excluded.name,
            full_name=excluded.full_name,
            owner=excluded.owner,
            html_url=excluded.html_url,
            description=excluded.description,
            primary_language=excluded.primary_language,
            languages=excluded.languages,
            topics=excluded.topics,
            stargazers_count=excluded.stargazers_count,
            forks_count=excluded.forks_count,
            archived=excluded.archived,
            disabled=excluded.disabled,
            fork=excluded.fork,
            updated_at=excluded.updated_at,
            pushed_at=excluded.pushed_at,
            starred_at=COALESCE(excluded.starred_at, repositories.starred_at),
            fetched_at=excluded.fetched_at
        """
        with self.transaction() as cx:
            cx.executemany(sql, rows)
        return len(rows)

    def get_repository(self, repo_id: int) -> Repository | None:
        row = self._conn.execute(
            "SELECT * FROM repositories WHERE id=?", (repo_id,)
        ).fetchone()
        return _row_to_repo(row) if row else None

    def iter_repositories(self, *, has_readme: bool | None = None) -> Iterator[Repository]:
        sql = "SELECT * FROM repositories"
        params: tuple[Any, ...] = ()
        if has_readme is True:
            sql += " WHERE has_readme=1"
        elif has_readme is False:
            sql += " WHERE has_readme=0"
        sql += " ORDER BY id"
        for row in self._conn.execute(sql, params):
            r = _row_to_repo(row)
            if r is not None:
                yield r

    def set_readme(
        self,
        repo_id: int,
        *,
        readme_hash: str,
        readme_path: str,
    ) -> None:
        with self.transaction() as cx:
            cx.execute(
                """
                UPDATE repositories
                   SET readme_hash=?, readme_path=?, has_readme=1
                 WHERE id=?
                """,
                (readme_hash, readme_path, repo_id),
            )

    def repositories_needing_readme(self, limit: int | None = None) -> list[Repository]:
        sql = "SELECT * FROM repositories WHERE has_readme=0 OR readme_hash IS NULL ORDER BY id"
        if limit:
            sql += " LIMIT ?"
            rows = self._conn.execute(sql, (limit,)).fetchall()
        else:
            rows = self._conn.execute(sql).fetchall()
        return [r for row in rows if (r := _row_to_repo(row)) is not None]

    # ---- analysis cache ------------------------------------------------

    def get_analysis(
        self,
        repo_id: int,
        *,
        analysis_version: str,
        model: str,
        readme_hash: str | None,
    ) -> AnalysisResult | None:
        row = self._conn.execute(
            """
            SELECT * FROM analyses
             WHERE repo_id=? AND analysis_version=? AND model=?
            """,
            (repo_id, analysis_version, model),
        ).fetchone()
        if not row:
            return None
        # Cache invalidation: if the README changed, the analysis is stale.
        if readme_hash is not None and row["readme_hash"] != readme_hash:
            return None
        return _row_to_analysis(row)

    def upsert_analysis(self, analysis: AnalysisResult, readme_hash: str) -> None:
        row = analysis.to_row()
        sql = """
        INSERT INTO analyses (
            repo_id, analysis_version, model, provider, summary,
            features, capabilities, use_cases, tech_stack, tags,
            value_score, primary_category, raw_response, analyzed_at, readme_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_id, analysis_version, model) DO UPDATE SET
            provider=excluded.provider,
            summary=excluded.summary,
            features=excluded.features,
            capabilities=excluded.capabilities,
            use_cases=excluded.use_cases,
            tech_stack=excluded.tech_stack,
            tags=excluded.tags,
            value_score=excluded.value_score,
            primary_category=excluded.primary_category,
            raw_response=excluded.raw_response,
            analyzed_at=excluded.analyzed_at,
            readme_hash=excluded.readme_hash
        """
        with self.transaction() as cx:
            cx.execute(
                sql,
                (
                    row["repo_id"], row["analysis_version"], row["model"],
                    row["provider"], row["summary"],
                    json.dumps(row["features"], ensure_ascii=False),
                    json.dumps(row["capabilities"], ensure_ascii=False),
                    json.dumps(row["use_cases"], ensure_ascii=False),
                    json.dumps(row["tech_stack"], ensure_ascii=False),
                    json.dumps(row["tags"], ensure_ascii=False),
                    row["value_score"], row["primary_category"],
                    json.dumps(row["raw_response"], ensure_ascii=False)
                    if row["raw_response"] is not None else None,
                    row["analyzed_at"], readme_hash,
                ),
            )

    def repositories_needing_analysis(
        self,
        *,
        analysis_version: str,
        model: str,
        limit: int | None = None,
    ) -> list[tuple[Repository, str]]:
        """Return (repo, readme_hash) pairs that still need a fresh analysis."""
        sql = """
        SELECT r.*, a.readme_hash AS a_hash
          FROM repositories r
          LEFT JOIN analyses a
            ON a.repo_id = r.id
           AND a.analysis_version = ?
           AND a.model = ?
         WHERE r.has_readme = 1
           AND (a.repo_id IS NULL OR a.readme_hash != r.readme_hash)
         ORDER BY r.id
        """
        params: tuple[Any, ...] = (analysis_version, model)
        if limit:
            sql += " LIMIT ?"
            params = (analysis_version, model, limit)
        out: list[tuple[Repository, str]] = []
        for row in self._conn.execute(sql, params):
            r = _row_to_repo(row)
            if r is not None and r.readme_hash:
                out.append((r, r.readme_hash))
        return out

    # ---- categories -----------------------------------------------------

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def upsert_category(self, name: str, *, description: str = "", parent: str | None = None) -> None:
        self._conn.execute(
            """
            INSERT INTO categories(name, description, parent, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                description=excluded.description,
                parent=excluded.parent
            """,
            (name, description, parent, self._now()),
        )

    def assign_category(
        self,
        repo_id: int,
        category: str,
        *,
        confidence: float = 1.0,
        source: str = "llm",
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO repo_categories(repo_id, category, confidence, source, assigned_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repo_id, category) DO UPDATE SET
                confidence=excluded.confidence,
                source=excluded.source
            """,
            (repo_id, category, confidence, source, self._now()),
        )

    def list_categories_with_counts(self) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            """
            SELECT category, COUNT(*) AS n
              FROM repo_categories
             GROUP BY category
             ORDER BY n DESC, category
            """
        ).fetchall()
        return [(r["category"], r["n"]) for r in rows]

    def repos_in_category(self, category: str) -> list[Repository]:
        rows = self._conn.execute(
            """
            SELECT r.* FROM repositories r
              JOIN repo_categories rc ON rc.repo_id = r.id
             WHERE rc.category = ?
             ORDER BY r.stargazers_count DESC, r.id
            """,
            (category,),
        ).fetchall()
        return [r for row in rows if (r := _row_to_repo(row)) is not None]

    # ---- jobs / checkpoints -------------------------------------------

    def create_job_run(self, command: str, config_snapshot: dict[str, Any]) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO job_runs(started_at, status, command, config_snapshot, phase_stats)
            VALUES (?, 'running', ?, ?, '{}')
            """,
            (self._now(), command, json.dumps(config_snapshot, ensure_ascii=False)),
        )
        return int(cur.lastrowid)

    def finish_job_run(
        self,
        job_id: int,
        *,
        status: str = "completed",
        error: str | None = None,
        phase_stats: dict[str, dict[str, int]] | None = None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE job_runs
               SET finished_at=?, status=?, error_message=?, phase_stats=?
             WHERE id=?
            """,
            (
                self._now(),
                status,
                error,
                json.dumps(phase_stats or {}, ensure_ascii=False),
                job_id,
            ),
        )

    def upsert_checkpoint(self, cp: Checkpoint) -> None:
        self._conn.execute(
            """
            INSERT INTO checkpoints(job_run_id, phase, cursor, processed_ids, failed_ids, last_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_run_id, phase) DO UPDATE SET
                cursor=excluded.cursor,
                processed_ids=excluded.processed_ids,
                failed_ids=excluded.failed_ids,
                last_id=excluded.last_id,
                updated_at=excluded.updated_at
            """,
            (
                cp.job_run_id, cp.phase, cp.cursor,
                json.dumps(cp.processed_ids), json.dumps(cp.failed_ids),
                cp.last_id, cp.updated_at,
            ),
        )

    def load_checkpoint(self, job_run_id: int, phase: str) -> Checkpoint | None:
        row = self._conn.execute(
            """
            SELECT * FROM checkpoints WHERE job_run_id=? AND phase=?
            """,
            (job_run_id, phase),
        ).fetchone()
        if not row:
            return None
        return Checkpoint(
            job_run_id=row["job_run_id"],
            phase=row["phase"],
            cursor=row["cursor"] or "",
            processed_ids=json.loads(row["processed_ids"] or "[]"),
            failed_ids=json.loads(row["failed_ids"] or "[]"),
            last_id=int(row["last_id"] or 0),
            updated_at=row["updated_at"] or "",
        )

    def latest_job_run(self, command: str | None = None) -> JobRun | None:
        sql = "SELECT * FROM job_runs"
        params: tuple[Any, ...] = ()
        if command:
            sql += " WHERE command=?"
            params = (command,)
        sql += " ORDER BY id DESC LIMIT 1"
        row = self._conn.execute(sql, params).fetchone()
        return _row_to_job(row) if row else None

    def summary(self) -> dict[str, int]:
        """Quick stats for `gsa status`."""
        out: dict[str, int] = {}
        for key, table in [
            ("repositories", "repositories"),
            ("analyses", "analyses"),
            ("categories", "categories"),
            ("job_runs", "job_runs"),
        ]:
            row = self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            out[key] = int(row["n"])
        return out


# ---------------------------------------------------------------------------
# Row → model helpers
# ---------------------------------------------------------------------------


def _row_to_repo(row: sqlite3.Row) -> Repository | None:
    try:
        return Repository(
            id=int(row["id"]),
            node_id=row["node_id"] or "",
            name=row["name"] or "",
            full_name=row["full_name"] or "",
            owner=row["owner"] or "",
            html_url=row["html_url"] or "",
            description=row["description"],
            primary_language=row["primary_language"],
            languages=tuple(json.loads(row["languages"] or "[]")),
            topics=tuple(json.loads(row["topics"] or "[]")),
            stargazers_count=int(row["stargazers_count"] or 0),
            forks_count=int(row["forks_count"] or 0),
            archived=bool(row["archived"]),
            disabled=bool(row["disabled"]),
            fork=bool(row["fork"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            pushed_at=row["pushed_at"],
            starred_at=row["starred_at"],
            fetched_at=row["fetched_at"] or "",
            readme_hash=row["readme_hash"],
            readme_path=row["readme_path"],
            has_readme=bool(row["has_readme"]),
        )
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def _row_to_analysis(row: sqlite3.Row) -> AnalysisResult:
    return AnalysisResult(
        repo_id=int(row["repo_id"]),
        analysis_version=row["analysis_version"],
        model=row["model"],
        provider=row["provider"] or "",
        summary=row["summary"] or "",
        features=tuple(json.loads(row["features"] or "[]")),
        capabilities=tuple(json.loads(row["capabilities"] or "[]")),
        use_cases=tuple(json.loads(row["use_cases"] or "[]")),
        tech_stack=tuple(json.loads(row["tech_stack"] or "[]")),
        tags=tuple(json.loads(row["tags"] or "[]")),
        value_score=float(row["value_score"] or 0.0),
        primary_category=row["primary_category"],
        raw_response=json.loads(row["raw_response"]) if row["raw_response"] else None,
        analyzed_at=row["analyzed_at"] or "",
    )


def _row_to_job(row: sqlite3.Row) -> JobRun:
    return JobRun(
        id=int(row["id"]),
        started_at=row["started_at"] or "",
        finished_at=row["finished_at"],
        status=row["status"] or "",
        command=row["command"] or "",
        config_snapshot=json.loads(row["config_snapshot"] or "{}"),
        phase_stats=json.loads(row["phase_stats"] or "{}"),
        error_message=row["error_message"],
    )