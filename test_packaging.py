"""Local packaging tests; no SSH connections or native runtime builds are made."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import xml.etree.ElementTree as ET


SOURCE = Path(__file__).resolve().parent


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tv227-packaging-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        shutil.copyfile(SOURCE / "install.sh", self.source / "install.sh")

    def invoke(self, *arguments):
        return subprocess.run(["sh", str(self.source / "install.sh"), "--es-cfg", str(self.root / "es_systems.cfg"), *arguments],
                              text=True, capture_output=True, timeout=30)

    def runtime(self, custom=False):
        root = self.source / "runtime"
        (root / "lib").mkdir(parents=True)
        (root / "lib/libmpv.so.2.5.0").write_bytes(b"packaging fixture, not a library")
        (root / "lib/libmpv.so.2").symlink_to("libmpv.so.2.5.0")
        licenses = root / "licenses/libmpv2"
        licenses.mkdir(parents=True)
        (licenses / "copyright").write_text("fixture copyright\n")
        manifest = {"packages": [{"package": "libmpv2", "source": "mpv",
                                  "version": "0.40.0-3+deb13u1+tv227sw1"}], "files": {}}
        if custom:
            sources = root / "sources"
            sources.mkdir()
            for name in ("mpv_0.40.0.orig.tar.gz", "mpv_0.40.0-3+deb13u1.debian.tar.xz",
                         "mpv_0.40.0-3+deb13u1.dsc", "build_runtime.py"):
                (sources / name).write_bytes(b"source fixture")
            manifest["build"] = {"options": ["-Dauto_features=disabled", "-Dlibmpv=true"]}
        for path in root.rglob("*"):
            if path.is_symlink():
                entry = {"symlink": os.readlink(path)}
            elif path.is_file():
                entry = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            else:
                continue
            manifest["files"][path.relative_to(root).as_posix()] = entry
        (root / "manifest.json").write_text(json.dumps(manifest))
        return root, manifest

    def test_absent_runtime_list_is_zero_bytes(self):
        result = self.invoke("--list-runtime")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.source / "runtime").exists())

    def test_existing_runtime_needs_valid_manifest(self):
        runtime = self.source / "runtime"
        runtime.mkdir()
        self.assertNotEqual(self.invoke("--list-runtime").returncode, 0)
        (runtime / "manifest.json").write_text("{malformed")
        self.assertNotEqual(self.invoke("--list-runtime").returncode, 0)

    def test_custom_build_source_and_options(self):
        root, manifest = self.runtime(custom=True)
        result = self.invoke("--list-runtime")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("runtime/sources/build_runtime.py", result.stdout)
        del manifest["files"]["sources/mpv_0.40.0.orig.tar.gz"]
        (root / "manifest.json").write_text(json.dumps(manifest))
        self.assertNotEqual(self.invoke("--list-runtime").returncode, 0)

    def test_versions_required(self):
        root, manifest = self.runtime()
        del manifest["packages"][0]["version"]
        (root / "manifest.json").write_text(json.dumps(manifest))
        self.assertNotEqual(self.invoke("--list-runtime").returncode, 0)

    def test_checksum_and_symlink_validation(self):
        root, manifest = self.runtime()
        binary = root / "lib/libmpv.so.2.5.0"
        original = binary.read_bytes()
        binary.write_bytes(b"changed")
        self.assertNotEqual(self.invoke("--list-runtime").returncode, 0)
        binary.write_bytes(original)
        link = root / "lib/libmpv.so.2"
        link.unlink()
        link.symlink_to("/usr/lib/libmpv.so.2")
        manifest["files"]["lib/libmpv.so.2"] = {"symlink": "/usr/lib/libmpv.so.2"}
        (root / "manifest.json").write_text(json.dumps(manifest))
        self.assertNotEqual(self.invoke("--list-runtime").returncode, 0)

    def test_extra_and_graphics_libraries_rejected(self):
        root, manifest = self.runtime()
        extra = root / "lib/libEGL.so.1"
        extra.write_bytes(b"never bundle graphics")
        self.assertNotEqual(self.invoke("--list-runtime").returncode, 0)
        manifest["files"]["lib/libEGL.so.1"] = {"sha256": hashlib.sha256(extra.read_bytes()).hexdigest()}
        (root / "manifest.json").write_text(json.dumps(manifest))
        self.assertNotEqual(self.invoke("--list-runtime").returncode, 0)

    def test_traversal_rejected(self):
        root, manifest = self.runtime()
        manifest["files"]["../escape"] = {"sha256": "0" * 64}
        (root / "manifest.json").write_text(json.dumps(manifest))
        self.assertNotEqual(self.invoke("--list-runtime").returncode, 0)

    def test_deploy_archive_without_runtime_uses_mock_transport(self):
        # Real tar/allowlist logic; fake SSH/scp never contact any device.
        for path in SOURCE.iterdir():
            if path.is_file():
                shutil.copyfile(path, self.source / path.name)
        shutil.copytree(SOURCE / "assets", self.source / "assets")
        shutil.copytree(SOURCE / "native", self.source / "native")
        mock_bin = self.root / "bin"
        mock_bin.mkdir()
        header = "#!" + sys.executable + "\n"
        (mock_bin / "ssh").write_text(header +
            'import json, os, sys\n'
            'with open(os.environ["MOCK_LOG"], "a") as log: log.write(json.dumps(sys.argv[1:])+"\\n")\n'
            'if "mktemp -d" in sys.argv[-1]: print("/tmp/melkor227-tv.TEST1234")\n')
        (mock_bin / "scp").write_text(header +
            'import os, shutil, sys\n'
            'shutil.copyfile(sys.argv[-2], os.environ["MOCK_ARCHIVE"])\n')
        for path in mock_bin.iterdir():
            path.chmod(0o755)
        key = self.root / "fake-key"
        key.touch()
        archive = self.root / "upload.tar.gz"
        log = self.root / "commands.jsonl"
        env = os.environ | {"PATH": str(mock_bin) + ":" + os.environ["PATH"],
                            "TV227_SSH_KEY": str(key), "MOCK_ARCHIVE": str(archive),
                            "MOCK_LOG": str(log)}
        result = subprocess.run(["bash", str(self.source / "deploy.sh")], env=env,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(archive) as bundle:
            names = bundle.getnames()
        self.assertIn("app.py", names)
        for name in ("tv_style.py", "tv_quality.py", "tv_globe.py", "assets/logo.rgba",
                     "tv_filter.py", "globe_filter.c", "build_globe_filter.sh", "native/libtvglobe.so",
                     "assets/intro-frames.zip", "assets/globe-atlas.z", "assets/AI-LICENSE"):
            self.assertIn(name, names)
        self.assertEqual([name for name in names if name.startswith("native/")],
                         ["native/libtvglobe.so"])
        self.assertNotIn("", names)
        self.assertFalse(any(name.startswith("runtime/") for name in names))
        commands = [json.loads(line)[-1] for line in log.read_text().splitlines()]
        install = next(command for command in commands if "sudo -n" in command)
        self.assertLess(install.index("--check-only"), install.index("sudo -n"))
        self.runtime(custom=True)
        result = subprocess.run(["bash", str(self.source / "deploy.sh")], env=env,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(archive) as bundle:
            link = bundle.getmember("runtime/lib/libmpv.so.2")
            self.assertTrue(link.issym())
            self.assertEqual(link.linkname, "libmpv.so.2.5.0")
            self.assertIn("runtime/licenses/libmpv2/copyright", bundle.getnames())
            self.assertIn("runtime/sources/mpv_0.40.0.orig.tar.gz", bundle.getnames())
            self.assertIn("runtime/sources/build_runtime.py", bundle.getnames())

    def test_real_host_preflight_and_staging_install(self):
        # Copy actual source/assets/native, never a fake app or mocked libmpv.
        for path in SOURCE.iterdir():
            if path.is_file():
                shutil.copyfile(path, self.source / path.name)
        shutil.copytree(SOURCE / "assets", self.source / "assets")
        shutil.copytree(SOURCE / "native", self.source / "native")
        result = self.invoke("--check-only")
        if result.returncode:
            self.skipTest("Host runtime unavailable: " + result.stderr)
        self.assertIn("software renderer available", result.stdout)
        card = self.root / "card with spaces"
        card.mkdir()
        for attempt in range(2):
            result = self.invoke("--prefix", str(card))
            self.assertEqual(result.returncode, 0, result.stderr)
            gamelist = card / "227ai/gamelist.xml"
            if attempt == 0:
                original = gamelist.read_bytes()
            else:
                self.assertEqual(gamelist.read_bytes(), original)
        app = card / "tools/melkor227-tv"
        self.assertEqual((app / "app.py").read_bytes(), (SOURCE / "app.py").read_bytes())
        for name in ("tv_style.py", "tv_quality.py", "tv_globe.py", "assets/intro-frames.zip",
                     "tv_filter.py", "globe_filter.c", "build_globe_filter.sh", "native/libtvglobe.so",
                     "assets/logo.rgba", "assets/DejaVuSansMono-Bold.ttf", "assets/globe-atlas.z"):
            self.assertEqual((app / name).read_bytes(), (SOURCE / name).read_bytes())
        self.assertEqual([path.name for path in (app / "native").iterdir()], ["libtvglobe.so"])
        self.assertEqual((app / "build_globe_filter.sh").stat().st_mode & 0o777, 0o755)
        self.assertFalse((app / "runtime").exists())
        self.assertEqual(len(ET.parse(gamelist).getroot().findall("game")), 1)
        self.assertTrue((card / "tools/Melkor 227 TV.sh").stat().st_mode & 0o111)
        self.assertFalse((card / "ports").exists())
        self.assertFalse(list((card / "227ai").glob("gamelist.xml.bak-*")))
        library = app / "native/libtvglobe.so"
        original = library.read_bytes()
        with library.open("rb") as mapped:
            (self.source / "native/libtvglobe.so").write_bytes(original + b"\0atomic-update")
            result = self.invoke("--prefix", str(card))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(mapped.read(), original)
        self.assertEqual(library.read_bytes(), original + b"\0atomic-update")


if __name__ == "__main__":
    unittest.main()
