#!/bin/bash
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP=""
for candidate in "$HERE/melkor227-tv" "$HERE/../tools/melkor227-tv" "$HERE"; do
    if [[ -f "$candidate/app.py" ]]; then
        APP="$candidate"
        break
    fi
done
export TV227_DATA="${TV227_DATA:-$HOME/.local/share/melkor227-tv}"
umask 077
mkdir -p -- "$TV227_DATA"
exec >>"$TV227_DATA/app.log" 2>&1
if [[ -z "$APP" ]]; then
    printf '%s\n' "Melkor 227 TV: app.py not found relative to $HERE"
    exit 1
fi
command -v flock >/dev/null || { printf '%s\n' 'Melkor 227 TV requires flock'; exit 1; }
exec 9>"$TV227_DATA/app.lock"
flock -n 9 || { printf '%s\n' 'Melkor 227 TV is already running'; exit 0; }

RUNTIME=""
CHILD=""
cleanup() {
    if [[ -n "$CHILD" ]]; then
        kill -TERM "$CHILD" 2>/dev/null || true
        wait "$CHILD" 2>/dev/null || true
    fi
    if [[ -n "$RUNTIME" ]]; then
        rmdir -- "$RUNTIME" 2>/dev/null || true
    fi
    # ES owns the launcher process; returning from it restores the menu.
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

DESKTOP=0
for argument in "$@"; do
    if [[ "$argument" == --desktop ]]; then DESKTOP=1; fi
done
if [[ "$DESKTOP" == 0 ]]; then
    export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-kmsdrm}"
    export SDL_RENDER_DRIVER=software
    # Use the verified firmware blob, not a bundled graphics stack.
    if [[ -f /usr/lib/aarch64-linux-gnu/libMali.so ]]; then
        export SDL_VIDEO_EGL_DRIVER=/usr/lib/aarch64-linux-gnu/libMali.so
        export SDL_VIDEO_GL_DRIVER=/usr/lib/aarch64-linux-gnu/libMali.so
    fi
    if [[ -z "${XDG_RUNTIME_DIR:-}" || ! -d "$XDG_RUNTIME_DIR" || ! -w "$XDG_RUNTIME_DIR" ]]; then
        RUNTIME=$(mktemp -d /tmp/tv227-runtime.XXXXXXXX)
        export XDG_RUNTIME_DIR="$RUNTIME"
    fi
fi
if [[ -d "$APP/runtime/lib" ]]; then
    export LD_LIBRARY_PATH="$APP/runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
export PYTHONDONTWRITEBYTECODE=1
printf '\n[%s] Starting Melkor 227 TV\n' "$(date -Is)"
python3 "$APP/app.py" "$@" &
CHILD=$!
STATUS=0
wait "$CHILD" || STATUS=$?
CHILD=""
exit "$STATUS"
