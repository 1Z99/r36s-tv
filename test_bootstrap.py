"""Exercise the actual shell auditor and embedded bootstrap without APT/network/SDL."""

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
import unittest
from unittest.mock import patch

import tv_dependencies as deps
import tv_release


SOURCE = Path(__file__).resolve().parent
BOOTSTRAP = (SOURCE / "install227tv.sh").read_text()
AUDITOR = BOOTSTRAP.split("bootstrap_audit() {", 1)[1].split("\nbootstrap() {", 1)[0]
AUDITOR = "bootstrap_audit() {" + AUDITOR


def plan(*names):
    return "\n".join("Inst {} (1:1.2~rc1-3 Debian:13 [arm64])".format(name) for name in names)


class ShellAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tv227-bootstrap-audit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.command("dpkg-query", 'print(os.environ.get("DPKG_STATE", ""), end="")\n'
                     'sys.exit(int(os.environ.get("DPKG_EXIT", "0")))\n')
        self.env = os.environ | {"PATH": str(self.bin) + ":" + os.environ["PATH"],
                                 "DPKG_STATE": "", "DPKG_EXIT": "0", "SUDO_UID": "0"}

    def command(self, name, body):
        path = self.bin / name
        path.write_text("#!" + sys.executable + "\nimport os, sys, json\n" + body)
        path.chmod(0o755)

    def audit(self, output, existing="", missing=("curl",), **environment):
        return subprocess.run(["sh", "-c", AUDITOR + '\nbootstrap_audit "$1"',
                               "bootstrap-audit", " ".join(missing)], input=output, text=True,
                              capture_output=True, timeout=10,
                              env=self.env | {"DPKG_STATE": existing} | environment)

    def test_new_packages_accept_arch_annotations_and_pin_exact_versions(self):
        for output in (plan("curl"), plan("curl:arm64", "helper:all"),
                       "Inst curl (1)\nConf curl (1 Debian [arm64])",
                       "Inst curl (1 Debian [all])", "  " + plan("curl") + "  "):
            with self.subTest(output=output):
                result = self.audit(output, "curl\tconfig-files\nhelper\tnot-installed\n")
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = deps._audit(output, ["curl"], set())
                self.assertEqual(result.stdout.splitlines(), [k + "=" + v for k, v in expected.items()])

    def test_unsafe_plans_match_python_policy(self):
        cases = [(plan("curl") + "\n" + removal, "")
                 for removal in ("Remv old [1]", "Purg old [1]")]
        cases += [(output, "") for output in (
            "Inst curl [1.0] (2.0 Debian [arm64])", plan("curl", "curl:arm64"),
            "Inst curl:amd64 (1 Debian [amd64])", "Inst curl (1 Debian [amd64])",
            "Inst curl (1 Debian [arm64] [amd64])", "Inst curl (1); touch /tmp/unsafe",
            "Inst unknown", "Instcurl (1)", "curl is already newest", plan("helper"),
            plan("curl") + "\nConf pending (1 Debian [arm64])",
            plan("curl", "libsdl2-2.0-0"), plan("curl", "libsdl2-ttf-2.0-0"))]
        cases += [(plan("curl", prefix + "-test"), "") for prefix in deps.PROTECTED]
        cases += [(plan("curl"), "curl:arm64\t" + state + "\n")
                  for state in ("installed", "unpacked", "half-configured", "triggers-pending")]
        for output, existing in cases:
            with self.subTest(output=output, existing=existing):
                with self.assertRaises(RuntimeError):
                    deps._audit(output, ["curl"], {"curl"} if existing else set())
                result = self.audit(output, existing)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(result.stdout, "")

    def test_package_database_failure_or_malformed_state_is_fatal(self):
        for state, code in (("", "1"), ("unrecognized state", "0"), ("bad/name\tinstalled\n", "0"),
                            ("curl\tconfig-files\n\nhelper\tinstalled", "0")):
            with self.subTest(state=state, code=code):
                self.assertNotEqual(self.audit(plan("curl"), state, DPKG_EXIT=code).returncode, 0)

    def workflow(self, verified=None, concurrent=False, arguments=()):
        # Run the real shell setup, stopping before Python. Only the firmware
        # identity file is redirected; all APT/dpkg commands come from mock PATH.
        release = self.root / "os-release"
        release.write_text('ID=debian\nVERSION_ID=13\n')
        shell = BOOTSTRAP.split("\npython3 -B -c", 1)[0] + '\n}\nbootstrap "$@"\n'
        shell = shell.replace(". /etc/os-release", '. "' + str(release) + '"')
        self.command("uname", 'print("aarch64")\n')
        self.command("id", 'print("0")\n')
        self.command("dpkg", 'print("arm64")\n')
        self.command("dpkg-query",
                     'from pathlib import Path\n'
                     'count = Path(os.environ["QUERY_COUNT"])\n'
                     'second = count.exists()\ncount.touch()\n'
                     'print("curl\\tinstalled" if second and os.environ["CONCURRENT"] == "1" else "")\n')
        self.command("apt-get",
                     'from pathlib import Path\n'
                     'log = Path(os.environ["APT_LOG"])\n'
                     'previous = log.read_text().splitlines() if log.exists() else []\n'
                     'with log.open("a") as stream: stream.write(json.dumps(sys.argv[1:])+"\\n")\n'
                     'if "--simulate" in sys.argv: print(os.environ["VERIFY" if previous else "PLAN"])\n')
        # Only curl is missing, independent of tools installed on the test host.
        override = ('command() { if [ "$1" = -v ] && [ "$2" = curl ]; then return 1; fi; '
                    '(unset -f command; command "$@"); }\n')
        log = self.root / "apt.jsonl"
        result = subprocess.run(["sh", "-c", override + shell, "bootstrap", *arguments], text=True, capture_output=True, timeout=10,
                                env=self.env | {"APT_LOG": str(log), "QUERY_COUNT": str(self.root / "queries"),
                                                "PLAN": plan("curl"), "VERIFY": verified or plan("curl"),
                                                "CONCURRENT": str(int(concurrent))})
        return result, [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_shell_workflow_installs_only_verified_pins(self):
        result, commands = self.workflow()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(commands), 3)
        self.assertIn("--yes", commands[-1])
        self.assertEqual(commands[-1][-1], "curl=1:1.2~rc1-3")
        for command in commands:
            for flag in ("--no-upgrade", "--no-remove", "--no-install-recommends"):
                self.assertIn(flag, command)

    def test_shell_workflow_reaudits_database_before_install(self):
        result, commands = self.workflow(concurrent=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(commands), 2)
        self.assertIn("Unsafe pinned dependency plan", result.stderr)

    def test_shell_workflow_rejects_changed_pins(self):
        result, commands = self.workflow(verified="Inst curl (2 Debian [arm64])")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(commands), 2)
        self.assertIn("Dependency plan changed", result.stderr)

    def test_dry_run_never_bootstraps_missing_dependencies(self):
        result, commands = self.workflow(arguments=("--dry-run",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no dependencies installed", result.stderr)
        self.assertEqual(commands, [])


class ManagerCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tv227-bootstrap-manager-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.card = self.root / "card"
        self.manager = self.card / "tools/227-tv-manager"
        self.manager.mkdir(parents=True)
        self.release = self.root / "release"
        self.release.mkdir()
        self.archive = self.release / "melkor227-tv-manager-1.0.2.tar.gz"
        self.files = {name: b"# verified manager fixture\n" for name in
                      ("tv_manager.py", "tv_installer.py", "tv_dependencies.py", "tv_system.py", "tv_release.py", "install.sh")}
        self.files["tv_installer.py"] = b'VERSION = "1.0.2"\n'
        self.metadata = dict(format=1, version="1.0.2", target=tv_release.TARGET,
                             base_url=tv_release.canonical_base("1.0.2"),
                             bundle=dict(file="melkor227-tv-1.0.2-darkosre-arm64.tar.gz", bytes=15,
                                         sha256="0"*64, unpacked_bytes=1024))
        self.files["release.json"] = json.dumps(self.metadata).encode()
        self.bundle(self.files)
        self.script = self.root / "Install 227 TV.sh"
        self.script.write_text(BOOTSTRAP)

    def bundle(self, files):
        with tarfile.open(self.archive, "w:gz") as bundle:
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                bundle.addfile(member, io.BytesIO(content))

    def launch(self, base=True, default="@RELEASE_BASE@", environment=None, dry_run=True, http=None):
        generated = BOOTSTRAP.replace("@MANAGER_FILE@", self.archive.name)
        generated = generated.replace("# @RELEASE_HELPER@", (SOURCE / "tv_release.py").read_text())
        generated = generated.replace("@MANAGER_SHA256@", hashlib.sha256(self.archive.read_bytes()).hexdigest())
        generated = generated.replace("@MANAGER_BYTES@", str(self.archive.stat().st_size))
        generated = generated.replace("@RELEASE_BASE@", default)
        self.script.write_text(generated)
        code = generated.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        read_text = Path.read_text

        def mounts(path, *args, **kwargs):
            if str(path) == "/proc/mounts":
                return "device /roms2 ext4 rw 0 0\n"
            return read_text(path, *args, **kwargs)

        def card_path(path):
            return self.card if path == "/roms2" else Path(path)

        arguments = ["bootstrap", str(self.script)]
        if dry_run:
            arguments.append("--dry-run")
        if base:
            arguments += ["--base", self.release.as_uri()]
        namespace = {"__name__": "__main__"}
        with patch.object(sys, "argv", arguments), patch.dict(os.environ, environment or {}, clear=True), \
                patch("pathlib.Path", side_effect=card_path), patch.object(Path, "read_text", mounts), \
                patch("os.access", return_value=True), patch("os.execv") as execute, \
                 patch("subprocess.run", side_effect=http or subprocess.CalledProcessError(7, "curl")):
            try:
                exec(compile(code, "install227tv.sh:python", "exec"), namespace)
            finally:
                if "lock" in namespace:
                    os.close(namespace["lock"])
        return execute

    def test_generated_https_default_yields_to_adjacent_verified_offline_release(self):
        shutil.copyfile(self.archive, self.script.parent / self.archive.name)
        payload = self.script.parent / self.metadata["bundle"]["file"]
        payload.write_bytes(b"offline payload")
        execute = self.launch(base=False, default="https://example.invalid/releases/v1/", dry_run=False)
        cache = self.card / "tools/.227-tv-downloads"
        self.assertEqual((cache / payload.name).read_bytes(), payload.read_bytes())
        self.assertEqual((cache / self.archive.name).read_bytes(), self.archive.read_bytes())
        self.assertIn(cache.as_uri(), execute.call_args.args[1])

    def test_explicit_environment_and_cli_base_override_adjacent_and_default(self):
        shutil.copyfile(self.archive, self.script.parent / self.archive.name)
        for cli in (False, True):
            with self.subTest(cli=cli):
                override = "https://override.invalid/releases/"
                environment = {"TV227_RELEASE_BASE": override}
                # Seed a verified cache so an explicit HTTPS override needs no
                # network; the selected base still determines manager downloads.
                cache = self.card / "tools/.227-tv-downloads"
                cache.mkdir(exist_ok=True)
                shutil.copyfile(self.archive, cache / self.archive.name)
                execute = self.launch(base=cli, default="https://default.invalid/", environment=environment)
                expected = self.release.as_uri() if cli else override
                self.assertIn(expected, execute.call_args.args[1])
                self.assertEqual((self.manager / ".release-base").read_text().strip(), expected)

    def test_adjacent_offline_manager_still_requires_pinned_size_and_hash(self):
        adjacent = self.script.parent / self.archive.name
        adjacent.write_bytes(b"x" * self.archive.stat().st_size)
        with self.assertRaisesRegex(SystemExit, "SHA-256 verification failed"):
            self.launch(base=False, default="https://example.invalid/")
        self.assertFalse((self.manager / "tv_manager.py").exists())

    def test_matching_marker_never_trusts_modified_or_missing_manager_files(self):
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                (self.manager / ".bootstrap-sha256").write_text(hashlib.sha256(self.archive.read_bytes()).hexdigest())
                (self.manager / "tv_manager.py").write_text("raise RuntimeError('tampered')\n")
                (self.manager / "obsolete.py").write_text("# stale module\n")
                bytecode = self.manager / "__pycache__"
                bytecode.mkdir(exist_ok=True)
                (bytecode / "old.pyc").write_bytes(b"stale")
                download = self.manager / "cache/payload.tar.gz"
                download.parent.mkdir(exist_ok=True)
                download.write_bytes(b"keep downloaded payload")
                execute = self.launch()
                for name, content in self.files.items():
                    self.assertEqual((self.manager / name).read_bytes(), content)
                self.assertFalse((self.manager / "obsolete.py").exists())
                self.assertFalse(bytecode.exists())
                self.assertEqual(download.read_bytes(), b"keep downloaded payload")
                self.assertEqual((self.manager / ".release-base").read_text().strip(), self.release.as_uri())
                self.assertIn(str(self.manager / "tv_manager.py"), execute.call_args.args[1])
                (self.manager / "tv_system.py").unlink()

    def test_verified_archive_and_remembered_release_work_offline(self):
        self.launch()
        self.archive.unlink()
        # Preserve the original pins while making the release source unavailable.
        self.archive = self.card / "tools/.227-tv-downloads" / self.archive.name
        execute = self.launch(base=False)
        self.assertIn(self.release.as_uri(), execute.call_args.args[1])

    def test_corrupted_cached_archive_is_reverified_and_replaced(self):
        self.launch()
        cached = self.card / "tools/.227-tv-downloads" / self.archive.name
        original = cached.read_bytes()
        cached.write_bytes(b"x" * len(original))
        self.launch()
        self.assertEqual(cached.read_bytes(), original)

    def latest_http(self, version="1.0.3", corrupt=False, mismatch=False):
        info = dict(self.metadata, version=version, base_url=tv_release.canonical_base(version))
        info["bundle"] = dict(info["bundle"], file="melkor227-tv-"+version+"-darkosre-arm64.tar.gz")
        files = dict(self.files)
        files["tv_installer.py"] = ('VERSION = "'+version+'"\n').encode()
        embedded = dict(info, version="1.0.2") if mismatch else info
        files["release.json"] = json.dumps(embedded).encode()
        latest = self.release / ("melkor227-tv-manager-"+version+".tar.gz")
        pinned = self.archive
        self.archive = latest
        self.bundle(files)
        self.archive = pinned
        info["manager"] = dict(file=latest.name, bytes=latest.stat().st_size,
                               sha256=hashlib.sha256(latest.read_bytes()).hexdigest())
        calls = []

        def http(command, **options):
            calls.append(command)
            if command[-1] == tv_release.LATEST_RELEASE:
                options["stdout"].write(b"{corrupt" if corrupt else json.dumps(info).encode())
            else:
                origin = latest if command[-1] == info["base_url"]+"/"+latest.name else pinned
                if origin == pinned:
                    self.assertEqual(command[-1], tv_release.canonical_base("1.0.2")+"/"+pinned.name)
                shutil.copyfile(origin, command[command.index("--output")+1])
            return subprocess.CompletedProcess(command, 0)
        return http, calls

    def test_old_stable_launcher_follows_later_latest_metadata_on_every_launch(self):
        self.launch()
        for version in ("1.0.3", "1.0.4"):
            with self.subTest(version=version):
                http, calls = self.latest_http(version)
                execute = self.launch(base=False, default=tv_release.canonical_base("1.0.2"), http=http)
                self.assertEqual((self.manager/"tv_installer.py").read_text(), 'VERSION = "'+version+'"\n')
                self.assertIn("--latest", execute.call_args.args[1])
                self.assertEqual(calls[0][-1], tv_release.LATEST_RELEASE)
                self.assertEqual(len(calls), 2)
                self.assertEqual(json.loads((self.manager/"release.json").read_text())["version"], version)

    def test_corrupt_latest_metadata_or_manager_manifest_never_replaces_code(self):
        self.launch()
        original = (self.manager/"tv_installer.py").read_bytes()
        for corrupt, mismatch in ((True, False), (False, True)):
            with self.subTest(corrupt=corrupt, mismatch=mismatch):
                http, _ = self.latest_http(corrupt=corrupt, mismatch=mismatch)
                with self.assertRaises((ValueError, SystemExit)):
                    self.launch(base=False, http=http)
                self.assertEqual((self.manager/"tv_installer.py").read_bytes(), original)

    def test_first_launch_of_old_bootstrap_caches_builtin_manager_for_later_offline_use(self):
        http, calls = self.latest_http()
        self.launch(base=False, http=http)
        self.assertEqual(len(calls), 3)
        self.assertEqual((self.manager/"tv_installer.py").read_text(), 'VERSION = "1.0.3"\n')
        execute = self.launch(base=False)
        self.assertIn("--discovery-error", execute.call_args.args[1])
        self.assertEqual((self.manager/"tv_installer.py").read_text(), 'VERSION = "1.0.2"\n')

    def test_corrupted_latest_manager_archive_never_replaces_existing_manager(self):
        self.launch()
        original = (self.manager/"tv_installer.py").read_bytes()
        http, _ = self.latest_http()
        def corrupt(command, **options):
            result = http(command, **options)
            if "--output" in command:
                Path(command[command.index("--output")+1]).write_bytes(b"corrupted archive")
            return result
        with self.assertRaisesRegex(SystemExit, "SHA-256 verification failed"):
            self.launch(base=False, http=corrupt)
        self.assertEqual((self.manager/"tv_installer.py").read_bytes(), original)

    def test_offline_fallback_reports_failed_check_and_uses_only_verified_builtin_cache(self):
        self.launch()
        execute = self.launch(base=False)
        arguments = execute.call_args.args[1]
        self.assertIn("--latest", arguments)
        self.assertIn("Latest release check failed", arguments[arguments.index("--discovery-error")+1])
        cached = self.card / "tools/.227-tv-downloads" / self.archive.name
        cached.write_bytes(b"corrupt")
        original = (self.manager/"tv_installer.py").read_bytes()
        with self.assertRaisesRegex(SystemExit, "no verified built-in manager"):
            self.launch(base=False)
        self.assertEqual((self.manager/"tv_installer.py").read_bytes(), original)

    def test_failed_replacement_rolls_back_existing_manager_and_cache(self):
        original = self.manager / "tv_manager.py"
        original.write_text("# existing manager\n")
        download = self.manager / "cache/payload.tar.gz"
        download.parent.mkdir()
        download.write_bytes(b"cached payload")
        replace = os.replace

        def fail_stage(source, destination):
            if Path(source).name == "manager" and destination == self.manager:
                raise OSError("fixture replacement failure")
            return replace(source, destination)

        with patch("os.replace", side_effect=fail_stage):
            with self.assertRaisesRegex(OSError, "fixture replacement failure"):
                self.launch()
        self.assertEqual(original.read_text(), "# existing manager\n")
        self.assertEqual(download.read_bytes(), b"cached payload")

    def test_redirected_cache_or_configuration_is_rejected(self):
        for name in ("cache", ".release-base"):
            with self.subTest(name=name):
                target = self.manager / name
                target.symlink_to(self.root / "nonexistent")
                with self.assertRaisesRegex(SystemExit, "Refusing redirected"):
                    self.launch()
                target.unlink()

    def test_unsafe_or_incomplete_archive_does_not_replace_manager(self):
        for files, message in ((dict(self.files, **{"../escape": b"unsafe"}), "Unsafe installer archive"),
                               ({"tv_manager.py": b"# incomplete"}, "bundle is incomplete")):
            with self.subTest(message=message):
                original = self.manager / "tv_manager.py"
                original.write_text("# existing manager\n")
                self.bundle(files)
                cached = self.card / "tools/.227-tv-downloads" / self.archive.name
                cached.unlink(missing_ok=True)
                with self.assertRaisesRegex(SystemExit, message):
                    self.launch()
                self.assertEqual(original.read_text(), "# existing manager\n")


class BootstrapLogTests(unittest.TestCase):
    def test_sudo_reenters_as_invoking_user_before_creating_storage(self):
        with tempfile.TemporaryDirectory(prefix="tv227-bootstrap-sudo-") as directory:
            root = Path(directory)
            commands = root / "bin"
            commands.mkdir()
            (commands / "id").write_text("#!/bin/sh\nprintf '0\\n'\n")
            (commands / "sudo").write_text("#!" + sys.executable + "\nimport json, os, sys\n"
                'print(json.dumps({"args": sys.argv[1:], "data": os.environ["TV227_DATA"], '
                '"base": os.environ["TV227_RELEASE_BASE"], "cwd": os.getcwd()}))\n')
            for command in commands.iterdir():
                command.chmod(0o755)
            environment = os.environ | {"PATH": str(commands) + ":" + os.environ["PATH"],
                "SUDO_UID": "1234", "SUDO_GID": "9999", "TV227_DATA": "./saved data",
                "TV227_RELEASE_BASE": "file:///offline%20release", "XDG_STATE_HOME": str(root / "state")}
            arguments = ["--source", "./source with spaces", "--cli", "--roms", "/roms2", "--yes"]
            result = subprocess.run(["sh", str(SOURCE / "install227tv.sh"), *arguments],
                                    env=environment, cwd=root, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            call = json.loads(result.stdout)
            self.assertEqual(call["args"], ["-n", "-H",
                "--preserve-env=TV227_DATA,TV227_RELEASE_BASE,XDG_STATE_HOME", "-u", "#1234", "--", "sh",
                str(SOURCE / "install227tv.sh"), *arguments])
            self.assertEqual(call["data"], environment["TV227_DATA"])
            self.assertEqual(call["base"], environment["TV227_RELEASE_BASE"])
            self.assertEqual(call["cwd"], str(root))
            self.assertFalse((root / "state").exists())

    def test_pre_sdl_failure_is_persistent_visible_and_nonzero(self):
        with tempfile.TemporaryDirectory(prefix="tv227-bootstrap-log-") as directory:
            root = Path(directory)
            local = root / "local"
            local.mkdir()
            (local / "tv_manager.py").write_text('import sys\nprint("pre-SDL fixture failure", file=sys.stderr)\nsys.exit(7)\n')
            environment = os.environ | {"HOME": str(root), "XDG_STATE_HOME": str(root / "state"), "SUDO_UID": "0"}
            for attempt in range(2):
                result = subprocess.run(["sh", str(SOURCE / "install227tv.sh"), "--source", str(local)],
                                        env=environment, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 7)
                self.assertIn("pre-SDL fixture failure", result.stderr)
                log = root / "state/melkor227-tv/bootstrap.log"
                self.assertIn(str(log), result.stderr)
                self.assertEqual(log.read_text().count("pre-SDL fixture failure"), attempt + 1)

    def test_cli_stdout_is_not_swallowed_by_log(self):
        with tempfile.TemporaryDirectory(prefix="tv227-bootstrap-stdout-") as directory:
            root = Path(directory)
            (root / "tv_manager.py").write_text('print(\'{"ok": true}\')\n')
            result = subprocess.run(["sh", str(SOURCE / "install227tv.sh"), "--source", str(root), "--cli"],
                                    env=os.environ | {"XDG_STATE_HOME": str(root / "state"), "SUDO_UID": "0"},
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"ok": True})

    def test_source_placeholders_and_help_remain_available(self):
        for name in ("MANAGER_FILE", "MANAGER_SHA256", "MANAGER_BYTES", "RELEASE_BASE"):
            self.assertIn("@" + name + "@", BOOTSTRAP)
        result = subprocess.run(["sh", str(SOURCE / "install227tv.sh"), "--help"],
                                env=os.environ | {"HOME": "/nonexistent", "XDG_STATE_HOME": "/nonexistent"},
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("227 TV downloader", result.stdout)


if __name__ == "__main__":
    unittest.main()
