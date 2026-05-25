#!/bin/bash
# run_daily.sh — Daily patent law pipeline.
# Runs PTAB → ITC → CAFC scrapers sequentially, then sends the unified
# Resend digest once all three have synced to Supabase.
# Called by cron at 15:00 UTC (= 11:00 AM EDT / 10:00 AM EST).

set -uo pipefail

DATE="${1:-$(date +%Y-%m-%d)}"
LOG_DIR=/app/logs
mkdir -p "$LOG_DIR"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "${LOG_DIR}/daily.log"; }

log "=== Daily pipeline starting: ${DATE} ==="

run_script() {
    local name="$1"; shift
    log "--- ${name} ---"
    if python "$@" >> "${LOG_DIR}/${name}.log" 2>&1; then
        log "${name}: OK"
    else
        log "${name}: FAILED (exit $?) — continuing"
    fi
}

run_script ptab  /app/scripts/ptab_daily.py  --no-trigger
run_script itc   /app/scripts/itc_daily.py   --no-trigger
run_script cafc  /app/scripts/cafc_daily.py  --no-trigger

log "--- Digest ---"
if [ -z "${DIGEST_SECRET:-}" ] || [ -z "${NEXT_PUBLIC_SITE_URL:-}" ]; then
    log "Digest: skipped (DIGEST_SECRET or NEXT_PUBLIC_SITE_URL not configured)"
else
    RESP=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Authorization: ${DIGEST_SECRET}" \
        -H "Content-Type: application/json" \
        -d "{\"date\": \"${DATE}\"}" \
        "${NEXT_PUBLIC_SITE_URL}/api/admin/send-digest" 2>&1)
    log "Digest response: ${RESP}"
fi

log "=== Daily pipeline done ==="
