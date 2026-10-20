"""stdlib tests; all installation paths are mocked, including on the real host.

Run: python3 -B -m unittest -v test_dependencies
"""

# MIT License. Copyright (c) 2026 1Z99. See assets/AI-LICENSE.
# Installation test patterns adapted from 227 AI tests/test_installer_deps.py.

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

import tv_dependencies as deps


def result(output="", code=0, stderr=""):
    return subprocess.CompletedProcess([], code, output, stderr)


def status(missing=(), fatal=None):
    return dict(ok=not missing and not fatal, missing=list(missing), fatal=fatal or {})


def plan(*packages):
    return "\n".join("Inst {} (1:1.2~rc1-3 Debian:13 [arm64])".format(name) for name in packages)


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tv-dependencies-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "runtime/lib").mkdir(parents=True)
        (self.root / "runtime/lib/libmpv.so.2").write_bytes(b"test ELF placeholder")
        (self.root / "tv_media.py").write_text("# mock source\n")
        self.ca = self.root / "ca.pem"
        self.ca.write_text("mock CA\n")
        self.guards = {}
        for target in ("ctypes.CDLL", "shutil.which", "subprocess.run", "subprocess.Popen",
                       "os.uname", "os.geteuid", "Path.read_text", "ssl.SSLContext"):
            owner, name = target.split(".")
            patcher = mock.patch.object(getattr(deps, owner), name,
                                       side_effect=AssertionError("Unmocked host action: " + target))
            self.guards[target] = patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(deps.ssl, "get_default_verify_paths", return_value=types.SimpleNamespace(
            cafile=str(self.ca), openssl_cafile=str(self.ca)))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("SSL_CERT_FILE", None)
        self.absent = self.patch("_absent", side_effect=lambda name, error=None, private=None:
            error is None or str(error).startswith(name + ": cannot open shared object file: No such file"))

    def patch(self, name, **kwargs):
        patcher = mock.patch.object(deps, name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def allow(self, target, value):
        guard = self.guards[target]
        guard.side_effect = None
        guard.return_value = value
        return guard

    def capabilities(self, absent=(), errors=None, closure="", relocations="", response=None):
        self.guards["shutil.which"].side_effect = lambda name: None if name in absent else "/usr/bin/" + name
        errors = errors or {}

        def load(name):
            if name in errors:
                raise OSError(errors[name])
            return mock.Mock()

        self.guards["ctypes.CDLL"].side_effect = load
        self.allow("ssl.SSLContext", mock.Mock())
        run = self.allow("subprocess.run", None)
        run.side_effect = [result(closure), result(relocations),
                           response or result('{"ok": true, "reason": "SW rgba"}')]
        return run

    def platform(self, release='ID=debian\nVERSION_ID="13"\n', machine="aarch64", uid=0):
        self.allow("Path.read_text", release)
        self.allow("os.uname", types.SimpleNamespace(machine=machine))
        self.allow("os.geteuid", uid)
        self.guards["shutil.which"].side_effect = lambda name: "/usr/bin/" + name

    def workflow(self, missing=("curl",), output=None, existing=""):
        checker = self.patch("check", side_effect=[status(missing), status()])
        self.platform()
        output = plan(*missing) if output is None else output
        run = self.allow("subprocess.run", None)
        run.side_effect = [result("arm64\n"), result(existing), result(output), result(output), result(existing)]
        run.reset_mock()
        process = mock.Mock()
        process.stdout = io.StringIO("Get:1 package\nSetting up package\n")
        process.poll.return_value = process.wait.return_value = 0
        popen = self.allow("subprocess.Popen", process)
        popen.reset_mock()
        return checker, run, process

    def test_import_performs_no_probes(self):
        spec = importlib.util.spec_from_file_location("fresh_tv_dependencies", deps.__file__)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        for guard in self.guards.values():
            guard.assert_not_called()

    def test_present_capabilities_do_not_query_platform_or_packages_or_init_sdl(self):
        run = self.capabilities()
        library = mock.Mock()
        self.allow("ctypes.CDLL", library)
        answer = deps.ensure(self.root)
        self.assertEqual(answer, dict(status(), installed=[]))
        loads = self.guards["ctypes.CDLL"]
        self.assertEqual([call.args[0] for call in loads.call_args_list], list(deps.SDL))
        library.SDL_Init.assert_not_called()
        library.SDL_InitSubSystem.assert_not_called()
        library.SDL_CreateWindow.assert_not_called()
        library.SDL_RenderGeometryRaw.assert_not_called()
        for target in ("Path.read_text", "os.uname", "os.geteuid", "subprocess.Popen"):
            self.guards[target].assert_not_called()
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args_list[0].args[0], ["ldd", str(self.root / "runtime/lib/libmpv.so.2")])
        self.assertEqual(run.call_args_list[1].args[0][0:2], ["ldd", "-r"])
        child = run.call_args_list[2]
        self.assertEqual(child.args[0], [sys.executable, "-B", "-c", deps.MEDIA_PROBE, str(self.root)])
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 10)
            self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(call.kwargs["env"]["TV_MEDIA_AO"], "null")
        self.guards["ssl.SSLContext"].return_value.load_verify_locations.assert_called_once_with(cafile=str(self.ca))

    def test_child_loader_path_is_set_before_exec_and_parent_is_unchanged(self):
        os.environ["LD_LIBRARY_PATH"] = "/firmware/lib"
        run = self.capabilities()
        deps.check(self.root)
        self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/firmware/lib")
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["env"]["LD_LIBRARY_PATH"], str(self.root / "runtime/lib") + ":/firmware/lib")
            self.assertEqual(call.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")

    def test_exact_sdl_sonames_tools_and_ca_missing_only(self):
        self.capabilities(absent=("curl", "flock"), errors={
            name: name + ": cannot open shared object file: No such file or directory" for name in deps.SDL})
        self.ca.unlink()
        answer = deps.check(self.root)
        self.assertEqual(answer["missing"], list(deps.SDL.values()) + ["curl", "util-linux", "ca-certificates"])
        self.assertEqual(answer["fatal"], {})

    def test_broken_sdl_or_missing_geometry_is_fatal_never_upgrade(self):
        for error in ("libSDL2-2.0.so.0: invalid ELF header", "libdrm.so.2: cannot open shared object file: No such file",
                      "libSDL2-2.0.so.0: undefined symbol: broken", "libSDL2-2.0.so.0: Permission denied"):
            with self.subTest(error=error):
                self.capabilities(errors={"libSDL2-2.0.so.0": error})
                answer = deps.ensure(self.root)
                self.assertIn("libSDL2-2.0.so.0", answer["fatal"])
                self.assertEqual(answer["missing"], [])
                self.guards["Path.read_text"].assert_not_called()
        self.capabilities()
        self.allow("ctypes.CDLL", mock.Mock(spec=[]))
        answer = deps.check(self.root)
        self.assertIn("SDL_RenderGeometryRaw", answer["fatal"]["libSDL2-2.0.so.0"])
        self.assertEqual(answer["missing"], [])

    def test_existing_ca_invalid_or_dangling_is_fatal(self):
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                self.capabilities()
                if dangling:
                    self.ca.unlink()
                    self.ca.symlink_to(self.root / "absent.pem")
                context = self.guards["ssl.SSLContext"].return_value
                context.load_verify_locations.side_effect = OSError("invalid CA bundle")
                answer = deps.check(self.root)
                self.assertEqual(answer["missing"], [])
                self.assertIn("ca-certificates", answer["fatal"])

    def test_all_direct_missing_are_one_plan_candidates_before_any_relocations(self):
        run = self.capabilities(closure="\n".join(name + " => not found" for name in deps.DIRECT))
        answer = deps.check(self.root)
        self.assertEqual(answer, status(deps.DIRECT.values()))
        self.assertEqual(run.call_count, 1)

    def test_explicit_transitives_and_duplicate_missing_are_mapped_not_guessed(self):
        self.capabilities(closure="libdav1d.so.7 => not found\nlibdav1d.so.7 => not found\nliboddcodec.so.99 => not found")
        answer = deps.check(self.root)
        self.assertEqual(answer["missing"], ["libdav1d7"])
        self.assertIn("liboddcodec.so.99", answer["fatal"])

    def test_base_firmware_missing_is_fatal(self):
        for name in ("libc.so.6", "libm.so.6", "libdrm.so.2", "libEGL.so.1", "ld-linux-aarch64.so.1"):
            with self.subTest(name=name):
                self.capabilities(closure=name + " => not found")
                answer = deps.ensure(self.root)
                self.assertEqual(answer["missing"], [])
                self.assertIn(name, answer["fatal"])
                self.guards["Path.read_text"].assert_not_called()

    def test_existing_dependency_file_is_not_a_missing_package(self):
        self.capabilities(closure="libavcodec.so.61 => not found")
        self.absent.side_effect = None
        self.absent.return_value = False
        answer = deps.check(self.root)
        self.assertEqual(answer["missing"], [])
        self.assertIn("will not replace", answer["fatal"]["libavcodec.so.61"])

    def test_relocation_or_version_errors_do_not_request_replacement(self):
        for closure, relocations in (("libc.so.6: version `GLIBC_2.38' not found", ""),
                                     ("", "undefined symbol: avcodec_new (private/libmpv.so.2)"),
                                     ("", "libavcodec.so.61 => not found")):
            with self.subTest(closure=closure, relocations=relocations):
                self.capabilities(closure=closure, relocations=relocations)
                answer = deps.ensure(self.root)
                self.assertIn("libmpv", answer["fatal"])
                self.assertEqual(answer["missing"], [])
                self.guards["Path.read_text"].assert_not_called()

    def test_missing_private_runtime_never_falls_back_to_system(self):
        self.capabilities()
        (self.root / "runtime/lib/libmpv.so.2").unlink()
        answer = deps.check(self.root)
        self.assertIn("restore the application bundle", answer["fatal"]["libmpv"])
        self.guards["subprocess.run"].assert_not_called()

    def test_probe_failures_timeouts_signals_and_invalid_json_are_fatal(self):
        for stage in range(3):
            for failure in (result("bad ELF", 1), result("", -11, "segfault"),
                            subprocess.TimeoutExpired("probe", 10), OSError("exec denied")):
                with self.subTest(stage=stage, failure=failure):
                    run = self.capabilities()
                    run.side_effect = [result()] * stage + [failure]
                    answer = deps.check(self.root)
                    self.assertIn("libmpv", answer["fatal"])
                    self.assertEqual(answer["missing"], [])
        for output in ("", "not JSON", "[]", "{}", '{"ok": false}', '{"ok": 1}'):
            with self.subTest(output=output):
                self.capabilities(response=result(output))
                self.assertIn("libmpv", deps.check(self.root)["fatal"])

    def test_media_child_uses_actual_availability_and_rejects_system_fallback(self):
        for mismatch, ready in ((False, True), (False, False), (True, True)):
            with self.subTest(mismatch=mismatch, ready=ready):
                private = mock.Mock()
                selected = mock.Mock() if mismatch else private
                fake_ctypes = types.SimpleNamespace(CDLL=mock.Mock(return_value=private), c_void_p=object(),
                    cast=lambda symbol, kind: types.SimpleNamespace(value=1 if symbol is private.mpv_create else 2))
                media = types.SimpleNamespace(_library=mock.Mock(return_value=selected),
                                             availability=mock.Mock(return_value=(ready, "real renderer result")))
                output = io.StringIO()
                with mock.patch.dict(sys.modules, {"ctypes": fake_ctypes, "tv_media": media}), \
                        mock.patch.object(sys, "argv", ["-c", str(self.root)]), \
                        mock.patch.object(sys, "path", list(sys.path)), contextlib.redirect_stdout(output):
                    if mismatch:
                        with self.assertRaisesRegex(RuntimeError, "different libmpv"):
                            exec(deps.MEDIA_PROBE, {})
                        media.availability.assert_not_called()
                    else:
                        with self.assertRaises(SystemExit) as caught:
                            exec(deps.MEDIA_PROBE, {})
                        self.assertEqual(caught.exception.code, 0 if ready else 1)
                        self.assertEqual(json.loads(output.getvalue())["ok"], ready)
                        media.availability.assert_called_once_with()
                fake_ctypes.CDLL.assert_called_once_with(str(self.root / "runtime/lib/libmpv.so.2"))

    def test_read_only_does_not_query_platform_packages_or_authority(self):
        self.capabilities(absent=("curl",))
        answer = deps.ensure(self.root, allow_install=False)
        self.assertEqual(answer, dict(status(["curl"]), installed=[]))
        for target in ("Path.read_text", "os.uname", "os.geteuid", "subprocess.Popen"):
            self.guards[target].assert_not_called()

    def test_unsupported_os_version_architecture_tools_and_dpkg_arch(self):
        for release, machine, absent, arch in (
            ("ID=arch\nVERSION_ID=13", "aarch64", None, "arm64"),
            ("ID=ubuntu\nVERSION_ID=13\nID_LIKE=debian", "aarch64", None, "arm64"),
            ("ID=debian\nVERSION_ID=12", "aarch64", None, "arm64"),
            ("ID=debian\nVERSION_ID=13", "x86_64", None, "arm64"),
            ("ID=debian\nVERSION_ID=13", "aarch64", "apt-get", "arm64"),
            ("ID=debian\nVERSION_ID=13", "aarch64", "dpkg-query", "arm64"),
            ("ID=debian\nVERSION_ID=13", "aarch64", None, "armhf")):
            with self.subTest(release=release, machine=machine, absent=absent, arch=arch):
                self.workflow()
                self.platform(release, machine)
                self.guards["shutil.which"].side_effect = lambda name: None if name == absent else "/usr/bin/" + name
                run = self.allow("subprocess.run", result(arch))
                answer = deps.ensure(self.root)
                self.assertIn("Debian 13", answer["fatal"]["setup"])
                self.assertFalse(any("apt-get" in call.args[0][0] for call in run.call_args_list))
                self.guards["subprocess.Popen"].assert_not_called()

    def test_os_release_unreadable_or_malformed_is_fatal(self):
        for error in (OSError("unreadable"), ValueError("bad quoting")):
            self.workflow()
            self.guards["Path.read_text"].side_effect = error
            answer = deps.ensure(self.root)
            self.assertIn("Cannot read os-release", answer["fatal"]["setup"])
            self.guards["subprocess.run"].assert_not_called()

    def test_no_root_or_sudo_authority_never_updates(self):
        for present in (True, False):
            with self.subTest(sudo=present):
                self.workflow()
                self.platform(uid=1000)
                self.guards["shutil.which"].side_effect = lambda name: None if name == "sudo" and not present else "/usr/bin/" + name
                run = self.guards["subprocess.run"]
                run.side_effect = [result("arm64"), result("password required", 1)]
                answer = deps.ensure(self.root)
                self.assertIn("no password", answer["fatal"]["setup"])
                self.assertFalse(any("update" in call.args[0] for call in run.call_args_list))
                self.guards["subprocess.Popen"].assert_not_called()

    def test_safe_install_pins_entire_plan_and_rechecks(self):
        checker, run, process = self.workflow(output=plan("curl:arm64", "libcurl4t64"))
        events = []
        os.environ["APT_CONFIG"] = "/untrusted"
        answer = deps.ensure(self.root, emit=events.append)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["installed"], ["curl:arm64", "libcurl4t64"])
        self.assertEqual(checker.call_args_list, [mock.call(self.root), mock.call(self.root)])
        pins = ["curl:arm64=1:1.2~rc1-3", "libcurl4t64=1:1.2~rc1-3"]
        command = self.guards["subprocess.Popen"].call_args
        self.assertEqual(command.args[0], ["/usr/bin/apt-get"] + deps.OPTIONS + ["--yes"] + deps.INSTALL + pins)
        self.assertNotIn("APT_CONFIG", command.kwargs["env"])
        self.assertEqual(command.kwargs["env"]["DEBIAN_FRONTEND"], "noninteractive")
        self.assertTrue(command.kwargs["start_new_session"])
        self.assertEqual(run.call_args_list[3].args[0], ["/usr/bin/apt-get"] + deps.OPTIONS + ["--simulate"] + deps.INSTALL + pins)
        self.assertTrue(any("Get:1" in event["detail"] for event in events))
        self.assertTrue(any("Setting up" in event["detail"] for event in events))
        self.assertTrue(all(event["phase"] == "dependencies" and event["elapsed"] >= 0 for event in events))
        process.wait.assert_called_once_with()
        process.kill.assert_not_called()
        process.terminate.assert_not_called()

    def test_all_direct_candidates_are_requested_together(self):
        self.workflow(missing=tuple(deps.DIRECT.values()))
        answer = deps.ensure(self.root)
        self.assertTrue(answer["ok"])
        first_plan = self.guards["subprocess.run"].call_args_list[2].args[0]
        self.assertEqual(first_plan[-len(deps.DIRECT):], list(deps.DIRECT.values()))

    def test_fresh_closure_install_then_actual_check_and_media_reprobe(self):
        self.capabilities()
        self.platform()
        output = plan("libavcodec61", "libavformat61", "libdav1d7")
        run = self.guards["subprocess.run"]
        run.side_effect = [result("libavcodec.so.61 => not found\nlibavformat.so.61 => not found"),
                           result("arm64"), result(), result(output), result(output), result(),
                           result("libavcodec.so.61 => /lib/libavcodec.so.61 (0x1234)"), result(),
                           result('{"ok": true, "reason": "actual SW probe"}', stderr="harmless warning\n")]
        process = mock.Mock()
        process.stdout = io.StringIO("Setting up libavcodec61\n")
        process.poll.return_value = process.wait.return_value = 0
        self.allow("subprocess.Popen", process)
        answer = deps.ensure(self.root)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["installed"], ["libavcodec61", "libavformat61", "libdav1d7"])
        self.assertEqual(run.call_args_list[3].args[0][-2:], ["libavcodec61", "libavformat61"])
        self.assertEqual(run.call_args_list[-2].args[0][:2], ["ldd", "-r"])
        self.assertEqual(run.call_args_list[-1].args[0][3], deps.MEDIA_PROBE)
        self.assertEqual(self.guards["ctypes.CDLL"].call_count, 4)
        self.assertEqual(self.guards["ssl.SSLContext"].call_count, 2)

    def test_noninteractive_sudo_environment(self):
        self.workflow()
        self.platform(uid=1000)
        self.guards["subprocess.run"].side_effect = [result("arm64"), result(), result(), result(plan("curl")),
                                                   result(plan("curl")), result()]
        self.assertTrue(deps.ensure(self.root)["ok"])
        command = self.guards["subprocess.Popen"].call_args.args[0]
        self.assertEqual(command[:7], ["/usr/bin/sudo", "-n", "--", "/usr/bin/env",
                                      "DEBIAN_FRONTEND=noninteractive", "LC_ALL=C", "/usr/bin/apt-get"])

    def test_update_only_after_first_failed_simulation_once(self):
        self.workflow()
        run = self.guards["subprocess.run"]
        run.side_effect = [result("arm64"), result(), result("stale", 100), result("refreshed"),
                           result(plan("curl")), result(plan("curl")), result()]
        self.assertTrue(deps.ensure(self.root)["ok"])
        updates = [call for call in run.call_args_list if "update" in call.args[0]]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].kwargs["timeout"], 120)
        self.assertEqual(updates[0].args[0], ["/usr/bin/apt-get"] + deps.OPTIONS + ["update"])

    def test_failed_update_retry_or_signal_stops_without_install(self):
        for responses in ([result("stale", 100), result("offline", 100)],
                          [result("stale", 100), result(), result("no candidate", 100)],
                          [result("crash", -11)]):
            self.workflow()
            run = self.guards["subprocess.run"]
            run.side_effect = [result("arm64"), result()] + responses
            answer = deps.ensure(self.root)
            self.assertIn("setup", answer["fatal"])
            self.guards["subprocess.Popen"].assert_not_called()
            if len(responses) == 1:
                self.assertFalse(any("update" in call.args[0] for call in run.call_args_list))

    def test_all_protected_prefixes_rejected_even_for_new_dependencies(self):
        for name in ("mesa-utils", "libegl1", "libgles2", "libgl1", "libgbm1", "libdrm2", "libmali-rk",
                     "firmware-linux", "kernel-image", "linux-image-arm64", "systemd", "libc6", "udev", "libmpv2"):
            with self.subTest(package=name):
                self.workflow(output=plan("curl", name))
                answer = deps.ensure(self.root)
                self.assertIn("Preserve firmware", answer["fatal"]["setup"])
                self.guards["subprocess.Popen"].assert_not_called()

    def test_upgrades_removals_reinstalls_and_existing_configurations_rejected(self):
        for output, existing in ((plan("curl") + "\nRemv old [1]", ""),
                                 (plan("curl") + "\nPurg old [1]", ""),
                                 ("Inst curl [1.0] (2.0 Debian [arm64])", ""),
                                 (plan("curl"), "curl\tinstalled\n"),
                                 (plan("curl"), "curl:arm64\tunpacked\n"),
                                 (plan("curl") + "\nConf pending (1 Debian [arm64])", "pending\thalf-configured\n")):
            with self.subTest(output=output, existing=existing):
                self.workflow(output=output, existing=existing)
                answer = deps.ensure(self.root)
                self.assertIn("Refusing", answer["fatal"]["setup"])
                self.guards["subprocess.Popen"].assert_not_called()

    def test_sdl_additions_only_for_exact_failed_probe(self):
        for name in deps.SDL.values():
            self.workflow(output=plan("curl", name))
            answer = deps.ensure(self.root)
            self.assertIn("exact missing SONAME", answer["fatal"]["setup"])
            self.guards["subprocess.Popen"].assert_not_called()
            self.workflow(missing=(name,))
            self.assertEqual(deps.ensure(self.root)["installed"], [name])

    def test_missing_not_in_plan_duplicates_foreign_and_malformed_rejected(self):
        for output in ("curl is already newest", plan("curl", "curl:arm64"), "Inst curl:amd64 (1 Debian [amd64])",
                       "Inst curl (1 Debian [amd64])", "Inst curl (1); touch /tmp/unsafe", "Inst unknown"):
            with self.subTest(output=output):
                self.workflow(output=output)
                self.assertIn("setup", deps.ensure(self.root)["fatal"])
                self.guards["subprocess.Popen"].assert_not_called()

    def test_pin_plan_change_or_concurrent_existing_package_rejected(self):
        for verified, existing in ((plan("curl", "extra"), ""), ("Inst curl (2 Debian [arm64])", ""),
                                   (plan("curl"), "curl\tinstalled\n")):
            self.workflow()
            self.guards["subprocess.run"].side_effect = [result("arm64"), result(), result(plan("curl")),
                                                       result(verified), result(existing)]
            self.assertIn("setup", deps.ensure(self.root)["fatal"])
            self.guards["subprocess.Popen"].assert_not_called()

    def test_package_database_failure_is_fatal(self):
        for database in (result("unreadable", 1), result("unrecognized state")):
            self.workflow()
            self.guards["subprocess.run"].side_effect = [result("arm64"), database]
            self.assertIn("setup", deps.ensure(self.root)["fatal"])
            self.guards["subprocess.Popen"].assert_not_called()

    def test_conf_and_config_files_only_are_safe_new_install(self):
        self.workflow(output=plan("curl") + "\nConf curl (1 Debian [arm64])", existing="curl\tconfig-files\n")
        self.assertTrue(deps.ensure(self.root)["ok"])

    def test_cancel_before_probe_or_immediately_before_install(self):
        cancel = threading.Event()
        cancel.set()
        answer = deps.ensure(self.root, cancel=cancel)
        self.assertIn("cancelled", answer["fatal"]["setup"])
        for guard in self.guards.values():
            guard.assert_not_called()
        self.workflow()
        cancel.clear()

        def emit(event):
            if event["label"] == "Installing system dependencies":
                cancel.set()

        self.assertIn("cancelled", deps.ensure(self.root, emit=emit, cancel=cancel)["fatal"]["setup"])
        self.guards["subprocess.Popen"].assert_not_called()

    def test_cancel_during_install_finishes_waits_rechecks_and_retains_installed(self):
        checker, _, process = self.workflow()
        cancel, events = threading.Event(), []

        def start(*args, **kwargs):
            cancel.set()
            return process

        self.guards["subprocess.Popen"].side_effect = start
        answer = deps.ensure(self.root, emit=events.append, cancel=cancel)
        self.assertIn("cancelled", answer["fatal"]["setup"])
        self.assertEqual(answer["installed"], ["curl"])
        self.assertEqual(checker.call_count, 2)
        self.assertTrue(any(event["label"] == "Finishing system setup safely" for event in events))
        process.wait.assert_called_once_with()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_silent_apt_cancellation_is_deferred_without_killing(self):
        _, _, process = self.workflow()
        cancel, release = threading.Event(), threading.Event()
        stream = mock.Mock()

        def read(limit):
            self.assertEqual(limit, 2048)
            if not release.wait(2):
                raise OSError("test stream not released")
            return ""

        def start(*args, **kwargs):
            cancel.set()
            return process

        def emit(event):
            if event["label"] == "Finishing system setup safely":
                release.set()

        stream.readline.side_effect = read
        process.stdout = stream
        self.guards["subprocess.Popen"].side_effect = start
        self.assertIn("cancelled", deps.ensure(self.root, cancel=cancel, emit=emit)["fatal"]["setup"])
        self.assertTrue(release.is_set())
        process.wait.assert_called_once_with()
        process.kill.assert_not_called()

    def test_apt_failure_reports_bounded_log_and_never_claims_installed(self):
        _, _, process = self.workflow()
        process.wait.return_value = 100
        process.stdout = io.StringIO("earliest\n" + "x" * 10000 + "\n" + "later\n" * 30 + "dpkg failed finally\n")
        answer = deps.ensure(self.root)
        self.assertEqual(answer["installed"], [])
        message = answer["fatal"]["setup"]
        self.assertIn("dpkg failed finally", message)
        self.assertNotIn("earliest", message)
        self.assertLess(len(message), 42000)

    def test_callback_failure_during_apt_still_drains_waits_and_rechecks(self):
        checker, _, process = self.workflow()
        process.stdout = io.StringIO("log output\n" * 1000)

        def emit(event):
            if event["detail"] == "log output":
                raise ValueError("UI gone")

        answer = deps.ensure(self.root, emit=emit)
        self.assertIn("after apt finished", answer["fatal"]["setup"])
        self.assertEqual(answer["installed"], ["curl"])
        self.assertEqual(checker.call_count, 2)
        process.wait.assert_called_once_with()
        process.kill.assert_not_called()

    def test_post_install_still_missing_or_broken_is_returned(self):
        checker, _, _ = self.workflow()
        checker.side_effect = [status(["curl"]), status(["curl"], {"libmpv": "ABI still broken"})]
        answer = deps.ensure(self.root)
        self.assertEqual(answer, dict(status(["curl"], {"libmpv": "ABI still broken"}), installed=["curl"]))

    def test_command_timeout_includes_output(self):
        self.workflow()
        self.guards["subprocess.run"].side_effect = subprocess.TimeoutExpired("fake", 120, output=b"network stalled")
        answer = deps.ensure(self.root)
        self.assertIn("network stalled", answer["fatal"]["setup"])
        self.guards["subprocess.Popen"].assert_not_called()


class AbsenceTests(unittest.TestCase):
    def test_absent_distinguishes_existing_files_and_dangling_symlinks(self):
        with tempfile.TemporaryDirectory(prefix="tv-soname-") as directory:
            name = "libtv227-test-only.so.99"
            path = Path(directory) / name
            with mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": directory}):
                self.assertTrue(deps._absent(name))
                path.write_bytes(b"invalid ELF")
                self.assertFalse(deps._absent(name))
                path.unlink()
                path.symlink_to(Path(directory) / "missing")
                self.assertFalse(deps._absent(name))
            self.assertFalse(deps._absent(name, OSError("another.so: cannot open shared object file: No such file")))
            self.assertFalse(deps._absent(name, OSError(name + ": invalid ELF header")))
            self.assertFalse(deps._absent(name, private=Path(directory)))

    def test_configured_loader_directories_and_unreadable_config_are_not_absent(self):
        with tempfile.TemporaryDirectory(prefix="tv-loader-") as directory:
            name = "libtv227-config-only.so.99"
            (Path(directory) / name).symlink_to(Path(directory) / "missing")
            with mock.patch.object(Path, "exists", autospec=True,
                                   side_effect=lambda path: str(path) == "/etc/ld.so.conf"):
                with mock.patch.object(Path, "read_text", return_value=directory + "\n"):
                    self.assertFalse(deps._absent(name))
                with mock.patch.object(Path, "read_text", side_effect=PermissionError("loader config unreadable")):
                    self.assertFalse(deps._absent(name))


class HostReadOnlyTests(unittest.TestCase):
    def test_actual_host_check_is_read_only_and_platform_independent(self):
        # A real host probe, never a device test. No OS-specific result assumption
        # and no platform skips; an arm64 private ELF on x86 should fail cleanly.
        run = subprocess.run

        def read_only(command, **kwargs):
            self.assertIn(command[0], ("ldd", sys.executable))
            self.assertEqual(kwargs["timeout"], 10)
            return run(command, **kwargs)

        with mock.patch.object(deps.subprocess, "run", side_effect=read_only), \
                mock.patch.object(deps.subprocess, "Popen", wraps=subprocess.Popen) as popen, \
                mock.patch.object(deps, "_authority", side_effect=AssertionError("authority queried")), \
                mock.patch.object(deps, "_run", side_effect=AssertionError("package command attempted")):
            answer = deps.ensure(allow_install=False)
        self.assertEqual(set(answer), {"ok", "missing", "fatal", "installed"})
        self.assertIsInstance(answer["fatal"], dict)
        self.assertIsInstance(answer["missing"], list)
        self.assertEqual(answer["ok"], not answer["missing"] and not answer["fatal"])
        self.assertEqual(answer["installed"], [])
        for call in popen.call_args_list:
            self.assertIn(call.args[0][0], ("ldd", sys.executable))


if __name__ == "__main__":
    unittest.main()
