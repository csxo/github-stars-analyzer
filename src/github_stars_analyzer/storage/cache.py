"""Content-addressed cache for README bodies and LLM raw responses.

The README cache is the second-tier cache; the SQLite `analyses` row is the
first-tier (we can rebuild the README cache from GitHub on demand, but we
can never reconstruct a past analysis without re-paying LLM tokens).

Filenames are content-hashed so:

* Identical READMEs collapse to the same file → cheap deduplication.
* Changing a README yields a new filename; the old one stays around for
  forensic diffing until you run `gsa clean --cache`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils.hash import short_hash, sha256_text


@dataclass
class ReadmeRecord:
    hash: str
    path: Path
    size: int


class ContentCache:
    """Filesystem-backed content-addressed cache."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- README ----------------------------------------------------------

    def store_readme(self, repo_full_name: str, body: str) -> ReadmeRecord:
        """Store a README body, returning a record of where it landed.

        Files are sharded under `readme/xx/xxxxxxxxxxxx.md` for filesystem
        friendliness with large caches.
        """
        digest = sha256_text(body)
        path = self._readme_path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(body, encoding="utf-8", errors="replace")
            tmp.replace(path)
        # Sidecar metadata so we can recover repo → hash without a DB hit.
        meta_path = path.with_suffix(path.suffix + ".meta.json")
        meta_path.write_text(
            json.dumps({"repo": repo_full_name, "hash": digest, "size": len(body)},
                      ensure_ascii=False),
            encoding="utf-8",
        )
        return ReadmeRecord(hash=digest, path=path, size=len(body))

    def read_readme(self, digest: str) -> str | None:
        path = self._readme_path(digest)
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
        return None

    def _readme_path(self, digest: str) -> Path:
        # 2-char shard for the filesystem; full 64-char SHA stays in the name.
        shard = digest[:2]
        return self.root / "readme" / shard / f"{short_hash(digest, length=64)}.md"

    # ---- LLM raw responses (debugging) ----------------------------------

    def store_response(self, repo_id: int, body: str) -> Path:
        sub = self.root / "llm" / f"{repo_id // 1000:03d}"
        sub.mkdir(parents=True, exist_ok=True)
        digest = sha256_text(body)
        path = sub / f"{repo_id}-{short_hash(digest, length=16)}.json"
        if not path.exists():
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(path)
        return path

    # ---- housekeeping ---------------------------------------------------

    def prune_orphans(self, keep: set[str]) -> int:
        """Delete README files whose hash is not in `keep`. Returns count."""
        removed = 0
        readme_dir = self.root / "readme"
        if not readme_dir.exists():
            return 0
        for shard in readme_dir.iterdir():
            if not shard.is_dir():
                continue
            for f in shard.iterdir():
                if f.suffix != ".md":
                    continue
                # Filename is the short hash; recover full via reading meta.
                meta = f.with_suffix(f.suffix + ".meta.json")
                digest = None
                if meta.exists():
                    try:
                        digest = json.loads(meta.read_text(encoding="utf-8")).get("hash")
                    except json.JSONDecodeError:
                        digest = None
                if digest is None:
                    digest = f.stem
                if digest not in keep:
                    f.unlink(missing_ok=True)
                    meta.unlink(missing_ok=True)
                    removed += 1
        return removed

    def stats(self) -> dict[str, Any]:
        readme_dir = self.root / "readme"
        llm_dir = self.root / "llm"
        return {
            "readme_files": sum(1 for _ in readme_dir.rglob("*.md")) if readme_dir.exists() else 0,
            "llm_responses": sum(1 for _ in llm_dir.rglob("*.json")) if llm_dir.exists() else 0,
        }