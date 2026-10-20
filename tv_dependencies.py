"""Capability-first dependency setup for the bundled Debian 13 arm64 runtime.

Importing does not probe. check() is read-only on any host. ensure() returns the
same {ok, missing, fatal} dictionary plus installed (the completed apt plan).
Expected setup errors, including cancellation, are returned in fatal, not raised.
source is the application directory containing tv_media.py and runtime/lib.
emit receives progress dictionaries; cancel is a threading.Event-like object.
No SDL initialization, display, audio device, or network probe is performed.
"""

# MIT License. Copyright (c) 2026 1Z99.
# Apt auditing/progress adapted from 227 AI installer/installer_deps.py.
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE. See also assets/AI-LICENSE.

from collections import deque
import ctypes
import json
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import threading
import time


SDL = {"libSDL2-2.0.so.0": "libsdl2-2.0-0",
       "libSDL2_ttf-2.0.so.0": "libsdl2-ttf-2.0-0"}
DIRECT = {
    "libass.so.9": "libass9",
    "libavcodec.so.61": "libavcodec61",
    "libavfilter.so.10": "libavfilter10",
    "libavformat.so.61": "libavformat61",
    "libavutil.so.59": "libavutil59",
    "libswresample.so.5": "libswresample5",
    "libswscale.so.8": "libswscale8",
    "libplacebo.so.349": "libplacebo349",
    "liblua5.2.so.0": "liblua5.2-0",
    "libasound.so.2": "libasound2t64",
}
# Explicit trixie mappings only. Apt supplies the rest of each new package's
# closure. Unknown SONAMEs need human review, never a guessed package name.
TRANSITIVE = {
    "libaom.so.3": "libaom3", "libdav1d.so.7": "libdav1d7",
    "libvpx.so.9": "libvpx9", "libx264.so.164": "libx264-164",
    "libx265.so.215": "libx265-215", "libopus.so.0": "libopus0",
    "libmp3lame.so.0": "libmp3lame0", "libopenjp2.so.7": "libopenjp2-7",
    "libvorbis.so.0": "libvorbis0a", "libvorbisenc.so.2": "libvorbisenc2",
    "libogg.so.0": "libogg0", "libspeex.so.1": "libspeex1",
    "libsoxr.so.0": "libsoxr0", "libzimg.so.2": "libzimg2",
    "libpostproc.so.58": "libpostproc58",
    "libfribidi.so.0": "libfribidi0", "libharfbuzz.so.0": "libharfbuzz0b",
    "libunibreak.so.6": "libunibreak6", "libfontconfig.so.1": "libfontconfig1",
    "libfreetype.so.6": "libfreetype6", "libwebp.so.7": "libwebp7",
    "libwebpmux.so.3": "libwebpmux3", "libwebpdemux.so.2": "libwebpdemux2",
    "liblzma.so.5": "liblzma5", "libbz2.so.1.0": "libbz2-1.0",
    "libz.so.1": "zlib1g", "libgnutls.so.30": "libgnutls30t64",
    "libssh.so.4": "libssh-4", "libsrt-gnutls.so.1.5": "libsrt1.5-gnutls",
    "librubberband.so.2": "librubberband2", "libmysofa.so.1": "libmysofa1",
}
PROTECTED = ("mesa", "libegl", "libgles", "libgl", "libgbm", "libdrm",
             "libmali", "firmware", "kernel", "linux-", "systemd", "libsystemd",
             "libc6", "udev", "libudev", "xserver", "libmpv")
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C",
       "DEBIAN_FRONTEND": "noninteractive"}
OPTIONS = ["-o", "Acquire::http::Timeout=20", "-o", "Acquire::https::Timeout=20",
           "-o", "Acquire::Retries=1", "-o", "DPkg::Lock::Timeout=30"]
INSTALL = ["--no-upgrade", "--no-remove", "--no-install-recommends", "install"]
GUIDANCE = ("Automatic installation requires ID=debian, VERSION_ID=13 (trixie), "
            "aarch64 and dpkg arm64. The private runtime targets that ABI, not "
            "every R36S firmware. Use matching Debian 13 arm64 firmware or rebuild "
            "the private runtime for your OS; do not replace firmware graphics/libc.")
MEDIA_PROBE = r'''
import ctypes, json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
# Fail before tv_media's system-libmpv fallback can hide a broken private build.
private = ctypes.CDLL(str(root / "runtime/lib/libmpv.so.2"))
import tv_media
tv_media._library()
# Both handles must resolve the same library, not a system libmpv fallback.
if ctypes.cast(tv_media._library().mpv_create, ctypes.c_void_p).value != ctypes.cast(private.mpv_create, ctypes.c_void_p).value:
    raise RuntimeError("tv_media selected a different libmpv, refusing system fallback")
ok, reason = tv_media.availability()
print(json.dumps({"ok": ok, "reason": reason}), flush=True)
sys.exit(0 if ok else 1)
'''


def _absent(name, error=None, private=None):
    if error is not None and not str(error).startswith(
            name + ": cannot open shared object file: No such file"):
        return False
    directories = ["/lib", "/usr/lib", "/lib64", "/usr/lib64", "/usr/local/lib",
                   "/lib/aarch64-linux-gnu", "/usr/lib/aarch64-linux-gnu",
                   "/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu"]
    directories += os.environ.get("LD_LIBRARY_PATH", "").split(":")
    if private is not None:
        directories.insert(0, str(private))
    try:
        # Firmware can add loader directories outside the Debian multiarch paths.
        # An unreadable configuration is not evidence that a library is absent.
        pending, seen = [Path("/etc/ld.so.conf")], set()
        while pending:
            config = pending.pop().resolve()
            if config in seen or not config.exists():
                continue
            seen.add(config)
            for line in config.read_text(encoding="utf-8").splitlines():
                fields = shlex.split(line, comments=True)
                if fields and fields[0] == "include":
                    for pattern in fields[1:]:
                        path = config.parent / pattern
                        pending.extend(path.parent.glob(path.name))
                else:
                    directories.extend(fields)
        return not any((Path(directory or ".") / name).is_symlink() or
                       (Path(directory or ".") / name).exists() for directory in directories)
    except (OSError, ValueError, RuntimeError):
        return False


def _probe(command, env):
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, errors="replace", timeout=10, env=env)
    if result.returncode:
        raise RuntimeError("Probe exited with status {} (negative means signal): {}\n{}".format(
            result.returncode, command[0], (result.stdout + result.stderr)[-4096:]))
    return result


def check(source: Path | None = None, manager_only=False) -> dict:
    """Probe actual capabilities, without apt, privilege, or platform queries."""
    missing, fatal = [], {}
    for name, package in SDL.items():
        try:
            library = ctypes.CDLL(name)
            if name == "libSDL2-2.0.so.0":
                getattr(library, "SDL_RenderGeometryRaw")
        except (OSError, AttributeError) as error:
            if isinstance(error, OSError) and _absent(name, error):
                missing.append(package)
            else:
                fatal[name] = "Existing SDL capability is broken; will not replace it: " + str(error)
    for executable, package in (("curl", "curl"), ("flock", "util-linux")):
        if not shutil.which(executable):
            missing.append(package)
    try:
        paths = ssl.get_default_verify_paths()
        ca = Path(os.environ.get("SSL_CERT_FILE", paths.cafile or paths.openssl_cafile))
        if not ca.exists() and not ca.is_symlink():
            missing.append("ca-certificates")
        else:
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cafile=str(ca))
    except (OSError, ValueError, TypeError) as error:
        fatal["ca-certificates"] = "Existing CA file is unusable; will not replace it: " + str(error)
    if manager_only:
        return dict(ok=not missing and not fatal, missing=list(dict.fromkeys(missing)), fatal=fatal)
    try:
        root = (Path(source) if source is not None else Path(__file__).parent).resolve()
        private = root / "runtime/lib"
        binary = private / "libmpv.so.2"
        if not binary.is_file() or not (root / "tv_media.py").is_file():
            raise RuntimeError("Missing bundled runtime/lib/libmpv.so.2 or tv_media.py; "
                               "restore the application bundle, not system libmpv.")
        env = dict(os.environ, LD_LIBRARY_PATH=str(private) + (
            ":" + os.environ["LD_LIBRARY_PATH"] if os.environ.get("LD_LIBRARY_PATH") else ""),
            LC_ALL="C", PYTHONDONTWRITEBYTECODE="1", TV_MEDIA_AO="null")
        # Missing providers produce spurious undefined symbols under ldd -r.
        # Audit the closure first, then relocations only when all providers exist.
        result = _probe(["ldd", str(binary)], env)
        closure = result.stdout + result.stderr
        absent = []
        for line in closure.splitlines():
            match = re.fullmatch(r"\s*(\S+) => not found\s*", line)
            if match:
                absent.append(match[1])
            elif "not found" in line or "undefined symbol" in line:
                raise RuntimeError("Existing runtime ABI is broken: " + line)
        for name in dict.fromkeys(absent):
            package = DIRECT.get(name) or TRANSITIVE.get(name)
            if not _absent(name, private=private):
                fatal[name] = "Existing library or symlink cannot load; will not replace it: " + name
            elif package is None:
                fatal[name] = ("Missing base firmware or unmapped runtime dependency: " + name +
                               ". Restore matching firmware or review its Debian 13 package; "
                               "no SONAME guessing or graphics/libc replacement is allowed.")
            else:
                missing.append(package)
        if not absent:
            result = _probe(["ldd", "-r", str(binary)], env)
            relocations = result.stdout + result.stderr
            if "not found" in relocations or "undefined symbol" in relocations:
                raise RuntimeError("Existing runtime has unresolved relocations; will not replace libraries:\n" +
                                   relocations[-4096:])
            result = _probe([sys.executable, "-B", "-c", MEDIA_PROBE, str(root)], env)
            ready = json.loads(result.stdout.strip().splitlines()[-1])
            if not isinstance(ready, dict) or ready.get("ok") is not True:
                raise RuntimeError("Private libmpv software RGBA probe failed: " + str(ready))
    except (OSError, RuntimeError, ValueError, IndexError, subprocess.TimeoutExpired) as error:
        fatal["libmpv"] = str(error) + "\n" + GUIDANCE
    return dict(ok=not missing and not fatal, missing=list(dict.fromkeys(missing)), fatal=fatal)


def _run(command):
    try:
        return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors="replace",
                              timeout=120, env=ENV)
    except (OSError, subprocess.TimeoutExpired) as error:
        output = getattr(error, "stdout", "") or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        raise RuntimeError("Dependency command failed: {}\n{}".format(error, output[-4096:])) from error


def _authority():
    try:
        release = {}
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key in ("ID", "VERSION_ID"):
                release[key] = " ".join(shlex.split(value))
    except (OSError, ValueError) as error:
        raise RuntimeError(GUIDANCE + " Cannot read os-release: " + str(error)) from error
    if release != {"ID": "debian", "VERSION_ID": "13"} or os.uname().machine != "aarch64":
        raise RuntimeError(GUIDANCE)
    apt, dpkg = shutil.which("apt-get"), shutil.which("dpkg")
    if not apt or not dpkg or not shutil.which("dpkg-query"):
        raise RuntimeError(GUIDANCE + " apt-get, dpkg and dpkg-query are required.")
    architecture = _run([dpkg, "--print-architecture"])
    if architecture.returncode or architecture.stdout.strip() != "arm64":
        raise RuntimeError(GUIDANCE + " dpkg reports: " + architecture.stdout[-2048:])
    if os.geteuid() == 0:
        return [apt]
    sudo = shutil.which("sudo")
    if not sudo or _run([sudo, "-n", "--", "true"]).returncode:
        raise RuntimeError("Root or noninteractive sudo -n is required; no password is requested or stored.")
    return [sudo, "-n", "--", "/usr/bin/env", "DEBIAN_FRONTEND=noninteractive", "LC_ALL=C", apt]


def _existing():
    result = _run([shutil.which("dpkg-query"), "-W", "-f=${binary:Package}\t${db:Status-Status}\n"])
    if result.returncode:
        raise RuntimeError("Cannot audit existing packages: " + result.stdout[-4096:])
    existing = set()
    for line in result.stdout.splitlines():
        name, sep, state = line.partition("\t")
        if not sep or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9-]+)?", name):
            raise RuntimeError("Unrecognized dpkg package state: " + line)
        if state not in ("not-installed", "config-files"):
            existing.add(name.split(":")[0])
    return existing


def _audit(output, missing, existing):
    plan, configured = {}, []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith(("Remv", "Purg")):
            raise RuntimeError("Refusing package removal: " + line)
        if line.startswith("Conf "):
            configured.append(line.split()[1].split(":")[0])
        if not line.startswith("Inst"):
            continue
        match = re.fullmatch(r"Inst ([a-z0-9][a-z0-9+.-]*(?::(?:arm64|all))?) "
                             r"\(([A-Za-z0-9.+:~\-]+)(?: [^\n]*)?\)", line)
        if not match:
            raise RuntimeError("Refusing upgrade or unrecognized apt plan: " + line)
        name, version = match.groups()
        base = name.split(":")[0]
        if any(arch not in ("arm64", "all") for arch in re.findall(r"\[([^\]]+)\]", line)):
            raise RuntimeError("Refusing foreign architecture in apt plan: " + line)
        if base in existing:
            raise RuntimeError("Refusing upgrade/reinstall of existing package: " + name)
        if base.startswith(PROTECTED):
            raise RuntimeError("Refusing protected graphics/base firmware package: " + name +
                               ". Preserve firmware; use a compatible runtime/dependency source instead.")
        if base in SDL.values() and base not in missing:
            raise RuntimeError("Refusing SDL installation without its exact missing SONAME probe: " + name)
        if base in {package.split(":")[0] for package in plan}:
            raise RuntimeError("Duplicate package in apt plan: " + name)
        plan[name] = version
    bases = {name.split(":")[0] for name in plan}
    if any(name not in bases or name in existing for name in configured):
        raise RuntimeError("Refusing configuration of packages outside the new-package plan.")
    if not set(missing).issubset(bases):
        raise RuntimeError("Apt cannot supply missing capabilities without replacing existing packages.")
    return plan


def _install(command, report, cancel):
    """Drain bounded chunks; never terminate apt while it may own dpkg."""
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors="replace",
                               env=ENV, start_new_session=True)
    lines, recent = queue.Queue(maxsize=64), deque(maxlen=20)

    def read():
        try:
            while True:
                line = process.stdout.readline(2048)
                if not line:
                    break
                lines.put(line.rstrip())
        except OSError as error:
            lines.put(str(error))
        finally:
            lines.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    eof = deferred = False
    callback_error = None
    while not eof or process.poll() is None:
        try:
            if cancel is not None and cancel.is_set() and not deferred:
                deferred = True
                report("Finishing system setup safely", "Cancellation takes effect after apt finishes.")
            line = lines.get(timeout=.1)
            if line is None:
                eof = True
            else:
                recent.append(line)
                report("Finishing system setup safely" if deferred else "Installing system dependencies", line)
        except queue.Empty:
            pass
        except Exception as error:
            # A broken UI must not abandon apt or leave its output pipe blocked.
            callback_error = error
    reader.join()
    process.stdout.close()
    code = process.wait()
    if code:
        raise RuntimeError("Apt exited with status {}:\n{}".format(code, "\n".join(recent)))
    return callback_error


def ensure(source: Path | None = None, emit=None, cancel=None, allow_install=True, manager_only=False) -> dict:
    """Optionally install an audited, new-packages-only plan, then reprobe.

    allow_install=False never queries the platform, package database or authority.
    installed lists completed packages, not attempted ones; cancellation during
    apt is deferred through completion and recheck. No automatic repair/reinstall.
    """
    started = time.monotonic()
    status = dict(ok=False, missing=[], fatal={}, installed=[])

    def report(label, detail=""):
        if emit is not None:
            emit(dict(phase="dependencies", label=label, detail=str(detail), done=0,
                      total=None, speed=None, elapsed=time.monotonic() - started))

    def checkpoint():
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Dependency setup cancelled; any running apt was allowed to finish safely.")

    try:
        checkpoint()
        report("Checking system dependencies")
        status = dict(check(source, manager_only=True) if manager_only else check(source), installed=[])
        if not allow_install or status["fatal"] or status["ok"]:
            return status
        checkpoint()
        apt = _authority() + OPTIONS
        existing = _existing()
        missing = status["missing"]
        report("Planning system dependencies", ", ".join(missing))
        checkpoint()
        simulation = _run(apt + ["--simulate"] + INSTALL + missing)
        if simulation.returncode:
            if simulation.returncode < 0:
                raise RuntimeError("Apt simulation died by signal: " + simulation.stdout[-4096:])
            checkpoint()
            report("Refreshing package lists", simulation.stdout[-2048:])
            checkpoint()
            update = _run(apt + ["update"])
            if update.returncode:
                raise RuntimeError("Cannot refresh package lists: " + update.stdout[-4096:])
            checkpoint()
            simulation = _run(apt + ["--simulate"] + INSTALL + missing)
        if simulation.returncode:
            raise RuntimeError("Cannot plan dependencies: " + simulation.stdout[-4096:])
        plan = _audit(simulation.stdout, missing, existing)
        pinned = [name + "=" + version for name, version in plan.items()]
        checkpoint()
        verified = _run(apt + ["--simulate"] + INSTALL + pinned)
        if verified.returncode or _audit(verified.stdout, missing, _existing()) != plan:
            raise RuntimeError("Apt plan changed; refusing installation: " + verified.stdout[-4096:])
        report("Installing system dependencies", ", ".join(pinned))
        checkpoint()
        callback_error = _install(apt + ["--yes"] + INSTALL + pinned, report, cancel)
        status["installed"] = list(plan)
        # Recheck even if the progress UI failed while apt was running.
        status = dict(check(source, manager_only=True) if manager_only else check(source), installed=list(plan))
        if callback_error is not None:
            raise RuntimeError("Progress callback failed after apt finished: " + str(callback_error))
        checkpoint()
        report("System dependencies ready" if status["ok"] else "Dependencies still unavailable")
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        status["ok"] = False
        status["fatal"]["setup"] = str(error)
    return status
