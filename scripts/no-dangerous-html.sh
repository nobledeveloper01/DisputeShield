#!/usr/bin/env bash
# §10: dispute descriptions are attacker-controlled text rendered in an agent's
# browser. dangerouslySetInnerHTML appears nowhere in this codebase, and this
# grep is what keeps that true.
#
# Source only. Build output and vendored dependencies are excluded because
# React's own minified internals contain every one of these strings, so scanning
# a built tree reports a failure that blames a library and cannot be fixed. In CI
# this job happens to run before anything is built, which is why it passed for as
# long as it did — but a gate that fires on a developer's machine after a routine
# `npm run build` is a gate people learn to ignore, and then it protects nothing.
set -euo pipefail

hits=$(grep -rn "dangerouslySetInnerHTML\|v-html\|innerHTML *=" \
        --include="*.ts" --include="*.tsx" --include="*.js" --include="*.jsx" \
        --exclude-dir=dist --exclude-dir=node_modules \
        --exclude-dir=test-results --exclude-dir=playwright-report \
        widget/src widget/tests dashboard/src dashboard/tests loader/src loader/test \
        2>/dev/null || true)

if [ -n "$hits" ]; then
    echo "FAIL: raw HTML injection sink found (§10):"
    echo "$hits"
    exit 1
fi
echo "ok: no raw HTML sinks"
