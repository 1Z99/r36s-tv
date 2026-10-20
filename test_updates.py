"""Latest-release selection and installed-version repair, with no live HTTP/APT."""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from build_release import archive
import tv_installer as core
import tv_release as releases
import tv_system
from tv_manager import ACTIONS, Manager, draw, main

ROOT = Path(__file__).resolve().parent


def metadata(version="1.0.2"):
    return dict(format=1, version=version, target=releases.TARGET, base_url=releases.canonical_base(version),
                bundle=dict(file="melkor227-tv-"+version+"-darkosre-arm64.tar.gz", bytes=100,
                            sha256="a"*64, unpacked_bytes=1024),
                manager=dict(file="melkor227-tv-manager-"+version+".tar.gz", bytes=100, sha256="b"*64))


class ReleasePolicyTests(unittest.TestCase):
    def test_fixed_https_endpoint_tag_scope_and_bounded_http(self):
        def http(command, **options):
            self.assertIn("=https", command)
            self.assertIn("--proto-redir", command)
            self.assertIn("--max-filesize", command)
            self.assertLessEqual(options["timeout"], 35)
            options["stdout"].write(json.dumps(metadata()).encode())
        with patch.object(releases.subprocess, "run", side_effect=http) as fetch:
            self.assertEqual(releases.fetch_release()["version"], "1.0.2")
            self.assertEqual(fetch.call_args.args[0][-1], releases.LATEST_RELEASE)
            releases.fetch_release("1.0.2")
            self.assertEqual(fetch.call_args.args[0][-1], releases.canonical_base("1.0.2")+"/release.json")

    def test_invalid_schema_versions_filenames_sizes_and_base_rejected(self):
        cases = [dict(format=True), dict(format=2), dict(target="other"), dict(version="1.0.2-rc1"),
                 dict(version="01.0.2"), dict(version="v1.0.2"), dict(base_url="https://github.com/evil/r36s-tv/releases/download/v1.0.2"),
                 dict(base_url=releases.canonical_base("1.0.1")), dict(base_url="http://github.com/1Z99/r36s-tv/releases/download/v1.0.2"),
                 dict(base_url="https://github.com@evil.invalid/1Z99/r36s-tv/releases/download/v1.0.2")]
        for key in ("bundle", "manager"):
            for value in (dict(file="../evil"), dict(file="other.tar.gz"), dict(bytes=True), dict(bytes=0),
                          dict(bytes=1024**3), dict(sha256="bad")):
                entry = metadata()[key] | value
                cases.append({key: entry})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                releases.validate_release(metadata() | changes)
        with self.assertRaises(ValueError):
            releases.validate_release(metadata(), "1.0.1")
        self.assertGreater(releases.stable_version("1.0.10"), releases.stable_version("1.0.9"))

    def test_metadata_read_is_bounded_and_malformed_response_rejected(self):
        for data in (b"x"*(releases.METADATA_LIMIT+1), b"{broken", b"[]"):
            with self.subTest(size=len(data)), patch.object(releases.subprocess, "run",
                    side_effect=lambda *args, **kwargs: kwargs["stdout"].write(data)):
                with self.assertRaises(ValueError):
                    releases.fetch_release()

    def test_recover_version_never_executes_installed_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"tv_installer.py"
            path.write_text('raise RuntimeError("must not execute")\nVERSION = "1.0.1"\n')
            self.assertEqual(releases.source_version(path), "1.0.1")
            for value in ('VERSION = get_latest()', 'VERSION = "1.0.2-rc1"', 'VERSION="1.0.0"\nVERSION="1.0.1"'):
                path.write_text(value)
                with self.assertRaises(ValueError):
                    releases.source_version(path)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.card = self.root / "card"
        self.card.mkdir()
        self.app = self.card / "tools/melkor227-tv"
        self.cfg = self.root / "es_systems.cfg"
        self.data = self.root / "saved"
        self.data.mkdir()
        (self.data / "favorites.json").write_text('["keep"]')
        for mock in (patch.dict(os.environ, TV227_DATA=str(self.data)),
                     patch.object(tv_system, "LOCK", self.root / "menu.lock"),
                     patch.object(core, "fetch_release", side_effect=RuntimeError("offline")),
                     patch.object(core, "ensure", side_effect=AssertionError("Unexpected APT"))):
            mock.start()
            self.addCleanup(mock.stop)
        self.releases = {}

    def release(self, version, legacy=False):
        if version in self.releases:
            return self.releases[version]
        contents = {name: (ROOT/name).read_bytes() for name in core.APP_FILES}
        contents.update({"assets/"+name: (ROOT/"assets"/name).read_bytes() for name in core.ASSET_FILES})
        contents["tv_installer.py"] = contents["tv_installer.py"].replace(
            ('VERSION = "'+core.VERSION+'"').encode(), ('VERSION = "'+version+'"').encode())
        if legacy:
            del contents["tv_release.py"]
        manifest = dict(format=1, version=version, target=releases.TARGET,
                        files={name: hashlib.sha256(data).hexdigest() for name, data in contents.items()})
        contents["bundle-manifest.json"] = json.dumps(manifest).encode()
        info = metadata(version)
        cache = self.card / "tools/227-tv-manager/cache"
        cache.mkdir(parents=True, exist_ok=True)
        payload = cache / info["bundle"]["file"]
        archive(payload, contents)
        info["bundle"].update(bytes=payload.stat().st_size, sha256=core.digest(payload),
                              unpacked_bytes=sum(map(len, contents.values()))+1024)
        self.releases[version] = info
        return info

    def worker(self, **kwargs):
        return core.Installer(self.card, development=True, es_cfg=self.cfg, **kwargs)

    def install(self, version="1.0.1", legacy=False):
        info = self.release(version, legacy)
        result = self.worker(release=info, base=releases.canonical_base(version)).run("install")
        self.assertEqual(result["version"], version)
        return info

    def snapshot(self):
        return {str(path.relative_to(self.card)): path.read_bytes() for path in self.card.rglob("*")
                if path.is_file() and "227-tv-manager" not in str(path)}

    def test_same_manager_updates_newer_then_reports_same_without_publication(self):
        self.install()
        latest = self.release("1.0.2")
        with patch.object(core, "fetch_release", return_value=latest) as fetch:
            worker = self.worker(release=metadata("1.0.1"))
            result = worker.run("update")
            self.assertEqual((result["outcome"], result["previous_version"], result["version"]),
                             ("updated", "1.0.1", "1.0.2"))
            self.assertTrue(result["latest_checked"])
            original = self.snapshot()
            with patch.object(worker, "_download", side_effect=AssertionError("No payload for up-to-date")):
                result = worker.run("update")
            self.assertEqual(result["outcome"], "up-to-date")
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(self.snapshot(), original)
        self.assertEqual((self.data/"favorites.json").read_text(), '["keep"]')

    def test_install_checks_latest_at_action_time_too(self):
        latest = self.release("1.0.2")
        with patch.object(core, "fetch_release", return_value=latest) as fetch:
            result = self.worker(release=metadata("1.0.1")).run("install")
        fetch.assert_called_once_with()
        self.assertEqual(result["version"], "1.0.2")

    def test_update_rejects_downgrade_without_changes(self):
        self.install("1.0.2")
        old = self.release("1.0.1")
        before = self.snapshot()
        with patch.object(core, "fetch_release", return_value=old), self.assertRaisesRegex(RuntimeError, "downgrade"):
            self.worker().run("update")
        self.assertEqual(self.snapshot(), before)

    def test_offline_repair_restores_installed_version_not_manager_version(self):
        self.install("1.0.1", legacy=True)
        record = (self.app/"installed-release.json").read_bytes()
        original = (self.app/"app.py").read_bytes()
        (self.app/"app.py").write_text("broken")
        result = self.worker(release=self.release("1.0.2")).run("repair")
        self.assertEqual((result["outcome"], result["version"], result["previous_version"]),
                         ("repaired", "1.0.1", "1.0.1"))
        self.assertFalse(result["latest_checked"])
        self.assertEqual((self.app/"app.py").read_bytes(), original)
        self.assertEqual((self.app/"installed-release.json").read_bytes(), record)

    def test_legacy_repair_recovers_version_and_fetches_only_matching_tag(self):
        info = self.install("1.0.0", legacy=True)
        (self.app/"installed-release.json").unlink()
        with patch.object(core, "fetch_release", return_value=info) as fetch:
            result = self.worker(release=self.release("1.0.2")).run("repair")
        fetch.assert_called_once_with("1.0.0")
        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(json.loads((self.app/"installed-release.json").read_text())["version"], "1.0.0")

    def test_current_same_version_repair_is_labelled_repaired_not_updated(self):
        self.install("1.0.2")
        record = (self.app/"installed-release.json").read_bytes()
        original = (self.app/"app.py").read_bytes()
        (self.app/"app.py").write_text("corrupt")
        result = self.worker().run("repair")
        self.assertEqual((result["outcome"], result["version"]), ("repaired", "1.0.2"))
        self.assertEqual((self.app/"app.py").read_bytes(), original)
        self.assertEqual((self.app/"installed-release.json").read_bytes(), record)

    def test_unidentified_legacy_version_never_guesses_latest(self):
        self.install()
        (self.app/"installed-release.json").unlink()
        (self.app/"tv_installer.py").write_text('VERSION = compute_version()\n')
        before = self.snapshot()
        with patch.object(core, "fetch_release") as fetch, self.assertRaises(ValueError):
            self.worker().run("repair")
        fetch.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_network_failure_update_fails_but_uninstall_remains_available(self):
        self.install()
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "Latest release check failed"):
            self.worker().run("update")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.worker().run("uninstall")["outcome"], "uninstalled")
        self.assertEqual((self.data/"favorites.json").read_text(), '["keep"]')

    def test_failed_publication_restores_installed_record_and_app(self):
        self.install()
        latest = self.release("1.0.2")
        before = self.snapshot()
        process = Mock(returncode=1)
        def failed(**kwargs):
            (self.app/"installed-release.json").write_text(json.dumps(latest))
            (self.app/"app.py").write_text("failed version")
            return "", "injected failure"
        process.communicate.side_effect = failed
        with patch.object(core, "fetch_release", return_value=latest), \
                patch.object(core.subprocess, "Popen", return_value=process), \
                self.assertRaisesRegex(RuntimeError, "previous app restored"):
            self.worker().run("update")
        self.assertEqual(self.snapshot(), before)

    def test_payload_manifest_mismatch_preserves_app_and_record(self):
        self.install()
        info = copy.deepcopy(self.release("1.0.2"))
        before = self.snapshot()
        real = core.verify_tree
        with patch.object(core, "fetch_release", return_value=info), \
                patch.object(core, "verify_tree", side_effect=lambda source: real(source) | {"version": "1.0.1"}), \
                self.assertRaisesRegex(ValueError, "manifest"):
            self.worker().run("update")
        self.assertEqual(self.snapshot(), before)

    def test_pinned_override_stays_offline_and_reports_no_latest_check(self):
        info = self.install()
        result = self.worker(release=info, base=self.root.as_uri()).run("update")
        self.assertEqual(result["outcome"], "up-to-date")
        self.assertFalse(result["latest_checked"])

    def test_explicit_source_update_cannot_downgrade_and_repair_cannot_switch_version(self):
        self.install("1.0.4")
        before = self.snapshot()
        worker = self.worker(source_dir=ROOT)
        with self.assertRaisesRegex(RuntimeError, "downgrade"):
            worker.run("update")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            worker.run("repair")
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_repair_does_not_fetch_payload_or_setup_dependencies(self):
        with patch.object(core.Installer, "_download", side_effect=AssertionError("download")), \
                patch.object(core, "fetch_release", side_effect=AssertionError("HTTP")), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            main(["--cli", "--desktop", "--roms", str(self.card), "--action", "repair", "--dry-run",
                  "--source", str(ROOT), "--es-cfg", str(self.cfg)])
        self.assertTrue(json.loads(output.getvalue())["dry_run"])
        self.assertFalse((self.card/"tools").exists())


class UpdateGuiTests(unittest.TestCase):
    def test_all_four_actions_dispatch_and_navigation_has_five_rows(self):
        worker = Mock()
        worker.return_value.run.return_value = dict(outcome="repaired", version="1.0.1")
        manager = Manager({}, cards=[dict(path="/roms2", name="SD2", available=True)], worker=worker)
        manager.card = 0
        for focus, action in enumerate(("install", "update", "repair", "uninstall"), 1):
            manager.focus = focus
            manager.start()
            manager.thread.join()
            manager.update()
            worker.return_value.run.assert_called_with(action)
        manager.state, manager.focus = "home", 4
        manager.button("down")
        self.assertEqual(manager.focus, 0)
        manager.button("up")
        self.assertEqual(manager.focus, 4)
        self.assertEqual(ACTIONS, ("Install", "Update", "Repair", "Uninstall TV"))

    def test_five_rows_do_not_overlap_status_or_footer(self):
        display = Mock()
        manager = Manager({}, cards=[])
        draw(display, manager)
        rows = [call.args for call in display.rect.call_args_list if call.args[0] == 30 and call.args[2] == 580]
        self.assertEqual(len(rows), 5)
        for first, second in zip(rows, rows[1:]):
            self.assertLess(first[1]+first[3], second[1])
        self.assertLess(rows[-1][1]+rows[-1][3], 404)


if __name__ == "__main__":
    unittest.main()
