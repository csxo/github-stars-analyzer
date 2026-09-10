"""Collectors: GitHub API client + high-level drivers."""

from .github_api import GitHubClient, GitHubAPIError, GitHubRateLimitError, RateLimitInfo
from .stars import collect_stars, collect_readmes

__all__ = [
    "GitHubClient",
    "GitHubAPIError",
    "GitHubRateLimitError",
    "RateLimitInfo",
    "collect_stars",
    "collect_readmes",
]