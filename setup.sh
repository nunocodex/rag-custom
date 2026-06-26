#!/bin/bash
# setup.sh — Bootstrap the RAG stack
# Creates .env, generates secrets, validates environment, starts containers.
# Test mode: set TEST_MODE=1 to skip Docker daemon check and docker compose up.

set -euo pipefail

# ---- Configuration ----
ENV_FILE=".env"
ENV_TEMPLATE=".env.example"
TEST_MODE="${TEST_MODE:-0}"
PLACEHOLDER_PATTERNS=("your-secure-secret-key-here" "changeme")

# ---- Colors ----
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ---- Helpers ----

# Generate a cryptographically secure random key (base64).
generate_key() {
    local key
    key=$(openssl rand -base64 32) || error "Failed to generate random key"
    # Validate base64 output (alphanumeric, +, /, =)
    echo "$key" | grep -qE '^[A-Za-z0-9+/=]+$' || error "Generated key contains invalid characters"
    echo "$key"
}

# Check if a key value is a known insecure placeholder.
is_placeholder() {
    local key="$1"
    for ph in "${PLACEHOLDER_PATTERNS[@]}"; do
        [ "$key" = "$ph" ] && return 0
    done
    return 1
}

# Update .env atomically: run sed to replace SEARXNG_SECRET_KEY,
# write to a temp file, validate, then mv into place.
# Uses | as sed delimiter (safe since base64 never contains |).
update_env_key() {
    local new_key="$1"
    local env_file="$2"
    local sed_expr="s|^SEARXNG_SECRET_KEY=.*|SEARXNG_SECRET_KEY=${new_key}|"
    tmp="${env_file}.tmp.$$"

    sed "$sed_expr" "$env_file" > "$tmp" || { rm -f "$tmp"; error "sed failed during key replacement"; }

    # Validate: ensure the output file has the new key
    grep -q "^SEARXNG_SECRET_KEY=${new_key}$" "$tmp" || {
        rm -f "$tmp"
        error "Key validation failed after sed replacement"
    }

    mv "$tmp" "$env_file"
}

# ================================================================
#  1. Validate Docker daemon
# ================================================================
if [ "$TEST_MODE" != "1" ]; then
    info "Checking Docker daemon..."
    if ! docker info >/dev/null 2>&1; then
        error "Docker daemon is not running."$'\n'"       Start Docker Desktop and enable WSL2 integration for this distro, then retry."
    fi
    info "Docker daemon is running."
fi

# ================================================================
#  2. Create required directories
# ================================================================
info "Creating directories..."
mkdir -p collection/app collection_data

# ================================================================
#  3. Bootstrap .env from template
# ================================================================
if [ ! -f "$ENV_FILE" ]; then
    if [ ! -f "$ENV_TEMPLATE" ]; then
        error "$ENV_TEMPLATE not found. Cannot create $ENV_FILE."
    fi
    info "Creating $ENV_FILE from $ENV_TEMPLATE..."
    cp "$ENV_TEMPLATE" "$ENV_FILE"
else
    info "$ENV_FILE already exists. Skipping template copy."
fi

# Normalize: ensure .env ends with a newline to prevent line fusion on append
if [ -s "$ENV_FILE" ]; then
    last_byte=$(tail -c 1 "$ENV_FILE" | od -A n -t x1 | tr -d ' ')
    if [ "$last_byte" != "0a" ]; then
        echo >> "$ENV_FILE"
    fi
fi

# ================================================================
#  4. Manage SEARXNG_SECRET_KEY
# ================================================================
#  - If missing: generate and add
#  - If placeholder: generate and replace
#  - If valid: preserve (idempotent)
# ================================================================
CURRENT_KEY=$(grep "^SEARXNG_SECRET_KEY=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)

if [ -z "$CURRENT_KEY" ]; then
    # Key does not exist at all → generate and add
    info "SEARXNG_SECRET_KEY not found. Generating new key..."
    NEW_KEY=$(generate_key)
    echo "SEARXNG_SECRET_KEY=${NEW_KEY}" >> "$ENV_FILE"
    info "Secret key generated and added to $ENV_FILE."

elif is_placeholder "$CURRENT_KEY"; then
    # Key exists but is an insecure placeholder → replace
    info "SEARXNG_SECRET_KEY is a placeholder ('$CURRENT_KEY'). Generating new key..."
    NEW_KEY=$(generate_key)
    update_env_key "$NEW_KEY" "$ENV_FILE"
    info "Placeholder replaced with new secret key."

else
    # Key exists and appears valid → preserve
    info "SEARXNG_SECRET_KEY already set (valid). Preserving existing key."
fi

# ================================================================
#  5. Set file permissions
# ================================================================
chmod 600 "$ENV_FILE"

# ================================================================
#  6. Validate .env integrity
# ================================================================
# Check for lines that don't match KEY=value format (skip comments/blanks)
BAD_LINES=$(grep -vnE '^\s*(#|$|[A-Za-z_][A-Za-z0-9_]*=)' "$ENV_FILE" || true)
if [ -n "$BAD_LINES" ]; then
    warn "Some lines in $ENV_FILE may be malformed:"
    echo "$BAD_LINES"
fi

# Check required variables
MISSING=0
for var in API_PORT HOST_DOCS_DIR CHROMA_COLLECTION SEARXNG_SECRET_KEY; do
    if ! grep -q "^${var}=" "$ENV_FILE" 2>/dev/null; then
        warn "Required variable '$var' is missing from $ENV_FILE"
        MISSING=$((MISSING + 1))
    fi
done

if [ "$MISSING" -gt 0 ]; then
    warn "$MISSING required variable(s) missing. Check $ENV_FILE."
fi

# Specific HOST_DOCS_DIR check (this var has no default in docker-compose)
HOST_DOCS_VAL=$(grep "^HOST_DOCS_DIR=" "$ENV_FILE" | cut -d= -f2- || true)
if [ -z "$HOST_DOCS_VAL" ]; then
    warn "HOST_DOCS_DIR is not set. File upload (/upload) and /ingest endpoints will not work."
    warn "  Set HOST_DOCS_DIR in $ENV_FILE to a host directory containing documents."
elif [ "$HOST_DOCS_VAL" = "./tech-docs" ] || [ "$HOST_DOCS_VAL" = "/home/nunocodex/projects/tech-docs" ]; then
    warn "HOST_DOCS_DIR appears to use a placeholder or local path. Verify it points to a valid directory."
fi

# ================================================================
#  7. Pull Ollama models via docker exec
# ================================================================
# Pull a model if not already present inside the ollama container.
pull_model() {
    local model="$1"
    local container="rag-ollama"
    if docker exec "$container" ollama list 2>/dev/null | grep -q "$model"; then
        info "Ollama model '$model' already present. Skipping."
    else
        info "Pulling Ollama model '$model' (this may take several minutes)..."
        docker exec "$container" ollama pull "$model" || {
            warn "Failed to pull model '$model'. You can retry manually:"
            warn "  docker exec $container ollama pull $model"
        }
    fi
}

# ================================================================
#  8. Start containers + health checks + model pull
# ================================================================
if [ "$TEST_MODE" != "1" ]; then
    info "Starting containers..."
    docker compose up -d --build

    # Wait for services to become healthy
    info "Waiting for ChromaDB..."
    for i in $(seq 1 30); do
        if curl -s http://localhost:8000/api/v1/heartbeat >/dev/null 2>&1; then
            info "ChromaDB is ready."
            break
        fi
        if [ "$i" -eq 30 ]; then
            warn "ChromaDB did not respond within timeout. Continuing anyway."
        fi
        sleep 2
    done

    info "Waiting for Ollama..."
    for i in $(seq 1 30); do
        if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
            info "Ollama is ready."
            break
        fi
        if [ "$i" -eq 30 ]; then
            warn "Ollama did not respond within timeout. Continuing anyway."
        fi
        sleep 2
    done

    info "Waiting for Redis..."
    for i in $(seq 1 15); do
        if docker exec rag-redis redis-cli ping 2>/dev/null | grep -q "PONG"; then
            info "Redis is ready."
            break
        fi
        if [ "$i" -eq 15 ]; then
            warn "Redis did not respond within timeout. Continuing anyway."
        fi
        sleep 2
    done

    # Pull required Ollama models
    pull_model "nomic-embed-text"
    pull_model "llama3.1:8b"
fi

# ================================================================
#  9. Git hint (if not already a repository)
# ================================================================
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    info ""
    info "Git repository not initialized. To publish on GitHub:"
    info "  git init"
    info "  git add -A"
    info "  git commit -m 'Initial commit: Enterprise RAG Stack with Ollama, ChromaDB, SearXNG'"
    info "  git remote add origin git@github.com:nunocodex/rag-custom.git"
    info "  git push -u origin main"
fi

echo ""
info "========================================"
info "  Setup complete!"
info "  API:               http://localhost:8080"
info "  Collection Service: http://localhost:8181"
info "  SearXNG:            http://localhost:7777"
info "========================================"
