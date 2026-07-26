#!/bin/bash
# CPU worker — metadata extraction + clip extraction
#
# Two phases, run in sequence on every cron tick:
#   ① Metadata extraction — for new MP4s in VIDEO_DIR that lack
#     _metadata.json, run advert-identifier-metadata-extract.
#   ③ Clip extraction — discover OCR'd breaks in READY_DIR via
#     advert-identifier-ready-breaks and run the clipper.
#
# Idempotent — safe to re-run.  Uses a local flock to prevent
# concurrent cron instances.
#
# Configuration via environment variables (REQUIRED):
#   VIDEO_DIR     — watch folder for source MP4s
#   CSV_FOLDER    — schedule CSVs for metadata extraction
#   VENV_PATH     — path to the Python venv root (e.g. /home/user/venv/advert-identifier)
#
# Optional:
#   READY_DIR     — where OCR outputs land (=VIDEO_DIR/ready_for_clipping)
#   CLIPPED_DIR   — output for final advert clips (=VIDEO_DIR/clipped_adverts)
#   BEFORE_SECS   — extraction window before break start (default: 10.0)
#   MAX_WORKERS   — parallel FFmpeg processes for clipping (default: 10)
#   LOG_DIR       — daily log files (=VIDEO_DIR/logs)

set -euo pipefail

# ── Required environment variables ────────────────────────────────────────
: "${VIDEO_DIR:?Must set VIDEO_DIR — path to source video watch folder}"
: "${CSV_FOLDER:?Must set CSV_FOLDER — path to schedule CSV files}"
: "${VENV_PATH:?Must set VENV_PATH — path to Python venv root}"

# ── Configuration ─────────────────────────────────────────────────────────
READY_DIR="${READY_DIR:-$VIDEO_DIR/ready_for_clipping}"
CLIPPED_DIR="${CLIPPED_DIR:-$VIDEO_DIR/clipped_adverts}"
BEFORE_SECS="${BEFORE_SECS:-10.0}"
MAX_WORKERS="${MAX_WORKERS:-10}"
LOG_DIR="${LOG_DIR:-$VIDEO_DIR/logs}"

# ── Paths ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
HELPER="$SCRIPT_DIR/advert-identifier-ready-breaks"

# ── Lock (local FS, not NFS) ──────────────────────────────────────────────
LOCKFILE="/tmp/cpu-worker.lock"
exec 200>"$LOCKFILE"
flock -n 200 || { echo "[cpu-worker] Another instance is running"; exit 0; }

# ── Venv ──────────────────────────────────────────────────────────────────
source "${VENV_PATH}/bin/activate"

# ── Logging ───────────────────────────────────────────────────────────────
mkdir -p "$LOG_DIR" "$READY_DIR" "$CLIPPED_DIR"
LOGFILE="$LOG_DIR/cpu-worker-$(date +%Y%m%d).log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOGFILE"; }

log "=== CPU worker start ==="

# ═══════════════════════════════════════════════════════════════════════════
# Phase ①: Metadata extraction
# ═══════════════════════════════════════════════════════════════════════════
for mp4 in "$VIDEO_DIR"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_*.mp4; do
    [ -f "$mp4" ] || continue
    stem=$(basename "$mp4" .mp4)
    meta="$VIDEO_DIR/${stem}_metadata.json"
    [ -f "$meta" ] && continue

    log "Phase ①: $stem — extracting metadata"
    if "$REPO_ROOT/bin/advert-identifier-metadata-extract" \
        --video "$mp4" \
        --csv-folder "$CSV_FOLDER" \
        --output-dir "$VIDEO_DIR" \
        --before-secs "$BEFORE_SECS" \
        >> "$LOGFILE" 2>&1; then
        log "  OK"
    else
        log "  FAILED"
    fi
done

# ═══════════════════════════════════════════════════════════════════════════
# Phase ③: Clip extraction (per-break, concurrent with GPU OCR)
# ═══════════════════════════════════════════════════════════════════════════
# advert-identifier-ready-breaks writes pipe-delimited lines to stdout
# and diagnostic messages to stderr.  We route stderr to the log while
# keeping stdout for the while-read loop.

"$HELPER" list \
    --ready-dir "$READY_DIR" \
    --video-dir "$VIDEO_DIR" \
    --format pipe \
    2>>"$LOGFILE" \
| while IFS='|' read -r stem bidx xml_file state_file meta_file video_file; do

    # Log inside the loop goes to the shared log
    exec 2>>"$LOGFILE"

    log "Phase ③: $stem break $bidx"

    if "$REPO_ROOT/bin/advert-identifier-single-advert-clip" \
        --xml-file "$xml_file" \
        --video-url "$video_file" \
        --json-file "$meta_file" \
        --state-file "$state_file" \
        --output-dir "$CLIPPED_DIR" \
        --ad-break-index "$bidx" \
        --max-workers "$MAX_WORKERS" \
        >> "$LOGFILE" 2>&1; then

        clip_count=$(python3 -c "
import json
s = json.load(open('$state_file'))
print(len(s['ad_breaks'][$bidx]['adverts']))
")

        if "$HELPER" mark-clipped \
            --state-file "$state_file" \
            --break-index "$bidx" \
            --clip-count "$clip_count" \
            >> "$LOGFILE" 2>&1; then
            log "  Clipped break $bidx: $clip_count clip(s)"
        else
            log "  WARNING: clips extracted but state NOT updated for $stem break $bidx"
        fi
    else
        log "  FAILED: $stem break $bidx"
    fi
done

log "=== CPU worker end ==="
