#!/usr/bin/env bash
# ADR-0001: the loader is the only DisputeShield code that runs in the host page's
# context. It stays small enough for a reviewing engineer to read in full before
# putting it on a payments page. A budget nobody enforces is a budget that grows.
set -euo pipefail

BUDGET_BYTES=4096
FILE="loader/dist/loader.js"

[ -f "$FILE" ] || { echo "FAIL: $FILE not built"; exit 1; }
SIZE=$(gzip -c "$FILE" | wc -c | tr -d ' ')

echo "loader.js: ${SIZE}B gzipped (budget ${BUDGET_BYTES}B)"
[ "$SIZE" -le "$BUDGET_BYTES" ] || { echo "FAIL: over budget"; exit 1; }
