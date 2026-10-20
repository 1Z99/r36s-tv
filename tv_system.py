"""Shared 227 EmulationStation menu integration, independent of app installation.

The caller installs the app in prefix/tools/melkor227-tv and retains its Tools
fallback. This module never restarts ES or touches app/user data. Uninstall
deliberately leaves the shared 227 registration, directories and themes behind,
even when TV was the last entry. ``changed`` lists written/deleted paths, not
backup files. Validation is read-only; mutations revalidate under the AI lock.
Failed mutations roll back planned files under that same lock; recovery XML
backups and newly created empty directories may remain.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import uuid
import xml.etree.ElementTree as ET


LAUNCHER = "Melkor 227 TV.sh"
ART = Path("images/melkor227.jpg")
LEGACY_ART = Path("images/melkor227.svg")
ART_SHA256 = "661127b28aadff8826861349b34257ca50cf5f192652006d92f99c35b51fddbd"
LOCK = Path("/tmp/227-menu.lock")


class _XMLRoot(ET.Element):
    pass


class _DocumentBuilder(ET.TreeBuilder):
    """Keep comments/PIs outside the root too; ES files can have header notes."""
    def __init__(self):
        super().__init__(element_factory=_XMLRoot, insert_comments=True, insert_pis=True)
        self.depth = 0
        self.finished = False
        self.outside = ([], [])

    def start(self, tag, attrs):
        self.depth += 1
        return super().start(tag, attrs)

    def end(self, tag):
        node = super().end(tag)
        self.depth -= 1
        if self.depth == 0:
            self.finished = True
        return node

    def comment(self, text):
        if self.depth:
            return super().comment(text)
        self.outside[int(self.finished)].append(ET.Comment(text))

    def pi(self, target, text):
        if self.depth:
            return super().pi(target, text)
        self.outside[int(self.finished)].append(ET.ProcessingInstruction(target, text))

    def doctype(self, name, pubid, system):
        raise ValueError("ES XML must not contain a DTD or custom entities")


def _safe(path: Path, kind: str, required: bool = False) -> None:
    """Reject redirects in every existing component, including dangling links."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Expected an absolute, non-traversing path: {path}")
    for component in (*reversed(path.parents), path):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            if component == path and required:
                raise ValueError(f"Missing {kind}: {path}") from None
            continue
        if stat.S_ISLNK(mode):
            raise ValueError(f"Refusing symlink: {component}")
        expected = stat.S_ISDIR if component != path or kind == "directory" else stat.S_ISREG
        if not expected(mode):
            raise ValueError(f"Unexpected file type: {component}")
        if component == path and kind != "directory" and component.lstat().st_nlink != 1:
            raise ValueError(f"Refusing hardlinked file: {component}")


def _xml(path: Path, root_tag: str) -> ET.Element:
    _safe(path, "file")
    if not path.exists():
        return ET.Element(root_tag)
    builder = _DocumentBuilder()
    root = ET.fromstring(path.read_bytes(), parser=ET.XMLParser(target=builder))
    if root.tag != root_tag:
        raise ValueError(f"Expected {root_tag} root: {path}")
    root.outside = builder.outside
    return root


def _payload(root: ET.Element) -> bytes:
    declaration, body = ET.tostring(root, encoding="utf-8", xml_declaration=True).split(b"\n", 1)
    before, after = getattr(root, "outside", ([], []))
    return b"\n".join([declaration, *(ET.tostring(node, encoding="utf-8") for node in before),
                       body, *(ET.tostring(node, encoding="utf-8") for node in after)]) + b"\n"


def _matches(directory: Path, text: str | None, target: Path) -> bool:
    return bool(text) and os.path.normpath(directory / text) == str(target)


def _entries(root: ET.Element, directory: Path, launcher: Path) -> list:
    entries = [game for game in root.findall("game")
               if any(_matches(directory, path.text, launcher) for path in game.findall("path"))]
    if len(entries) > 1 or any(len(game.findall("path")) != 1 for game in entries):
        raise ValueError(f"Duplicate/ambiguous TV entry: {directory}")
    return entries


def _owned(path: Path, marker: bytes) -> bool:
    _safe(path, "file")
    if not path.exists():
        return False
    if marker not in path.read_bytes():
        raise ValueError(f"Refusing unrelated file at TV path: {path}")
    return True


def artwork_owned(path: Path) -> bool:
    """Accept only the exact shipped JPEG, rejecting unsafe or unrelated files."""
    _safe(path, "file")
    if not path.exists():
        return False
    if hashlib.sha256(path.read_bytes()).hexdigest() != ART_SHA256:
        raise ValueError(f"Refusing unrelated file at TV path: {path}")
    return True


@contextmanager
def _locked():
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    while True:
        try:
            descriptor = os.open(LOCK, flags)
            break
        except FileNotFoundError:
            try:
                descriptor = os.open(LOCK, flags | os.O_CREAT | os.O_EXCL, 0o666)
                os.fchmod(descriptor, 0o666)
                break
            except FileExistsError:
                continue
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode) or os.fstat(descriptor).st_nlink != 1:
            raise ValueError(f"Unsafe menu lock: {LOCK}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _write(path: Path, data: bytes, mode: int, backup: bool = False) -> None:
    """Stage beside the destination; back up changed XML before replacement."""
    _safe(path, "file")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}-", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if backup and path.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            saved = path.with_name(f"{path.name}.bak-{stamp}-{uuid.uuid4().hex}")
            with saved.open("xb") as stream:
                os.fchmod(stream.fileno(), path.stat().st_mode & 0o777)
                stream.write(path.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _restore(path: Path, original: tuple[bytes, int] | None) -> None:
    """Recover independently of _write, which may itself be the failure source."""
    _safe(path, "file")
    if original is None:
        if not path.exists():
            return
        path.unlink()
    else:
        data, mode = original
        if (path.exists() and path.read_bytes() == data
                and stat.S_IMODE(path.stat().st_mode) == mode):
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}-rollback-",
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class SystemMenu:
    def __init__(self, source: Path, prefix: Path,
                 es_cfg: Path = Path("/etc/emulationstation/es_systems.cfg"),
                 themes: Path | None = None):
        self.source = Path(source)
        self.prefix = Path(prefix)
        self.es_cfg = Path(es_cfg)
        self.themes = Path(themes) if themes is not None else None

    def validate(self) -> Path:
        """Return the registered menu directory without creating anything."""
        directory, _, _ = self._prepare(install=True)
        return directory

    def install(self) -> dict:
        with _locked():
            return self._apply(install=True)

    def uninstall(self) -> dict:
        """Remove only TV menu metadata/launcher/art; retain shared ES and data."""
        with _locked():
            return self._apply(install=False)

    def _prepare(self, install: bool):
        _safe(self.prefix, "directory", required=True)
        _safe(self.es_cfg.parent, "directory", required=True)
        systems = _xml(self.es_cfg, "systemList")
        matches = [system for system in systems.findall("system")
                   if any(name.text == "227" for name in system.findall("name"))]
        if len(matches) > 1:
            raise ValueError("Duplicate exact-name 227 systems")
        directory = self.prefix / "227ai"
        if matches:
            system = matches[0]
            paths = system.findall("path")
            if len(system.findall("name")) != 1 or len(paths) != 1 or not paths[0].text:
                raise ValueError("Ambiguous or missing 227 system path/name")
            directory = Path(paths[0].text)
        # The unmodified source launcher uses ../tools. Reject nested paths
        # rather than registering an entry whose launcher cannot locate the app.
        if (not directory.is_absolute() or ".." in directory.parts
                or directory.parent != self.prefix or directory.name in {"tools", "ports"}):
            raise ValueError(f"227 path must be a direct child of the selected prefix: {directory}")
        _safe(directory, "directory")
        _safe(directory / "images", "directory")
        # Validate the app route too, but leave installation and Tools fallback
        # ownership with the caller, including during uninstall.
        _safe(self.prefix / "tools", "directory")
        _safe(self.prefix / "tools/melkor227-tv", "directory")
        _safe(self.prefix / "tools/melkor227-tv/app.py", "file")
        launcher = directory / LAUNCHER
        art = directory / ART
        if self.es_cfg in {launcher, art, directory / LEGACY_ART, directory / "gamelist.xml",
                            self.prefix / "ports" / LAUNCHER, self.prefix / "ports" / ART,
                            self.prefix / "ports" / LEGACY_ART,
                            self.prefix / "ports/gamelist.xml"}:
            raise ValueError("ES configuration overlaps a managed TV menu path")
        launcher_owned = _owned(launcher, b"melkor227-tv")
        art_owned = artwork_owned(art)
        old_art = directory / LEGACY_ART
        _safe(old_art, "file")
        gamelist = directory / "gamelist.xml"
        games = _xml(gamelist, "gameList")
        entries = _entries(games, directory, launcher)
        if entries and not launcher_owned and entries[0].findtext("name") not in {
                "227 TV", "Melkor227TV", "Melkor 227 TV"}:
            raise ValueError(f"Refusing unrelated entry at TV path: {gamelist}")
        writes = {}
        removes = []

        def xml_change(path, root):
            writes[path] = (_payload(root), 0o644, True)

        if install:
            origin = self.source / LAUNCHER
            image = self.source / "assets/melkor227.jpg"
            _safe(origin, "file", required=True)
            _safe(image, "file", required=True)
            if not _owned(origin, b"melkor227-tv") or not artwork_owned(image):
                raise ValueError("Invalid TV source assets")
            writes[launcher] = (origin.read_bytes(), 0o755, False)
            writes[art] = (image.read_bytes(), 0o644, False)
            if not matches:
                system = ET.SubElement(systems, "system")
                for tag, text in {"name": "227", "fullname": "227", "path": str(directory),
                                  "extension": ".sh .SH", "command": "%ROM%",
                                  "platform": "227", "theme": "227"}.items():
                    ET.SubElement(system, tag).text = text
                xml_change(self.es_cfg, systems)
            game = entries[0] if entries else ET.SubElement(games, "game")
            dirty = not entries
            for tag, text in {
                "path": "./" + LAUNCHER, "name": "227 TV",
                "desc": "Internet TV atlas: globe, search, favorites and experimental four-view matrix.",
                "image": "./" + ART.as_posix(),
            }.items():
                elements = game.findall(tag)
                if len(elements) > 1:
                    raise ValueError(f"Ambiguous TV metadata: {tag}")
                element = elements[0] if elements else ET.SubElement(game, tag)
                if element.text != text:
                    element.text = text
                    dirty = True
            if dirty:
                xml_change(gamelist, games)
            self._theme_writes(writes)
        else:
            for game in entries:
                games.remove(game)
            if entries:
                xml_change(gamelist, games)
            if launcher_owned:
                removes.append(launcher)
            if art_owned and not any(_matches(directory, node.text, art)
                                      for node in games.iter() if node.tag in {"image", "thumbnail", "marquee"}):
                removes.append(art)

        if (old_art.exists() and b"<title>Melkor 227 TV</title>" in old_art.read_bytes()
                and not any(_matches(directory, node.text, old_art) for node in games.iter()
                            if node.tag in {"image", "thumbnail", "marquee"})):
            removes.append(old_art)

        # Migrate only the historical exact Ports launcher, never name matches.
        ports = self.prefix / "ports"
        _safe(ports, "directory")
        legacy = ports / LAUNCHER
        legacy_owned = _owned(legacy, b"melkor227-tv")
        legacy_list = ports / "gamelist.xml"
        legacy_games = _xml(legacy_list, "gameList")
        legacy_entries = _entries(legacy_games, ports, legacy)
        for relative in (LEGACY_ART, ART):
            _safe(ports / relative, "file")
        if legacy_owned:
            used_art = {ports / relative for relative in (LEGACY_ART, ART)
                        if any(_matches(ports, node.text, ports / relative)
                               for game in legacy_entries for node in game.iter()
                               if node.tag in {"image", "thumbnail", "marquee"})}
            for game in legacy_entries:
                legacy_games.remove(game)
            if legacy_entries:
                xml_change(legacy_list, legacy_games)
            removes.append(legacy)
            for legacy_art in sorted(used_art):
                shared = any(_matches(ports, node.text, legacy_art) for node in legacy_games.iter()
                             if node.tag in {"image", "thumbnail", "marquee"})
                if not shared and legacy_art.exists():
                    data = legacy_art.read_bytes()
                    owned = (b"<title>Melkor 227 TV</title>" in data if legacy_art == ports / LEGACY_ART
                             else hashlib.sha256(data).hexdigest() == ART_SHA256)
                    if owned:
                        removes.append(legacy_art)
        return directory, writes, removes

    def _theme_writes(self, writes):
        if self.themes is None:
            return
        _safe(self.themes, "directory", required=True)
        for theme in sorted(self.themes.iterdir()):
            if theme.is_symlink():
                continue
            _safe(theme, "directory" if theme.is_dir() else "file")
            if not theme.is_dir():
                continue
            target = theme / "227/theme.xml"
            _safe(target, "file")
            if target.exists():
                continue
            _safe(theme / "theme.xml", "file")
            if not (theme / "theme.xml").exists():
                continue
            parent = _xml(theme / "theme.xml", "theme")
            version = (parent.findtext("formatVersion") or "7").strip()
            if not version.isdigit():
                raise ValueError(f"Invalid theme formatVersion: {theme}")
            root = ET.Element("theme")
            ET.SubElement(root, "formatVersion").text = version
            ET.SubElement(root, "include").text = "../theme.xml"
            writes[target] = (_payload(root), 0o644, True)

    def _apply(self, install: bool) -> dict:
        directory, writes, removes = self._prepare(install)
        originals = {}
        for path in (*writes, *removes):
            _safe(path, "file")
            originals[path] = ((path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                               if path.exists() else None)
        changed = []
        try:
            for path, (data, mode, backup) in writes.items():
                _safe(path, "file")
                exists = path.exists()
                if exists and path.read_bytes() == data:
                    if not backup and path.stat().st_mode & 0o777 != mode:
                        path.chmod(mode)
                        changed.append(str(path))
                    continue
                mode = path.stat().st_mode & 0o777 if exists and backup else mode
                _write(path, data, mode, backup)
                changed.append(str(path))
            for path in removes:
                _safe(path, "file", required=True)
                path.unlink()
                changed.append(str(path))
        except Exception as error:
            failures = []
            for path, original in reversed(originals.items()):
                try:
                    _restore(path, original)
                except Exception as recovery_error:
                    failures.append(f"{path}: {type(recovery_error).__name__}: {recovery_error}")
            if failures:
                raise error from RuntimeError("Menu rollback incomplete: " + "; ".join(failures))
            raise
        return {"system_dir": str(directory), "launcher": str(directory / LAUNCHER), "changed": changed}
