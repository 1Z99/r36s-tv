#!/usr/bin/env python3
"""Build pinned local TV downloader/payload assets. Does not publish anything."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

from tv_installer import APP_FILES, ASSET_FILES, VERSION, digest

ROOT = Path(__file__).resolve().parent


def archive(path, contents):
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as bundle:
        for name, data in sorted(contents.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), 0
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            bundle.addfile(info, io.BytesIO(data))


def build(output, base=""):
    output = Path(output).absolute()
    if not output.parent.is_dir():
        raise ValueError("Release parent must already exist")
    output.mkdir(exist_ok=True)
    runtime = subprocess.run(["sh", str(ROOT / "install.sh"), "--list-runtime"],
                             capture_output=True, text=True, check=True).stdout.splitlines()
    if not runtime:
        raise ValueError("The tested ARM64 playback runtime must be bundled")
    names = list(APP_FILES)+["assets/"+name for name in ASSET_FILES]+runtime
    contents = {name: (ROOT / name).read_bytes() for name in names}
    elf = contents["native/libtvglobe.so"]
    if elf[:4] != b"\x7fELF" or int.from_bytes(elf[18:20], "little") != 183:
        raise ValueError("Release must contain the tested aarch64 globe helper")
    manifest = dict(format=1, version=VERSION, target="debian13-arm64-darkosre",
                    files={name: hashlib.sha256(data).hexdigest() for name, data in contents.items()})
    contents["bundle-manifest.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode()+b"\n"
    payload = output / ("melkor227-tv-"+VERSION+"-darkosre-arm64.tar.gz")
    archive(payload, contents)
    release = dict(format=1, version=VERSION, target=manifest["target"], base_url=base,
                   bundle=dict(file=payload.name, sha256=digest(payload), bytes=payload.stat().st_size,
                               unpacked_bytes=sum(len(data) for data in contents.values())+1048576))
    manager_names = [name for name in APP_FILES if name.endswith(".py") or name in {"LICENSE", "CREDITS.md"}]
    manager_assets = ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf", "FONT-LICENSE", "AI-LICENSE",
                      "logo.rgba", "intro-background.rgba")
    manager = {name: contents[name] for name in manager_names}
    manager["Melkor 227 TV.sh"] = contents["Melkor 227 TV.sh"]
    manager["assets/melkor227.svg"] = contents["assets/melkor227.svg"]
    manager.update({"assets/"+name: contents["assets/"+name] for name in manager_assets})
    manager["release.json"] = json.dumps(release, sort_keys=True, indent=2).encode()+b"\n"
    bootstrap = output / ("melkor227-tv-manager-"+VERSION+".tar.gz")
    archive(bootstrap, manager)
    release["manager"] = dict(file=bootstrap.name, sha256=digest(bootstrap), bytes=bootstrap.stat().st_size)
    script = (ROOT / "install227tv.sh").read_text()
    for key, value in (("@MANAGER_FILE@", bootstrap.name), ("@MANAGER_SHA256@", release["manager"]["sha256"]),
                       ("@MANAGER_BYTES@", str(bootstrap.stat().st_size)), ("@RELEASE_BASE@", base)):
        script = script.replace(key, value)
    target = output / "Install 227 TV.sh"
    target.write_text(script)
    target.chmod(0o755)
    for path in (payload, bootstrap):
        (output / (path.name+".sha256")).write_text(digest(path)+"  "+path.name+"\n")
    (output / "release.json").write_text(json.dumps(release, indent=2, sort_keys=True)+"\n")
    for name in ("INSTALLATION.md", "PUBLISHING.md"):
        (output / name).write_bytes((ROOT / name).read_bytes())
    print(json.dumps(release, indent=2))
    print("Generated:", target, "(not published)")
    return release


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="", help="Future HTTPS release directory; blank supports adjacent offline archives")
    args = parser.parse_args()
    if args.base_url and (not args.base_url.startswith("https://") or any(c in args.base_url for c in "\n\r\"'\\")):
        parser.error("Release base must be a plain HTTPS directory URL")
    build(args.output, args.base_url)
