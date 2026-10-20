import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import app
from tv_backend import Health, State


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        globe = Mock(lat=18., lon=-20., zoom=1.)
        globe.advance_flight.return_value = False
        patcher = patch.object(app, "Globe", return_value=globe)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ui = app.App(Mock(), Path(self.temp.name), demo=True)
        self.addCleanup(self.ui.close)

    def test_tabs_favorites_and_search_keyboard(self):
        station = self.ui.selected()
        self.ui.action("favorite")
        self.ui.action("next")
        self.assertEqual(self.ui.rows, [station])
        self.ui.action("prev")
        self.ui.action("search")
        self.ui.draft = "arabic"
        self.ui.action("search")
        self.assertEqual(len(self.ui.rows), 2)
        self.ui.action("back")
        self.assertEqual(len(self.ui.rows), 8)
        self.assertEqual(self.ui.query, "")

    def test_country_menu_clamps_after_refresh(self):
        wanted = self.ui.country_items()[-1][0]
        self.ui.modal, self.ui.menu_index = "countries", 999
        self.ui.stations = self.ui.stations[:1]
        self.ui.action("ok")
        self.assertEqual(self.ui.country, wanted)
        self.assertEqual(self.ui.modal, "")

    def test_globe_zoom_and_pick(self):
        self.ui.action("select")
        self.assertEqual(self.ui.focus, "globe")
        self.ui.globe.lat, self.ui.globe.lon = app.country_center(self.ui.by_country["SY"])
        self.ui.action("plus")
        self.ui.globe.zoom_by.assert_called_with(1.2)
        self.ui.action("ok")
        self.assertEqual(self.ui.country, "SY")
        self.assertEqual(self.ui.focus, "list")
        self.assertTrue(self.ui.markers)

    def test_video_reuses_only_healthy_full_resolution_player(self):
        self.ui.demo = False
        station = self.ui.selected()
        player = Mock(width=640, height=480)
        player.poll.return_value = {"error": "", "ended": False}
        self.ui.players, self.ui.playing, self.ui.cell = {0: player}, {0: station}, 3
        self.ui.play(station)
        self.assertEqual(self.ui.cell, 0)
        self.ui.action("ok")
        player.pause.assert_called_once()
        for status, width, change_url in (({"error": "failed"}, 640, False),
                                          ({"ended": True}, 640, False),
                                          ({}, 298, False), ({}, 640, True)):
            with self.subTest(status=status, width=width, change_url=change_url):
                player.poll.return_value, player.width = status, width
                self.ui.players, self.ui.playing = {0: player}, {0: station}
                candidate = dict(station, url="https://changed.example/live") if change_url else station
                with patch.object(self.ui, "start_player") as start:
                    self.ui.play(candidate)
                    start.assert_called_once_with(0, candidate)

    def test_persistence_failure_is_visible_and_rolled_back(self):
        old = copy.deepcopy(self.ui.state.data)
        with patch.object(self.ui.state, "save", side_effect=OSError("SD card full")):
            self.ui.action("favorite")
        self.assertEqual(self.ui.state.data, old)
        self.assertEqual(self.ui.modal, "error")
        self.assertIn("SD card full", self.ui.error)
        self.ui.action("back")
        with patch.object(self.ui.state, "save", side_effect=OSError("read only")):
            self.ui.modal, self.ui.menu_index = "menu", 6
            self.ui.action("ok")
        self.assertEqual(self.ui.state.data, old)
        self.assertEqual(self.ui.modal, "error")

    def test_matrix_focus_handoff_and_back_cleanup(self):
        first, second = Mock(), Mock()
        self.ui.players = {0: first, 1: second}
        self.ui.statuses = {0: {"audio_active": True}, 1: {"audio_active": False}}
        self.ui.view = "matrix"
        self.ui.action("right")
        self.assertEqual(self.ui.pending_audio, 1)
        first.set_audible.assert_called_with(False)
        second.set_audible.assert_called_with(False)
        first.poll.return_value = second.poll.return_value = {"audio_active": False}
        first.frame.return_value = second.frame.return_value = None
        self.ui.update()
        second.set_audible.assert_called_with(True)
        self.ui.action("back")
        self.assertEqual(self.ui.view, "browse")
        self.assertFalse(self.ui.players)
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_single_back_stops_audio_and_cancels_quality_work(self):
        player = Mock()
        self.ui.players, self.ui.video_textures = {0: player}, {0: "texture"}
        self.ui.view = "video"
        cancellation = self.ui.stream_cancel
        self.ui.pending_streams[1] = ({}, False)
        self.ui.action("back")
        player.close.assert_called_once()
        self.assertTrue(cancellation.is_set())
        self.assertFalse(self.ui.players)
        self.assertFalse(self.ui.pending_streams)
        self.assertEqual(self.ui.view, "browse")

    def test_back_dismisses_modal_without_stopping_video(self):
        self.ui.players, self.ui.view, self.ui.modal = {0: Mock()}, "video", "menu"
        self.ui.action("back")
        self.assertEqual(self.ui.view, "video")
        self.ui.players[0].close.assert_not_called()

    def test_stale_quality_result_cannot_restart_after_back(self):
        self.ui.demo = False
        with patch.object(app, "resolve_station", return_value=self.ui.selected()):
            self.ui.play(self.ui.selected())
            self.ui.action("back")
            time.sleep(.02)
            with patch.object(self.ui, "finish_player") as finish:
                self.ui.update()
                finish.assert_not_called()

    def test_window_exposure_requests_redraw_without_input(self):
        self.ui.dirty = False
        self.ui.window_event(3)
        self.assertTrue(self.ui.dirty)
        self.assertGreater(self.ui.redraw_until, time.monotonic())

    def test_timer_expiry_redraws_toast_without_a_button(self):
        self.ui.redraw_until = 0
        self.ui.notice_until = 100
        with patch.object(app.time, "monotonic", return_value=99):
            self.ui.update()
        self.ui.dirty = False
        with patch.object(app.time, "monotonic", return_value=101):
            self.ui.update()
        self.assertTrue(self.ui.dirty)

    def test_paused_video_does_not_redraw_for_hidden_clock_changes(self):
        self.ui.redraw_until, self.ui.last_input, self.ui.notice_until = 0, 0, 0
        self.ui.view = "video"
        player = Mock()
        player.poll.return_value = {"paused": True, "time": 1, "audio_active": False}
        player.frame.return_value = None
        self.ui.players = {0: player}
        self.ui.frame_cells.add(0)
        with patch.object(app.time, "monotonic", return_value=100):
            self.ui.update()
            self.ui.dirty = False
            player.poll.return_value = {"paused": True, "time": 2, "audio_active": False}
            self.ui.update()
        self.assertFalse(self.ui.dirty)

    def test_loading_animates_without_decoded_frames(self):
        self.ui.redraw_until = 0
        self.ui.pending_streams[0] = ({}, False)
        self.ui.statuses[0] = {"buffering": True}
        with patch.object(app.time, "monotonic", return_value=100):
            self.ui.update()
        self.ui.dirty = False
        with patch.object(app.time, "monotonic", return_value=100.1):
            self.ui.update()
        self.assertTrue(self.ui.dirty)

    def test_globe_crossfade_runs_to_completion_without_controller_input(self):
        self.ui.redraw_until = 0
        self.ui.globe.fade_until, self.ui.globe.trace_active = 100.2, False
        with patch.object(app.time, "monotonic", return_value=100.):
            self.ui.update()
        for now in (100.1, 100.3):
            self.ui.dirty = False
            with patch.object(app.time, "monotonic", return_value=now):
                self.ui.update()
            self.assertTrue(self.ui.dirty)
        self.ui.dirty = False
        with patch.object(app.time, "monotonic", return_value=100.4):
            self.ui.update()
        self.assertFalse(self.ui.dirty)

    def test_local_ai_keyboard_controls(self):
        self.ui.action("search")
        self.ui.draft = "tv"
        self.ui.action("favorite")
        self.assertEqual(self.ui.draft, "tv ")
        self.ui.action("back")
        self.assertEqual(self.ui.draft, "tv")
        self.assertEqual(self.ui.modal, "search")
        self.ui.keyboard_row, self.ui.keyboard_col = 0, 0
        self.ui.action("left")
        self.assertEqual(self.ui.keyboard_col, 0)

    def test_eight_short_channel_rows_and_wider_map(self):
        self.ui.stations[0]["name"] = "12345678901234567890"
        self.ui.draw_browse()
        calls = self.ui.d.text.call_args_list
        self.assertTrue(any(call.args[0] == "123456789012345" and call.args[4] == 13 for call in calls))
        for row in self.ui.rows:
            self.assertTrue(any(call.args[0] == row["name"][:15] for call in calls))
        self.assertEqual(len(self.ui.rows), app.VISIBLE_ROWS)
        self.ui.globe.draw.assert_called_with("", [], self.ui.selected()["uuid"],
                                             viewport=app.MAP_VIEWPORT, bounds=(0, 84, app.MAP_WIDTH, 354))
        self.ui.move_pointer(app.LIST_X+15, app.LIST_Y+app.ROW_HEIGHT*7+10)
        self.assertEqual(self.ui.index, 7)

    def test_catalog_status_is_in_header_and_hd_notice_is_not_a_toast(self):
        self.ui.workers.add("catalog")
        self.assertEqual(self.ui.header_status(), "Loading catalog")
        old = self.ui.notice
        self.ui.playback_notice(0, "HD SOURCE | 1280x720 - lowest available; playback may be slower")
        self.assertEqual(self.ui.notice, old)
        self.assertIn("1280x720", self.ui.playback_notices[0][0])
        self.ui.d.text.reset_mock()
        self.ui.loading((0, 0, 640, 480), self.ui.playback_notices[0])
        self.assertTrue(any(call.args[3] == app.GREY for call in self.ui.d.text.call_args_list))

    def test_quality_menu_switches_a_real_resolution_and_frame_rate(self):
        self.ui.demo, self.ui.view = False, "video"
        station = dict(self.ui.selected(), url="https://tv.test/low.m3u8", source_url="https://tv.test/master.m3u8",
                       renditions=[dict(url="https://tv.test/low.m3u8", height=360, fps=25),
                                   dict(url="https://tv.test/high.m3u8", height=720, fps=50)])
        self.ui.playing[0], self.ui.players[0] = station, Mock()
        old_player = self.ui.players[0]
        self.ui.action("menu")
        self.assertEqual(self.ui.menu_items()[0], "Resolution / frame rate")
        self.ui.action("ok")
        self.assertEqual(self.ui.modal, "quality")
        self.assertEqual([self.ui.quality_label(r) for r in self.ui.quality_rows],
                         ["Auto / up to 480p", "360p / 25 fps", "720p / 50 fps"])
        self.ui.menu_index = 2
        with patch.object(self.ui, "finish_player") as finish:
            self.ui.action("ok")
        old_player.close.assert_called_once()
        changed = finish.call_args.args[1]
        self.assertEqual(changed["url"], "https://tv.test/high.m3u8")
        self.assertEqual(changed["quality_fps"], 50)
        self.assertEqual(changed["source_url"], "https://tv.test/master.m3u8")
        self.assertEqual(self.ui.view, "video")
        self.assertEqual(self.ui.modal, "")

    def test_quality_auto_restores_original_and_back_keeps_playback(self):
        self.ui.demo, self.ui.view = False, "video"
        self.ui.playing[0] = dict(self.ui.selected(), url="https://tv.test/high.m3u8",
                                  source_url="https://tv.test/master.m3u8")
        self.ui.players[0] = Mock()
        self.ui.open_quality()
        self.ui.action("back")
        self.assertEqual(self.ui.modal, "menu")
        self.ui.players[0].close.assert_not_called()
        self.ui.open_quality()
        with patch.object(self.ui, "start_player") as start:
            self.ui.action("ok")
        self.assertEqual(start.call_args.args[1]["url"], "https://tv.test/master.m3u8")

    def test_fullscreen_settings_has_no_outer_dialog_box(self):
        self.ui.modal = "menu"
        self.ui.d.measure.side_effect = lambda text, size: (len(text)*7, size)
        self.ui.draw_modal()
        self.assertIn(((self.ui.d.backdrop, (0, 0, 640, 480)), {}), self.ui.d.copy.call_args_list)
        self.assertFalse(any(call.args[:4] == (49, 49, 542, 382) for call in self.ui.d.rect.call_args_list))

    def test_dpad_adjusts_crt_without_closing_settings(self):
        self.ui.view = "video"
        self.ui.playing[0] = self.ui.selected()
        self.ui.action("menu")
        self.ui.menu_index = 8  # CRT overlay after the playback-quality item.
        self.ui.action("right")
        self.assertTrue(self.ui.state.data["crt"])
        self.assertEqual(self.ui.modal, "menu")
        self.ui.action("down")
        self.ui.action("right")
        self.assertTrue(self.ui.state.data["intense"])
        self.ui.action("left")
        self.assertFalse(self.ui.state.data["intense"])
        self.ui.action("ok")
        self.assertTrue(self.ui.state.data["intense"])
        self.assertEqual(self.ui.modal, "menu")

    def test_globe_resolution_is_adjustable_and_persisted(self):
        self.ui.modal, self.ui.menu_index = "menu", 6
        self.ui.action("right")
        self.assertEqual(self.ui.state.data["globe_resolution"], 256)
        self.assertEqual(State(self.temp.name).data["globe_resolution"], 256)
        self.ui.globe.set_resolution.assert_called_with(256)
        self.ui.action("left")
        self.assertEqual(self.ui.state.data["globe_resolution"], 224)
        self.assertEqual(self.ui.modal, "menu")

    def test_all_globe_resolution_options_cycle_and_persist(self):
        self.ui.modal, self.ui.menu_index = "menu", 6
        observed = set()
        for _ in app.GLOBE_RESOLUTIONS:
            self.ui.action("right")
            value = self.ui.state.data["globe_resolution"]
            observed.add(value)
            self.assertEqual(State(self.temp.name).data["globe_resolution"], value)
        self.assertEqual(observed, set(app.GLOBE_RESOLUTIONS))
        self.assertEqual(self.ui.state.data["globe_resolution"], 224)

    def test_country_info_has_no_background_rectangle(self):
        self.ui.draw_browse()
        self.assertFalse(any(call.args[:4] == (10, 398, app.MAP_WIDTH-20, 40)
                             for call in self.ui.d.rect.call_args_list))

    def test_country_selection_starts_flight_and_finite_name_reveal(self):
        with patch.object(app.time, "monotonic", return_value=100.):
            self.ui.select_country("SY")
        self.ui.globe.fly_to.assert_called_once_with(self.ui.by_country["SY"])
        item = self.ui.channel_reveals[self.ui.rows[0]["uuid"]]
        self.assertEqual(item["reveal"], 0)
        self.ui.d.measure.side_effect = lambda text, size: (len(text)*8, size)
        self.ui.d.text.reset_mock()
        self.ui.draw_browse()
        self.assertTrue(any(call.args[3] == app.GREEN and call.args[0] != item["content"]
                            for call in self.ui.d.text.call_args_list if len(call.args) > 4 and call.args[4] == 13))
        for i in range(1, 31):
            with patch.object(app.time, "monotonic", return_value=100.+i*.06):
                self.ui.update()
        self.assertEqual(item["reveal"], len(item["content"]))
        self.ui.redraw_until = 0
        self.ui.dirty = False
        with patch.object(app.time, "monotonic", return_value=102.):
            self.ui.update()
        self.assertFalse(self.ui.dirty)

    def test_country_reveal_is_strictly_sequential_and_only_tracks_visible_rows(self):
        self.ui.rows = [dict(self.ui.stations[0], uuid=str(i)) for i in range(20)]
        self.ui.index = 0
        with patch.object(app.time, "monotonic", return_value=100.):
            self.ui.start_channel_reveal()
        self.assertEqual(len(self.ui.channel_reveals), app.VISIBLE_ROWS)
        items = list(self.ui.channel_reveals.values())
        self.assertEqual(items[0]["start"], 100.)
        self.assertTrue(all(item["start"] is None for item in items[1:]))
        self.ui.d.text.reset_mock()
        self.ui.draw_browse()
        pending = [call for call in self.ui.d.text.call_args_list if call.args[0] == "..."]
        self.assertEqual(len(pending), app.VISIBLE_ROWS-1)
        started = set()
        for tick in range(1, 101):
            with patch.object(app.time, "monotonic", return_value=100.+tick*.06):
                self.ui.update()
            active = [i for i, item in enumerate(items) if item["start"] is not None and item["reveal"] < len(item["content"])]
            self.assertLessEqual(len(active), 1)
            for i, item in enumerate(items):
                if item["start"] is not None:
                    started.add(i)
                    self.assertTrue(all(prev["reveal"] == len(prev["content"]) for prev in items[:i]))
        self.assertEqual(started, set(range(app.VISIBLE_ROWS)))
        self.assertTrue(all(item["reveal"] == len(item["content"]) for item in items))

    def test_real_startup_does_not_read_or_fetch_station_catalog(self):
        with patch.object(app.Catalog, "load_cached", side_effect=AssertionError("world cache read")) as load, \
                patch.object(app.Catalog, "refresh", side_effect=AssertionError("world download")) as refresh, \
                patch.object(app.Catalog, "country", side_effect=AssertionError("startup country fetch")) as country:
            ui = app.App(Mock(), self.temp.name, demo=False)
            try:
                self.assertFalse(ui.stations)
                self.assertFalse(ui.rows)
                self.assertFalse(ui.workers)
                self.assertGreater(len(ui.country_items()), 100)
                load.assert_not_called()
                refresh.assert_not_called()
                country.assert_not_called()
            finally:
                ui.close()

    def test_only_selected_country_loads_and_stale_results_are_ignored(self):
        self.ui.demo = False
        with patch.object(self.ui, "work") as work:
            self.ui.select_country("SY")
            self.assertEqual(work.call_args.args[0], "country:SY")
            self.assertFalse(self.ui.rows)
            self.ui.select_country("DE")
            self.assertEqual(work.call_args.args[0], "country:DE")
        syria = dict(uuid="sy", name="Syria", url="https://example.org/sy", countryCode="SY")
        germany = dict(uuid="de", name="Germany", url="https://example.org/de", countryCode="DE")
        self.ui.events.put(("country:SY", [syria], ""))
        self.ui.events.put(("country:DE", [germany], ""))
        self.ui.update()
        self.assertEqual(self.ui.stations, [germany])
        self.assertEqual(self.ui.rows, [germany])
        self.assertEqual(set(self.ui.channel_reveals), {"de"})
        self.ui.select_country("")
        self.assertFalse(self.ui.rows)
        self.assertFalse(self.ui.stations)

    def test_search_stays_within_the_selected_country(self):
        self.ui.select_country("SY")
        self.ui.action("search")
        self.ui.draft = "syria"
        self.ui.action("search")
        self.assertEqual(self.ui.country, "SY")
        self.assertTrue(all(row["countryCode"] == "SY" for row in self.ui.rows))
        self.assertFalse(self.ui.channel_reveals)

    def test_world_reset_and_manual_controls_interrupt_country_flight(self):
        self.ui.select_country("SY")
        self.ui.select_country("")
        self.ui.globe.fly_to.assert_called_with(None)
        self.assertFalse(self.ui.channel_reveals)
        for action in ("minus", "plus", "menu", "back"):
            self.ui.modal = ""
            self.ui.globe.cancel_flight.reset_mock()
            self.ui.action(action)
            if action in ("minus", "plus"):
                self.ui.globe.zoom_by.assert_called()
            else:
                self.ui.globe.cancel_flight.assert_called_once()

    def test_world_spins_then_pauses_on_hover_drag_menu_and_country(self):
        g = self.ui.globe
        g.flight, g.trace_active, g.fade_until, g.zoom, g.lon = None, False, 0., .8, -20.
        self.ui.last_input = 0
        self.ui.pointer_x, self.ui.pointer_y = 620., 440.
        for now in (100., 100.1):
            with patch.object(app.time, "monotonic", return_value=now):
                self.ui.update()
        self.assertTrue(self.ui.idle_spinning)
        self.assertGreater(g.lon, -20.)
        position = g.lon
        with patch.object(app.time, "monotonic", return_value=101.):
            self.ui.move_pointer(app.MAP_CENTER_X, app.MAP_CENTER_Y)
            self.ui.update()
        self.assertFalse(self.ui.idle_spinning)
        self.assertEqual(g.lon, position)
        self.ui.pointer_x, self.ui.pointer_y, self.ui.last_input = 620., 440., 0
        for field, value in (("drag_origin", (200, 260)), ("modal", "menu"), ("country", "SY")):
            with self.subTest(field=field):
                setattr(self.ui, field, value)
                with patch.object(app.time, "monotonic", return_value=105.):
                    self.ui.update()
                self.assertFalse(self.ui.idle_spinning)
                setattr(self.ui, field, None if field == "drag_origin" else "")

    def test_idle_rotation_is_time_based_and_border_animation_is_only_a_redraw(self):
        g = self.ui.globe
        g.flight, g.trace_active, g.fade_until, g.zoom = None, False, 0., .8
        positions = []
        for fps in (10, 30):
            g.lon = -20.
            self.ui.last_input, self.ui.idle_spinning = 0., False
            self.ui.pointer_x, self.ui.pointer_y = 620., 440.
            for i in range(fps*3+1):
                with patch.object(app.time, "monotonic", return_value=100.+i/fps):
                    self.ui.update()
            positions.append(g.lon)
        self.assertAlmostEqual(positions[0], positions[1], delta=.3)
        self.ui.country, g.trace_active = "SY", True
        self.ui.redraw_until = 0
        with patch.object(app.time, "monotonic", return_value=105.):
            self.ui.update()
        self.ui.dirty = False
        with patch.object(app.time, "monotonic", return_value=105.1):
            self.ui.update()
        self.assertTrue(self.ui.dirty)
        self.assertFalse(self.ui.idle_spinning)

    def test_playback_country_focus_does_not_start_hidden_animation(self):
        self.ui.select_country("SY", animate=False)
        self.ui.globe.fly_to.assert_not_called()
        self.assertFalse(self.ui.channel_reveals)

    def test_exit_requires_confirmation(self):
        self.ui.action("back")
        self.assertTrue(self.ui.running)
        self.ui.action("ok")
        self.assertTrue(self.ui.running)
        self.ui.action("back")
        self.ui.action("down")
        self.ui.action("ok")
        self.assertFalse(self.ui.running)

    def test_cancel_scan_returns_without_waiting_for_network(self):
        entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
        health = Health(self.temp.name)

        def blocked(station):
            entered.set()
            release.wait(2)
            return True, ""

        with patch.object(health, "_probe", side_effect=blocked):
            thread = threading.Thread(target=health.scan, args=(self.ui.stations,), kwargs={"cancel": cancel})
            thread.start()
            self.assertTrue(entered.wait(1))
            cancel.set()
            thread.join(.5)
            release.set()
            self.assertFalse(thread.is_alive())
            self.assertFalse(health.path.exists())


class RenderTests(unittest.TestCase):
    def test_intro_launch_presents_ui_without_any_input(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "startup.png"
            env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_RENDER_DRIVER="software")
            result = subprocess.run([sys.executable, "-B", str(app.ROOT / "app.py"), "--desktop",
                                     "--demo", "--run-seconds", "3.3", "--capture-after", str(output)],
                                    env=env, capture_output=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue(output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_offline_scenes_and_demo_state_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_RENDER_DRIVER="software")
            for scene in ("browse", "search", "matrix", "settings", "quality", "loading"):
                output = root / (scene + ".png")
                result = subprocess.run([sys.executable, "-B", str(app.ROOT / "app.py"),
                                         "--demo", "--scene", scene, "--data-dir", str(root / "real-data"),
                                         "--screenshot", str(output)], env=env, capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertTrue(output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertGreater(output.stat().st_size, 5000)
            self.assertFalse((root / "real-data").exists())


if __name__ == "__main__":
    unittest.main()
