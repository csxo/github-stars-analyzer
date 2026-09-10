"""Minimal test package.

These are deliberately scoped — they exercise the deterministic modules
(utils, similarity, schema, markdown rendering). The collectors and
LLM-driven analyzer require live API access and are not unit-tested here.
"""

from __future__ import annotations