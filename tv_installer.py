"""Verified TV release downloads and a per-app installer for the shared 227 menu."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urljoin, urlsplit

from tv_dependencies import ensure
from tv_release import TARGET, canonical_base, fetch_release, source_version, stable_version, validate_release
from tv_system import ART, LEGACY_ART, LAUNCHER, SystemMenu, artwork_owned

VERSION = "1.0.2"
APP_FILES = ("app.py", "tv_backend.py", "tv_media.py", "tv_quality.py", "tv_style.py",
             "tv_globe.py", "tv_filter.py", "tv_system.py", "tv_dependencies.py",
             "tv_installer.py", "tv_manager.py", "tv_release.py", "globe_filter.c", "build_globe_filter.sh",
             "native/libtvglobe.so", "Melkor 227 TV.sh", "install.sh", "LICENSE",
             "README.md", "INSTALLATION.md", "PUBLISHING.md", "DEVELOPMENT.md", "CREDITS.md")
ASSET_FILES = ("globe.json", "countries.json", "NOTICE.md", "DejaVuSansMono.ttf", "FONT-LICENSE",
               "melkor227.jpg", "globe-atlas.z", "intro-frames.zip", "intro-background.rgba",
               "logo.rgba", "DejaVuSansMono-Bold.ttf", "AI-LICENSE")


class Cancelled(RuntimeError):
    pass


def digest(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            sha.update(block)
    return sha.hexdigest()


def safe_path(path):
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("Path traversal is not allowed: " + str(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Refusing redirected path: " + str(part))
    return path


def managed_destinations(prefix):
    """Validate app-owned destinations without following redirects or links."""
    tools = safe_path(Path(prefix) / "tools")
    app = tools / "melkor227-tv"
    manager = tools / "227-tv-manager"
    for directory in (tools, app, manager, tools / "images"):
        safe_path(directory)
        if directory.exists() and not directory.is_dir():
            raise ValueError("Expected managed directory: " + str(directory))
    for root in (app, manager):
        if not root.exists():
            continue
        # Private runtime soname aliases are intentional, manifest-pinned links.
        aliases = {}
        manifest = root / "runtime/manifest.json"
        safe_path(manifest)
        if root == app and manifest.is_file():
            aliases = json.loads(manifest.read_text()).get("files", {})
        for path in root.rglob("*"):
            mode = path.lstat()
            if stat.S_ISLNK(mode.st_mode):
                name = path.relative_to(root).as_posix()
                entry = aliases.get(name.removeprefix("runtime/"), {})
                link = entry.get("symlink", "")
                if (name.startswith("runtime/lib/") and len(Path(name).parts) == 3
                        and path.name.startswith(("libmpv.so.", "libmujs.so.", "libsixel.so.", "libva-wayland.so."))
                        and link and Path(link).name == link and os.readlink(path) == link
                        and "sha256" in aliases.get("lib/" + link, {})
                        and not (path.parent / link).is_symlink()):
                    target = safe_path(path.parent / link)
                    if target.is_file() and target.stat().st_nlink == 1:
                        continue
                raise ValueError("Refusing redirected destination: " + str(path))
            if not (stat.S_ISDIR(mode.st_mode) or stat.S_ISREG(mode.st_mode)):
                raise ValueError("Unexpected managed file type: " + str(path))
            if stat.S_ISREG(mode.st_mode) and mode.st_nlink != 1:
                raise ValueError("Refusing hardlinked destination: " + str(path))
    for relative, marker in ((LAUNCHER, b"melkor227-tv"),
                             (LEGACY_ART, b"<title>Melkor 227 TV</title>")):
        path = safe_path(tools / relative)
        if path.exists():
            if not path.is_file() or path.stat().st_nlink != 1:
                raise ValueError("Unsafe Tools destination: " + str(path))
            if marker not in path.read_bytes():
                raise ValueError("Refusing unrelated Tools file: " + str(path))
    artwork_owned(tools / ART)
    return app


def prune_legacy_art(directory, relative, menu_directory=None):
    """Remove only marked SVG artwork that no menu metadata still references."""
    from tv_system import _matches, _xml

    path = safe_path(directory / relative)
    if not path.exists() or b"<title>Melkor 227 TV</title>" not in path.read_bytes():
        return
    games = _xml(directory / "gamelist.xml", "gameList")
    # A staged copy's absolute references still name its live app directory.
    menu_directory = menu_directory or directory
    if not any(_matches(menu_directory, node.text, menu_directory / relative) for node in games.iter()
               if node.tag in {"image", "thumbnail", "marquee"}):
        path.unlink()


@contextmanager
def app_locked():
    # Match the launcher's empty/unset TV227_DATA fallback, even for uninstall.
    caller = None
    if os.geteuid() == 0 and os.environ.get("SUDO_UID") not in (None, "0"):
        caller = pwd.getpwuid(int(os.environ["SUDO_UID"]))
    home = Path(caller.pw_dir) if caller else Path.home()
    data = safe_path(os.environ.get("TV227_DATA") or home/".local/share/melkor227-tv")
    missing = [path for path in (*reversed(data.parents), data) if not path.exists()]
    data.mkdir(parents=True, exist_ok=True)
    # A direct sudo source install must not leave root-owned app data/locks.
    if caller:
        for path in missing:
            os.chown(path, caller.pw_uid, caller.pw_gid)
    path = safe_path(data / "app.lock")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Unsafe app lock: " + str(path))
        if caller:
            os.fchown(descriptor, caller.pw_uid, caller.pw_gid)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Close the running TV app before installing, updating or uninstalling it") from exc
        yield
    finally:
        os.close(descriptor)


def normalize_manager_owner(tools):
    """Leave sudo-created management storage reusable by the invoking user."""
    if os.geteuid() != 0 or os.environ.get("SUDO_UID") in (None, "0"):
        return
    caller = pwd.getpwuid(int(os.environ["SUDO_UID"]))
    manager = safe_path(tools / "227-tv-manager")
    paths = [tools, manager]
    if manager.exists():
        paths.extend(manager.rglob("*"))
    for path in paths:
        safe_path(path)
        if not path.exists():
            continue
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) or (
                    stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                raise ValueError("Unsafe management storage: " + str(path))
            os.fchown(descriptor, caller.pw_uid, caller.pw_gid)
        finally:
            os.close(descriptor)


def restore_app(app, stage):
    """Restore an exact tree, not a merge that leaves failed-version files."""
    managed_destinations(app.parent.parent)
    if app.exists():
        shutil.rmtree(app)
    backup = stage / "previous-app"
    if backup.exists():
        os.replace(backup, app)
    for relative in (LAUNCHER, LEGACY_ART, ART):
        target = app.parent / relative
        saved = stage / "previous-tools" / relative
        if saved.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(saved, target)
        else:
            target.unlink(missing_ok=True)
    images = app.parent / "images"
    if (images.exists() and not (stage / "previous-tools/images").exists()
            and not any(images.iterdir())):
        images.rmdir()


def discover_cards():
    mounts = {}
    for line in Path("/proc/mounts").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 4:
            mounts[fields[1]] = fields[3].split(",")
    return [dict(path=path, name=name,
                 available=path in mounts and "ro" not in mounts[path] and not Path(path).is_symlink()
                 and os.access(path, os.W_OK | os.X_OK))
            for path, name in (("/roms", "SD1"), ("/roms2", "SD2"))]


def compatible():
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep:
            release[key] = value.strip('"\'')
    if (platform.machine() != "aarch64" or release.get("ID") != "debian"
            or release.get("VERSION_ID") != "13"):
        raise RuntimeError("This release targets dArkOSRE on Debian 13 arm64. Older ArkOS/Ubuntu, "
                           "32-bit firmware and other R36S distributions need a matching runtime build. "
                           "No firmware or graphics libraries were changed.")


def extract_bundle(archive, target, maximum=256*1024*1024):
    target = safe_path(target)
    with tarfile.open(archive, "r:gz") as bundle:
        members, seen, total = bundle.getmembers(), set(), 0
        if len(members) > 4096:
            raise ValueError("Too many release archive entries")
        for member in members:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or ".." in name.parts or not name.parts or str(name) != member.name
                    or "\\" in member.name or not (member.isfile() or member.isdir()) or member.name in seen):
                raise ValueError("Unsafe release archive entry: " + member.name)
            seen.add(member.name)
            total += member.size
            if member.size < 0 or total > maximum:
                raise ValueError("Release archive exceeds its unpacked size limit")
        for member in members:
            path = safe_path(target / member.name)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, path.open("xb") as output:
                    shutil.copyfileobj(source, output)
                path.chmod(0o755 if member.mode & 0o111 else 0o644)


def verify_tree(root):
    root = safe_path(root)
    manifest = json.loads((root / "bundle-manifest.json").read_text())
    expected = manifest["files"]
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() or p.is_symlink()}
    if actual != set(expected) | {"bundle-manifest.json"}:
        raise ValueError("Bundle contains missing or unexpected files")
    for name, sha in expected.items():
        path = PurePosixPath(name)
        if (path.is_absolute() or ".." in path.parts or not re.fullmatch("[0-9a-f]{64}", sha)
                or digest(safe_path(root / name)) != sha):
            raise ValueError("Bundle file verification failed: " + name)
    for name in APP_FILES:
        if name.endswith(".py") and name in expected:
            compile((root / name).read_bytes(), name, "exec")
    return manifest


class Installer:
    def __init__(self, prefix, source_dir=None, emit=None, cancel=None,
                 es_cfg="/etc/emulationstation/es_systems.cfg", themes=None,
                  base=None, release=None, development=False, pinned=False, discovery_error=""):
        self.prefix = safe_path(prefix)
        self.source_dir = safe_path(source_dir) if source_dir else None
        self.emit, self.cancel = emit, cancel
        self.es_cfg, self.themes = Path(es_cfg), Path(themes) if themes else None
        self.base = base or os.environ.get("TV227_RELEASE_BASE", "") or (release or {}).get("base_url", "")
        self.release = release
        self.pinned = pinned or bool(base or os.environ.get("TV227_RELEASE_BASE") or source_dir)
        self.discovery_error = discovery_error
        self.development = development
        if self.themes is None and not development:
            candidate = Path("/etc/emulationstation/themes")
            if candidate.is_dir():
                self.themes = candidate.resolve()
        self.started = time.monotonic()

    def event(self, phase, label, detail="", done=0, total=None, **extra):
        if self.emit:
            self.emit(dict(phase=phase, label=label, detail=str(detail), done=done,
                           total=total, elapsed=time.monotonic()-self.started, **extra))

    def checkpoint(self):
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled("Installation cancelled. Installed TV and saved data were kept.")

    def validate_card(self):
        if not self.prefix.is_dir() or not os.access(self.prefix, os.W_OK | os.X_OK):
            raise RuntimeError("The selected card must already exist and be writable: " + str(self.prefix))
        if not self.development:
            compatible()
            if not any(row["available"] and row["path"] == str(self.prefix) for row in discover_cards()):
                raise RuntimeError("Selected ROM card is not mounted and writable")
        return self.prefix

    def _download(self, info, cache):
        name, sha = info["file"], info["sha256"]
        if (Path(name).name != name or not re.fullmatch("[0-9a-f]{64}", sha)
                or type(info["bytes"]) is not int or not 0 < info["bytes"] <= 128*1024*1024):
            raise ValueError("Invalid pinned release metadata")
        target = safe_path(cache / name)
        if target.is_file() and target.stat().st_size == info["bytes"] and digest(target) == sha:
            return target
        if not self.base:
            raise RuntimeError("No release URL is configured. Use the generated release installer "
                               "or set TV227_RELEASE_BASE to the matching HTTPS release directory.")
        parsed = urlsplit(self.base)
        if parsed.scheme not in ("https", "file"):
            raise ValueError("Release downloads require HTTPS (file:// is supported for local bundles)")
        url = urljoin(self.base.rstrip("/")+"/", name)
        part = safe_path(cache / (name+"."+hashlib.sha256(url.encode()).hexdigest()[:12]+".part"))
        cache.mkdir(parents=True, exist_ok=True)
        if parsed.scheme == "file":
            from urllib.request import url2pathname
            origin = Path(url2pathname(urlsplit(url).path))
            with origin.open("rb") as src, part.open("wb") as out:
                while True:
                    self.checkpoint()
                    block = src.read(256*1024)
                    if not block:
                        break
                    out.write(block)
                    self.event("download", "Copying verified TV bundle", name, out.tell(), info["bytes"])
        else:
            for attempt in range(2):
                self.checkpoint()
                command = ["curl", "--fail", "--location", "--proto", "=https", "--proto-redir", "=https",
                           "--connect-timeout", "15", "--speed-limit", "1024", "--speed-time", "30",
                           "--max-time", "600", "--max-filesize", str(info["bytes"]),
                           "--silent", "--show-error", "--output", str(part)]
                if part.exists() and attempt == 0:
                    command += ["--continue-at", "-"]
                with tempfile.TemporaryFile() as errors:
                    process = subprocess.Popen(command+[url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
                    tick, previous = time.monotonic(), part.stat().st_size if part.exists() else 0
                    try:
                        while process.poll() is None:
                            self.checkpoint()
                            size = part.stat().st_size if part.exists() else 0
                            if size > info["bytes"]:
                                raise ValueError("Downloaded release exceeds its pinned size")
                            now = time.monotonic()
                            self.event("download", "Downloading 227 TV", name, size, info["bytes"],
                                       speed=max(0, size-previous)/max(.001, now-tick))
                            tick, previous = now, size
                            time.sleep(.15)
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
                    errors.seek(0)
                    message = errors.read(4096).decode(errors="replace")
                if process.returncode == 0 and part.stat().st_size == info["bytes"] and digest(part) == sha:
                    break
                part.unlink(missing_ok=True)
                if attempt:
                    raise RuntimeError("TV download failed verification: " + message)
        if part.stat().st_size != info["bytes"] or digest(part) != sha:
            part.unlink(missing_ok=True)
            raise ValueError("TV bundle SHA-256/size mismatch; installed files were not changed")
        os.replace(part, target)
        return target

    def _authority(self):
        if self.development or os.geteuid() == 0:
            return []
        result = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5)
        if result.returncode:
            raise RuntimeError("Installation needs root or sudo -n; no password is requested or stored")
        return ["sudo", "-n"]

    def _uninstall(self, app):
        from tv_system import LAUNCHER
        managed_destinations(self.prefix)
        if self.development or os.geteuid() == 0:
            SystemMenu(app, self.prefix, self.es_cfg, self.themes).uninstall()
            fallback = self.prefix / "tools" / LAUNCHER
            if fallback.exists():
                fallback.unlink()
            if app.exists():
                shutil.rmtree(app)
        else:
            command = [*self._authority(), sys.executable, "-B", str(Path(__file__).resolve()),
                       "--internal-uninstall", str(self.prefix), str(self.es_cfg)]
            subprocess.run(command, check=True, timeout=60)
        return dict(outcome="uninstalled", system="227", saved_data="preserved")

    def run(self, action):
        if action not in ("install", "update", "repair", "uninstall"):
            raise ValueError("Unknown installer action")
        self.validate_card()
        self.checkpoint()
        managed_destinations(self.prefix)
        with app_locked():
            try:
                return self._run_locked(action)
            finally:
                normalize_manager_owner(self.prefix / "tools")

    def _run_locked(self, action):
        managed_destinations(self.prefix)
        tools = safe_path(self.prefix / "tools")
        tools.mkdir(exist_ok=True)
        app = safe_path(tools / "melkor227-tv")
        manager = safe_path(tools / "227-tv-manager")
        manager.mkdir(exist_ok=True)
        descriptor = os.open(manager / "install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(descriptor, "a+b") as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Unsafe installation lock")
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another TV installation is running") from exc
            if action == "uninstall":
                self.event("uninstall", "Removing TV only", "227 AI, the shared system and your TV favorites are kept")
                return self._uninstall(app)
            previous_version = None
            installed = None
            if app.exists() and not self.source_dir:
                record = safe_path(app / "installed-release.json")
                if record.is_file():
                    installed = json.loads(record.read_text())
                    validate_release(installed, manager=False, canonical=False)
                    previous_version = installed["version"]
                else:
                    previous_version = source_version(app / "tv_installer.py")
            elif self.source_dir and (app / "tv_installer.py").is_file():
                previous_version = source_version(app / "tv_installer.py")
            if action == "repair" and not app.exists():
                raise RuntimeError("TV is not installed; choose Install")
            info = None
            if not self.source_dir:
                if action == "repair":
                    info = installed or fetch_release(previous_version)
                    self.base = info.get("base_url") or canonical_base(previous_version)
                elif not self.pinned:
                    self.event("release", "Checking latest stable TV release")
                    try:
                        info = fetch_release()
                    except Exception as exc:
                        raise RuntimeError("Latest release check failed; Update/Install was not performed. "
                                           "Repair and Uninstall remain available. " + str(exc)) from exc
                    self.base = info["base_url"]
                else:
                    info = self.release or json.loads((Path(__file__).parent / "release.json").read_text())
                validate_release(info, manager=False, canonical=False)
            selected_version = source_version(self.source_dir / "tv_installer.py") if self.source_dir else info["version"]
            if action == "repair" and selected_version != previous_version:
                raise RuntimeError("Repair source does not match the installed TV version")
            if action != "repair" and previous_version is not None:
                if stable_version(selected_version) < stable_version(previous_version):
                    raise RuntimeError("Refusing to downgrade TV from " + previous_version + " to " + selected_version)
                if action == "update" and selected_version == previous_version:
                    return dict(outcome="up-to-date", version=previous_version, previous_version=previous_version,
                                latest_checked=not self.pinned, system="227", saved_data="preserved")
            with tempfile.TemporaryDirectory(prefix=".tv227-stage-", dir=tools) as directory:
                stage = Path(directory)
                if self.source_dir:
                    source = self.source_dir
                else:
                    archive = self._download(info["bundle"], manager / "cache")
                    source = stage / "payload"
                    self.event("verify", "Verifying and unpacking TV")
                    extract_bundle(archive, source, info["bundle"].get("unpacked_bytes", 256*1024*1024))
                    manifest = verify_tree(source)
                    if any(manifest.get(key) != info[key] for key in ("format", "version", "target")):
                        raise ValueError("Bundle manifest does not match selected release")
                    if source_version(source / "tv_installer.py") != info["version"]:
                        raise ValueError("Bundle installer version does not match selected release")
                    # install.sh copies this into its publication stage, never after publication.
                    record = dict(info, base_url=self.base)
                    (stage / "selected-release.json").write_text(json.dumps(record, sort_keys=True)+"\n")
                menu = SystemMenu(source, self.prefix, self.es_cfg, self.themes)
                menu.validate()
                needed = sum(p.stat().st_size for p in source.rglob("*") if p.is_file())*2+16*1024*1024
                if shutil.disk_usage(self.prefix).free < needed:
                    raise RuntimeError("Not enough free space for staging and rollback (%s MiB required)" % (needed//1048576))
                self.checkpoint()
                if not self.development:
                    self.event("dependencies", "Checking playback dependencies")
                    status = ensure(source, self.emit, self.cancel)
                    self.checkpoint()
                    if not status["ok"]:
                        raise RuntimeError("Dependency setup failed: " + json.dumps(status, indent=2))
                self.checkpoint()
                self.event("install", "Installing TV into the 227 system", "Do not power off during this step")
                managed_destinations(self.prefix)
                backup = stage / "previous-app"
                if app.exists():
                    shutil.copytree(app, backup, symlinks=True)
                if (tools / "images").exists():
                    (stage / "previous-tools/images").mkdir(parents=True)
                for relative in (LAUNCHER, LEGACY_ART, ART):
                    origin = tools / relative
                    if origin.exists():
                        saved = stage / "previous-tools" / relative
                        saved.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(origin, saved)
                # Shipped 1.0.0/1.0.1 publishers cannot stage a release record.
                # New bundles publish themselves so future file lists remain usable.
                legacy = stable_version(selected_version) < (1, 0, 2)
                publisher = Path(__file__).parent / "install.sh" if legacy else source / "install.sh"
                command = [*self._authority(), "sh", str(publisher), "--prefix", str(self.prefix),
                           "--es-cfg", str(self.es_cfg), "--app-lock-held"]
                if legacy:
                    command += ["--payload", str(source)]
                if info:
                    command += ["--release-record", str(stage / "selected-release.json")]
                if self.themes:
                    command += ["--themes", str(self.themes)]
                # Publication and rollback are deliberately noncancellable.
                try:
                    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               text=True, start_new_session=True)
                    try:
                        stdout, stderr = process.communicate(timeout=120)
                    except BaseException:
                        # Let sudo forward TERM to its elevated child before escalation.
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            process.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.communicate()
                        raise
                    if process.returncode:
                        raise RuntimeError(stdout + stderr)
                except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
                    try:
                        if self.development or os.geteuid() == 0:
                            restore_app(app, stage)
                        else:
                            subprocess.run([*self._authority(), sys.executable, "-B", str(Path(__file__).resolve()),
                                            "--internal-restore", str(self.prefix), str(stage)], check=True, timeout=60)
                    except Exception as recovery:
                        # TemporaryDirectory must not discard the only surviving backup.
                        recovery_dir = Path(tempfile.mkdtemp(prefix=".tv227-recovery-", dir=tools))
                        os.replace(stage, recovery_dir / "backup")
                        raise RuntimeError("TV installation failed: %s; rollback failed: %s; recovery files: %s"
                                           % (exc, recovery, recovery_dir)) from exc
                    raise RuntimeError("TV installation failed; previous app restored where present.\n" + str(exc)) from exc
                version = info["version"] if info else source_version(source / "tv_installer.py")
                outcome = {"install": "installed", "update": "updated", "repair": "repaired"}[action]
                self.event("complete", "227 TV " + outcome + " " + version, stdout)
                return dict(outcome=outcome, version=version, previous_version=previous_version,
                            latest_checked=not self.pinned and action != "repair", system="227",
                            launcher=str(menu.validate() / "Melkor 227 TV.sh"), saved_data="preserved")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--internal-restore" and os.geteuid() == 0:
        restore_app(safe_path(Path(sys.argv[2]) / "tools/melkor227-tv"), safe_path(sys.argv[3]))
    elif len(sys.argv) == 4 and sys.argv[1] == "--internal-uninstall" and os.geteuid() == 0:
        worker = Installer(sys.argv[2], es_cfg=sys.argv[3])
        worker.validate_card()
        worker._uninstall(safe_path(worker.prefix / "tools/melkor227-tv"))
    else:
        raise SystemExit("Use install227tv.sh or tv_manager.py, not this internal worker")
