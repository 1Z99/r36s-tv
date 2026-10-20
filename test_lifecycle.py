"""Display ownership checks never open a real handheld window."""

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import app


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proc = Path(self.temp.name)

    def process(self, pid, parent, executable, started="1234"):
        root = self.proc / str(pid)
        root.mkdir(exist_ok=True)
        fields = ["S", str(parent)] + ["0"]*17 + [started]
        (root / "stat").write_text("%s (name with spaces) %s\n" % (pid, " ".join(fields)))
        link = root / "exe"
        if link.is_symlink():
            link.unlink()
        link.symlink_to(executable)

    def test_normal_menu_launch_finds_actual_es_ancestor(self):
        self.process(100, 90, "/usr/bin/python3")
        self.process(90, 80, "/usr/bin/bash")
        self.process(80, 70, "/usr/bin/emulationstation/emulationstation", "888")
        self.assertEqual(app.display_owner(100, self.proc), (80, "888"))

    def test_ssh_nohup_and_service_wrapper_are_not_owners(self):
        self.process(100, 90, "/usr/bin/python3")
        self.process(90, 1, "/usr/bin/bash")
        self.assertIsNone(app.display_owner(100, self.proc))
        self.process(90, 80, "/usr/bin/sshd-session")
        self.process(80, 1, "/usr/bin/bash")
        self.assertIsNone(app.display_owner(100, self.proc))

    def test_es_restart_or_launcher_orphan_changes_owner(self):
        self.process(100, 90, "/usr/bin/python3")
        self.process(90, 80, "/usr/bin/bash")
        self.process(80, 1, "/usr/bin/emulationstation/emulationstation", "888")
        owner = app.display_owner(100, self.proc)
        self.process(80, 1, "/usr/bin/emulationstation/emulationstation", "999")
        self.assertNotEqual(owner, app.display_owner(100, self.proc))
        self.process(90, 1, "/usr/bin/bash")
        self.assertIsNone(app.display_owner(100, self.proc))

    def test_missing_broken_or_cyclic_proc_chain_fails_closed(self):
        self.assertIsNone(app.display_owner(100, self.proc))
        self.process(100, 90, "/usr/bin/python3")
        self.process(90, 100, "/usr/bin/bash")
        self.assertIsNone(app.display_owner(100, self.proc))
        (self.proc / "100/stat").write_text("broken")
        self.assertIsNone(app.display_owner(100, self.proc))

    def test_background_launch_rejected_before_display_initialization(self):
        with patch.dict(os.environ, SDL_VIDEODRIVER="kmsdrm"), patch.object(app, "display_owner", return_value=None), \
                patch.object(app, "Display") as display, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                app.main(["--skip-intro"])
            self.assertEqual(error.exception.code, 2)
            display.assert_not_called()

    def test_diagnostic_bypass_cannot_leave_an_unbounded_window(self):
        for extra in ([], ["--run-seconds", "0"], ["--run-seconds", "61"], ["--run-seconds", "nan"]):
            with self.subTest(extra=extra), patch.object(app, "Display") as display, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    app.main(["--device-test", *extra])
                display.assert_not_called()

    def test_main_closes_when_launching_es_session_disappears(self):
        display = Mock(joy=None)
        display.sdl.SDL_PollEvent.return_value = 0
        ui = Mock(running=True, dirty=False, input_active=True, focus="list")
        with patch.dict(os.environ, SDL_VIDEODRIVER="kmsdrm"), \
                patch.object(app, "display_owner", side_effect=[(80, "888"), None]), \
                patch.object(app, "Display", return_value=display), \
                patch.object(app, "App", return_value=ui), contextlib.redirect_stdout(io.StringIO()):
            app.main(["--demo", "--skip-intro", "--run-seconds", "2"])
        ui.close.assert_called_once()
        display.close.assert_called_once()
        display.sdl.SDL_RenderPresent.assert_not_called()


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(app, "Globe", return_value=Mock(lat=18., lon=-20., zoom=1.)):
            self.ui = app.App(Mock(), Path(self.temp.name), demo=True)
        self.addCleanup(self.ui.close)

    def test_handheld_intro_focus_events_do_not_exit_or_disable_input(self):
        self.ui.handheld = True
        for event in (2, 13, 7, 13):
            with contextlib.redirect_stdout(io.StringIO()):
                self.ui.window_event(event)
        self.assertTrue(self.ui.running)
        self.assertTrue(self.ui.input_active)
        self.assertEqual(self.ui.reported_focus_events, {2, 7, 13})
        self.ui.action("down")
        self.assertEqual(self.ui.index, 1)
        self.ui.move_pointer(500, 200)
        self.assertEqual((self.ui.pointer_x, self.ui.pointer_y), (500, 200))

    def test_desktop_focus_can_return_without_consuming_background_input(self):
        self.ui.window_event(13)
        self.ui.action("down")
        self.assertEqual(self.ui.index, 0)
        self.assertTrue(self.ui.running)
        self.ui.window_event(12)
        self.ui.action("down")
        self.assertEqual(self.ui.index, 1)


if __name__ == "__main__":
    unittest.main()
