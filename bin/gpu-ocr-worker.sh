#!/bin/bash
# GPU server worker — OCR detection stage
#
# Watches VIDEO_DIR for new source videos with extracted metadata,
# runs OCR detection per ad break, and moves completed videos (with
# all artifacts) to READY_DIR for consumption by the CPU clipper.
#
# Idempotent — safe to re-run.  Uses a local flock to prevent
# concurrent cron instances.
#
# Configuration via environment variables (REQUIRED):
#   VIDEO_DIR          — where source MP4s and their metadata live
#   VENV_PATH          — path to the Python venv root (e.g. /home/user/venv/advert-identifier)
#   OCR_ENDPOINT       — vLLM endpoint (default: http://localhost:8000/v1/chat/completions)
#
# Optional:
#   READY_DIR          — where OCR outputs are written (=VIDEO_DIR/ready_for_clipping)
#   OCR_MODEL          — PaddleOCR-VL model name (default: PaddlePaddle/PaddleOCR-VL)
#   DETECTION_FPS      — frame extraction rate (default: 5.0)
#   BEFORE_SECS / AFTER_SECS — extraction window (default: 10 / 360)
#   ANCHOR_THRESHOLD   — anchor re-estimation threshold (default: 0.6)
#   LOG_DIR            — where daily log files go (=VIDEO_DIR/logs)

set -euo pipefail

# ── Required environment variables ────────────────────────────────────────
: "${VIDEO_DIR:?Must set VIDEO_DIR — path to source video watch folder}"
: "${VENV_PATH:?Must set VENV_PATH — path to Python venv root}"

# ── Configuration ─────────────────────────────────────────────────────────
READY_DIR="${READY_DIR:-$VIDEO_DIR/ready_for_clipping}"
OCR_ENDPOINT="${OCR_ENDPOINT:-http://localhost:8000/v1/chat/completions}"
OCR_MODEL="${OCR_MODEL:-PaddlePaddle/PaddleOCR-VL}"
DETECTION_FPS="${DETECTION_FPS:-5.0}"
BEFORE_SECS="${BEFORE_SECS:-10.0}"
AFTER_SECS="${AFTER_SECS:-360.0}"
ANCHOR_THRESHOLD="${ANCHOR_THRESHOLD:-0.6}"
LOG_DIR="${LOG_DIR:-$VIDEO_DIR/logs}"

# ── Paths ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# ── Lock (local FS, not NFS) ──────────────────────────────────────────────
LOCKFILE="/tmp/gpu-ocr-worker.lock"
exec 200>"$LOCKFILE"
flock -n 200 || { echo "[gpu-worker] Another instance is running"; exit 0; }

# ── Venv ──────────────────────────────────────────────────────────────────
source "${VENV_PATH}/bin/activate"

# ── Logging ───────────────────────────────────────────────────────────────
mkdir -p "$LOG_DIR" "$READY_DIR"
LOGFILE="$LOG_DIR/gpu-worker-$(date +%Y%m%d).log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOGFILE"; }

log "=== GPU worker start ==="

for mp4 in "$VIDEO_DIR"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_*.mp4; do
    [ -f "$mp4" ] || continue

    stem=$(basename "$mp4" .mp4)
    meta_orig="$VIDEO_DIR/${stem}_metadata.json"
    state_orig="$VIDEO_DIR/${stem}_pipeline_state.json"
    done_marker="$READY_DIR/${stem}.done"

    # Need metadata + pipeline state first (produced by cpu-worker Phase ①)
    [ -f "$state_orig" ] || continue
    # Already processed
    [ -f "$done_marker" ] && continue

    log "Processing: $stem"

    # Copy metadata + state into ready_dir once, so detect.py writes
    # its XML + state updates alongside the consumer.  If a previous
    # run crashed mid-way the copies already exist — don't clobber
    # partial progress.
    if [ ! -f "$READY_DIR/${stem}_pipeline_state.json" ]; then
        cp "$meta_orig" "$READY_DIR/"
        cp "$state_orig" "$READY_DIR/"
    fi

    meta="$READY_DIR/${stem}_metadata.json"
    state="$READY_DIR/${stem}_pipeline_state.json"

    # Read break count
    break_count=$(python3 -c "
import json
d = json.load(open('$meta'))
print(len(d.get('ad_breaks', [])))
")

    all_success=true
    for ((i=1; i<=break_count; i++)); do
        # Skip if all adverts in this break are already detected
        status=$(python3 -c "
import json
s = json.load(open('$state'))
adverts = s.get('ad_breaks', [])[$((i-1))].get('adverts', [])
if all(a.get('status') == 'detected' and a.get('detection') is not None for a in adverts):
    print('detected')
else:
    print('pending')
")
        [ "$status" = "detected" ] && { log "  Break $i/$break_count: already detected"; continue; }

        xml_out="$READY_DIR/${stem}_break${i}.xml"
        log "  Break $i/$break_count: OCR detection..."

        if "$REPO_ROOT/bin/advert-identifier" \
            -v "$mp4" \
            --metadata-file "$meta" \
            --ad-break-index "$i" \
            --output "$xml_out" \
            --output-dir "$READY_DIR" \
            --before-secs "$BEFORE_SECS" \
            --after-secs "$AFTER_SECS" \
            --fps "$DETECTION_FPS" \
            --ocr-endpoint "$OCR_ENDPOINT" \
            --ocr-model "$OCR_MODEL" \
            --anchor-threshold "$ANCHOR_THRESHOLD" \
            >> "$LOGFILE" 2>&1; then
            log "  Break $i/$break_count: OK"
        else
            log "  Break $i/$break_count: FAILED"
            all_success=false
        fi
    done

    # All breaks processed — signal completion
    touch "$done_marker"
    log "  Done marker created"

    # Move video to ready_dir so clipper can access it
    mv "$mp4" "$READY_DIR/"
    log "  Video moved"

    # Remove originals from watch dir (copies already in ready_dir)
    rm -f "$meta_orig" "$state_orig"
    log "  Originals cleaned"

    if $all_success; then
        log "  Completed: $stem (all OK)"
    else
        log "  Completed: $stem (some breaks FAILED)"
    fi
done

log "=== GPU worker end ==="
