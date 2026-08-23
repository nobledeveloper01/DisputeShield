#!/usr/bin/env bash
# Empty database to a filed dispute with a computed SLA deadline.
#
# §6.1 claims thirty minutes to integrate. This script is what keeps the claim
# honest when a later phase is tempted to add a step: CI asserts the step count
# below, so adding a step is a deliberate, reviewed change rather than drift.
set -euo pipefail

STEPS=4
echo "DisputeShield hello-world — $STEPS steps"

echo "[1/$STEPS] infrastructure"
docker compose up -d >/dev/null

echo "[2/$STEPS] schema"
.venv/bin/python manage.py migrate --no-input >/dev/null

echo "[3/$STEPS] seed: tenant, categories, calendar, default SLA policy"
.venv/bin/python manage.py disputeshield_init --demo

echo "[4/$STEPS] file a dispute and print its computed deadline"
.venv/bin/python manage.py disputeshield_demo_dispute

echo "ok"
