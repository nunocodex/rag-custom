"""
collection/app/utils.py — Pure utility functions (no external dependencies).

Extracted from main.py to enable unit testing without importing
FastAPI, httpx, llama-index, or chromadb.

These functions are re-exported by collection/app/main.py.
"""

import hashlib


def compute_content_hash(text: str) -> str:
    """Compute a SHA-256 hex digest for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def calculate_trust_score(url: str, title: str, engine: str) -> int:
    """
    Assign a trust score (1-10) based on URL domain, title keywords,
    and search engine provenance.

    Max score = default (5) + boosts, clamped to [1, 10].
    """
    score = 5  # default

    # Boost for known authoritative domains
    if any(domain in url for domain in ["github.com", "arxiv.org",
                                         "wikipedia.org", "stackoverflow.com",
                                         "docs."]):
        score += 3
    if any(domain in url for domain in [".edu", ".gov", ".org"]):
        score += 2

    # Boost for known authoritative engines
    if engine in ["wikipedia", "github", "arxiv", "pubmed"]:
        score += 2

    # Penalty for clickbait-style titles
    if any(word in title.lower()
           for word in ["top 10", "amazing", "best", "secret",
                        "you won't believe"]):
        score -= 2

    return max(1, min(10, score))
