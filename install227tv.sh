#!/bin/sh
# Generated releases replace the @...@ pins below with pinned release metadata.
set -eu
if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then
    printf '%s\n' '227 TV downloader and graphical installer' \
      'Put the generated Install 227 TV.sh in the games card tools directory.' \
      'Open Tools > Install 227 TV, choose SD1/SD2, then Install or Update / repair.' \
      'TV is registered under 227 beside AI, not Ports.' \
      'Advanced: --cli --roms /roms --action install|update|uninstall --yes' \
      'Local source: --source PATH. Hosting override: TV227_RELEASE_BASE=https://.../'
    exit 0
fi

# Tools must be able to reuse everything created by a first launch through sudo.
# Re-enter before logging or creating storage, retaining explicit caller options.
if [ "$(id -u)" = 0 ] && [ "${SUDO_UID:-0}" != 0 ]; then
    case "$SUDO_UID" in ''|*[!0-9]*) printf '%s\n' 'Invalid invoking sudo UID.' >&2; exit 1;; esac
    exec sudo -n -H --preserve-env=TV227_DATA,TV227_RELEASE_BASE,XDG_STATE_HOME \
        -u "#$SUDO_UID" -- sh "$0" "$@"
fi

bootstrap_audit() {
    EXISTING=$(dpkg-query -W '-f=${binary:Package}\t${db:Status-Status}\n') || return 1
    awk -v existing="$EXISTING" -v missing="$1" '
      BEGIN {
        count=split(existing, lines, "\n")
        for(i=1; i<=count; i++) {
          if(existing == "") break
          if(split(lines[i], fields, "\t") != 2 ||
             fields[1] !~ /^[a-z0-9][a-z0-9+.\055]*(:[a-z0-9\055]+)?$/) {bad=1; continue}
          name=fields[1]; sub(/:.*/, "", name)
          if(fields[2] != "not-installed" && fields[2] != "config-files") installed[name]=1
        }
        count=split(missing, requests, " ")
        for(i=1; i<=count; i++) required[requests[i]]=1
      }
      {sub(/^[[:space:]]+/, ""); sub(/[[:space:]]+$/, "")}
      /^(Remv|Purg)/ {bad=1}
      /^Conf / {name=$2; sub(/:.*/, "", name); configured[name]=1}
      /^Inst/ {
        if($0 !~ /^Inst [a-z0-9][a-z0-9+.\055]*(:arm64|:all)? \([A-Za-z0-9.+:~\055]+( .*)?\)$/) {bad=1; next}
        name=$2; sub(/:.*/, "", name)
        if(installed[name] || planned[name]++ ||
           name ~ /^(mesa|libegl|libgles|libgl|libgbm|libdrm|libmali|firmware|kernel|linux-|systemd|libsystemd|libc6|udev|libudev|xserver|libmpv)/ ||
           (name ~ /^(libsdl2-2.0-0|libsdl2-ttf-2.0-0)$/ && !required[name])) bad=1
        rest=$0
        while(match(rest, /\[[^]]+\]/)) {
          arch=substr(rest, RSTART+1, RLENGTH-2)
          if(arch != "arm64" && arch != "all") bad=1
          rest=substr(rest, RSTART+RLENGTH)
        }
        version=$3; sub(/^\(/, "", version); sub(/\)$/, "", version)
        pins[++total]=$2 "=" version
      }
      END {
        for(name in configured) if(!planned[name] || installed[name]) bad=1
        for(name in required) if(!planned[name]) bad=1
        if(bad) exit 1
        for(i=1; i<=total; i++) print pins[i]
      }'
}

bootstrap() {
# Fresh supported firmware normally includes these. Bootstrap only genuinely
# missing tools; reject upgrades and graphics/base-system replacement plans.
MISSING=''
command -v python3 >/dev/null 2>&1 || MISSING=python3
command -v curl >/dev/null 2>&1 || MISSING="${MISSING:+$MISSING }curl"
[ -r /etc/ssl/certs/ca-certificates.crt ] || MISSING="${MISSING:+$MISSING }ca-certificates"
if [ -n "$MISSING" ]; then
    [ "$(uname -m)" = aarch64 ] || { printf '%s\n' 'Bootstrap requires Debian 13 arm64.' >&2; exit 1; }
    . /etc/os-release
    [ "$ID" = debian ] && [ "$VERSION_ID" = 13 ] || { printf '%s\n' 'Use matching Debian 13 dArkOSRE firmware.' >&2; exit 1; }
    [ "$(dpkg --print-architecture)" = arm64 ] || exit 1
    AUTH=''
    if [ "$(id -u)" != 0 ]; then sudo -n true || exit 1; AUTH='sudo -n'; fi
    PLAN=$($AUTH env LC_ALL=C apt-get --simulate --no-upgrade --no-remove --no-install-recommends install $MISSING) || exit 1
    PINS=$(printf '%s\n' "$PLAN" | bootstrap_audit "$MISSING") || { printf '%s\n' 'Unsafe dependency plan; firmware left unchanged.' >&2; exit 1; }
    [ -n "$PINS" ] || exit 1
    VERIFY=$($AUTH env LC_ALL=C apt-get --simulate --no-upgrade --no-remove --no-install-recommends install $PINS) || exit 1
    VERIFIED_PINS=$(printf '%s\n' "$VERIFY" | bootstrap_audit "$MISSING") || { printf '%s\n' 'Unsafe pinned dependency plan; firmware left unchanged.' >&2; exit 1; }
    [ "$PINS" = "$VERIFIED_PINS" ] || { printf '%s\n' 'Dependency plan changed; firmware left unchanged.' >&2; exit 1; }
    $AUTH env DEBIAN_FRONTEND=noninteractive LC_ALL=C apt-get --yes --no-upgrade --no-remove --no-install-recommends install $PINS
fi
python3 -B -c 'import sys; sys.exit(sys.version_info < (3, 10))' || {
    printf '%s\n' 'Python 3.10 or newer is required; an existing Python is never upgraded.' >&2; exit 1;
}
SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/$(basename -- "$0")
exec python3 -B - "$SELF" "$@" <<'PY'
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urljoin, urlsplit

self = Path(sys.argv[1]).absolute()
arguments = sys.argv[2:]
if "--source" in arguments:
    index = arguments.index("--source")
    if len(arguments) <= index+1:
        sys.exit("--source requires a complete local source directory")
    source = Path(arguments[index+1]).absolute()
    if not (source / "tv_manager.py").is_file():
        sys.exit("Local source is missing tv_manager.py")
    os.execv(sys.executable, [sys.executable, "-B", str(source / "tv_manager.py"), *arguments])

filename = "@MANAGER_FILE@"
expected = "@MANAGER_SHA256@"
default_base = "@RELEASE_BASE@"
if expected.startswith("@"):
    sys.exit("This is an unpublished source template. Run build_release.py and use its generated Install 227 TV.sh, or use --source PATH.")
size = int("@MANAGER_BYTES@")
base = os.environ.get("TV227_RELEASE_BASE", "")
if "--base" in arguments:
    index = arguments.index("--base")
    if len(arguments) <= index+1:
        sys.exit("--base requires an HTTPS release directory or file:// offline directory")
    base = arguments[index+1]
if not base and (self.parent / filename).is_file():
    base = self.parent.as_uri()
if not base and not default_base.startswith("@"):
    base = default_base

mounts = {line.split()[1]: line.split()[3].split(",") for line in Path("/proc/mounts").read_text().splitlines() if len(line.split()) >= 4}
own = next((card for card in ("/roms", "/roms2") if str(self).startswith(card+"/tools/")), None)
card = next((Path(card) for card in dict.fromkeys((own, "/roms2", "/roms")) if card and card in mounts
             and "ro" not in mounts[card] and not Path(card).is_symlink() and os.access(card, os.W_OK | os.X_OK)), None)
if card is None:
    sys.exit("No writable games card is mounted at /roms or /roms2")
tools, manager = card / "tools", card / "tools/227-tv-manager"
if tools.is_symlink() or manager.is_symlink():
    sys.exit("Refusing redirected installer storage")
tools.mkdir(exist_ok=True)
shortcut = tools / "Install 227 TV.sh"
if shortcut.is_symlink() or (shortcut.exists() and "227 TV downloader" not in shortcut.read_text(errors="replace")):
    sys.exit("Refusing unrelated file at Install 227 TV.sh")
if self.resolve() != shortcut.resolve():
    temporary = tools / ".Install-227-TV.new"
    if temporary.is_symlink():
        sys.exit("Refusing redirected installer shortcut")
    temporary.write_bytes(self.read_bytes())
    temporary.chmod(0o755)
    os.replace(temporary, shortcut)
lock = os.open(tools / ".227-tv-manager.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit("The 227 TV installer is already running")
os.set_inheritable(lock, True)
cache = tools / ".227-tv-downloads"
if cache.is_symlink():
    sys.exit("Refusing redirected download cache")
cache.mkdir(exist_ok=True)
archive = cache / filename
remembered = manager / ".release-base"
if remembered.is_symlink():
    sys.exit("Refusing redirected release configuration")
if not base and remembered.is_file() and not remembered.is_symlink():
    base = remembered.read_text().strip()
if not base and archive.is_file():
    base = cache.as_uri()
if urlsplit(base).scheme not in ("https", "file"):
    sys.exit("No public release is configured. Keep both tar.gz files beside this script for offline installation, or set TV227_RELEASE_BASE to your HTTPS release directory.")

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()

if archive.is_symlink():
    sys.exit("Refusing redirected download")
if not archive.is_file() or archive.stat().st_size != size or sha(archive) != expected:
    part = archive.with_suffix(archive.suffix+".part")
    if part.is_symlink():
        sys.exit("Refusing redirected partial download")
    url = urljoin(base.rstrip("/")+"/", filename)
    if urlsplit(base).scheme == "file":
        from urllib.request import url2pathname
        origin = Path(url2pathname(urlsplit(url).path))
        if origin.resolve() == part.resolve():
            sys.exit("Download source and temporary destination overlap")
        shutil.copyfile(origin, part)
    else:
        subprocess.run(["curl", "--fail", "--location", "--proto", "=https", "--proto-redir", "=https",
                        "--connect-timeout", "15", "--speed-limit", "1024", "--speed-time", "30",
                        "--max-time", "300", "--max-filesize", str(size), "--output", str(part), url], check=True)
    if part.stat().st_size != size or sha(part) != expected:
        part.unlink(missing_ok=True)
        sys.exit("Installer SHA-256 verification failed; no installed app was changed")
    os.replace(part, archive)

# A marker cannot attest to mutable extracted code. Restore only pinned files on
# every launch, retaining downloads but never stale modules or bytecode.
with tempfile.TemporaryDirectory(prefix=".227-tv-bootstrap-", dir=tools) as folder:
    stage = Path(folder) / "manager"
    stage.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        seen, total = set(), 0
        for member in bundle.getmembers():
            path = PurePosixPath(member.name)
            total += member.size
            if (not member.isfile() or path.is_absolute() or ".." in path.parts or "\\" in member.name
                    or str(path) != member.name or member.name in seen or total > 16*1024*1024):
                sys.exit("Unsafe installer archive entry")
            seen.add(member.name)
            target = stage / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as src, target.open("xb") as out:
                shutil.copyfileobj(src, out)
    for name in ("tv_manager.py", "tv_installer.py", "tv_dependencies.py", "tv_system.py", "release.json"):
        if not (stage / name).is_file():
            sys.exit("Installer bundle is incomplete: "+name)
    for path in stage.glob("*.py"):
        compile(path.read_bytes(), str(path), "exec")
    if (manager / "cache").is_symlink():
        sys.exit("Refusing redirected installer cache")
    if (manager / "cache").exists():
        shutil.copytree(manager / "cache", stage / "cache", symlinks=True)
    old = Path(folder) / "old"
    if manager.exists():
        os.replace(manager, old)
    try:
        os.replace(stage, manager)
    except BaseException:
        if old.exists():
            os.replace(old, manager)
        raise
if urlsplit(base).scheme == "file" and "--dry-run" not in arguments:
    from urllib.request import url2pathname
    release = json.loads((manager / "release.json").read_text())
    payload = release["bundle"]["file"]
    if Path(payload).name != payload:
        sys.exit("Unsafe payload filename")
    origin = Path(url2pathname(urlsplit(urljoin(base.rstrip("/")+"/", payload)).path))
    destination = cache / payload
    if origin.is_file() and origin.resolve() != destination.resolve():
        if destination.is_symlink():
            sys.exit("Refusing redirected offline bundle")
        shutil.copyfile(origin, destination)
    if destination.is_file():
        base = cache.as_uri()
remembered.write_text(base+"\n")
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.execv(sys.executable, [sys.executable, "-B", str(manager / "tv_manager.py"), "--base", base, *arguments])
PY
}

BOOTSTRAP_LOG=${XDG_STATE_HOME:-${HOME:?HOME is required}/.local/state}/melkor227-tv/bootstrap.log
mkdir -p -- "$(dirname -- "$BOOTSTRAP_LOG")"
# Keep stdout intact for --cli JSON output; stderr survives even pre-SDL errors.
printf '\n%s\n' 'Starting 227 TV bootstrap' >>"$BOOTSTRAP_LOG"
bootstrap_finished() {
    status=$?
    trap - 0
    if [ "$status" -ne 0 ]; then
        printf '227 TV bootstrap failed (status %s). Log: %s\n' "$status" "$BOOTSTRAP_LOG" >&2
        tail -n 12 -- "$BOOTSTRAP_LOG" >&2 || :
        if [ -t 2 ] && command -v dialog >/dev/null 2>&1; then
            dialog --title '227 TV installation failed' --textbox "$BOOTSTRAP_LOG" 20 74 >&2 || :
        fi
    fi
    exit "$status"
}
trap bootstrap_finished 0
(bootstrap "$@") 2>>"$BOOTSTRAP_LOG"
