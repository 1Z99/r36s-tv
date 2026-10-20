"""Offline release/download/installer-manager tests; never install OS packages."""

from contextlib import redirect_stderr, redirect_stdout
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import tv_installer as core
import tv_system
from tv_manager import Manager

ROOT = Path(__file__).resolve().parent


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.card, self.source = self.root / "card with spaces", self.root / "source"
        self.card.mkdir()
        self.source.mkdir()
        self.cfg = self.root / "es_systems.cfg"
        self.data = self.root / "saved data"
        environment = patch.dict(os.environ, TV227_DATA=str(self.data))
        environment.start()
        self.addCleanup(environment.stop)
        menu_lock = patch.object(tv_system, "LOCK", self.root / "227-menu.lock")
        menu_lock.start()
        self.addCleanup(menu_lock.stop)

    def worker(self, **options):
        return core.Installer(self.card, es_cfg=self.cfg, development=True, **options)

    def source_tree(self):
        for name in core.APP_FILES:
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        shutil.copytree(ROOT / "assets", self.source / "assets")

    def assert_app_locked(self):
        with (self.data / "app.lock").open("a+b") as stream:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def source_install(self):
        # Execute the source installer's exact Python body with host libraries.
        script = (ROOT / "install.sh").read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        output = io.StringIO()
        with patch.object(sys, "argv", ["-", str(self.source), "--prefix", str(self.card),
                                       "--es-cfg", str(self.cfg)]), redirect_stdout(output), redirect_stderr(output):
            try:
                exec(compile(script, "install.sh", "exec"), {"__name__": "__main__"})
            except SystemExit as exc:
                return exc.code, output.getvalue()
        return 0, output.getvalue()

    def test_safe_archive_rejects_links_traversal_duplicates_and_bombs(self):
        for name, kind, duplicate in (("../escape", tarfile.REGTYPE, False),
                                      ("/absolute", tarfile.REGTYPE, False),
                                      ("link", tarfile.SYMTYPE, False),
                                      ("hard", tarfile.LNKTYPE, False),
                                      ("one", tarfile.REGTYPE, True)):
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=self.root) as folder:
                archive = Path(folder) / "unsafe.tar.gz"
                with tarfile.open(archive, "w:gz") as bundle:
                    member = tarfile.TarInfo(name)
                    member.type, member.size, member.linkname = kind, 1 if kind == tarfile.REGTYPE else 0, "one"
                    bundle.addfile(member, io.BytesIO(b"x") if member.size else None)
                    if duplicate:
                        bundle.addfile(member, io.BytesIO(b"x"))
                with self.assertRaises(ValueError):
                    core.extract_bundle(archive, Path(folder)/"out")
                self.assertFalse((self.root / "escape").exists())

    def test_download_checks_pinned_size_hash_and_never_touches_app_on_failure(self):
        origin = self.root / "release"
        origin.mkdir()
        file = origin / "payload.tar.gz"
        file.write_bytes(b"verified payload")
        info = dict(file=file.name, bytes=file.stat().st_size, sha256=core.digest(file))
        worker = self.worker(base=origin.as_uri())
        cached = worker._download(info, self.root / "cache")
        self.assertEqual(cached.read_bytes(), file.read_bytes())
        file.unlink()
        self.assertEqual(worker._download(info, self.root / "cache"), cached)
        cached.unlink()
        file.write_bytes(b"broken archive!!")
        with self.assertRaises(ValueError):
            worker._download(info, self.root / "cache")
        self.assertFalse((self.card / "tools/melkor227-tv").exists())

    def test_cancelled_copy_is_not_promoted(self):
        origin = self.root / "release"
        origin.mkdir()
        file = origin / "payload.tar.gz"
        file.write_bytes(b"payload")
        event = threading.Event()
        event.set()
        worker = self.worker(base=origin.as_uri(), cancel=event)
        with self.assertRaises(core.Cancelled):
            worker._download(dict(file=file.name, bytes=7, sha256=core.digest(file)), self.root / "cache")
        self.assertFalse((self.root / "cache/payload.tar.gz").exists())

    def test_real_source_install_update_and_uninstall_preserve_ai_and_user_data(self):
        self.source_tree()
        system = self.card / "227ai"
        system.mkdir()
        ai = system / "227 AI.sh"
        ai.write_text("AI launcher remains unchanged\n")
        (system / "gamelist.xml").write_text('<gameList><!--keep--><game><path>./227 AI.sh</path>'
                                            '<name>227 AI</name><playcount>42</playcount></game></gameList>')
        self.cfg.write_text('<systemList><system><name>227</name><path>'+str(system)+'/'
                            '</path><command>%ROM%</command><extension>.sh .SH</extension><theme>227</theme></system></systemList>')
        original_cfg = self.cfg.read_bytes()
        self.data.mkdir()
        favorites = self.data / "favorites.json"
        favorites.write_text('["saved favorite"]')
        worker = self.worker(source_dir=self.source)
        first = worker.run("install")
        self.assertEqual(first["system"], "227")
        self.assertTrue((system / "Melkor 227 TV.sh").is_file())
        self.assertFalse((self.card / "ports").exists())
        self.assertIn("<playcount>42</playcount>", (system / "gamelist.xml").read_text())
        original = (system / "gamelist.xml").read_bytes()
        worker.run("update")
        self.assertEqual((system / "gamelist.xml").read_bytes(), original)
        self.assertEqual(self.cfg.read_bytes(), original_cfg)
        result = worker.run("uninstall")
        self.assertEqual(result["saved_data"], "preserved")
        self.assertFalse((system / "Melkor 227 TV.sh").exists())
        self.assertEqual(ai.read_text(), "AI launcher remains unchanged\n")
        self.assertIn("<playcount>42</playcount>", (system / "gamelist.xml").read_text())
        self.assertEqual(self.cfg.read_bytes(), original_cfg)
        self.assertFalse((self.card / "tools/melkor227-tv").exists())
        self.assertEqual(favorites.read_text(), '["saved favorite"]')

    def test_dependency_failure_never_publishes_launcher(self):
        self.source_tree()
        worker = self.worker(source_dir=self.source)
        worker.development = False
        with patch.object(worker, "validate_card"), patch.object(core, "ensure", return_value={
                "ok": False, "missing": [], "fatal": {"ABI": "mismatch"}}):
            with self.assertRaisesRegex(RuntimeError, "Dependency setup failed"):
                worker.run("install")
        self.assertFalse((self.card / "227ai/Melkor 227 TV.sh").exists())

    def test_failed_update_restores_previous_app(self):
        self.source_tree()
        app = self.card / "tools/melkor227-tv"
        app.mkdir(parents=True)
        (app / "old.txt").write_text("working")
        failed = Mock(returncode=1)
        failed.communicate.return_value = ("", "injected install error")
        with patch.object(core.subprocess, "Popen", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "previous app restored"):
                self.worker(source_dir=self.source).run("update")
        self.assertEqual((app / "old.txt").read_text(), "working")

    def test_install_errors_restore_exact_tree_and_tools_while_locked(self):
        self.source_tree()
        app = self.card / "tools/melkor227-tv"
        for existing in (False, True):
            for failure in ("exit", "timeout", "oserror"):
                with self.subTest(existing=existing, failure=failure):
                    app.mkdir(parents=True, exist_ok=True)
                    if existing:
                        (app / "old.txt").write_text("working")
                    else:
                        app.rmdir()
                    fallback = app.parent / "Melkor 227 TV.sh"
                    fallback.write_text("# melkor227-tv old launcher")
                    art = app.parent / "images/melkor227.svg"
                    art.parent.mkdir(exist_ok=True)
                    art.write_text("<title>Melkor 227 TV</title> old art")
                    process = Mock(returncode=1, pid=12345)

                    def fail(*args, **kwargs):
                        self.assert_app_locked()
                        if app.exists():
                            shutil.rmtree(app)
                        app.mkdir()
                        (app / "failed-version.txt").write_text("must disappear")
                        fallback.write_text("# melkor227-tv failed launcher")
                        art.write_text("<title>Melkor 227 TV</title> failed art")
                        if failure == "timeout":
                            raise subprocess.TimeoutExpired("install", 120)
                        if failure == "oserror":
                            raise OSError("injected IO error")
                        return "", "injected failure"

                    calls = 0

                    def communicate(*args, **kwargs):
                        nonlocal calls
                        calls += 1
                        return fail() if calls == 1 else ("", "")

                    process.communicate.side_effect = communicate
                    restore = core.restore_app

                    def locked_restore(*args):
                        self.assert_app_locked()
                        return restore(*args)

                    with patch.object(core.subprocess, "Popen", return_value=process), \
                            patch.object(core.os, "killpg") as kill, \
                            patch.object(core, "restore_app", side_effect=locked_restore):
                        with self.assertRaisesRegex(RuntimeError, "previous app restored"):
                            self.worker(source_dir=self.source).run("update" if existing else "install")
                    self.assertEqual(app.exists(), existing)
                    self.assertFalse((app / "failed-version.txt").exists())
                    if existing:
                        self.assertEqual((app / "old.txt").read_text(), "working")
                        shutil.rmtree(app)
                    self.assertEqual(fallback.read_text(), "# melkor227-tv old launcher")
                    self.assertEqual(art.read_text(), "<title>Melkor 227 TV</title> old art")
                    self.assertEqual(kill.call_count, failure != "exit")

    def test_spawn_oserror_and_rollback_failure_preserve_recovery_backup(self):
        self.source_tree()
        app = self.card / "tools/melkor227-tv"
        app.mkdir(parents=True)
        (app / "old.txt").write_text("working")
        with patch.object(core.subprocess, "Popen", side_effect=OSError("cannot spawn")):
            with self.assertRaisesRegex(RuntimeError, "previous app restored"):
                self.worker(source_dir=self.source).run("update")
        with patch.object(core.subprocess, "Popen", side_effect=OSError("cannot spawn")), \
                patch.object(core, "restore_app", side_effect=OSError("cannot restore")):
            with self.assertRaisesRegex(RuntimeError, "rollback failed.*recovery files"):
                self.worker(source_dir=self.source).run("update")
        backups = list((self.card / "tools").glob(".tv227-recovery-*/backup/previous-app/old.txt"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "working")

    def test_unprivileged_rollback_uses_exact_restore_worker_under_app_lock(self):
        self.source_tree()
        app = self.card / "tools/melkor227-tv"
        app.mkdir(parents=True)
        (app / "old.txt").write_text("working")
        process = Mock(returncode=1)

        def failed_install(*args, **kwargs):
            (app / "new-only.txt").write_text("remove me")
            return "", "injected failure"

        process.communicate.side_effect = failed_install
        worker = self.worker(source_dir=self.source)
        worker.development = False

        def restore(command, **kwargs):
            self.assert_app_locked()
            self.assertIn("--internal-restore", command)
            self.assertEqual(command[-2], str(self.card))
            core.restore_app(app, Path(command[-1]))
            return subprocess.CompletedProcess(command, 0)

        with patch.object(worker, "validate_card"), patch.object(worker, "_authority", return_value=["sudo", "-n"]), \
                patch.object(core, "ensure", return_value={"ok": True}), \
                patch.object(core.os, "geteuid", return_value=1000), \
                patch.object(core.subprocess, "Popen", return_value=process), \
                patch.object(core.subprocess, "run", side_effect=restore) as recovery:
            with self.assertRaisesRegex(RuntimeError, "previous app restored"):
                worker.run("update")
        recovery.assert_called_once()
        self.assertEqual(list(app.iterdir()), [app / "old.txt"])

    def test_running_app_blocks_every_action_even_in_development(self):
        self.data.mkdir()
        with (self.data / "app.lock").open("wb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for action in ("install", "update", "uninstall"):
                with self.subTest(action=action), self.assertRaisesRegex(RuntimeError, "Close the running TV app"):
                    self.worker(source_dir=self.source).run(action)
        self.assertFalse((self.card / "tools").exists())

    def test_uninstall_holds_lock_and_preserves_saved_data(self):
        self.data.mkdir()
        saved = self.data / "favorites.json"
        saved.write_text('["keep"]')

        def uninstall(*args):
            self.assert_app_locked()
            return {"outcome": "uninstalled"}

        with patch.object(core.Installer, "_uninstall", side_effect=uninstall):
            self.worker().run("uninstall")
        self.assertEqual(saved.read_text(), '["keep"]')
        with core.app_locked():
            self.assert_app_locked()

    def test_destination_redirects_and_unrelated_tools_rejected_before_writes(self):
        self.source_tree()
        original_card = self.card
        directories = {".", "melkor227-tv", "227-tv-manager", "images", "melkor227-tv/assets", "melkor227-tv/native"}
        for relative in (".", "melkor227-tv", "227-tv-manager", "melkor227-tv/assets", "melkor227-tv/native", "melkor227-tv/app.py",
                         "melkor227-tv/assets/logo.rgba", "melkor227-tv/native/libtvglobe.so",
                         "227-tv-manager/install.lock", "images", "Melkor 227 TV.sh", "images/melkor227.svg"):
            for kind in ("symlink", "hardlink"):
                if kind == "hardlink" and relative in directories:
                    continue
                with self.subTest(relative=relative, kind=kind), tempfile.TemporaryDirectory(dir=self.root) as folder:
                    self.card = Path(folder)
                    path = self.card / "tools" / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    outside = self.root / ("outside-dir" if relative in directories else "outside-file")
                    if relative in directories:
                        outside.mkdir(exist_ok=True)
                        sentinel = outside / "sentinel"
                    else:
                        sentinel = outside
                    sentinel.write_text("do not touch")
                    if kind == "symlink":
                        path.symlink_to(outside)
                    else:
                        os.link(outside, path)
                    for action in ("install", "update", "uninstall"):
                        with self.assertRaises(ValueError):
                            self.worker(source_dir=self.source).run(action)
                    status, output = self.source_install()
                    self.assertNotEqual(status, 0, output)
                    self.assertEqual(sentinel.read_text(), "do not touch")
                    if relative in directories:
                        self.assertEqual(list(outside.iterdir()), [sentinel])
                    self.assertFalse(self.cfg.exists())
                    self.assertFalse((self.card / "227ai").exists())
        self.card = original_card
        for relative in ("Melkor 227 TV.sh", "images/melkor227.svg"):
            path = self.card / "tools" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("unrelated user content")
            for action in ("install", "update", "uninstall"):
                with self.assertRaisesRegex(ValueError, "unrelated Tools"):
                    self.worker(source_dir=self.source).run(action)
            status, output = self.source_install()
            self.assertNotEqual(status, 0, output)
            self.assertEqual(path.read_text(), "unrelated user content")
            self.assertFalse(self.cfg.exists())
            path.unlink()

    def test_source_staging_and_menu_failures_leave_live_app_unchanged(self):
        self.source_tree()
        app = self.card / "tools/melkor227-tv"
        app.mkdir(parents=True)
        (app / "old.txt").write_text("working")
        fallback = app.parent / "Melkor 227 TV.sh"
        fallback.write_text("# melkor227-tv old launcher")
        copy = shutil.copyfile

        def fail_copy(origin, target, *args, **kwargs):
            self.assert_app_locked()
            if Path(target).name == "tv_media.py":
                self.assertEqual((app / "old.txt").read_text(), "working")
                raise OSError("injected staging failure")
            return copy(origin, target, *args, **kwargs)

        with patch.object(shutil, "copyfile", side_effect=fail_copy):
            status, output = self.source_install()
        self.assertEqual(status, 1, output)
        self.assertIn("injected staging failure", output)
        self.assertEqual((app / "old.txt").read_text(), "working")

        def fail_menu(*args):
            self.assert_app_locked()
            self.assertTrue((app / "app.py").is_file())
            raise OSError("injected registration failure")

        with patch.object(core.SystemMenu, "install", side_effect=fail_menu):
            status, output = self.source_install()
        self.assertEqual(status, 1, output)
        self.assertIn("injected registration failure", output)
        self.assertEqual(list(app.iterdir()), [app / "old.txt"])
        self.assertEqual(fallback.read_text(), "# melkor227-tv old launcher")
        self.assertFalse((app.parent / "images/melkor227.svg").exists())
        self.assertFalse(self.cfg.exists())

    def test_direct_source_install_rejects_running_app(self):
        self.source_tree()
        self.data.mkdir()
        with (self.data / "app.lock").open("wb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(["sh", str(self.source / "install.sh"), "--prefix", str(self.card),
                                     "--es-cfg", str(self.cfg)], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Close the running TV app", result.stderr)
        self.assertFalse((self.card / "tools").exists())

    def test_source_publication_failure_restores_previous_tree(self):
        self.source_tree()
        app = self.card / "tools/melkor227-tv"
        app.mkdir(parents=True)
        (app / "old.txt").write_text("working")
        replace = os.replace

        def fail_publish(origin, target):
            if Path(origin).name == "app" and Path(target) == app:
                self.assert_app_locked()
                self.assertFalse(app.exists())
                raise OSError("injected publication failure")
            return replace(origin, target)

        with patch.object(os, "replace", side_effect=fail_publish):
            status, output = self.source_install()
        self.assertEqual(status, 1, output)
        self.assertIn("injected publication failure", output)
        self.assertEqual(list(app.iterdir()), [app / "old.txt"])
        self.assertFalse(self.cfg.exists())

    def test_installed_runtime_soname_aliases_survive_updates_but_redirects_do_not(self):
        self.source_tree()
        app = self.card / "tools/melkor227-tv"
        library = app / "runtime/lib/libmpv.so.2.0.0"
        library.parent.mkdir(parents=True)
        library.write_bytes(b"runtime fixture, never loaded")
        alias = library.with_name("libmpv.so.2")
        alias.symlink_to(library.name)
        manifest = app / "runtime/manifest.json"
        manifest.write_text(json.dumps({"files": {
            "lib/" + alias.name: {"symlink": library.name},
            "lib/" + library.name: {"sha256": core.digest(library)}}}))
        for _ in range(2):
            status, output = self.source_install()
            self.assertEqual(status, 0, output)
            self.assertTrue(alias.is_symlink())
        alias.unlink()
        alias.symlink_to(self.root / "outside")
        with self.assertRaises(ValueError):
            core.managed_destinations(self.card)

    def test_redirected_app_lock_is_rejected_without_touching_target(self):
        self.data.mkdir()
        outside = self.root / "outside-lock"
        outside.write_text("keep lock target")
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                path = self.data / "app.lock"
                if kind == "symlink":
                    path.symlink_to(outside)
                else:
                    os.link(outside, path)
                with self.assertRaises(ValueError):
                    self.worker().run("uninstall")
                self.assertEqual(outside.read_text(), "keep lock target")
                self.assertFalse((self.card / "tools").exists())
                path.unlink()

    def test_empty_and_unset_data_override_use_launcher_default(self):
        home = self.root / "home"
        default = home / ".local/share/melkor227-tv/app.lock"
        with patch.object(Path, "home", return_value=home):
            for value in (None, ""):
                with self.subTest(value=value), patch.dict(os.environ):
                    if value is None:
                        os.environ.pop("TV227_DATA", None)
                    else:
                        os.environ["TV227_DATA"] = value
                    with core.app_locked(), default.open("a+b") as probe:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_direct_sudo_uses_invoking_users_lock_and_ownership(self):
        account = Mock(pw_dir=str(self.root / "sudo-user"), pw_uid=1234, pw_gid=5678)
        for override in ("", str(self.data)):
            with self.subTest(override=override), \
                    patch.dict(os.environ, SUDO_UID="1234", TV227_DATA=override), \
                    patch.object(core.os, "geteuid", return_value=0), \
                    patch.object(core.pwd, "getpwuid", return_value=account), \
                    patch.object(core.os, "chown") as chown, \
                    patch.object(core.os, "fchown") as fchown:
                expected = Path(override or account.pw_dir + "/.local/share/melkor227-tv")
                with core.app_locked(), (expected / "app.lock").open("a+b") as probe:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertTrue(chown.called)
                self.assertEqual(fchown.call_args.args[1:], (1234, 5678))

    def test_direct_sudo_normalizes_management_storage_on_success_and_failure(self):
        account = Mock(pw_dir=str(self.root / "sudo-user"), pw_uid=1234, pw_gid=5678)
        manager = self.card / "tools/227-tv-manager"
        cache = manager / "cache"
        cache.mkdir(parents=True)
        old = cache / "old-download.part"
        old.write_bytes(b"preserve resumable download")
        unrelated = manager.parent / "unrelated-tool"
        unrelated.write_text("leave unrelated ownership alone")
        origin = self.root / "release"
        origin.mkdir()
        payload = origin / "payload.tar.gz"
        payload.write_bytes(b"verified download, deliberately not a tar archive")
        release = {"bundle": dict(file=payload.name, bytes=payload.stat().st_size, sha256=core.digest(payload))}

        for failure in (False, True):
            owned = []

            def record(descriptor, uid, gid):
                owned.append((Path(os.readlink("/proc/self/fd/" + str(descriptor))), uid, gid))

            with self.subTest(failure=failure), \
                    patch.dict(os.environ, SUDO_UID="1234", SUDO_GID="9999"), \
                    patch.object(core.os, "geteuid", return_value=0), \
                    patch.object(core.pwd, "getpwuid", return_value=account), \
                    patch.object(core.os, "chown"), patch.object(core.os, "fchown", side_effect=record), \
                    patch.object(core.Installer, "_uninstall", return_value={"outcome": "uninstalled"}):
                worker = self.worker(base=origin.as_uri(), release=release)
                if failure:
                    with self.assertRaises(tarfile.ReadError):
                        worker.run("install")
                else:
                    worker.run("uninstall")
            paths = {path for path, uid, gid in owned}
            self.assertTrue({manager.parent, manager, manager / "install.lock", cache, old}.issubset(paths))
            self.assertNotIn(unrelated, paths)
            self.assertTrue(all((uid, gid) == (1234, 5678) for path, uid, gid in owned))
            self.assertEqual((manager / "install.lock").stat().st_mode & 0o777, 0o600)
            self.assertEqual(old.read_bytes(), b"preserve resumable download")
            if failure:
                self.assertIn(cache / payload.name, paths)

    def test_management_ownership_never_follows_symlinks_or_chowns_hardlinks(self):
        manager = self.card / "tools/227-tv-manager"
        manager.mkdir(parents=True)
        outside = self.root / "outside"
        outside.write_text("do not chown")
        target = manager / "download"
        account = Mock(pw_uid=1234, pw_gid=5678)
        for kind in ("symlink", "hardlink"):
            if kind == "symlink":
                target.symlink_to(outside)
            else:
                os.link(outside, target)
            changed = []

            def record(descriptor, *identity):
                changed.append(Path(os.readlink("/proc/self/fd/" + str(descriptor))))

            with self.subTest(kind=kind), patch.dict(os.environ, SUDO_UID="1234"), \
                    patch.object(core.os, "geteuid", return_value=0), \
                    patch.object(core.pwd, "getpwuid", return_value=account), \
                    patch.object(core.os, "fchown", side_effect=record):
                with self.assertRaises(ValueError):
                    core.normalize_manager_owner(manager.parent)
            self.assertNotIn(target, changed)
            self.assertNotIn(outside, changed)
            self.assertEqual(outside.read_text(), "do not chown")
            target.unlink()

    def test_management_ownership_is_unchanged_without_a_sudo_caller(self):
        for uid, sudo_uid in ((1000, "1234"), (0, "0"), (0, None)):
            with self.subTest(uid=uid, sudo_uid=sudo_uid), patch.dict(os.environ), \
                    patch.object(core.os, "geteuid", return_value=uid), \
                    patch.object(core.pwd, "getpwuid") as account, \
                    patch.object(core.os, "fchown") as chown:
                if sudo_uid is None:
                    os.environ.pop("SUDO_UID", None)
                else:
                    os.environ["SUDO_UID"] = sudo_uid
                core.normalize_manager_owner(self.card / "tools")
                account.assert_not_called()
                chown.assert_not_called()

    def test_failed_fresh_source_registration_removes_new_app_and_fallbacks(self):
        self.source_tree()
        with patch.object(core.SystemMenu, "install", side_effect=OSError("registration failed")):
            status, output = self.source_install()
        self.assertEqual(status, 1, output)
        for relative in ("melkor227-tv", "Melkor 227 TV.sh", "images/melkor227.svg"):
            self.assertFalse((self.card / "tools" / relative).exists())
        self.assertFalse(self.cfg.exists())

    def test_symlinked_card_rejected(self):
        link = self.root / "redirect"
        link.symlink_to(self.card)
        with self.assertRaises(ValueError):
            core.Installer(link)
        self.source_tree()
        self.card = link
        status, output = self.source_install()
        self.assertEqual(status, 1, output)
        self.assertFalse((link / "tools").exists())

    def test_release_builder_pins_manager_and_all_runtime_assets(self):
        from build_release import build
        with patch("builtins.print"):
            release = build(self.root / "release")
        output = self.root / "release"
        script = (output / "Install 227 TV.sh").read_text()
        self.assertIn(release["manager"]["sha256"], script)
        self.assertNotIn("@MANAGER_SHA256@", script)
        with tarfile.open(output / release["manager"]["file"]) as bundle:
            self.assertIn("Melkor 227 TV.sh", bundle.getnames())
            self.assertIn("assets/melkor227.svg", bundle.getnames())
            self.assertIn("assets/intro-background.rgba", bundle.getnames())
        archive = output / release["bundle"]["file"]
        self.assertEqual(core.digest(archive), release["bundle"]["sha256"])
        core.extract_bundle(archive, self.root / "unpacked", release["bundle"]["unpacked_bytes"])
        manifest = core.verify_tree(self.root / "unpacked")
        self.assertIn("runtime/lib/libmpv.so.2", manifest["files"])
        self.assertIn("native/libtvglobe.so", manifest["files"])
        self.assertIn("assets/intro-frames.zip", manifest["files"])
        self.assertFalse(any(".ssh" in name or "state.json" in name for name in manifest["files"]))
        (self.root / "unpacked/app.py").write_text("corrupted")
        with self.assertRaises(ValueError):
            core.verify_tree(self.root / "unpacked")


class ManagerTests(unittest.TestCase):
    def manager(self):
        return Manager({}, cards=[dict(path="/roms", name="SD1", available=True),
                                  dict(path="/roms2", name="SD2", available=False)])

    def test_card_selection_and_confirmation_are_explicit(self):
        manager = self.manager()
        manager.button("down")
        manager.button("ok")
        self.assertIsNone(manager.card)
        self.assertEqual(manager.state, "home")
        manager.button("right")
        self.assertEqual(manager.card, 0)
        manager.button("down")
        manager.button("ok")
        self.assertEqual(manager.state, "confirm")
        self.assertFalse(manager.yes)
        manager.button("ok")
        self.assertEqual(manager.state, "home")

    def test_unavailable_card_never_starts_worker(self):
        manager = self.manager()
        manager.card, manager.focus = 1, 1
        manager.button("ok")
        self.assertEqual(manager.state, "home")
        self.assertIn("not mounted", manager.error)

    def test_cancel_confirmation_and_worker_result(self):
        manager = self.manager()
        manager.state = "working"
        manager.button("back")
        self.assertEqual(manager.state, "cancel_confirm")
        self.assertFalse(manager.cancel.is_set())
        manager.button("right")
        manager.button("ok")
        self.assertTrue(manager.cancel.is_set())
        manager.events.put(dict(phase="result", state="success", result={"system": "227"}))
        manager.update()
        self.assertEqual(manager.state, "success")
        manager.button("back")
        self.assertEqual(manager.state, "home")

    def test_error_details_scroll_without_retrying_install(self):
        manager = self.manager()
        manager.state, manager.error = "failed", "long error "*200
        manager.button("details")
        manager.button("down")
        self.assertEqual(manager.state, "details")
        self.assertEqual(manager.detail_scroll, 1)
        manager.button("back")
        self.assertEqual(manager.state, "failed")
        self.assertIsNone(manager.thread)

    def test_manager_screenshot_uses_app_visuals_without_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "installer.png"
            result = subprocess.run([sys.executable, "-B", str(ROOT / "tv_manager.py"),
                                     "--desktop", "--screenshot", str(path)], capture_output=True,
                                    env=dict(os.environ, SDL_VIDEODRIVER="dummy"), timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
