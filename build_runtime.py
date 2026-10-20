#!/usr/bin/env python3
"""Build a private, native arm64 libmpv; never install packages or graphics libs.

Run explicitly on the target-compatible Debian 13 arm64 machine, not through
deploy.sh. All downloads, headers, tools and build products stay in a new work
directory. A successful headless probe is required before publishing --output.
"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote


VERSION = "0.40.0-3+deb13u1"
POOL = "https://deb.debian.org/debian/pool/main/m/mpv/"
SOURCES = ("mpv_0.40.0.orig.tar.gz", "mpv_" + VERSION + ".debian.tar.xz",
           "mpv_" + VERSION + ".dsc")
OPTIONS = ["-Dauto_features=disabled", "-Dlibmpv=true", "-Dcplayer=false",
           "-Dalsa=enabled", "-Dgl=disabled", "-Dlua=lua52",
           "-Dbuildtype=release", "-Dstrip=true", "-Dbuild-date=false"]
DEVELOPMENT = (
    "libavcodec-dev", "libavfilter-dev", "libavformat-dev", "libavutil-dev",
    "libswscale-dev", "libswresample-dev", "libpostproc-dev", "libplacebo-dev",
    "libass-dev", "libasound2-dev", "libvulkan-dev", "liblcms2-dev",
    "libfreetype-dev", "libfribidi-dev", "libharfbuzz-dev", "libfontconfig-dev",
    "libpng-dev", "libbrotli-dev", "zlib1g-dev", "libbz2-dev", "libglib2.0-dev",
    "libgio-2.0-dev", "libgraphite2-dev", "libpcre2-dev", "libexpat1-dev",
    "libunibreak-dev", "libsysprof-capture-4-dev", "liblua5.2-dev", "ninja-build", "libmpv2",
)
FFMPEG_RUNTIME = {"libavcodec-dev": "libavcodec61", "libavfilter-dev": "libavfilter10",
                  "libavformat-dev": "libavformat61", "libavutil-dev": "libavutil59",
                  "libswresample-dev": "libswresample5", "libswscale-dev": "libswscale8",
                  "libpostproc-dev": "libpostproc58"}


def run(*command, cwd=None, env=None):
    result = subprocess.run(command, cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError("Command failed ({}): {}\n{}".format(
            result.returncode, " ".join(map(str, command)), result.stdout))
    return result.stdout


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-parent", type=Path, default=Path("/tmp"))
    parser.add_argument("--reuse-work", type=Path,
                        help="resume an existing private work directory, reusing cached .debs and extraction")
    parser.add_argument("--output", type=Path, required=True,
                        help="new runtime directory; existing paths are never replaced")
    parser.add_argument("--media-probe", type=Path,
                        default=Path(__file__).resolve().with_name("tv_media.py"))
    parser.add_argument("--lock", type=Path, help="reuse packages.lock.json or a runtime manifest")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if platform.machine() != "aarch64" or run("dpkg", "--print-architecture").strip() != "arm64":
        parser.error("native Debian 13 arm64 is required; no cross-build or firmware changes")
    os_release = Path("/etc/os-release").read_text()
    if not re.search(r'^ID="?debian"?$', os_release, re.M) or not re.search(
            r'^VERSION_ID="?13"?$', os_release, re.M):
        parser.error("requires Debian 13 (trixie); do not substitute foreign runtime packages")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    output = args.output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        parser.error("--output must not exist")
    # Verify parents before mkdtemp or any download/extraction.
    print(run("ls", "-ld", str(args.work_parent), str(output.parent)), end="", flush=True)
    if not args.work_parent.is_dir() or not output.parent.is_dir():
        parser.error("work/output parents must already exist")
    probe = args.media_probe.resolve()
    probe_source = probe.read_text()
    for tool in ("apt", "apt-cache", "dpkg-deb", "curl", "tar", "patch", "gcc",
                 "meson", "pkg-config", "ldd", "strip"):
        if not shutil.which(tool):
            parser.error("missing existing build tool: " + tool + "; nothing was installed")
    if args.reuse_work:
        work = args.reuse_work.resolve(strict=True)
        if not work.is_dir() or not work.name.startswith("melkor227-runtime."):
            parser.error("--reuse-work must be an existing private melkor227-runtime.* directory")
    else:
        work = Path(tempfile.mkdtemp(prefix="melkor227-runtime.", dir=args.work_parent)).resolve()
    print("Private work directory: " + str(work), flush=True)
    downloads = work if args.reuse_work and list(work.glob("*.deb")) else work / "downloads"
    downloads.mkdir(exist_ok=True)
    root = work / "build-root"
    root.mkdir(exist_ok=True)
    lock = json.loads(args.lock.read_text()) if args.lock else {}
    if "build" in lock:
        lock = lock["build"]["package_lock"]
    if args.lock and set(lock) != set(DEVELOPMENT):
        raise ValueError("lock file package set does not match this build recipe")
    for package in DEVELOPMENT:
        if not args.lock:
            if package in FFMPEG_RUNTIME:
                lock[package] = run("dpkg-query", "-W", "-f=${Version}",
                                    FFMPEG_RUNTIME[package] + ":arm64").strip()
                continue
            cached = list(downloads.glob(package + "_*.deb"))
            if len(cached) == 1:
                lock[package] = run("dpkg-deb", "-f", str(cached[0]), "Version").strip()
                continue
            policy = run("apt-cache", "policy", package + ":arm64")
            match = re.search(r"^\s*Candidate:\s+(\S+)", policy, re.M)
            if not match or match[1] == "(none)":
                raise RuntimeError("No cached apt candidate for " + package + "; no apt update attempted")
            lock[package] = match[1]
    if lock["libmpv2"] != VERSION:
        raise ValueError("libmpv2 must match the pinned Debian source " + VERSION)
    (work / "packages.lock.json").write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    packages = []
    for package, version in sorted(lock.items()):
        candidates = [path for path in downloads.glob(package + "_*.deb")
                      if run("dpkg-deb", "-f", str(path), "Version").strip() == version]
        if not candidates:
            print("Download/extract only: {}={}".format(package, version), flush=True)
            try:
                run("apt", "download", package + ":arm64=" + version, cwd=downloads)
            except RuntimeError:
                if package not in FFMPEG_RUNTIME:
                    raise
                # Older matching headers may have left the live Debian mirror.
                api = "https://snapshot.debian.org/mr/binary/{}/{}/binfiles".format(
                    package, quote(version, safe=""))
                metadata = json.loads(run("curl", "--silent", "--show-error", "--fail", "--location", api))
                snapshot_hash = next(item["hash"] for item in metadata["result"]
                                     if item["architecture"] == "arm64")
                url = "https://snapshot.debian.org/file/" + snapshot_hash
                deb = downloads / (package + "_" + version.split(":")[-1] + "_arm64.deb")
                print("Matching firmware headers from Debian Snapshot: " + url, flush=True)
                run("curl", "--fail", "--location", "--output", str(deb), url)
                if hashlib.sha1(deb.read_bytes()).hexdigest() != snapshot_hash:
                    raise RuntimeError("Snapshot content hash mismatch: " + package)
                deb.with_suffix(".origin.json").write_text(json.dumps(
                    {"url": url, "snapshot_api": api, "snapshot_sha1": snapshot_hash}))
            candidates = [path for path in downloads.glob(package + "_*.deb")
                          if run("dpkg-deb", "-f", str(path), "Version").strip() == version]
        else:
            print("Reuse cached package: {}={}".format(package, version), flush=True)
        if len(candidates) != 1:
            raise RuntimeError("Ambiguous package download: " + package)
        deb = candidates[0]
        if run("dpkg-deb", "-f", str(deb), "Version").strip() != version:
            raise RuntimeError("Downloaded version mismatch: " + package)
        if package in FFMPEG_RUNTIME or not (root / "usr/share/doc" / package).exists():
            run("dpkg-deb", "-x", str(deb), str(root))
        try:
            package_metadata = run("apt-cache", "show", package + "=" + version)
        except RuntimeError:
            package_metadata = run("dpkg-deb", "-f", str(deb))
        origin = deb.with_suffix(".origin.json")
        packages.append({"package": package, "version": version, "sha256": digest(deb),
                         "package_metadata": package_metadata,
                         "download_source": json.loads(origin.read_text()) if origin.exists()
                         else {"method": "apt download", "selection": package + ":arm64=" + version}})
    for name in SOURCES:
        if not (downloads / name).is_file():
            run("curl", "--fail", "--location", "--proto", "=https", "--output",
                str(downloads / name), POOL + name)
    dsc = (downloads / SOURCES[2]).read_text()
    if "Checksums-Sha256:\n" not in dsc:
        raise ValueError("Debian source descriptor has no SHA256 section")
    checksums = dsc.split("Checksums-Sha256:\n", 1)[1].split("\n\n", 1)[0]
    for name in SOURCES[:2]:
        expected = re.search(r"^ ([0-9a-f]{64}) \d+ " + re.escape(name) + r"$", checksums, re.M)
        if not expected or digest(downloads / name) != expected[1]:
            raise RuntimeError("Debian source checksum mismatch: " + name)
    source_parent = work / "native-source"
    source_parent.mkdir(exist_ok=True)
    source = source_parent / "mpv-0.40.0"
    if not source.exists():
        run("tar", "-xzf", str(downloads / SOURCES[0]), "-C", str(source_parent))
        run("tar", "-xJf", str(downloads / SOURCES[1]), "-C", str(source))
        series = source / "debian/patches/series"
        for name in series.read_text().splitlines():
            if name.strip() and not name.startswith("#"):
                patch = source / "debian/patches" / name.strip()
                run("patch", "-p1", "-i", str(patch), cwd=source)

    # Dev .so links must resolve to installed firmware libraries, never static
    # archives or newer downloaded runtime libraries. No graphics library ships.
    libdir = root / "usr/lib/aarch64-linux-gnu"
    for link in libdir.glob("*.so"):
        if link.is_symlink():
            target = Path(os.readlink(link)).name
            system = Path("/usr/lib/aarch64-linux-gnu") / target
            if not system.is_file():
                soname = re.match(r"(.+\.so\.\d+)", target)
                if soname:
                    system = Path("/usr/lib/aarch64-linux-gnu") / soname[1]
            if system.is_file():
                link.unlink()
                link.symlink_to(system.resolve())
    for archive in root.rglob("*.a"):
        archive.unlink()
    # The downloaded binary package is documentation only, never a link input.
    for library in libdir.glob("libmpv.so*"):
        library.unlink()
    env = os.environ.copy()
    for name in ("LD_LIBRARY_PATH", "LD_PRELOAD", "CFLAGS", "CPPFLAGS", "CXXFLAGS",
                 "LDFLAGS", "LIBRARY_PATH", "CPATH", "C_INCLUDE_PATH", "PKG_CONFIG_PATH"):
        env.pop(name, None)
    env.update(PATH=str(root / "usr/bin") + ":" + os.environ["PATH"],
               CC=shutil.which("gcc"), CCACHE_DISABLE="1", TMPDIR=str(work),
               PKG_CONFIG_SYSROOT_DIR=str(root),
               PKG_CONFIG_LIBDIR=str(libdir / "pkgconfig") + ":" + str(root / "usr/share/pkgconfig"))
    reconfigure = ["--clearcache", "--reconfigure"] if (source / "build/build.ninja").exists() else []
    print(run("meson", "setup", *reconfigure, "build", *OPTIONS, cwd=source, env=env), flush=True)
    # Extracted package mtimes can predate objects, hiding a header downgrade
    # from Ninja. Invalidate objects whenever the firmware header lock changes.
    header_lock = {package: lock[package] for package in FFMPEG_RUNTIME}
    header_stamp = source / "build/firmware-headers.json"
    if not header_stamp.exists() or json.loads(header_stamp.read_text()) != header_lock:
        print(run(str(root / "usr/bin/ninja"), "-C", "build", "-t", "clean",
                  cwd=source, env=env), flush=True)
        header_stamp.write_text(json.dumps(header_lock, sort_keys=True) + "\n")
    print(run(str(root / "usr/bin/ninja"), "-C", "build", "-j", str(args.jobs),
               cwd=source, env=env), flush=True)
    # Meson's private DESTDIR install removes the temporary build RUNPATH.
    installed = work / "install-root"
    installed.mkdir(exist_ok=True)
    print(run("meson", "install", "-C", "build", "--no-rebuild", "--destdir", str(installed),
              cwd=source, env=env), flush=True)
    files = list(installed.rglob("libmpv.so.*.*.*"))
    if len(files) != 1:
        raise RuntimeError("Expected one versioned shared libmpv build")
    runtime = Path(tempfile.mkdtemp(prefix="package-", dir=work)) / "runtime"
    (runtime / "lib").mkdir(parents=True)
    binary = runtime / "lib" / files[0].name
    shutil.copyfile(files[0], binary)
    run("strip", "--strip-unneeded", str(binary))
    # ROM cards can use exFAT, which cannot store Unix symbolic links.
    shutil.copyfile(binary, runtime / "lib/libmpv.so.2")
    probe_env = env | {"LD_LIBRARY_PATH": str(runtime / "lib"), "PYTHONDONTWRITEBYTECODE": "1"}
    relocations = run("ldd", "-r", str(binary), env=probe_env)
    if "not found" in relocations or "undefined symbol" in relocations:
        raise RuntimeError("Unresolved firmware ABI/dependency; do not bundle replacement graphics/libc:\n" + relocations)
    # New Python is required: ctypes.CDLL uses a soname and loader startup paths.
    result = subprocess.run([sys.executable, "-B", "-c",
        'import sys; scope={"__name__":"tv_media"}; '
        'exec(compile(sys.stdin.read(), "tv_media.py", "exec"), scope); '
        'ok, message=scope["availability"](); print(message, flush=True); sys.exit(0 if ok else 1)'],
        input=probe_source, text=True, env=probe_env, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Headless preflight failed (status {}). A signal/ENOSYS may indicate a kernel "
                           "syscall incompatibility; retain this work tree for diagnosis.\n{}\n{}".format(
                               result.returncode, result.stdout, result.stderr))
    licenses = runtime / "licenses/libmpv2"
    shutil.copytree(root / "usr/share/doc/libmpv2", licenses, symlinks=False)
    for license_file in source.glob("LICENSE*"):
        if license_file.is_file():
            shutil.copyfile(license_file, licenses / license_file.name)
    common = runtime / "licenses/common-licenses"
    common.mkdir()
    for name in ("GPL-2", "GPL-3", "LGPL-2", "LGPL-2.1", "LGPL-3", "BSD"):
        shutil.copyfile(Path("/usr/share/common-licenses") / name, common / name)
    sources = runtime / "sources"
    sources.mkdir()
    for name in SOURCES:
        shutil.copyfile(downloads / name, sources / name)
    shutil.copyfile(Path(__file__).resolve(), sources / "build_runtime.py")
    manifest = {
        "format": 1, "architecture": "arm64",
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "packages": [{"package": "libmpv2", "version": VERSION + "+tv227sw1",
                      "source": "mpv", "source_version": VERSION, "origin": POOL + SOURCES[2]}],
        "build": {"options": OPTIONS, "packages": packages, "package_lock": lock,
                  "compiler": run("gcc", "--version"), "meson": run("meson", "--version").strip(),
                  "kernel": platform.release(), "os_release": os_release,
                  "installed_packages": run("dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"),
                  "ldd_relocations": relocations, "preflight": result.stdout.strip(),
                  "source_sha256": {name: digest(downloads / name) for name in SOURCES}},
        "files": {},
    }
    for path in sorted(runtime.rglob("*")):
        if path.is_symlink():
            manifest["files"][path.relative_to(runtime).as_posix()] = {"symlink": os.readlink(path)}
        elif path.is_file():
            manifest["files"][path.relative_to(runtime).as_posix()] = {"sha256": digest(path)}
    (runtime / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    # Publish only after the real probe and after assembling licenses/source.
    shutil.copytree(runtime, output, symlinks=True)
    print("Runtime: {}\nBytes (regular files): {}\nPreflight: {}\nLock: {}".format(
        output, sum(path.stat().st_size for path in output.rglob("*")
                    if path.is_file() and not path.is_symlink()), result.stdout.strip(), work / "packages.lock.json"))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        sys.exit("Runtime build failed; private work directory retained. " + str(exc))
