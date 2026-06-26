from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import chromadb
from chromadb.config import Settings as ChromaSettings
from fastapi import FastAPI, File, HTTPException, UploadFile

# LlamaIndex
from llama_index.core import (
    Settings,
    SimpleDirectoryReader,
    VectorStoreIndex,
)
from llama_index.core.storage import StorageContext
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.vector_stores.chroma import ChromaVectorStore  # CORRETTO
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

# ---------- Config ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
CHROMA_HOST = os.getenv("CHROMA_HOST", "chromadb")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8000))
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "rag_collection")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.1:8b")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 512))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 128))
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", str(32 * 1024 * 1024)))  # 32 MB default
DOCS_DIR = "/app/docs"

# ---------- Pydantic Models ----------
class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4096)
    top_k: int = Field(default=3, ge=1, le=10)

class QueryResponse(BaseModel):
    response: str
    sources: list[str]

class IngestResponse(BaseModel):
    status: str
    ingested: int
    message: str | None = None

# ---------- Lifespan ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify dependencies
    await verify_services()
    yield
    # Shutdown: nothing to clean

app = FastAPI(
    title="Enterprise RAG API",
    description="FastAPI + LlamaIndex + Ollama + ChromaDB (enterprise-grade)",
    version="2.0.0",
    lifespan=lifespan,
)

# ---------- Globals ----------
_index: VectorStoreIndex | None = None
_chroma_client: chromadb.HttpClient | None = None
_vector_store: ChromaVectorStore | None = None

# ---------- Retry Helpers ----------
@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=10))
async def verify_services():
    """Check connectivity to Ollama and ChromaDB."""
    # Check Ollama
    import httpx
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
            resp.raise_for_status()
            logger.info("Ollama reachable")
        except Exception as e:
            logger.error(f"Ollama not reachable: {e}")
            raise

    # Check ChromaDB
    try:
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        client.heartbeat()
        logger.info("ChromaDB reachable")
    except Exception as e:
        logger.error(f"ChromaDB not reachable: {e}")
        raise

def get_chroma_client() -> chromadb.HttpClient:
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.HttpClient(
            host=CHROMA_HOST,
            port=CHROMA_PORT,
            settings=ChromaSettings(anonymized_telemetry=False)
        )
    return _chroma_client

def get_vector_store() -> ChromaVectorStore:
    global _vector_store
    if _vector_store is None:
        client = get_chroma_client()
        try:
            client.get_collection(CHROMA_COLLECTION)
        except Exception:
            client.create_collection(CHROMA_COLLECTION)
        collection = client.get_collection(CHROMA_COLLECTION)
        _vector_store = ChromaVectorStore(chroma_collection=collection)
    return _vector_store

def get_index() -> VectorStoreIndex | None:
    global _index
    if _index is not None:
        return _index

    # Configure LlamaIndex settings
    Settings.embed_model = OllamaEmbedding(
        model_name=EMBEDDING_MODEL,
        base_url=OLLAMA_BASE_URL,
    )
    Settings.llm = Ollama(
        model=LLM_MODEL,
        base_url=OLLAMA_BASE_URL,
        request_timeout=120.0,
        temperature=0.1,
    )
    Settings.chunk_size = CHUNK_SIZE
    Settings.chunk_overlap = CHUNK_OVERLAP

    vector_store = get_vector_store()
    try:
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        _index = VectorStoreIndex.from_vector_store(
            vector_store, storage_context=storage_context
        )
        logger.info("Index loaded from ChromaDB")
    except Exception as e:
        logger.warning(f"Index not found: {e}. Create via /ingest first.")
        _index = None
    return _index

# ---------- Endpoints ----------
@app.get("/")
async def root():
    return {"status": "ok", "service": "Enterprise RAG API"}

@app.get("/health")
async def health():
    try:
        await verify_services()
        return {"status": "healthy"}
    except Exception:
        raise HTTPException(status_code=503, detail="Dependencies not available")

@app.post("/ingest", response_model=IngestResponse)
async def ingest_documents():
    """Scan DOCS_DIR and ingest all supported files."""
    global _index
    if not os.path.exists(DOCS_DIR):
        raise HTTPException(status_code=404, detail="Docs directory not found")

    try:
        reader = SimpleDirectoryReader(DOCS_DIR, recursive=True)
        docs = reader.load_data()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read docs: {str(e)}")

    if not docs:
        raise HTTPException(status_code=400, detail="No documents found in directory")

    # Ensure index exists
    index = get_index()
    if index is None:
        # Fresh creation
        vector_store = get_vector_store()
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        _index = VectorStoreIndex.from_documents(docs, storage_context=storage_context)
        logger.info(f"Created new index with {len(docs)} documents")
    else:
        # Insert incrementally
        for doc in docs:
            index.insert(doc)
        logger.info(f"Inserted {len(docs)} documents into existing index")

    return IngestResponse(status="ok", ingested=len(docs))

@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    index = get_index()
    if index is None:
        raise HTTPException(status_code=503, detail="Index not initialized. Please ingest documents first.")

    # Retrieve
    retriever = index.as_retriever(similarity_top_k=req.top_k)
    nodes = retriever.retrieve(req.query)

    if not nodes:
        return QueryResponse(response="No relevant context found.", sources=[])

    # Build context
    context_text = "\n\n".join([node.text for node in nodes])
    sources = list(set(
        node.metadata.get("file_name", "unknown") for node in nodes if "file_name" in node.metadata
    ))

    # Generate response using LLM
    prompt = f"""Context information is below.
---------------------
{context_text}
---------------------
Given the context information and not prior knowledge, answer the query.
Query: {req.query}
Answer:"""

    try:
        llm = Settings.llm
        response = await llm.acomplete(prompt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM generation failed: {str(e)}")

    return QueryResponse(response=response.text, sources=sources)

# ---------- Optional: Upload endpoint ----------
@app.post("/upload", response_model=IngestResponse)
async def upload_file(file: UploadFile = File(...)):
    """Upload a single file and ingest it."""
    global _index
    import shutil
    import uuid

    # Validate file size before reading
    upload_id = str(uuid.uuid4())
    temp_dir = os.path.join("/tmp/uploads", upload_id)
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, file.filename)

    size_check_ok = True
    try:
        # Stream-write with size check
        total_size = 0
        with open(temp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1 MB chunks
                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE:
                    size_check_ok = False
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds maximum upload size of {MAX_UPLOAD_SIZE // (1024*1024)} MB",
                    )
                f.write(chunk)

        if size_check_ok:
            # Read into LlamaIndex
            reader = SimpleDirectoryReader(temp_dir, recursive=False)
            docs = reader.load_data()

            if not docs:
                raise HTTPException(status_code=400, detail="Could not parse file")

            index = get_index()
            if index is None:
                vector_store = get_vector_store()
                storage_context = StorageContext.from_defaults(vector_store=vector_store)
                _index = VectorStoreIndex.from_documents(docs, storage_context=storage_context)
            else:
                for doc in docs:
                    index.insert(doc)

            return IngestResponse(status="ok", ingested=len(docs))

    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process upload: {str(e)}")
    finally:
        # Clean up unique temp directory
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
