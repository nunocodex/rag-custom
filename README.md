# RAG Custom — Enterprise RAG Stack

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A self‑hosted, production‑ready **Retrieval‑Augmented Generation (RAG)** pipeline powered by **Ollama** LLMs, **ChromaDB** vector store, **SearXNG** meta‑search, and **Redis** caching — all orchestrated via **Docker Compose**.

Designed for developers who want a fully local, private, and customizable RAG system without relying on third‑party APIs. Run it on a single machine with a GPU, or on a server for team use.

---

## Architecture

```
                    ┌─────────────────────┐
                    │     Web Browser      │
                    │  (API Consumer)      │
                    └──────┬──────────┬────┘
                           │          │
                    ┌──────▼──┐  ┌────▼──────────┐
                    │ API     │  │ Collection     │
                    │ (:8080) │  │ Service (:8181)│
                    │ FastAPI │  │ FastAPI        │
                    └──┬──┬───┘  └──┬──┬──────────┘
                       │  │         │  │
              ┌────────▼──▼─────────▼──▼──────────┐
              │           Docker Compose          │
              │  ┌────────┐ ┌────────┐ ┌───────┐  │
              │  │ Ollama │ │ChromaDB│ │ Redis │  │
              │  │ :11434 │ │ :8000  │ │ :6379 │  │
              │  └────────┘ └────────┘ └───────┘  │
              │  ┌──────────┐ ┌────────────────┐  │
              │  │ SearXNG  │ │ Scheduler       │  │
              │  │ :7777    │ │ (cron, :none)   │  │
              │  └──────────┘ └────────────────┘  │
              └────────────────────────────────────┘
```

| Service | Role | Port | Container |
|---------|------|------|-----------|
| **API** | RAG query & document ingestion endpoint | `8080` | `rag-api` |
| **Collection Service** | Web search via SearXNG + ingestion | `8181` | `rag-collection` |
| **Ollama** | LLM inference + embedding generation | `11434` | `rag-ollama` |
| **ChromaDB** | Vector store for document embeddings | `8000` | `rag-chromadb` |
| **SearXNG** | Privacy‑focused meta‑search engine | `7777` | `searxng` |
| **Redis** | Result caching for search queries | `6379` | `rag-redis` |
| **Scheduler** | Scheduled refresh of expired documents | — | (cron in Alpine) |

---

## Prerequisites

| Requirement | Version / Detail |
|-------------|------------------|
| **Docker** | 24+ with Docker Compose v2 (`docker compose` plugin) |
| **NVIDIA GPU** | Optional but recommended for LLM inference speed |
| **nvidia-container-toolkit** | Required for GPU acceleration (see [install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)) |
| **RAM** | 16 GB minimum (llama3.1:8b uses ~6 GB VRAM) |
| **OS** | Linux (tested) / WSL2 with Docker Desktop |
| **Disk** | ~10 GB for Docker images + ~5 GB for LLM models |

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/nunocodex/rag-custom.git
cd rag-custom

# 2. (Optional) Copy and edit the environment file
#    Setup will auto-create .env from .env.example if missing
vim .env   # Set HOST_DOCS_DIR to your documents path

# 3. Run setup
./setup.sh
```

The setup script will:

1. ✅ Check that Docker daemon is running
2. ✅ Create required directories
3. ✅ Bootstrap `.env` from `.env.example` (if missing) and generate a secure `SEARXNG_SECRET_KEY`
4. ✅ Validate environment variables and warn about missing configuration
5. ✅ Start all services via `docker compose up -d --build`
6. ✅ Wait for ChromaDB, Ollama, and Redis to become healthy
7. ✅ Pull required Ollama models (`nomic-embed-text`, `llama3.1:8b`)
8. ✅ Print connection URLs and next steps

---

## Configuration

### Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `API_PORT` | `8080` | RAG API port |
| `HOST_DOCS_DIR` | _(none, required)_ | Host directory with documents for ingestion (`/upload`, `/ingest` endpoints) |
| `CHROMA_COLLECTION` | `rag_collection` | ChromaDB collection name |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Ollama model for text embeddings |
| `LLM_MODEL` | `llama3.1:8b` | Ollama model for LLM inference |
| `CHUNK_SIZE` | `512` | Document chunk size (characters) |
| `CHUNK_OVERLAP` | `128` | Overlap between consecutive chunks |
| `SEARXNG_SECRET_KEY` | _(auto‑generated)_ | Secret key for SearXNG (generated on first setup) |
| `TTL_DAYS_DEFAULT` | `30` | Default TTL for cached search results |
| `TRUST_SCORE_MIN` | `3` | Minimum trust score for search result ingestion |
| `SCHEDULE_INTERVAL_HOURS` | `24` | Interval between automatic document refresh cycles |

---

## API Reference

### RAG API (`http://localhost:8080`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | API root (health info) |
| `/health` | GET | Service health check |
| `/ingest` | POST | Ingest all documents from the configured `HOST_DOCS_DIR` |
| `/query` | POST | Query the RAG index with a question |
| `/upload` | POST | Upload a single document file for ingestion |

**Example — Query:**

```bash
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What is this project about?", "top_k": 3}'
```

**Example — Upload file:**

```bash
curl -X POST http://localhost:8080/upload \
  -F "file=@document.pdf"
```

### Collection Service (`http://localhost:8181`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Service health check |
| `/search` | POST | Search the web via SearXNG and ingest results |
| `/documents/status` | GET | List managed documents with expiry info |
| `/documents/refresh` | POST | Force refresh all expired documents |

**Example — Web Search:**

```bash
curl -X POST http://localhost:8181/search \
  -H "Content-Type: application/json" \
  -d '{"query": "latest AI research papers", "top_k": 5}'
```

---

## Manual Steps (if needed)

### Pull Ollama Models

The setup script pulls models automatically, but you can also do it manually:

```bash
docker exec rag-ollama ollama pull nomic-embed-text
docker exec rag-ollama ollama pull llama3.1:8b
```

### Check Service Logs

```bash
docker compose logs -f api       # RAG API logs
docker compose logs -f collection-service  # Collection service logs
docker compose logs -f ollama    # Ollama logs
```

### Stop All Services

```bash
docker compose down
# To also remove volumes (will delete persisted data):
docker compose down -v
```

---

## Production Deployment

- **Rotate `SEARXNG_SECRET_KEY`**: For production, generate a new key: `openssl rand -base64 32`
- **Enable TLS**: Place a reverse proxy (Caddy, Nginx, Traefik) in front of the services and terminate TLS
- **Resource limits**: Add `deploy.resources.limits` to Docker Compose services to prevent memory exhaustion
- **Backup ChromaDB**: The `data/` directory contains the vector store — back it up regularly
- **Authentication**: Consider adding an API gateway with JWT/OAuth2 for production use

---

## Development

```bash
# Run tests
bash setup.test.sh

# Run a single service locally (without Docker)
cd api && pip install -r requirements.txt && uvicorn app.main:app --reload
cd collection && pip install -r requirements.txt && uvicorn app.main:app --reload --port 8181
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `docker: command not found` | [Install Docker Desktop](https://docs.docker.com/desktop/) and enable WSL2 integration |
| `nvidia-container-cli: initialization error` | Install [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) |
| Ollama container exits immediately | Remove `runtime: nvidia` from `docker-compose.yaml` if no GPU is available |
| `Model 'llama3.1:8b' not found` | Run `docker exec rag-ollama ollama pull llama3.1:8b` manually |
| `Connection refused` on ChromaDB | Wait 10–15 seconds for ChromaDB to initialize; check `docker compose logs chromadb` |
| `.env` parsing errors | Delete `.env` and re-run `./setup.sh` to regenerate from `.env.example` |

---

## License

[MIT](LICENSE) © 2026 nunocodex

---

## References

- [Ollama](https://ollama.ai/) — Local LLM inference
- [ChromaDB](https://www.trychroma.com/) — Open‑source vector database
- [LlamaIndex](https://www.llamaindex.ai/) — Data framework for LLM applications
- [SearXNG](https://searxng.github.io/) — Privacy‑respecting meta‑search engine
- [FastAPI](https://fastapi.tiangolo.com/) — Python web framework
- [LangChain / LlamaIndex Integration](https://docs.llamaindex.ai/en/stable/)
