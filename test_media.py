"""Run with python3 -B -m unittest -v test_media (ffmpeg integration is optional)."""

import ctypes as C
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import tv_media as media
from tv_quality import resolve_station


class DiagnosticsTests(unittest.TestCase):
    def test_library_soname_fallback(self):
        media._library.cache_clear()
        library = Mock()
        try:
            with patch.object(media.C, "CDLL", side_effect=[OSError("no .2"), library]) as load:
                with patch.object(media.ctypes.util, "find_library", return_value="libmpv.so.1"):
                    self.assertIs(media._library(), library)
            self.assertEqual([call.args[0] for call in load.call_args_list],
                             ["libmpv.so.2", "libmpv.so.1"])
            self.assertIs(library.mpv_create.restype, C.c_void_p)
            self.assertIs(library.mpv_render_context_update.restype, C.c_uint64)
        finally:
            media._library.cache_clear()

    def test_missing_library(self):
        media._library.cache_clear()
        try:
            with patch.object(media.C, "CDLL", side_effect=OSError("not installed")):
                ok, reason = media.availability()
            self.assertFalse(ok)
            self.assertIn("libmpv unavailable", reason)
        finally:
            media._library.cache_clear()

    def test_bad_dimensions(self):
        for dimensions in ((0, 480), (640, -1), (1.5, 2), (True, 2)):
            with self.assertRaises(ValueError):
                media.Player(*dimensions)


class PlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = patch.dict(os.environ, {"TV_MEDIA_AO": "null"})
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        ok, reason = media.availability()
        if not ok:
            raise unittest.SkipTest(reason)
        if not shutil.which("ffmpeg"):
            raise unittest.SkipTest("ffmpeg unavailable for generated test video")
        cls.temp = tempfile.TemporaryDirectory(prefix="tv-media-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.video = str(Path(cls.temp.name) / "test.mkv")
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=12",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "4", "-c:v", "mpeg4", "-threads", "2", "-c:a", "pcm_s16le",
            cls.video,
        ], check=True, timeout=30)

    def test_unsupported_renderer_cleans_up(self):
        lib = media._library()
        for operation, expected in (("mpv_render_context_create", "api_type=sw"),
                                    ("mpv_render_context_render", "rgba")):
            with self.subTest(operation=operation):
                with patch.object(lib, operation, return_value=-19), patch.object(
                        lib, "mpv_terminate_destroy", wraps=lib.mpv_terminate_destroy) as destroy:
                    ok, reason = media.availability()
                self.assertFalse(ok)
                self.assertIn(expected, reason)
                destroy.assert_called_once()

    def player(self, **kwargs):
        player = media.Player(**kwargs)
        self.addCleanup(player.close)
        return player

    def wait(self, player, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = player.poll()
            frame = player.frame()
            if predicate(status, frame):
                return status, frame
            time.sleep(0.005)
        self.fail("Timed out waiting for playback: {!r}".format(player.poll()))

    def test_frames_pause_volume_audio_and_close(self):
        # Deliberately non-64-byte pitch: no implicit SDL row padding is allowed.
        player = self.player(width=97, height=65, audible=False)
        self.assertIsNone(player.frame())
        player.play({"url": self.video})
        status, frame = self.wait(player, lambda s, f: f is not None and s["playing"])
        self.assertEqual(status["error"], "")
        self.assertIsInstance(frame, C.Array)
        self.assertEqual(len(frame), 97 * 65 * 4)
        self.assertEqual((player.width, player.height), (97, 65))
        self.wait(player, lambda s, f: s["source_width"] == 96 and s["source_height"] == 64)
        self.assertEqual((player.source_width, player.source_height), (96, 64))
        self.assertEqual(player.quality_warning, "")
        self.assertIsInstance(player.poll()["dropped_frames"], int)
        self.assertIsInstance(player.poll()["decoder_dropped_frames"], int)
        pixels = bytes(frame)
        self.assertEqual(pixels[(32 * 97 + 48) * 4 + 3], 255)
        self.assertLessEqual(set(pixels[3::4]), {0, 255})
        self.assertGreater(len(set(pixels[0::4])), 10)
        self.assertFalse(status["audio_active"])
        _, next_frame = self.wait(player, lambda s, f: f is not None and bytes(f) != pixels)
        self.assertIs(frame, next_frame)
        player.pause()
        self.wait(player, lambda s, f: s["paused"])
        paused_time = player.poll()["time"]
        time.sleep(0.15)
        self.assertAlmostEqual(player.poll()["time"], paused_time, delta=0.05)
        player.set_volume(180)
        self.assertEqual(player.poll()["volume"], 100)
        player.set_volume(-1)
        self.assertEqual(player.poll()["volume"], 0)
        player.set_audible(True)
        player.pause()
        self.wait(player, lambda s, f: s["audio_active"] and not s["paused"]
                  and s["time"] > paused_time + 0.1)
        self.assertTrue(player.poll()["audible"])
        player.set_audible(False)
        self.wait(player, lambda s, f: not s["audio_active"] and f is not None)
        player.close()
        player.close()
        self.assertIsNone(player.frame())
        self.assertTrue(player.poll()["closed"])
        self.assertFalse(player.poll()["playing"])
        with self.assertRaises(media.MediaError):
            player.pause()

    def test_failure_and_recovery(self):
        player = self.player(width=96, height=64)
        player.play({"url": str(Path(self.temp.name) / "missing.mkv")})
        status, _ = self.wait(player, lambda s, f: bool(s["error"]))
        self.assertFalse(status["buffering"])
        self.assertTrue(status["ended"])
        player.play({"url": self.video})
        status, _ = self.wait(player, lambda s, f: s["playing"] and f is not None)
        self.assertEqual(status["error"], "")

    def test_nonblocking_render_parameter(self):
        player = self.player(audible=False)
        params = {param.type: param.data for param in player._params if param.type}
        self.assertIn(12, params)
        self.assertEqual(C.cast(params[12], C.POINTER(C.c_int)).contents.value, 0)
        self.assertEqual(len(player._buffer), 640 * 480 * 4)

    def test_hd_source_warning_and_reset_without_changing_surface(self):
        hd = str(Path(self.temp.name) / "hd.mkv")
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
            "-i", "color=blue:size=1280x720:rate=12", "-t", "2",
            "-c:v", "mpeg4", "-threads", "2", hd,
        ], check=True, timeout=30)
        player = self.player(audible=False)
        player.play({"url": hd})
        self.assertEqual(player.quality_warning, "")
        status, frame = self.wait(player, lambda s, f: s["source_height"] == 720 and f is not None)
        self.assertEqual(status["source_width"], 1280)
        self.assertIn("HD SOURCE", status["quality_warning"])
        self.assertEqual(status["error"], "")
        self.assertEqual((player.width, player.height), (640, 480))
        self.assertEqual(len(frame), 640 * 480 * 4)
        player.play({"url": self.video})
        self.assertIsNone(player.source_height)
        self.assertEqual(player.quality_warning, "")
        status, _ = self.wait(player, lambda s, f: s["source_height"] == 64)
        self.assertEqual(status["quality_warning"], "")

    def test_resolver_warning_is_replaced_by_actual_dimensions(self):
        player = self.player(audible=False)
        player.play({"url": self.video, "quality_warning": "HD SOURCE | declared 720p"})
        self.assertIn("HD SOURCE", player.quality_warning)
        status, _ = self.wait(player, lambda s, f: s["source_height"] == 64)
        self.assertEqual(status["quality_warning"], "")

    def test_network_failure(self):
        # Bind a local TCP port without listening: connections deterministically fail.
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            player = self.player(width=96, height=64, audible=False)
            player.play({"url": "http://127.0.0.1:{}/broken.m3u8".format(sock.getsockname()[1])})
            status, _ = self.wait(player, lambda s, f: bool(s["error"]))
        self.assertFalse(status["buffering"])
        self.assertTrue(status["ended"])

    def test_http_hls_prefers_low_bitrate(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        # Two independently colored variants make selection observable in RGBA.
        resources = {}
        for name, color, size, rate in (("low", "red", "320x180", 12), ("high", "blue", "1280x720", 24)):
            segment = str(Path(self.temp.name) / (name + ".ts"))
            subprocess.run([
                "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                "-i", "color={}:size={}:rate={}".format(color, size, rate), "-t", "2",
                "-c:v", "mpeg2video", "-threads", "2", "-f", "mpegts", segment,
            ], check=True, timeout=30)
            resources["/" + name + ".ts"] = Path(segment).read_bytes()
            resources["/" + name + ".m3u8"] = (
                "#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n"
                "#EXTINF:2,\n{}.ts\n#EXT-X-ENDLIST\n".format(name)).encode()
        resources["/master.m3u8"] = (
            b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100000\nlow.m3u8\n"
            b"#EXT-X-STREAM-INF:BANDWIDTH=1000000\nhigh.m3u8\n")
        # Deliberately reverse bitrate order: only source resolution selection
        # (not mpv's bitrate setting or its output scaler) can choose SD here.
        resources["/folder/resolution.m3u8"] = (
            b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=320x180\n../low.m3u8\n"
            b"#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=1280x720\n../high.m3u8\n")
        resources["/separate.m3u8"] = (
            b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="Main",DEFAULT=YES,URI="audio.m3u8"\n'
            b'#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=320x180,FRAME-RATE=12,AUDIO="aac"\nlow.m3u8\n'
            b'#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720,FRAME-RATE=24,AUDIO="aac"\nhigh.m3u8\n')
        audio = Path(self.temp.name) / "tone.aac"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "sine=frequency=440:sample_rate=48000", "-t", "2", "-c:a", "aac", str(audio)],
                       check=True, timeout=15)
        resources["/tone.aac"] = audio.read_bytes()
        resources["/audio.m3u8"] = b"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\ntone.aac\n#EXT-X-ENDLIST\n"
        requested = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requested.append(self.path)
                if (self.headers.get("User-Agent") != "TV-media-test/1.0"
                        or self.headers.get("Referer") != "https://example.test/player"):
                    self.send_error(403)
                    return
                if self.path == "/resolution.m3u8":
                    self.send_response(302)
                    self.send_header("Location", "/folder/resolution.m3u8")
                    self.end_headers()
                    return
                body = resources.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl"
                                 if self.path.endswith("m3u8") else "video/mp2t")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            player = self.player(width=96, height=64, audible=False)
            player.play({"url": "http://127.0.0.1:{}/master.m3u8".format(server.server_port),
                         "user_agent": "TV-media-test/1.0",
                         "referrer": "https://example.test/player"})
            status, frame = self.wait(player, lambda s, f: s["playing"] and f is not None
                                      and f[(32 * 96 + 48) * 4] > 200)
            self.assertEqual(status["error"], "")
            self.assertLess(frame[(32 * 96 + 48) * 4 + 2], 30)
            player.close()
            requested.clear()  # Raw-master mpv probing above may read both variants.
            selected = resolve_station({
                "url": "http://127.0.0.1:{}/resolution.m3u8".format(server.server_port),
                "user_agent": "TV-media-test/1.0", "referrer": "https://example.test/player"})
            self.assertTrue(selected["url"].endswith("/low.m3u8"))
            player = self.player(audible=False)
            player.play(selected)
            status, frame = self.wait(player, lambda s, f: s["source_height"] == 180 and f is not None)
            self.assertEqual(status["source_width"], 320)
            self.assertFalse(status["quality_warning"])
            self.assertEqual(len(frame), 640 * 480 * 4)
            self.assertGreater(frame[(240 * 640 + 320) * 4], 200)
            self.assertNotIn("/high.ts", requested)
            self.assertNotIn("/high.m3u8", requested)
            player.close()
            selected = resolve_station({
                "url": "http://127.0.0.1:{}/separate.m3u8".format(server.server_port),
                "user_agent": "TV-media-test/1.0", "referrer": "https://example.test/player"})
            self.assertEqual([(r["height"], r["fps"]) for r in selected["renditions"]], [(180, 12), (720, 24)])
            pinned = dict(selected, **selected["renditions"][1])
            player = self.player(width=96, height=64, audible=True)
            player.play(pinned)
            status, frame = self.wait(player, lambda s, f: s["source_height"] == 720 and f is not None
                                      and s["audio_active"] and s["source_fps"] == 24)
            self.assertEqual(status["error"], "")
            self.assertGreater(frame[(32 * 96 + 48) * 4+2], 200)
            self.assertIn("/tone.aac", requested)
            private = Path(player._playlists.name)
            self.assertTrue(private.exists())
            player.close()
            self.assertFalse(private.exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_eof_and_validation(self):
        player = self.player(width=96, height=64, audible=False)
        for station in ({}, {"url": ""}, {"url": "bad\0url"}, {"url": "av://lavfi:testsrc"},
                        {"url": self.video, "user_agent": "invalid\r\nheader"}):
            with self.assertRaises(ValueError):
                player.play(station)
        player.play({"url": self.video})
        status, _ = self.wait(player, lambda s, f: s["ended"], timeout=7)
        self.assertEqual(status["error"], "")
        self.assertFalse(status["buffering"])
        self.assertFalse(status["playing"])


if __name__ == "__main__":
    unittest.main()
