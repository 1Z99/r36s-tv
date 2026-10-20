#!/bin/sh
# Device-side installer: all installation work uses the Python standard library.
set -eu
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONDONTWRITEBYTECODE=1
python3 -B -c 'import sys; sys.exit(sys.version_info < (3, 10))' || {
    printf '%s\n' '227 TV installation requires Python 3.10 or newer.' >&2; exit 1;
}
# The loader reads this at process startup, before ctypes loads libmpv by soname.
if [ -d "$SOURCE/runtime/lib" ]; then
    export LD_LIBRARY_PATH="$SOURCE/runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
exec python3 - "$SOURCE" "$@" <<'PY'
import argparse
from contextlib import ExitStack
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

source = Path(sys.argv[1])
parser = argparse.ArgumentParser(description="Install Melkor 227 TV without changing firmware")
parser.add_argument("--prefix", type=Path, default=Path("/roms"),
                    help="existing card root (default: /roms)")
parser.add_argument("--check-only", action="store_true",
                     help="probe runtime libraries only; no writes or display initialization")
parser.add_argument("--list-runtime", action="store_true",
                    help="validate and list the private runtime packaging allowlist; no probes or writes")
parser.add_argument("--es-cfg", type=Path, default=Path("/etc/emulationstation/es_systems.cfg"))
parser.add_argument("--themes", type=Path, help="Optional ES themes root; existing 227 themes are kept")
parser.add_argument("--app-lock-held", action="store_true", help=argparse.SUPPRESS)
args = parser.parse_args(sys.argv[2:])


def runtime_files():
    root = source / "runtime"
    if not root.exists() and not root.is_symlink():
        return []
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Runtime must be a real directory")
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Runtime manifest must not be a symlink")
    manifest = json.loads(manifest_path.read_text())
    allowed = {"libmpv2", "libmujs3", "libsixel1", "libva-wayland2"}
    packages = {item["package"] for item in manifest["packages"]}
    if "libmpv2" not in packages or not packages <= allowed:
        raise ValueError("Unexpected private runtime packages")
    if any(not item.get("version") or not item.get("source") for item in manifest["packages"]):
        raise ValueError("Runtime package versions and sources are required")
    libraries = ("libmpv.so.", "libmujs.so.", "libsixel.so.", "libva-wayland.so.")
    sources = {"mpv_0.40.0.orig.tar.gz", "mpv_0.40.0-3+deb13u1.debian.tar.xz",
               "mpv_0.40.0-3+deb13u1.dsc", "build_runtime.py"}
    entries = manifest["files"]
    if "build" in manifest:
        if not manifest["build"].get("options") or not all(
                "sources/" + name in entries for name in sources):
            raise ValueError("Custom runtime requires build options and corresponding source/recipe")
    result = ["runtime/manifest.json"]
    for name, entry in sorted(entries.items()):
        path = Path(name)
        if (path.is_absolute() or ".." in path.parts or path.as_posix() != name
                or len(path.parts) < 2 or
                not ((path.parts[0] == "lib" and len(path.parts) == 2
                      and path.name.startswith(libraries)) or
                     (path.parts[0] == "licenses" and len(path.parts) >= 3
                      and path.parts[1] in packages | {"common-licenses"}) or
                     (path.parts[0] == "sources" and len(path.parts) == 2
                      and path.name in sources))):
            raise ValueError("Unsafe runtime allowlist entry: " + name)
        target = root / path
        if root.is_symlink() or any(parent.is_symlink() for parent in target.parents
                                    if parent != source and source in parent.parents):
            raise ValueError("Symlinked runtime directory: " + name)
        if "symlink" in entry:
            link = entry["symlink"]
            if (path.parts[0] != "lib" or Path(link).name != link
                    or "lib/" + link not in entries or
                    "sha256" not in entries["lib/" + link]
                    or not target.is_symlink() or os.readlink(target) != link):
                raise ValueError("Unsafe runtime symlink: " + name)
        elif (target.is_symlink() or not target.is_file() or
              hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]):
            raise ValueError("Runtime checksum mismatch: " + name)
        result.append("runtime/" + name)
    for package, soname in (("libmpv2", "libmpv.so.2"), ("libmujs3", "libmujs.so.3"),
                            ("libsixel1", "libsixel.so.1"), ("libva-wayland2", "libva-wayland.so.2")):
        if package in packages and "lib/" + soname not in entries:
            raise ValueError("Missing runtime soname: " + soname)
        if package in packages and "licenses/" + package + "/copyright" not in entries:
            raise ValueError("Missing runtime copyright: " + package)
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*")
              if path.is_file() or path.is_symlink()}
    if actual != set(entries) | {"manifest.json"}:
        raise ValueError("Runtime contains files outside the manifest allowlist")
    return result


def check_runtime():
    failures = []
    for name, soname, symbol in (("SDL2", "libSDL2-2.0.so.0", "SDL_GetVersion"),
                                  ("SDL2_ttf", "libSDL2_ttf-2.0.so.0", "TTF_Linked_Version")):
        try:
            library = ctypes.CDLL(ctypes.util.find_library(name) or soname)
            getattr(library, symbol)
            print("OK: {} ({})".format(name, library._name))
        except (OSError, AttributeError) as exc:
            failures.append("{} unavailable: {}".format(name, exc))
    sys.path.insert(0, str(source))
    try:
        from tv_media import availability
        available, diagnostic = availability()
        if not available:
            failures.append(diagnostic + "\nSystem libmpv or an optional private runtime must "
                            "support api_type=sw, RGBA. No packages or firmware were changed.")
        else:
            print("OK: " + diagnostic)
    except (ImportError, OSError) as exc:
        failures.append("Cannot probe tv_media.availability: {}".format(exc))
    if not shutil.which("flock"):
        failures.append("flock is required by the launcher (util-linux); firmware was not changed")
    if failures:
        raise RuntimeError("Runtime preflight failed:\n" + "\n".join(failures))
    print("OK: Python {} and flock; no SDL display initialized".format(sys.version.split()[0]))


locks = ExitStack()
try:
    runtime = runtime_files()
    if args.list_runtime:
        if runtime:
            print("\n".join(runtime))
        sys.exit(0)
    check_runtime()
    if args.check_only:
        sys.exit(0)
    sys.path.insert(0, str(source))
    from tv_installer import app_locked, managed_destinations, prune_legacy_art, restore_app, safe_path
    prefix = safe_path(args.prefix.expanduser())
    if not prefix.is_dir():
        raise RuntimeError("Card root must already exist and be mounted: {} (not created)".format(prefix))
    if not os.access(prefix, os.W_OK | os.X_OK):
        raise RuntimeError("Card root is not writable: {}".format(prefix))
    from tv_system import ART, LEGACY_ART, LAUNCHER, SystemMenu
    menu = SystemMenu(source, prefix, args.es_cfg, args.themes)
    menu.validate()
    app = managed_destinations(prefix)
    # The manager holds this same lock across subprocess execution and rollback.
    if not args.app_lock_held:
        locks.enter_context(app_locked())
    files = ["app.py", "tv_backend.py", "tv_media.py", "tv_quality.py", "tv_style.py", "tv_globe.py",
             "tv_system.py", "tv_dependencies.py", "tv_installer.py", "tv_manager.py",
              "tv_filter.py", "globe_filter.c", "build_globe_filter.sh", "native/libtvglobe.so",
             "Melkor 227 TV.sh", "install.sh", "LICENSE", "README.md", "INSTALLATION.md", "PUBLISHING.md",
             "DEVELOPMENT.md", "CREDITS.md"]
    assets = ["globe.json", "countries.json", "NOTICE.md", "DejaVuSansMono.ttf",
              "FONT-LICENSE", "melkor227.jpg", "globe-atlas.z", "intro-frames.zip",
              "intro-background.rgba", "logo.rgba", "DejaVuSansMono-Bold.ttf", "AI-LICENSE"]
    for relative in files + ["assets/" + name for name in assets]:
        if not (source / relative).is_file():
            raise RuntimeError("Missing packaged file: {}".format(source / relative))
    tools = prefix / "tools"
    tools.mkdir(exist_ok=True)
    fallback = tools / "Melkor 227 TV.sh"
    with tempfile.TemporaryDirectory(dir=tools, prefix=".tv227-publish-") as temporary:
        stage = Path(temporary)
        staged = stage / "app"
        if app.exists():
            shutil.copytree(app, staged, symlinks=True)
        else:
            staged.mkdir()
        safe_path(staged / "assets").mkdir(exist_ok=True)
        safe_path(staged / "native").mkdir(exist_ok=True)
        # Build a complete tree before touching the live app, including native code.
        if runtime:
            destination = safe_path(staged / "runtime")
            if destination.exists():
                shutil.rmtree(destination)
            destination.mkdir()
            for relative in runtime:
                origin = source / relative
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if origin.is_symlink():
                    target.symlink_to(os.readlink(origin))
                else:
                    shutil.copyfile(origin, target)
                    target.chmod(0o644)
        for relative in files + ["assets/" + name for name in assets]:
            target = safe_path(staged / relative)
            shutil.copyfile(source / relative, target)
            target.chmod(0o755 if relative.endswith(".sh") else 0o644)
        prune_legacy_art(staged, Path("assets") / LEGACY_ART.name, menu_directory=app)
        if (tools / "images").exists():
            (stage / "previous-tools/images").mkdir(parents=True)
        for relative in (LAUNCHER, LEGACY_ART, ART):
            original = tools / relative
            if original.exists():
                saved = stage / "previous-tools" / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, saved)
        managed_destinations(prefix)
        previous = stage / "previous-app"
        if app.exists():
            os.replace(app, previous)
        try:
            os.replace(staged, app)
            for relative, origin, mode in (
                    (LAUNCHER, source / LAUNCHER, 0o755),
                    (ART, source / "assets" / ART.name, 0o644)):
                target = tools / relative
                target.parent.mkdir(exist_ok=True)
                replacement = stage / target.name
                shutil.copyfile(origin, replacement)
                replacement.chmod(mode)
                os.replace(replacement, target)
            prune_legacy_art(tools, LEGACY_ART)
            registration = menu.install()
        except Exception as error:
            try:
                restore_app(app, stage)
            except Exception as recovery:
                recovery_dir = Path(tempfile.mkdtemp(prefix=".tv227-recovery-", dir=tools))
                os.replace(stage, recovery_dir / "backup")
                raise RuntimeError("{}; rollback failed: {}; recovery files: {}".format(
                    error, recovery, recovery_dir)) from error
            raise
    print("227 system launcher: {}".format(registration["launcher"]))
    print("Tools fallback: {}\nInstalled app: {}".format(fallback, app / "app.py"))
    print("Saved data was not copied, replaced or removed. EmulationStation was not restarted.")
except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
    print("Install failed: {}".format(exc), file=sys.stderr)
    sys.exit(1)
finally:
    locks.close()
PY
