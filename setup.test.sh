#!/bin/bash
# setup.test.sh — Test harness for setup.sh
# Tests T1–T6 as defined in PLAN.md
# Usage: bash setup.test.sh

set -euo pipefail

PASS=0
FAIL=0
TEST_DIR=""

# Absolute path to the project root (where this script lives)
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)

# ---- Helpers ----
setup() {
    TEST_DIR=$(mktemp -d)
    cp "$SCRIPT_DIR/setup.sh" "$TEST_DIR/"
    cp "$SCRIPT_DIR/.env.example" "$TEST_DIR/"
    cd "$TEST_DIR"
    export TEST_MODE=1
}

teardown() {
    unset TEST_MODE
    cd / >/dev/null 2>&1 || true
    rm -rf "$TEST_DIR" 2>/dev/null || true
}

assert() {
    local desc="$1"
    local expected="$2"
    local actual="$3"
    if [ "$expected" = "$actual" ]; then
        echo "  PASS: $desc"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $desc"
        echo "    expected: $expected"
        echo "    actual:   $actual"
        FAIL=$((FAIL + 1))
    fi
}

assert_contains() {
    local file="$1"
    local pattern="$2"
    local desc="$3"
    if grep -qE "$pattern" "$file"; then
        echo "  PASS: $desc"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $desc (pattern '$pattern' not found in $file)"
        FAIL=$((FAIL + 1))
    fi
}

assert_not_contains() {
    local file="$1"
    local pattern="$2"
    local desc="$3"
    if ! grep -qE "$pattern" "$file"; then
        echo "  PASS: $desc"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $desc (unexpected pattern '$pattern' found in $file)"
        FAIL=$((FAIL + 1))
    fi
}

# ================================================================
# T1: .env does not exist → create from template + generate key
# ================================================================
echo ""
echo "=== T1: .env does not exist → create from template + generate key ==="
setup

rm -f .env
bash setup.sh 2>&1 || true

# Verify .env was created
DESC="T1a: .env file created"
if [ -f .env ]; then echo "  PASS: $DESC"; PASS=$((PASS + 1)); else echo "  FAIL: $DESC"; FAIL=$((FAIL + 1)); fi

# Verify SEARXNG_SECRET_KEY exists and is not placeholder
assert_contains ".env" "^SEARXNG_SECRET_KEY=[A-Za-z0-9+/=]+$" "T1b: SEARXNG_SECRET_KEY set to valid base64"
assert_not_contains ".env" "SEARXNG_SECRET_KEY=your-secure-secret-key-here" "T1c: No placeholder key"

# Verify no line fusion (last line should start with a known key)
LAST_LINE=$(tail -1 .env)
if echo "$LAST_LINE" | grep -qE '^[A-Za-z_][A-Za-z0-9_]*='; then
    echo "  PASS: T1d: No line fusion (last line is valid KEY=value)"
    PASS=$((PASS + 1))
else
    echo "  FAIL: T1d: Line fusion detected: '$LAST_LINE'"
    FAIL=$((FAIL + 1))
fi

# Verify all required vars present
for var in API_PORT HOST_DOCS_DIR CHROMA_COLLECTION SEARXNG_SECRET_KEY; do
    if grep -q "^${var}=" .env; then
        : # ok
    else
        echo "  FAIL: T1e: Required var $var missing from .env"
        FAIL=$((FAIL + 1))
    fi
done
echo "  PASS: T1e: All required variables present"
PASS=$((PASS + 1))

teardown

# ================================================================
# T2: .env exists with placeholder key → replace it
# ================================================================
echo ""
echo "=== T2: .env exists with placeholder key → replace it ==="
setup

cp .env.example .env
# Verify it starts with placeholder
assert_contains ".env" "^SEARXNG_SECRET_KEY=your-secure-secret-key-here$" "T2a: Placeholder present before setup"

bash setup.sh 2>&1 || true

# Verify placeholder was replaced
assert_not_contains ".env" "your-secure-secret-key-here" "T2b: Placeholder removed"
assert_contains ".env" "^SEARXNG_SECRET_KEY=[A-Za-z0-9+/=]+$" "T2c: Valid key after setup"

# Verify exactly one SEARXNG_SECRET_KEY line
KEY_COUNT=$(grep -c "^SEARXNG_SECRET_KEY=" .env || true)
assert "T2d: Exactly 1 SEARXNG_SECRET_KEY line" "1" "$KEY_COUNT"

teardown

# ================================================================
# T3: .env exists with valid key → preserve it (idempotent)
# ================================================================
echo ""
echo "=== T3: .env exists with valid key → preserve it ==="
setup

cp .env.example .env
# First run: generate key
bash setup.sh 2>&1 || true
FIRST_KEY=$(grep "^SEARXNG_SECRET_KEY=" .env | head -1 | cut -d= -f2-)

# Second run: should preserve the key
bash setup.sh 2>&1 || true
SECOND_KEY=$(grep "^SEARXNG_SECRET_KEY=" .env | head -1 | cut -d= -f2-)

assert "T3a: Key preserved across runs" "$FIRST_KEY" "$SECOND_KEY"
assert "T3b: Exactly 1 key line after second run" "1" "$(grep -c '^SEARXNG_SECRET_KEY=' .env || true)"

teardown

# ================================================================
# T4: .env.example missing trailing newline → no line fusion
# ================================================================
echo ""
echo "=== T4: .env.example missing trailing newline → no line fusion ==="
setup

# Remove trailing newline from .env.example (simulate the original bug)
printf 'SCHEDULE_INTERVAL_HOURS=24' > .env.example
assert_contains ".env.example" "SCHEDULE_INTERVAL_HOURS=24" "T4a: Template no-newline setup confirmed"

rm -f .env
bash setup.sh 2>&1 || true

# Verify .env has proper line endings (no fusion)
LAST_LINE=$(tail -1 .env)
if echo "$LAST_LINE" | grep -qE '^SEARXNG_SECRET_KEY='; then
    echo "  PASS: T4b: No line fusion (last line is SEARXNG_SECRET_KEY)"
    PASS=$((PASS + 1))
else
    echo "  FAIL: T4b: Line fusion detected: '$LAST_LINE'"
    FAIL=$((FAIL + 1))
fi

# Verify SCHEDULE_INTERVAL_HOURS has proper value
SCHED_VAL=$(grep "^SCHEDULE_INTERVAL_HOURS=" .env | cut -d= -f2-)
assert "T4c: SCHEDULE_INTERVAL_HOURS value preserved" "24" "$SCHED_VAL"

teardown

# ================================================================
# T5: Docker daemon not running → exit 1 with message
# ================================================================
echo ""
echo "=== T5: Docker daemon not running → exit 1 ==="
setup

unset TEST_MODE
# Mock docker to fail
docker() { return 1; }
export -f docker 2>/dev/null || true

cp .env.example .env
EXIT_CODE=0
bash setup.sh 2>&1 && EXIT_CODE=$? || EXIT_CODE=$?

# Docker check fails, script should exit before modifying .env
assert "T5a: Exit code 1" "1" "$EXIT_CODE"

# Verify .env was NOT modified (still has placeholder)
assert_contains ".env" "your-secure-secret-key-here" "T5b: .env unchanged (no key generation before docker check)"

teardown

# ================================================================
# T6: Double run (idempotency) → .env identical after second run
# ================================================================
echo ""
echo "=== T6: Double run → .env identical ==="
setup

cp .env.example .env

# First run
export TEST_MODE=1
bash setup.sh 2>&1 || true
cp .env .env.run1

# Second run
bash setup.sh 2>&1 || true
cp .env .env.run2

# Compare sorted files (ignoring any line ordering differences)
if diff -q .env.run1 .env.run2 >/dev/null 2>&1; then
    echo "  PASS: T6: .env identical after second run"
    PASS=$((PASS + 1))
else
    echo "  FAIL: T6: .env differs after second run"
    diff .env.run1 .env.run2 || true
    FAIL=$((FAIL + 1))
fi

teardown

# ================================================================
# T7: HOST_DOCS_DIR not set → setup.sh emits warning
# ================================================================
echo ""
echo "=== T7: HOST_DOCS_DIR not set → warning emitted ==="
setup

cp .env.example .env
# Remove HOST_DOCS_DIR line to simulate missing config
sed -i '/^HOST_DOCS_DIR=/d' .env

OUTPUT=$(bash setup.sh 2>&1 || true)

if echo "$OUTPUT" | grep -qi "HOST_DOCS_DIR"; then
    echo "  PASS: T7: Warning about HOST_DOCS_DIR emitted"
    PASS=$((PASS + 1))
else
    echo "  FAIL: T7: No warning about HOST_DOCS_DIR (output below)"
    echo "$OUTPUT" | tail -10
    FAIL=$((FAIL + 1))
fi

# Verify setup still completed (warning, not error)
if [ -f .env ]; then
    echo "  PASS: T7-cont: Script completed (continued despite missing HOST_DOCS_DIR)"
    PASS=$((PASS + 1))
else
    echo "  FAIL: T7-cont: Script did not complete"
    FAIL=$((FAIL + 1))
fi

teardown


# ================================================================
# T8: setup.sh contains pull_model logic with ollama list check
# ================================================================
echo ""
echo "=== T8: setup.sh contains model pull logic ==="

# T8a: Verify setup.sh contains pull_model function
if grep -q "pull_model" "$SCRIPT_DIR/setup.sh"; then
    echo "  PASS: T8a: pull_model function found in setup.sh"
    PASS=$((PASS + 1))
else
    echo "  FAIL: T8a: pull_model function not found in setup.sh"
    FAIL=$((FAIL + 1))
fi

# T8b: Verify setup.sh checks ollama list before pulling (idempotency guard)
if grep -qE "ollama list|ollama.*pull.*\$model" "$SCRIPT_DIR/setup.sh"; then
    echo "  PASS: T8b: ollama list check or model pull reference found"
    PASS=$((PASS + 1))
else
    echo "  FAIL: T8b: No ollama reference found in setup.sh"
    FAIL=$((FAIL + 1))
fi

# T8-update (H3): Verify pull_model warning is guarded inside { } block
if grep -qE "\|\| \{" "$SCRIPT_DIR/setup.sh"; then
    echo "  PASS: T8-update: pull_model warning guarded by || {"
    PASS=$((PASS + 1))
else
    echo "  FAIL: T8-update: pull_model warning NOT guarded by || {"
    FAIL=$((FAIL + 1))
fi

# T8c: Verify setup.sh pulls both required models (llama3.1:8b, nomic-embed-text)
for model in "llama3.1:8b" "nomic-embed-text"; do
    if grep -q "$model" "$SCRIPT_DIR/setup.sh"; then
        echo "  PASS: T8c: Model $model referenced in setup.sh"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: T8c: Model $model not found in setup.sh"
        FAIL=$((FAIL + 1))
    fi
done


# ================================================================
# ================================================================
# T16: scheduler/scripts/update.sh uses correct endpoint URL
# ================================================================
echo ""
echo "=== T16: update.sh uses correct endpoint URL ==="

if [ -f "$SCRIPT_DIR/scheduler/scripts/update.sh" ]; then
    # Check port 8181 (correct) not 8081 (wrong)
    if grep -qE "8181/documents/refresh" "$SCRIPT_DIR/scheduler/scripts/update.sh"; then
        echo "  PASS: T16a: update.sh calls correct endpoint (8181/documents/refresh)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: T16a: update.sh does not call correct endpoint"
        FAIL=$((FAIL + 1))
    fi

    # Check error handling with --fail
    if grep -qF -- "--fail" "$SCRIPT_DIR/scheduler/scripts/update.sh"; then
        echo "  PASS: T16b: update.sh uses curl --fail for error handling"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: T16b: update.sh missing curl --fail"
        FAIL=$((FAIL + 1))
    fi

    # Check set -euo pipefail
    if grep -q "set -euo pipefail" "$SCRIPT_DIR/scheduler/scripts/update.sh"; then
        echo "  PASS: T16c: update.sh uses set -euo pipefail"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: T16c: update.sh missing set -euo pipefail"
        FAIL=$((FAIL + 1))
    fi
else
    echo "  FAIL: T16: scheduler/scripts/update.sh not found"
    FAIL=$((FAIL + 1))
fi


# ================================================================
# T9: .gitignore exists and blocks .env from tracking
# ================================================================
echo ""
echo "=== T9: .gitignore exists and blocks .env ==="

export GIT_DIR="$SCRIPT_DIR"

# T9a: .gitignore file exists
if [ -f "$SCRIPT_DIR/.gitignore" ]; then
    echo "  PASS: T9a: .gitignore file exists"
    PASS=$((PASS + 1))
else
    echo "  FAIL: T9a: .gitignore file missing"
    FAIL=$((FAIL + 1))
fi

# T9b: .env is in .gitignore
if grep -qE '^\.env$|^\.env/' "$SCRIPT_DIR/.gitignore"; then
    echo "  PASS: T9b: .env excluded in .gitignore"
    PASS=$((PASS + 1))
else
    echo "  FAIL: T9b: .env not excluded in .gitignore"
    FAIL=$((FAIL + 1))
fi

# T9c: Dangerous paths excluded (data/, collection_data/, scheduler/logs/, .pi/)
for dir in "data/" "collection_data/" "scheduler/logs/" "\.pi/"; do
    if grep -qE "^$dir" "$SCRIPT_DIR/.gitignore"; then
        echo "  PASS: T9c: $dir excluded in .gitignore"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: T9c: $dir not excluded in .gitignore"
        FAIL=$((FAIL + 1))
    fi
done


# ================================================================
# T10: searxng-settings.yml uses env var interpolation (no hardcoded secret)
# ================================================================
echo ""
echo "=== T10: searxng-settings.yml uses env var interpolation ==="

if [ -f "$SCRIPT_DIR/searxng-settings.yml" ]; then
    # Verify secret_key uses env var interpolation, not a hardcoded value
    SECRET_LINE=$(grep "secret_key:" "$SCRIPT_DIR/searxng-settings.yml")
    # shellcheck disable=SC2016
    if echo "$SECRET_LINE" | grep -q '\${SEARXNG_SECRET_KEY'; then
        echo "  PASS: T10a: secret_key uses env var interpolation (\${SEARXNG_SECRET_KEY})"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: T10a: secret_key does not use env var interpolation"
        echo "    Found: $SECRET_LINE"
        FAIL=$((FAIL + 1))
    fi

    # Verify no hardcoded secret key value (long random string)
    if echo "$SECRET_LINE" | grep -qvE '[a-zA-Z0-9+/]{20,}='; then
        echo "  PASS: T10b: No hardcoded secret key value detected"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: T10b: Possible hardcoded secret key detected"
        echo "    Found: $SECRET_LINE"
        FAIL=$((FAIL + 1))
    fi
else
    echo "  FAIL: T10: searxng-settings.yml not found"
    FAIL=$((FAIL + 1))
fi


# ================================================================
# Summary
# ================================================================
echo ""
echo "=============================="
echo "  Results: $PASS passed, $FAIL failed"
echo "=============================="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
