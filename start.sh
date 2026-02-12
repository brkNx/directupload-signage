sleep 10
cd /home/onur/directupload-signage
chmod +x start.sh
#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  DirectUpload Signage — Startup Script for Raspberry Pi 5
#  Run: chmod +x start.sh && ./start.sh
# ═══════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

FLASK_PORT=5000
TUNNEL_LOG="/tmp/cloudflared.log"
TUNNEL_URL_FILE="$SCRIPT_DIR/tunnel_url.txt"

# ── Colours ───────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[signage]${NC} $*"; }
warn() { echo -e "${YELLOW}[signage]${NC} $*"; }
err()  { echo -e "${RED}[signage]${NC} $*" >&2; }

# ── Cleanup on exit ──────────────────────────────────────────────
cleanup() {
    log "Shutting down…"
    [ -n "${TUNNEL_PID:-}" ]   && kill "$TUNNEL_PID"   2>/dev/null
    [ -n "${FLASK_PID:-}" ]    && kill "$FLASK_PID"    2>/dev/null
    [ -n "${CHROMIUM_PID:-}" ] && kill "$CHROMIUM_PID" 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Pre-flight checks ────────────────────────────────────────────
if ! command -v cloudflared >/dev/null 2>&1; then
    err "cloudflared not found. Install it first:"
    err "  curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 -o /usr/local/bin/cloudflared"
    err "  sudo chmod +x /usr/local/bin/cloudflared"
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    err "python3 not found."
    exit 1
fi

log "cloudflared version: $(cloudflared --version 2>&1 || echo 'unknown')"

# Make sure Flask is installed
python3 -c "import flask" 2>/dev/null || {
    warn "Flask not found — installing…"
    pip3 install flask --break-system-packages -q
}

# Ensure static directory exists
mkdir -p "$SCRIPT_DIR/static"

# Kill any leftover processes from previous runs
pkill -f "cloudflared tunnel" 2>/dev/null || true
pkill -f "python3.*app.py"   2>/dev/null || true
sleep 1

# ══════════════════════════════════════════════════════════════════
#  STEP 1 — Start Cloudflare Quick Tunnel
# ══════════════════════════════════════════════════════════════════
log "Starting Cloudflare Tunnel → localhost:${FLASK_PORT}"

# Clear old log
> "$TUNNEL_LOG"

cloudflared tunnel --url "http://localhost:${FLASK_PORT}" \
    >> "$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

sleep 3

# Verify cloudflared is still alive
if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    err "cloudflared crashed immediately. Log output:"
    echo "---"
    cat "$TUNNEL_LOG"
    echo "---"
    err "Try running manually: cloudflared tunnel --url http://localhost:5000"
    exit 1
fi

log "cloudflared PID: $TUNNEL_PID (running)"

# ══════════════════════════════════════════════════════════════════
#  STEP 2 — Extract the trycloudflare.com URL
# ══════════════════════════════════════════════════════════════════
log "Waiting for tunnel URL…"
TUNNEL_URL=""
RETRIES=0
MAX_RETRIES=40

while [ -z "$TUNNEL_URL" ] && [ "$RETRIES" -lt "$MAX_RETRIES" ]; do
    sleep 1
    RETRIES=$((RETRIES + 1))

    # Use grep -Eo (extended regex — portable, works on all systems)
    TUNNEL_URL=$(grep -Eo 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1) || true

    # Show progress every 5 seconds
    if [ $((RETRIES % 5)) -eq 0 ]; then
        log "  …still waiting (${RETRIES}s)"
    fi

    # Check if cloudflared died while waiting
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        err "cloudflared died while waiting for tunnel. Full log:"
        echo "---"
        cat "$TUNNEL_LOG"
        echo "---"
        break
    fi
done

if [ -z "$TUNNEL_URL" ]; then
    warn "Could not obtain tunnel URL after ${MAX_RETRIES}s."
    warn "Falling back to localhost. Debug: cat $TUNNEL_LOG"
    echo "http://localhost:${FLASK_PORT}" > "$TUNNEL_URL_FILE"
else
    log "Tunnel URL: ${TUNNEL_URL}"
    echo "$TUNNEL_URL" > "$TUNNEL_URL_FILE"
fi

# ══════════════════════════════════════════════════════════════════
#  STEP 3 — Start the Flask Application
# ══════════════════════════════════════════════════════════════════
log "Starting Flask server on port ${FLASK_PORT}…"
python3 "$SCRIPT_DIR/app.py" &
FLASK_PID=$!

sleep 2

if ! kill -0 "$FLASK_PID" 2>/dev/null; then
    err "Flask failed to start. Check for port conflicts."
    exit 1
fi
log "Flask PID: $FLASK_PID (running)"

# ══════════════════════════════════════════════════════════════════
#  STEP 4 — Launch Chromium in Kiosk Mode
# ══════════════════════════════════════════════════════════════════
log "Launching Chromium kiosk → http://localhost:${FLASK_PORT}"

# Determine the correct Chromium binary
CHROMIUM=""
for candidate in chromium chromium google-chrome; do
    if command -v "$candidate" >/dev/null 2>&1; then
        CHROMIUM="$candidate"
        break
    fi
done

if [ -z "$CHROMIUM" ]; then
    warn "No Chromium found. Open http://localhost:${FLASK_PORT} manually."
else
    # Clear crash recovery flags
    CHROMIUM_PROFILE="${HOME}/.config/chromium/Default"
    if [ -f "${CHROMIUM_PROFILE}/Preferences" ]; then
        sed -i 's/"exited_cleanly":false/"exited_cleanly":true/' \
            "${CHROMIUM_PROFILE}/Preferences" 2>/dev/null || true
        sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/' \
            "${CHROMIUM_PROFILE}/Preferences" 2>/dev/null || true
    fi

    # Disable screen blanking / DPMS
    xset s off      2>/dev/null || true
    xset -dpms      2>/dev/null || true
    xset s noblank  2>/dev/null || true

    "$CHROMIUM" \
        --kiosk \
        --disable-translate \
        --disable-infobars \
        --no-first-run \
        --noerrdialogs \
        --disable-session-crashed-bubble \
        --disable-component-update \
        --autoplay-policy=no-user-gesture-required \
        --check-for-update-interval=31536000 \
        --disable-features=TranslateUI \
        --disable-pinch \
        --overscroll-history-navigation=0 \
        "http://localhost:${FLASK_PORT}" &
    CHROMIUM_PID=$!
    log "Chromium PID: $CHROMIUM_PID (kiosk mode)"
fi

# ══════════════════════════════════════════════════════════════════
#  Keep alive — health monitor
# ══════════════════════════════════════════════════════════════════
echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " DirectUpload Signage is RUNNING"
log " Display : http://localhost:${FLASK_PORT}"
log " Admin   : ${TUNNEL_URL:-http://localhost:${FLASK_PORT}}/admin"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "Press Ctrl+C to stop all services."
echo ""

# Wait forever — auto-restart Flask if it dies
while true; do
    sleep 10

    if [ -n "${TUNNEL_PID:-}" ] && ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        warn "cloudflared exited unexpectedly — tunnel may be down."
        TUNNEL_PID=""
    fi
    if [ -n "${FLASK_PID:-}" ] && ! kill -0 "$FLASK_PID" 2>/dev/null; then
        err "Flask exited — restarting…"
        python3 "$SCRIPT_DIR/app.py" &
        FLASK_PID=$!
    fi
done
