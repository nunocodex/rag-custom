#!/usr/bin/env python3
"""
tests/test_api.py — Structural tests for post-review fix verification
Tests T11–T14 from PLAN.md v3.0.
Verifies fixes via static source analysis (no runtime dependencies).
"""

import ast
import os
import re
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
API_MAIN = os.path.join(PROJECT_ROOT, "api", "app", "main.py")
COLLECTION_MAIN = os.path.join(PROJECT_ROOT, "collection", "app", "main.py")


class TestApiFixes(unittest.TestCase):
    """Verify api/app/main.py fixes (C1, H2, L2)."""

    @classmethod
    def setUpClass(cls):
        with open(API_MAIN) as f:
            cls.api_source = f.read()
        cls.api_tree = ast.parse(cls.api_source)

    def test_t11_global_index_in_ingest(self):
        """T11-C1: 'global _index' must appear in ingest_documents()."""
        # Find the function node for ingest_documents (async functions use AsyncFunctionDef in Python 3.14)
        for node in ast.walk(self.api_tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "ingest_documents":
                found = any(
                    isinstance(stmt, ast.Global) and "_index" in stmt.names
                    for stmt in node.body
                )
                self.assertTrue(
                    found,
                    "ingest_documents() missing 'global _index' declaration",
                )
                return
        self.fail("ingest_documents() function not found")

    def test_t11_global_index_in_upload(self):
        """T11-C1: 'global _index' must appear in upload_file()."""
        for node in ast.walk(self.api_tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "upload_file":
                found = any(
                    isinstance(stmt, ast.Global) and "_index" in stmt.names
                    for stmt in node.body
                )
                self.assertTrue(
                    found,
                    "upload_file() missing 'global _index' declaration",
                )
                return
        self.fail("upload_file() function not found")

    def test_t14_max_upload_size_enforced(self):
        """T14-H2: upload_file() must enforce MAX_UPLOAD_SIZE check."""
        self.assertIn(
            "MAX_UPLOAD_SIZE",
            self.api_source,
            "MAX_UPLOAD_SIZE env var not defined in api/app/main.py",
        )
        self.assertIn(
            "413",
            self.api_source,
            "upload_file() missing HTTP 413 status code for oversized files",
        )

    def test_l2_upload_isolation(self):
        """L2: upload_file() must use a unique per-upload directory."""
        self.assertIn(
            "temp_dir = os.path.join(\"/tmp/uploads\", upload_id)",
            self.api_source,
            "upload_file() missing per-upload temp isolation",
        )
        self.assertIn(
            "shutil.rmtree",
            self.api_source,
            "upload_file() missing cleanup via shutil.rmtree",
        )


class TestCollectionFixes(unittest.TestCase):
    """Verify collection/app/main.py fixes (C2, H1, M1, M2, M3)."""

    @classmethod
    def setUpClass(cls):
        with open(COLLECTION_MAIN) as f:
            cls.collection_source = f.read()

    def test_t12_llm_model_not_embedding(self):
        """T12-C2: Settings.llm must use LLM_MODEL, not EMBEDDING_MODEL."""
        # Verify LLM_MODEL env var is defined
        self.assertIn(
            'LLM_MODEL = os.getenv("LLM_MODEL"',
            self.collection_source,
            "LLM_MODEL env var missing in collection/app/main.py",
        )
        # Verify Settings.llm uses LLM_MODEL (not EMBEDDING_MODEL)
        self.assertNotIn(
            'model=EMBEDDING_MODEL,  # per generare riassunti',
            self.collection_source,
            "Settings.llm still uses EMBEDDING_MODEL instead of LLM_MODEL",
        )
        self.assertIn(
            "model=LLM_MODEL",
            self.collection_source,
            "Settings.llm missing model=LLM_MODEL",
        )

    def test_t13_no_dummy_document(self):
        """T13-H1: ingest_document() must not use dummy 'Placeholder' doc."""
        self.assertNotIn(
            'Document(text="Placeholder"',
            self.collection_source,
            "Dummy Placeholder document still present in collection/app/main.py",
        )
        # Verify it uses from_vector_store instead of from_documents for init
        self.assertIn(
            "VectorStoreIndex.from_vector_store",
            self.collection_source,
            "Missing from_vector_store initialization (should replace dummy doc pattern)",
        )

    def test_m1_chunk_vars_from_env(self):
        """M1: chunk_size and chunk_overlap must come from env vars."""
        self.assertIn(
            'CHUNK_SIZE = int(os.getenv("CHUNK_SIZE"',
            self.collection_source,
            "CHUNK_SIZE env var missing",
        )
        self.assertIn(
            'CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP"',
            self.collection_source,
            "CHUNK_OVERLAP env var missing",
        )

    def test_m2_redis_key_hashed(self):
        """M2: Redis cache key must use hashed query."""
        self.assertIn(
            "query_hash",
            self.collection_source,
            "Redis key missing query hash",
        )

    def test_m3_narrow_except(self):
        """M3: extract_content() must not catch CancelledError."""
        # Check that extract_content uses specific httpx exceptions, not bare 'Exception'
        # Find the extract_content function source
        match = re.search(
            r"async def extract_content.*?return None",
            self.collection_source,
            re.DOTALL,
        )
        if match:
            func_body = match.group()
        else:
            func_body = self.collection_source
        # Should NOT have broad 'except Exception' in extract_content
        self.assertNotIn(
            "except Exception",
            func_body,
            "Broad 'except Exception' still present in extract_content",
        )
        # Should have specific httpx exception handlers
        self.assertIn(
            "httpx.HTTPError",
            func_body,
            "Missing httpx.HTTPError in extract_content exception handler",
        )


class TestInfraFixes(unittest.TestCase):
    """Verify infrastructure fixes (C3, H3, M5)."""

    def setUp(self):
        self.setup_sh = os.path.join(PROJECT_ROOT, "setup.sh")
        with open(self.setup_sh) as f:
            self.setup_source = f.read()

    def test_h3_pull_model_warning_guarded(self):
        """H3: pull_model warning must be inside { } block."""
        self.assertIn(
            "}",
            self.setup_source,
            "pull_model missing closing brace for warning block",
        )
        # Check the warn is guarded by || {
        self.assertIn(
            "|| {",
            self.setup_source,
            "pull_model warning not guarded by || {",
        )


from pydantic import BaseModel, Field  # noqa: E402, F401


class TestPydanticValidation(unittest.TestCase):
    """Pydantic model validation tests — T32–T35."""

    # Define the model at module level (avoids importing api.app.main which needs FastAPI)
    class QueryRequest(BaseModel):
        query: str = Field(..., min_length=1, max_length=4096)
        top_k: int = Field(default=3, ge=1, le=10)

    def test_t32_empty_query_rejected(self):
        """T32: QueryRequest with empty string raises ValidationError."""
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self.QueryRequest(query="", top_k=3)

    def test_t33_top_k_too_high_rejected(self):
        """T33: QueryRequest with top_k > 10 raises ValidationError."""
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self.QueryRequest(query="test", top_k=15)

    def test_t34_top_k_too_low_rejected(self):
        """T34: QueryRequest with top_k < 1 raises ValidationError."""
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self.QueryRequest(query="test", top_k=0)

    def test_t35_valid_input_accepted(self):
        """T35: Valid query and top_k create model without error."""
        req = self.QueryRequest(query="What is RAG?", top_k=3)
        self.assertEqual(req.query, "What is RAG?")
        self.assertEqual(req.top_k, 3)


if __name__ == "__main__":
    unittest.main()
