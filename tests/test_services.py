#!/usr/bin/env python3
"""
test_services.py — Integration tests with mocked external services.

Tests T27–T31 from PLAN.md v4.0.
Covers verify_services and get_index from api/app/main.py.
"""

import unittest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock heavy dependencies NOT installed in this env, before importing
for mod_name in [
    "fastapi", "fastapi.responses",
    "llama_index", "llama_index.core",
    "llama_index.core.storage", "llama_index.core.retrievers",
    "llama_index.llms", "llama_index.embeddings",
    "llama_index.vector_stores", "llama_index.readers",
    "llama_index.llms.ollama", "llama_index.embeddings.ollama",
    "llama_index.vector_stores.chroma", "llama_index.readers.file",
    "llama_index.readers.web",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

from api.app.main import verify_services, get_index  # noqa: E402
from tests.mock_utils import (
    mock_ollama_healthy,
    mock_ollama_timeout,
    mock_chromadb_healthy,
    mock_chromadb_failing,
    mock_vector_store_index,
)


class TestVerifyServices(unittest.TestCase):
    """Tests for verify_services — T27, T28, T29."""

    @patch("asyncio.sleep", return_value=None)
    def test_t27_both_healthy(self, _mock_sleep):
        """T27: Ollama + ChromaDB both healthy → no exception."""
        with mock_ollama_healthy():
            with mock_chromadb_healthy():
                try:
                    import asyncio
                    asyncio.run(verify_services())
                except Exception as e:
                    self.fail(f"verify_services raised {type(e).__name__}: {
                              e}")

    @patch("asyncio.sleep", return_value=None)
    def test_t28_ollama_unreachable(self, _mock_sleep):
        """T28: Ollama unreachable → exception propagates after retries."""
        with mock_ollama_timeout():
            with mock_chromadb_healthy():
                import asyncio
                with self.assertRaises(Exception) as ctx:
                    asyncio.run(verify_services())
                # Tenacity wraps last exception in RetryError
                error_text = str(ctx.exception).lower()
                self.assertTrue(
                    "timeout" in error_text or "retryerror" in error_text,
                    f"Expected timeout-related error, got: {ctx.exception}",
                )

    @patch("asyncio.sleep", return_value=None)
    def test_t29_chromadb_unreachable(self, _mock_sleep):
        """T29: ChromaDB unreachable → exception propagates."""
        with mock_ollama_healthy():
            with mock_chromadb_failing():
                import asyncio
                with self.assertRaises(Exception) as ctx:
                    asyncio.run(verify_services())
                # Tenacity wraps last exception in RetryError
                error_text = str(ctx.exception).lower()
                self.assertTrue(
                    "refused" in error_text or "retryerror" in error_text,
                    f"Expected connection-refused error, got: {ctx.exception}",
                )


class TestGetIndex(unittest.TestCase):
    """Tests for get_index — T30, T31."""

    def setUp(self):
        # Reset the global _index so each test starts fresh
        import api.app.main
        api.app.main._index = None
        api.app.main._chroma_client = None
        api.app.main._vector_store = None

    def test_t30_index_loaded_successfully(self):
        """T30: VectorStore loads → index is returned (not None)."""
        with mock_ollama_healthy():
            with mock_chromadb_healthy():
                with mock_vector_store_index(index_exists=True):
                    index = get_index()
                    self.assertIsNotNone(
                        index, "Index should not be None when VectorStore loads"
                    )

    def test_t31_index_not_found(self):
        """T31: No VectorStore → index is None, no crash."""
        with mock_ollama_healthy():
            with mock_chromadb_healthy():
                with mock_vector_store_index(index_exists=False):
                    index = get_index()
                    self.assertIsNone(
                        index, "Index should be None when VectorStore is empty"
                    )


if __name__ == "__main__":
    unittest.main()
