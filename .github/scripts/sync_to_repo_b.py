#!/usr/bin/env python3
"""Sync a local directory to a GitHub repository via the Contents API.

Mirrors the layout of `<local_dir>` into `<owner/repo>@<branch>` using GitHub's
REST Contents API. Only files that differ (or are missing) are uploaded;
remote files that don't exist locally are removed. The script uses only the
Python standard library so it can run inside a GitHub Actions step with
nothing but a fine-grained PAT in the environment.

Usage:
    python sync_to_repo_b.py <local_dir> <owner/repo> [--branch main]

Environment:
    SYNC_PAT  Fine-grained PAT with `contents: write` on the target repo.

Why not plain `git push`?
    1. The shared GITHUB_TOKEN has no access to private/sister repos of an org.
    2. Pure-Contents-API commits work in a single job with no checkout dance.

Reference workflow: .github/workflows/sync-to-repo-b.yml
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"


# ----------------------------------------------------------------------------
# GitHub primitives
# ----------------------------------------------------------------------------

def git_blob_sha(content: bytes) -> str:
    """Compute the git blob SHA for `content` — same as `git hash-object`."""
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def gh(method: str, path: str, data: dict | None = None):
    """Minimal GitHub REST helper. Returns (status_code, parsed_body | text)."""
    headers = {
        "Authorization": f"Bearer {os.environ['SYNC_PAT']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "gsa-sync-to-repo-b",
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(API + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            raw = resp.read()
            if not raw:
                return resp.status, {}
            return resp.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        return exc.code, exc.read().decode("utf-8", errors="replace")


# ----------------------------------------------------------------------------
# Tree / file ops
# ----------------------------------------------------------------------------

def list_remote_tree(repo: str, branch: str) -> dict[str, str]:
    """Return {path: blob_sha} for every blob in the repo's tree."""
    code, data = gh("GET", f"/repos/{repo}/git/trees/{branch}?recursive=1")
    if code == 200 and isinstance(data, dict):
        return {
            entry["path"]: entry["sha"]
            for entry in data.get("tree", [])
            if entry.get("type") == "blob"
        }
    if code == 409:
        # Empty repo (no commits yet) — treat as empty.
        return {}
    raise RuntimeError(f"Failed to list tree of {repo}@{branch}: {code} {data!r}")


def get_contents_sha(repo: str, branch: str, path: str) -> str | None:
    """Return the Contents-API SHA of an existing file, or None if missing."""
    quoted = urllib.parse.quote(path, safe="/")
    code, data = gh("GET", f"/repos/{repo}/contents/{quoted}?ref={branch}")
    if code == 200 and isinstance(data, dict):
        return data.get("sha")
    if code == 404:
        return None
    raise RuntimeError(f"Failed to read {repo}@{branch}:{path}: {code} {data!r}")


def put_file(repo: str, branch: str, path: str, raw: bytes, sha: str | None,
             message: str | None = None) -> None:
    quoted = urllib.parse.quote(path, safe="/")
    payload = {
        "message": message or f"sync: update {path}",
        "branch": branch,
        "content": base64.b64encode(raw).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    code, data = gh("PUT", f"/repos/{repo}/contents/{quoted}", payload)
    if code not in (200, 201):
        raise RuntimeError(f"PUT {path} failed: {code} {data!r}")


def delete_file(repo: str, branch: str, path: str, sha: str,
                message: str | None = None) -> None:
    quoted = urllib.parse.quote(path, safe="/")
    payload = {
        "message": message or f"sync: remove {path}",
        "branch": branch,
        "sha": sha,
    }
    code, data = gh("DELETE", f"/repos/{repo}/contents/{quoted}", payload)
    if code != 200:
        raise RuntimeError(f"DELETE {path} failed: {code} {data!r}")


# ----------------------------------------------------------------------------
# Diff & sync
# ----------------------------------------------------------------------------

def collect_local(root: pathlib.Path) -> dict[str, bytes]:
    """Return {rel_posix_path: bytes} for every regular file under root."""
    out: dict[str, bytes] = {}
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def is_ignored(path: str, ignore_first_segments: set[str]) -> bool:
    """Skip anything whose first path segment matches `ignore_first_segments`.

    So ".git" matches ".git", ".git/HEAD", ".git/config", but NOT
    ".gitignore". This is enough to preserve top-level meta files like
    LICENSE / README even though they're not in the source directory.
    """
    return path.split("/", 1)[0] in ignore_first_segments


def sync(src: pathlib.Path, repo: str, branch: str,
         ignore_first_segments: set[str]) -> int:
    print(f"== Syncing {src}  →  {repo}@{branch} ==", flush=True)
    t0 = time.time()

    print("Listing remote tree…", flush=True)
    remote = list_remote_tree(repo, branch)
    print(f"  remote blobs: {len(remote)}", flush=True)

    print("Reading local files…", flush=True)
    local_raw = collect_local(src)
    local = {rel: git_blob_sha(raw) for rel, raw in local_raw.items()}
    print(f"  local files:  {len(local)}", flush=True)

    upserts = [rel for rel, sha in local.items() if remote.get(rel) != sha]
    deletes = [
        rel for rel in remote
        if rel not in local and not is_ignored(rel, ignore_first_segments)
    ]
    print(f"Plan: {len(upserts)} upserts, {len(deletes)} deletes", flush=True)

    errors = 0

    for rel in upserts:
        raw = local_raw[rel]
        try:
            existing = get_contents_sha(repo, branch, rel)
        except RuntimeError as exc:  # pragma: no cover - network path
            print(f"META FAIL {rel}: {exc}", file=sys.stderr, flush=True)
            errors += 1
            continue
        try:
            put_file(repo, branch, rel, raw, sha=existing)
            print(f"UPSERT {rel}  ({len(raw)} B)", flush=True)
        except RuntimeError as exc:  # pragma: no cover - network path
            print(f"PUT FAIL {rel}: {exc}", file=sys.stderr, flush=True)
            errors += 1
        time.sleep(0.15)

    for rel in deletes:
        try:
            existing = get_contents_sha(repo, branch, rel)
        except RuntimeError as exc:  # pragma: no cover - network path
            print(f"META FAIL {rel}: {exc}", file=sys.stderr, flush=True)
            errors += 1
            continue
        if not existing:
            print(f"SKIP delete (already gone): {rel}", flush=True)
            continue
        try:
            delete_file(repo, branch, rel, sha=existing)
            print(f"DELETE {rel}", flush=True)
        except RuntimeError as exc:  # pragma: no cover - network path
            print(f"DEL FAIL {rel}: {exc}", file=sys.stderr, flush=True)
            errors += 1
        time.sleep(0.15)

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s · errors: {errors}", flush=True)
    return errors


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("local_dir", help="Source directory whose contents mirror target repo")
    parser.add_argument("repo", help="Target repo in `owner/name` form")
    parser.add_argument("--branch", default="main", help="Target branch (default: main)")
    parser.add_argument(
        "--ignore-prefix",
        default=".git,LICENSE,LICENSE_CN.md,README.md,README_CN.md",
        help="Comma-separated top-level path segments that must be preserved "
             "on the remote even if missing locally",
    )
    args = parser.parse_args()

    if "SYNC_PAT" not in os.environ:
        print("ERROR: SYNC_PAT environment variable is required.", file=sys.stderr)
        return 2

    src = pathlib.Path(args.local_dir).resolve()
    if not src.is_dir():
        print(f"ERROR: not a directory: {src}", file=sys.stderr)
        return 2

    ignore = {seg.strip() for seg in args.ignore_prefix.split(",") if seg.strip()}

    return sync(src, args.repo, args.branch, ignore)


if __name__ == "__main__":
    sys.exit(main())
