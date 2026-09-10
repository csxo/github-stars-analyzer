"""Content hashing utilities.

`README hash` and `analysis version` together determine whether a repo's stored
analysis is still valid. Bumping `analysis_version` invalidates *every* cached
analysis in one shot, which is exactly what you want when you change the
prompt schema or output structure.
"""

from __future__ import annotations

import hashlib


def sha256_text(text: str) -> str:
    """Return the hex SHA-256 of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(text: str, *, length: int = 12) -> str:
    """Truncated SHA-256 for filenames and display."""
    return sha256_text(text)[:length]