import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import chromadb
import httpx
import redis.asyncio as redis
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from bs4 import BeautifulSoup
from fastapi import BackgroundTasks, FastAPI

# LlamaIndex
from llama_index.core import Document, Settings, VectorStoreIndex
from llama_index.core.storage import StorageContext
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.vector_stores.chroma import ChromaVectorStore
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from .utils import calculate_trust_score, compute_content_hash

# ---------- Configuration ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CHROMA_HOST = os.getenv("CHROMA_HOST", "chromadb")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8000))
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "rag_collection")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.1:8b")
TTL_DAYS_DEFAULT = int(os.getenv("TTL_DAYS_DEFAULT", "30"))
TRUST_SCORE_MIN = int(os.getenv("TRUST_SCORE_MIN", "3"))
SCHEDULE_INTERVAL_HOURS = int(os.getenv("SCHEDULE_INTERVAL_HOURS", "24"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "512"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "128"))

# ---------- Pydantic Models ----------
class WebSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=512)
    top_k: int = Field(default=5, ge=1, le=20)
    include_extraction: bool = Field(default=True)
    ttl_days: int = Field(default=TTL_DAYS_DEFAULT, ge=1, le=365)

class WebSearchResponse(BaseModel):
    query: str
    sources: list[dict[str, Any]]
    cached: bool
    ingested: int

class DocumentStatus(BaseModel):
    url: str
    version: int
    last_fetch: datetime
    ttl_expiry: datetime
    trust_score: int
    hash: str
    source: str

# ---------- Lifespan ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: start scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        refresh_expired_documents,
        trigger=IntervalTrigger(hours=SCHEDULE_INTERVAL_HOURS),
        id="refresh_expired",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started: refresh_expired every %s hours", SCHEDULE_INTERVAL_HOURS)
    yield
    # Shutdown
    scheduler.shutdown()

app = FastAPI(
    title="Collection Service",
    description="Orchestrates web search, caching, deduplication, and RAG ingestion",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------- Globals ----------
redis_client = None
_chroma_client = None
_vector_store = None
_index = None

# ---------- Redis Client ----------
async def get_redis():
    global redis_client
    if redis_client is None:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return redis_client

# ---------- Chroma Helpers ----------
def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.HttpClient(
            host=CHROMA_HOST,
            port=CHROMA_PORT,
            settings=chromadb.config.Settings(anonymized_telemetry=False)
        )
    return _chroma_client

def get_vector_store():
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

def get_index():
    global _index
    if _index is not None:
        return _index

    Settings.embed_model = OllamaEmbedding(
        model_name=EMBEDDING_MODEL,
        base_url=OLLAMA_BASE_URL,
    )
    Settings.llm = Ollama(
        model=LLM_MODEL,
        base_url=OLLAMA_BASE_URL,
        request_timeout=60.0,
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
        logger.warning(f"Index not found: {e}")
        _index = None
    return _index

# ---------- Core Functions ----------

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def search_searxng(query: str, top_k: int) -> list[dict[str, Any]]:
    """Search via SearXNG API."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{SEARXNG_URL}/search",
            params={
                "q": query,
                "format": "json",
                "categories": "general",
                "engines": "duckduckgo,bing,brave,stackoverflow,wikipedia,github",
                "language": "en",
            }
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        # Deduplicate by URL
        seen = set()
        deduped = []
        for r in results:
            url = r.get("url", "")
            if url and url not in seen:
                seen.add(url)
                deduped.append(r)
        return deduped[:top_k]

async def extract_content(url: str) -> str | None:
    """Extract text content from URL using httpx + BeautifulSoup (fallback)."""
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            # Remove script and style elements
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            # Clean excessive whitespace
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = "\n".join(chunk for chunk in chunks if chunk)
            return text if len(text) > 50 else None
    except (httpx.HTTPError, httpx.TimeoutException, httpx.RequestError) as e:
        logger.warning("Extraction error for %s: %s", url, str(e))
        return None

async def ingest_document(content: str, metadata: dict[str, Any]) -> bool:
    """Ingest a document into ChromaDB, creating index if missing."""
    global _index

    index = get_index()
    if index is None:
        # Create a new empty index from vector store (no dummy documents)
        logger.info("Index not found, creating new empty index from vector store...")
        vector_store = get_vector_store()
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        _index = VectorStoreIndex.from_vector_store(
            vector_store, storage_context=storage_context
        )
        logger.info("New empty index created.")
        index = _index

    try:
        doc = Document(text=content, metadata=metadata)
        index.insert(doc)
        logger.info("Ingested document: %s", metadata.get("url", "unknown"))
        return True
    except Exception as e:
        logger.error("Ingestion failed: %s", str(e))
        return False

async def cache_result(query: str, results: list[dict[str, Any]], ttl_days: int) -> None:
    """Cache search results in Redis with TTL."""
    r = await get_redis()
    # Hash the query to avoid long/special-character keys in Redis
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    key = f"search:{query_hash}"
    ttl_seconds = ttl_days * 86400
    await r.setex(key, ttl_seconds, json.dumps(results))

async def get_cached_result(query: str) -> list[dict[str, Any]] | None:
    """Retrieve cached search results from Redis."""
    r = await get_redis()
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    key = f"search:{query_hash}"
    data = await r.get(key)
    if data:
        return json.loads(data)
    return None

async def refresh_expired_documents():
    """Background job: refresh expired documents."""
    logger.info("Running refresh_expired_documents job...")
    r = await get_redis()
    keys = await r.keys("doc:*")
    expired = []
    for key in keys:
        doc_data = await r.get(key)
        if doc_data:
            doc = json.loads(doc_data)
            if datetime.now() > datetime.fromisoformat(doc["ttl_expiry"]):
                expired.append(doc)
    if expired:
        logger.info("Refreshing %s expired documents", len(expired))
        for doc in expired:
            url = doc["url"]
            content = await extract_content(url)
            if content:
                new_hash = compute_content_hash(content)
                if new_hash != doc["hash"]:
                    doc["version"] += 1
                    doc["hash"] = new_hash
                    doc["last_fetch"] = datetime.now().isoformat()
                    doc["ttl_expiry"] = (datetime.now() + timedelta(days=doc.get("ttl_days", TTL_DAYS_DEFAULT))).isoformat()
                    # Re-ingest
                    await ingest_document(content, {"url": url, "source": doc["source"], "trust_score": doc["trust_score"]})
                    await r.set(key, json.dumps(doc))
                    logger.info("Refreshed and re-ingested: %s", url)
    else:
        logger.info("No expired documents found.")

# ---------- API Endpoints ----------

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/search", response_model=WebSearchResponse)
async def web_search(req: WebSearchRequest, background_tasks: BackgroundTasks):
    """Main search endpoint: cache → search → extract → ingest."""
    query = req.query.strip()
    top_k = req.top_k
    ttl_days = req.ttl_days

    # 1. Check cache
    cached = await get_cached_result(query)
    if cached:
        logger.info("Cache hit for query: %s", query)
        return WebSearchResponse(
            query=query,
            sources=cached,
            cached=True,
            ingested=0
        )

    # 2. Search via SearXNG
    results = await search_searxng(query, top_k)
    sources = []
    ingested_count = 0

    for result in results:
        url = result.get("url", "")
        title = result.get("title", "")
        engine = result.get("engine", "unknown")
        snippet = result.get("snippet", "")

        # Skip if no URL
        if not url:
            continue

        # Trust score
        trust = calculate_trust_score(url, title, engine)
        if trust < TRUST_SCORE_MIN:
            logger.info("Skipping low-trust URL: %s (score %s)", url, trust)
            continue

        # Extract content (or create fallback)
        content = None
        if req.include_extraction:
            content = await extract_content(url)
            if not content:
                fallback_parts = [f"Title: {title}", f"URL: {url}"]
                if snippet:
                    fallback_parts.append(f"Snippet: {snippet}")
                content = "\n".join(fallback_parts)
                logger.warning("Extraction failed for %s, using fallback", url)
        else:
            fallback_parts = [f"Title: {title}", f"URL: {url}"]
            if snippet:
                fallback_parts.append(f"Snippet: {snippet}")
            content = "\n".join(fallback_parts)

        if content:
            # Compute hash for deduplication
            content_hash = compute_content_hash(content)
            # Ingest into RAG
            metadata = {
                "url": url,
                "title": title,
                "engine": engine,
                "trust_score": trust,
                "source": "web",
                "query": query,
                "ttl_days": ttl_days,
                "hash": content_hash,
            }
            success = await ingest_document(content, metadata)
            if success:
                ingested_count += 1
                # Store document metadata in Redis for TTL management
                r = await get_redis()
                doc_key = f"doc:{url}"
                doc_meta = {
                    "url": url,
                    "hash": content_hash,
                    "version": 1,
                    "last_fetch": datetime.now().isoformat(),
                    "ttl_expiry": (datetime.now() + timedelta(days=ttl_days)).isoformat(),
                    "trust_score": trust,
                    "source": "web",
                    "ttl_days": ttl_days,
                }
                await r.setex(doc_key, ttl_days * 86400, json.dumps(doc_meta))

        source_entry = {
            "url": url,
            "title": title,
            "engine": engine,
            "snippet": snippet,
            "trust_score": trust,
            "extracted": content is not None,
        }
        sources.append(source_entry)

    # 3. Cache the result (sources only, not full content) for future searches
    await cache_result(query, sources, ttl_days)

    return WebSearchResponse(
        query=query,
        sources=sources,
        cached=False,
        ingested=ingested_count
    )

@app.get("/documents/status")
async def list_documents():
    """List all ingested documents with their status."""
    r = await get_redis()
    keys = await r.keys("doc:*")
    docs = []
    for key in keys:
        data = await r.get(key)
        if data:
            doc = json.loads(data)
            docs.append(DocumentStatus(
                url=doc["url"],
                version=doc["version"],
                last_fetch=datetime.fromisoformat(doc["last_fetch"]),
                ttl_expiry=datetime.fromisoformat(doc["ttl_expiry"]),
                trust_score=doc["trust_score"],
                hash=doc["hash"],
                source=doc["source"]
            ))
    return {"total": len(docs), "documents": docs}

@app.post("/documents/refresh")
async def refresh_now(background_tasks: BackgroundTasks):
    """Manually trigger refresh of expired documents."""
    background_tasks.add_task(refresh_expired_documents)
    return {"status": "refresh started"}
