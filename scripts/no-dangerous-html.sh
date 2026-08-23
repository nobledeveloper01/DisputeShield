#!/usr/bin/env bash
# §10: dispute descriptions are attacker-controlled text rendered in an agent's
# browser. dangerouslySetInnerHTML appears nowhere in this codebase, and this
# grep is what keeps that true.
set -euo pipefail

hits=$(grep -rn "dangerouslySetInnerHTML\|v-html\|innerHTML *=" \
        --include="*.ts" --include="*.tsx" --include="*.js" --include="*.jsx" \
        widget/ dashboard/ loader/ 2>/dev/null || true)

if [ -n "$hits" ]; then
    echo "FAIL: raw HTML injection sink found (§10):"
    echo "$hits"
    exit 1
fi
echo "ok: no raw HTML sinks"
