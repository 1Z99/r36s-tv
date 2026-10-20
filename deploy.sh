#!/bin/bash
set -euo pipefail
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
HOST="${TV227_SSH_HOST:-192.168.179.3}"
USER_NAME="${TV227_SSH_USER:-ark}"
KEY="${TV227_SSH_KEY:-$HOME/.ssh/r36s_arkos}"
PREFIX="${TV227_PREFIX:-/roms}"
if [[ $# -ne 0 ]]; then
    printf '%s\n' 'Usage: ./deploy.sh (TV227_SSH_HOST, TV227_SSH_USER, TV227_SSH_KEY, TV227_PREFIX override defaults)' >&2
    exit 2
fi
OPTIONS=(-i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15)
REMOTE="$USER_NAME@$HOST"
FILES=(app.py tv_backend.py tv_media.py tv_quality.py tv_style.py tv_globe.py 'Melkor 227 TV.sh' install.sh LICENSE
       tv_system.py tv_dependencies.py tv_installer.py tv_manager.py
       README.md INSTALLATION.md PUBLISHING.md DEVELOPMENT.md CREDITS.md
       tv_filter.py globe_filter.c build_globe_filter.sh native/libtvglobe.so
       assets/globe.json assets/countries.json assets/NOTICE.md
       assets/DejaVuSansMono.ttf assets/FONT-LICENSE assets/melkor227.svg
       assets/globe-atlas.z assets/intro-frames.zip assets/intro-background.rgba
       assets/logo.rgba assets/DejaVuSansMono-Bold.ttf assets/AI-LICENSE)
RUNTIME_FILES=$(sh "$SOURCE/install.sh" --list-runtime)
if [[ -n "$RUNTIME_FILES" ]]; then
    mapfile -t RUNTIME_ALLOWLIST <<< "$RUNTIME_FILES"
    FILES+=("${RUNTIME_ALLOWLIST[@]}")
fi
for relative in "${FILES[@]}"; do
    [[ -f "$SOURCE/$relative" ]] || { printf 'Missing packaged file: %s\n' "$relative" >&2; exit 1; }
done
[[ -f "$KEY" ]] || { printf 'SSH key not found: %s\n' "$KEY" >&2; exit 1; }
python3 -B -c 'import pathlib, sys
root = pathlib.Path(sys.argv[1])
for name in ("app.py", "tv_backend.py", "tv_media.py", "tv_quality.py", "tv_style.py", "tv_globe.py", "tv_filter.py"):
    path = root / name
    compile(path.read_bytes(), str(path), "exec")
' "$SOURCE"
ARCHIVE=""
STAGE=""
cleanup() {
    if [[ -n "$ARCHIVE" ]]; then rm -f -- "$ARCHIVE"; fi
    if [[ -n "$STAGE" ]]; then
        ssh "${OPTIONS[@]}" "$REMOTE" "rm -rf -- '$STAGE'" || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
ARCHIVE=$(mktemp "$SOURCE/.tv227-upload.XXXXXXXX.tar.gz")
tar -czf "$ARCHIVE" -C "$SOURCE" -- "${FILES[@]}"
STAGE=$(ssh "${OPTIONS[@]}" "$REMOTE" 'ls -ld /tmp >&2 && mktemp -d /tmp/melkor227-tv.XXXXXXXX')
if [[ ! "$STAGE" =~ ^/tmp/melkor227-tv\.[A-Za-z0-9]+$ ]]; then
    printf 'Unsafe remote staging path: %s\n' "$STAGE" >&2
    STAGE=""
    exit 1
fi
scp "${OPTIONS[@]}" "$ARCHIVE" "$REMOTE:$STAGE/bundle.tar.gz"
# POSIX shell quoting also handles a card prefix containing spaces or apostrophes.
QUOTED_PREFIX=$(python3 -c 'import shlex, sys; print(shlex.quote(sys.argv[1]))' "$PREFIX")
printf '%s\n' 'Checking Python, firmware SDL2/SDL2_ttf and system/private libmpv software rendering (no display)...'
ssh "${OPTIONS[@]}" "$REMOTE" "tar -xzf '$STAGE/bundle.tar.gz' -C '$STAGE' && sh '$STAGE/install.sh' --check-only && sudo -n sh '$STAGE/install.sh' --prefix $QUOTED_PREFIX"
printf '%s\n' 'Installed. No display was launched and EmulationStation was not restarted.'
