#!/usr/bin/env python3
"""
test_units.py — Unit tests for pure functions.

Tests T17–T26 from PLAN.md v4.0.
Covers compute_content_hash and calculate_trust_score.
"""

import unittest
import sys
import os

# Import the actual functions from the pure utils module (no external deps)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from collection.app.utils import compute_content_hash, calculate_trust_score


class TestComputeContentHash(unittest.TestCase):
    """Tests for compute_content_hash — T17, T18, T19."""

    def test_t17_same_input_same_hash(self):
        """T17: Same input produces the same hash (determinism)."""
        text = "The quick brown fox jumps over the lazy dog"
        h1 = compute_content_hash(text)
        h2 = compute_content_hash(text)
        self.assertEqual(h1, h2, "Hash must be deterministic for identical input")

    def test_t18_different_input_different_hash(self):
        """T18: Different inputs produce different hashes."""
        h1 = compute_content_hash("Hello, world!")
        h2 = compute_content_hash("Hello, world?")
        self.assertNotEqual(h1, h2, "Different inputs must produce different hashes")

    def test_t19_empty_string_hash(self):
        """T19: Empty string produces a valid SHA-256 hex digest."""
        h = compute_content_hash("")
        self.assertEqual(len(h), 64, "SHA-256 hex digest must be 64 characters")
        self.assertTrue(all(c in "0123456789abcdef" for c in h),
                        "SHA-256 hex digest must contain only hex characters")


class TestCalculateTrustScore(unittest.TestCase):
    """Tests for calculate_trust_score — T20–T26."""

    def _test(self, desc: str, url: str, title: str, engine: str, expected: int):
        """Helper: run a single trust score assertion."""
        score = calculate_trust_score(url, title, engine)
        self.assertEqual(score, expected,
                         f"{desc}: expected {expected}, got {score}")

    def test_t20_github_engine_github(self):
        """T20: GitHub URL + GitHub engine → 10 (max)."""
        self._test(
            "github project with github engine",
            "https://github.com/nunocodex/rag-custom",
            "RAG Custom Project",
            "github",
            10,
        )

    def test_t21_wikipedia_engine_wikipedia(self):
        """T21: Wikipedia URL + Wikipedia engine → 10 (max)."""
        self._test(
            "wikipedia with wikipedia engine",
            "https://en.wikipedia.org/wiki/RAG",
            "RAG — Wikipedia",
            "wikipedia",
            10,
        )

    def test_t22_edu_domain_standard_engine(self):
        """T22: .edu domain + standard engine → 7."""
        self._test(
            "edu domain with duckduckgo engine",
            "https://research.university.edu/papers/ai",
            "AI Research Paper",
            "duckduckgo",
            7,  # 5 + 2 (edu)
        )

    def test_t23_clickbait_title_penalty(self):
        """T23: Clickbait title triggers penalty → 3."""
        self._test(
            "clickbait title with standard engine",
            "https://blog.example.com/post",
            "Top 10 Amazing Secrets You Won't Believe!",
            "bing",
            3,  # 5 - 2 (clickbait)
        )

    def test_t24_clickbait_multiple_triggers(self):
        """T24: Multiple clickbait keywords in title → 3."""
        self._test(
            "multiple clickbait keywords",
            "https://clickbait.example.com",
            "The Best Secret You Won't Believe Is Amazing!",
            "unknown",
            3,  # 5 - 2 (clickbait)
        )

    def test_t25_default_score(self):
        """T25: Unknown domain + unknown engine → 5 (default)."""
        self._test(
            "bare minimum (no boosts, no penalties)",
            "https://example.com/page",
            "Some random title",
            "none",
            5,  # default
        )

    def test_t26_score_clamping(self):
        """T26: Score clamped to [1, 10] even with extreme boosts."""
        # Massively boosted: github domain (+3) + .org domain (+2) + stackoverflow engine (+2) = up to 12
        # But clamped to 10
        score = calculate_trust_score(
            "https://github.com/org/project",
            "Open Source Project",
            "stackoverflow",
        )
        self.assertLessEqual(score, 10, "Score must not exceed 10")
        self.assertGreaterEqual(score, 1, "Score must be at least 1")

        # Massively penalized
        score = calculate_trust_score(
            "https://spam.com/best-deals",
            "Top 10 Amazing Best Secret Deals",
            "unknown",
        )
        self.assertLessEqual(score, 10, "Score must not exceed 10")
        self.assertGreaterEqual(score, 1, "Score must be at least 1")


if __name__ == "__main__":
    unittest.main()
