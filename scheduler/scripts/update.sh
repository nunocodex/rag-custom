#!/bin/bash
# Trigger refresh of expired documents
set -euo pipefail
curl --fail --silent --show-error -X POST http://collection-service:8181/documents/refresh