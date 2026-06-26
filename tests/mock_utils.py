"""
mock_utils.py — Shared mock factories for integration tests.

Provides helper functions that create mock objects for external services
(Ollama, ChromaDB, LlamaIndex) using unittest.mock. All mocks use
MagicMock to avoid tight coupling to specific API versions.

Usage:
    from tests.mock_utils import mock_ollama_healthy

    with mock_ollama_healthy():
        await verify_services()  # runs against mock, not real Ollama
"""

from unittest.mock import AsyncMock, MagicMock, patch
from typing import Optional


def mock_ollama_healthy():
    """
    Patch httpx.AsyncClient so that GET /api/tags returns 200.
    Usage as context manager:
        with mock_ollama_healthy():
            ...
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client

    return patch("httpx.AsyncClient", return_value=mock_client)


def mock_ollama_timeout():
    """
    Patch httpx.AsyncClient so that GET /api/tags raises TimeoutException.
    Usage as context manager:
        with mock_ollama_timeout():
            ...
    """
    from httpx import TimeoutException

    mock_client = AsyncMock()
    mock_client.get.side_effect = TimeoutException("Connection timed out")
    mock_client.__aenter__.return_value = mock_client

    return patch("httpx.AsyncClient", return_value=mock_client)


def mock_chromadb_healthy():
    """
    Patch chromadb.HttpClient so that heartbeat() succeeds.
    """
    mock_client = MagicMock()
    mock_client.heartbeat.return_value = 1234567890.0

    return patch("chromadb.HttpClient", return_value=mock_client)


def mock_chromadb_failing():
    """
    Patch chromadb.HttpClient so that heartbeat() raises ValueError.
    """
    mock_client = MagicMock()
    mock_client.heartbeat.side_effect = ValueError("Connection refused")

    return patch("chromadb.HttpClient", return_value=mock_client)


def mock_ollama_chromadb_healthy():
    """Convenience: both Ollama and ChromaDB mocks active simultaneously."""
    return mock_ollama_healthy(), mock_chromadb_healthy()


def mock_vector_store_index(index_exists: bool = True):
    """
    Mock LlamaIndex's VectorStoreIndex.from_vector_store.
    If index_exists=True: returns a MagicMock index.
    If index_exists=False: raises an exception (no index found).
    """
    mock_index = MagicMock() if index_exists else None

    if not index_exists:

        def _raise(*args, **kwargs):
            raise ValueError("No existing collection found")

        return patch(
            "llama_index.core.VectorStoreIndex.from_vector_store",
            side_effect=_raise,
        )

    return patch(
        "llama_index.core.VectorStoreIndex.from_vector_store",
        return_value=mock_index,
    )


# Default Ollama + ChromaDB URLs for test assertions
FAKE_OLLAMA_URL = "http://mock-ollama:11434"
FAKE_CHROMA_HOST = "mock-chromadb"
FAKE_CHROMA_PORT = 8000
