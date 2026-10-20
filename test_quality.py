"""Offline resolver tests: python3 -B -m unittest -v test_quality."""

import copy
import io
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib import error

import tv_quality as quality


MEDIA = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nsegment.ts\n"


def master(*variants, extra=""):
    return "#EXTM3U\n" + extra + "".join(
        "#EXT-X-STREAM-INF:{}\n{}\n".format(attrs, url) for attrs, url in variants)


class Response(io.BytesIO):
    def __init__(self, url, body=MEDIA, headers=None):
        super().__init__(body.encode() if isinstance(body, str) else body)
        self.url = url
        self.headers = headers or {"Content-Type": "application/vnd.apple.mpegurl"}
        self.read_sizes = []

    def geturl(self):
        return self.url

    def read1(self, size):
        self.read_sizes.append(size)
        return super().read1(size)


class ResolverTests(unittest.TestCase):
    def resolve(self, resources, station=None, **kwargs):
        calls = []

        def open_url(req, timeout):
            calls.append((req, timeout))
            body = resources[req.full_url]
            if isinstance(body, Exception):
                raise body
            if callable(body):
                return body()
            return body if isinstance(body, Response) else Response(req.full_url, body)

        with patch.object(quality.request, "build_opener", return_value=Mock(open=open_url)):
            result = quality.resolve_station(station or {"url": "https://tv.test/master.m3u8"}, **kwargs)
        return result, calls

    def test_lowest_height_before_bitrate_and_h264_tie(self):
        url = "https://tv.test/master.m3u8"
        resources = {url: master(
            ('BANDWIDTH=50000,RESOLUTION=1280x720,CODECS="avc1.4d,mp4a.40.2"', "hd.m3u8"),
            ('BANDWIDTH=400000,RESOLUTION=640x360,CODECS="hev1.1,mp4a.40.2"', "hevc.m3u8"),
            ('BANDWIDTH=400000,RESOLUTION=640x360,CODECS="avc1.4d,mp4a.40.2"', "sd.m3u8")),
            "https://tv.test/sd.m3u8": MEDIA}
        result, calls = self.resolve(resources)
        self.assertEqual(result["url"], "https://tv.test/sd.m3u8")
        self.assertEqual(result["selected_url"], result["url"])
        self.assertEqual((result["quality_width"], result["quality_height"]), (640, 360))
        self.assertEqual(result["quality_warning"], "")
        self.assertEqual(len(calls), 2)

    def test_redirect_base_signed_queries_and_headers(self):
        original = "https://tv.test/start?token=do-not-copy"
        final = "https://cdn.test/live/path/master.m3u8?other=secret"
        chosen = "https://cdn.test/live/sd.m3u8?token=a%2Fb&sig=x+y&arg=1,2"
        station = {"url": original, "user_agent": "Agent/1,2", "referrer": "https://tv.test/watch?a=1"}
        result, calls = self.resolve({original: Response(final, master(
            ("BANDWIDTH=200000,RESOLUTION=320x180", "../sd.m3u8?token=a%2Fb&sig=x+y&arg=1,2"))),
            chosen: MEDIA}, station)
        self.assertEqual(result["url"], chosen)
        self.assertEqual(result["source_url"], original)
        self.assertEqual(result["source_user_agent"], station["user_agent"])
        self.assertEqual(result["source_referrer"], station["referrer"])
        for req, timeout in calls:
            self.assertEqual(req.get_header("User-agent"), station["user_agent"])
            self.assertEqual(req.get_header("Referer"), station["referrer"])
            self.assertLessEqual(timeout, 3)
        self.assertNotIn("do-not-copy", chosen)

    def test_urljoin_does_not_propagate_master_query(self):
        result, _ = self.resolve({
            "https://tv.test/master.m3u8?auth=x": master(("BANDWIDTH=1", "low.m3u8")),
            "https://tv.test/low.m3u8": MEDIA}, {"url": "https://tv.test/master.m3u8?auth=x"})
        self.assertEqual(result["url"], "https://tv.test/low.m3u8")
        self.assertIsNone(result["quality_height"])
        self.assertFalse(result["quality_warning"])

    def test_missing_low_variant_plays_hd_with_warning(self):
        result, calls = self.resolve({"https://tv.test/master.m3u8": master(
            ("BANDWIDTH=1,RESOLUTION=640x360", "low.m3u8"),
            ("BANDWIDTH=2,RESOLUTION=1280x720", "hd.m3u8")),
            "https://tv.test/low.m3u8": error.URLError("offline"),
            "https://tv.test/hd.m3u8": MEDIA})
        self.assertEqual(result["url"], "https://tv.test/hd.m3u8")
        self.assertIn("HD SOURCE", result["quality_warning"])
        self.assertIn("1280x720", result["quality_warning"])
        self.assertEqual(len(calls), 3)

    def test_external_audio_pins_480p_or_lower_even_when_hd_bitrate_is_lower(self):
        result, calls = self.resolve({"https://tv.test/master.m3u8": master(
            ('BANDWIDTH=300000,AVERAGE-BANDWIDTH=100000,RESOLUTION=640x360,AUDIO="aac"', "low.m3u8"),
            ('BANDWIDTH=200000,AVERAGE-BANDWIDTH=400000,RESOLUTION=1280x720,AUDIO="aac"', "hd.m3u8"),
            extra='#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="English, Main",URI="audio.m3u8"\n'),
            "https://tv.test/low.m3u8": MEDIA})
        self.assertEqual(result["url"], "https://tv.test/master.m3u8")
        self.assertEqual(result["selected_variant_url"], "https://tv.test/low.m3u8")
        self.assertEqual(result["quality_height"], 360)
        self.assertIn("https://tv.test/low.m3u8", result["playlist"])
        self.assertNotIn("hd.m3u8", result["playlist"])
        self.assertEqual(len(calls), 2)

    def test_rendition_choices_preserve_frame_rates_and_external_audio(self):
        result, _ = self.resolve({"https://tv.test/master.m3u8": master(
            ('BANDWIDTH=100000,RESOLUTION=640x360,FRAME-RATE=25,AUDIO="aac"', "low.m3u8"),
            ('BANDWIDTH=200000,RESOLUTION=1280x720,FRAME-RATE=50,AUDIO="aac"', "high.m3u8"),
            extra='#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="Main",URI="sound/audio.m3u8?token=x"\n'),
            "https://tv.test/low.m3u8": MEDIA})
        self.assertEqual([(r["height"], r["fps"]) for r in result["renditions"]], [(360, 25), (720, 50)])
        high = result["renditions"][1]
        self.assertIn('URI="https://tv.test/sound/audio.m3u8?token=x"', high["playlist"])
        self.assertIn("https://tv.test/high.m3u8", high["playlist"])
        self.assertNotIn("https://tv.test/low.m3u8", high["playlist"])
        self.assertEqual(high["playlist"].count("#EXT-X-STREAM-INF"), 1)

    def test_auto_reselection_keeps_pinned_audio_and_does_not_invent_fps(self):
        pin = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhttps://tv.test/low.m3u8\n'
        result, _ = self.resolve({"https://tv.test/master.m3u8": MEDIA,
                                  "https://tv.test/low.m3u8": MEDIA},
                                 {"url": "https://tv.test/master.m3u8", "alternatives": [
                                     {"url": "https://tv.test/low.m3u8", "height": 360, "playlist": pin}]})
        self.assertEqual(result["playlist"], pin)
        self.assertIsNone(result["quality_fps"])

    def test_invalid_and_missing_frame_rates_are_not_options(self):
        for fps in ("NaN", "-1", "0", "999", "invalid"):
            rows, _ = quality._variants(master(("BANDWIDTH=1,RESOLUTION=320x180,FRAME-RATE="+fps,
                                                "video.m3u8")), "https://tv.test/", {})
            self.assertIsNone(rows[0]["fps"])

    def test_invalid_low_response_does_not_prevent_fallback(self):
        for body in ("<html>unavailable</html>", "#EXTM3U\n#no streams\n", b"\x47" * 512):
            result, _ = self.resolve({"https://tv.test/master.m3u8": master(
                ("BANDWIDTH=1,RESOLUTION=320x240", "low.m3u8"),
                ("BANDWIDTH=2,RESOLUTION=640x480", "sd.m3u8")),
                "https://tv.test/low.m3u8": body,
                "https://tv.test/sd.m3u8": MEDIA})
            self.assertEqual(result["url"], "https://tv.test/sd.m3u8")
            self.assertFalse(result["quality_warning"])

    def test_external_audio_unavailable_child_has_unknown_quality(self):
        result, _ = self.resolve({"https://tv.test/master.m3u8": master(
            ('BANDWIDTH=1,RESOLUTION=320x240,AUDIO="aac"', "low.m3u8"),
            extra='#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",URI="audio.m3u8"\n'),
            "https://tv.test/low.m3u8": error.URLError("offline")},
            {"url": "https://tv.test/master.m3u8", "height": 1080})
        self.assertEqual(result["url"], "https://tv.test/master.m3u8")
        self.assertIsNone(result["quality_height"])
        self.assertFalse(result["quality_warning"])

    def test_in_band_audio_can_select_variant(self):
        result, _ = self.resolve({"https://tv.test/master.m3u8": master(
            ('BANDWIDTH=2,RESOLUTION=320x240,AUDIO="aac"', "low.m3u8"),
            extra='#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="Main"\n'),
            "https://tv.test/low.m3u8": MEDIA})
        self.assertEqual(result["url"], "https://tv.test/low.m3u8")

    def test_audio_only_variant_is_not_selected(self):
        result, _ = self.resolve({"https://tv.test/master.m3u8": master(
            ('BANDWIDTH=1,CODECS="mp4a.40.2"', "audio.m3u8"),
            ("BANDWIDTH=2,RESOLUTION=320x240", "video.m3u8")),
            "https://tv.test/video.m3u8": MEDIA})
        self.assertEqual(result["quality_height"], 240)

    def test_alternative_hints_headers_and_original_are_preserved(self):
        station = {"url": "https://tv.test/hd.m3u8", "name": "TV", "user_agent": "Original",
                   "alternatives": [
                       {"url": "https://other.test/480.m3u8", "height": 480,
                        "user_agent": "Alternate", "referrer": "https://other.test/"},
                       {"url": "https://other.test/240.m3u8", "height": 240,
                        "user_agent": "Alternate", "referrer": "https://other.test/"}]}
        saved = copy.deepcopy(station)
        result, calls = self.resolve({station["url"]: MEDIA,
                                     "https://other.test/480.m3u8": MEDIA,
                                     "https://other.test/240.m3u8": MEDIA}, station)
        self.assertEqual(calls[0][0].full_url, "https://other.test/480.m3u8")
        self.assertEqual(result["quality_height"], 480)
        self.assertEqual(result["user_agent"], "Alternate")
        self.assertEqual(result["referrer"], "https://other.test/")
        self.assertEqual(result["source_user_agent"], "Original")
        self.assertIn(station["url"], [row["url"] for row in result["alternatives"]])
        self.assertEqual(station, saved)

    def test_auto_prefers_480p_then_360p_instead_of_the_lowest(self):
        source = "https://tv.test/master.m3u8"
        playlist = master(*[("BANDWIDTH=%s,RESOLUTION=%sx%s" % (h*1000, h*16//9, h), "%s.m3u8" % h)
                            for h in (240, 360, 480, 720)])
        resources = {source: playlist, "https://tv.test/480.m3u8": MEDIA}
        result, _ = self.resolve(resources)
        self.assertEqual(result["quality_height"], 480)
        resources.update({"https://tv.test/480.m3u8": error.URLError("unavailable"),
                          "https://tv.test/360.m3u8": MEDIA})
        result, _ = self.resolve(resources)
        self.assertEqual(result["quality_height"], 360)

    def test_auto_uses_smallest_hd_only_fallback_and_matrix_cap(self):
        source = "https://tv.test/master.m3u8"
        playlist = master(("BANDWIDTH=1,RESOLUTION=1920x1080", "1080.m3u8"),
                          ("BANDWIDTH=2,RESOLUTION=1280x720", "720.m3u8"))
        result, _ = self.resolve({source: playlist, "https://tv.test/720.m3u8": MEDIA})
        self.assertEqual(result["quality_height"], 720)
        self.assertTrue(result["quality_warning"])
        result, _ = self.resolve({source: master(("BANDWIDTH=1,RESOLUTION=320x180", "180.m3u8"),
                                                ("BANDWIDTH=2,RESOLUTION=640x360", "360.m3u8")),
                                  "https://tv.test/180.m3u8": MEDIA}, max_height=240)
        self.assertEqual(result["quality_height"], 180)

    def test_at_most_four_unique_source_candidates(self):
        station = {"url": "https://tv.test/master.m3u8", "alternatives": [
            {"url": "https://tv.test/{}.m3u8".format(i), "height": 100 + i} for i in range(8)]}
        resources = {row["url"]: error.URLError("no") for row in station["alternatives"]}
        result, calls = self.resolve(resources, station)
        self.assertEqual(len(calls), 4)
        self.assertEqual(result["url"], station["url"])
        self.assertEqual(result["quality_warning"], "")

    def test_fixed_media_unknown_and_hinted(self):
        url = "https://tv.test/master.m3u8"
        result, calls = self.resolve({url: MEDIA})
        self.assertIsNone(result["quality_height"])
        self.assertFalse(result["quality_warning"])
        self.assertEqual(len(calls), 1)  # Never fetch segment.ts.
        result, _ = self.resolve({url: MEDIA}, {"url": url, "height": 720})
        self.assertIn("720p", result["quality_warning"])

    def test_france24_verified_sibling_first_and_fallback(self):
        original = "https://live.france24.com/hls/live/2037220/F24_EN_HI_HLS/master_5000.m3u8?sig=x%2Fy"
        low = original.replace("master_5000", "master_500")
        result, calls = self.resolve({low: MEDIA}, {"url": original})
        self.assertEqual(result["url"], low)
        self.assertEqual(result["source_url"], original)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(result["quality_height"])
        result, calls = self.resolve({low: error.URLError("no low"), original: MEDIA}, {"url": original})
        self.assertEqual(result["url"], original)
        self.assertEqual([c[0].full_url for c in calls], [low, original])
        result, calls = self.resolve({low: "<html>Unavailable</html>", original: MEDIA}, {"url": original})
        self.assertEqual(result["url"], original)
        for url in (original.replace("live.france24.com", "fake.france24.com"),
                    original.replace("https:", "http:"),
                    original.replace("live.france24.com", "live.france24.com.evil.test")):
            result, calls = self.resolve({url: MEDIA}, {"url": url})
            self.assertEqual([c[0].full_url for c in calls], [url])

    def test_large_direct_and_non_playlist_responses_are_bounded(self):
        url = "https://tv.test/master.m3u8"
        direct = Response(url, b"x" * 1024, {"Content-Type": "video/mp4", "Content-Length": "999999999"})
        result, _ = self.resolve({url: direct})
        self.assertEqual(direct.read_sizes, [])
        self.assertEqual(result["url"], url)
        sniff = Response(url, b"x" * 10000, {"Content-Type": "application/octet-stream"})
        self.resolve({url: sniff})
        self.assertEqual(sniff.read_sizes, [512])
        large = Response(url, MEDIA, {"Content-Length": str(quality.MAX_DOWNLOAD + 1)})
        result, _ = self.resolve({url: large})
        self.assertEqual(large.read_sizes, [])
        self.assertEqual(result["quality_warning"], "")

    def test_combined_download_budget(self):
        url = "https://tv.test/master.m3u8"
        oversized = Response(url, "#EXTM3U\n" + "#comment\n" * 200000)
        result, _ = self.resolve({url: oversized})
        self.assertLessEqual(sum(oversized.read_sizes), quality.MAX_DOWNLOAD)
        self.assertEqual(result["quality_warning"], "")

    def test_cancel_before_and_during_io(self):
        cancel = threading.Event()
        cancel.set()
        result, calls = self.resolve({}, cancel=cancel)
        self.assertFalse(calls)
        self.assertEqual(result["quality_warning"], "")
        cancel.clear()
        started, release = threading.Event(), threading.Event()

        def slow():
            started.set()
            release.wait(1)
            return Response("https://tv.test/master.m3u8")

        timer = threading.Timer(.05, cancel.set)
        timer.start()
        try:
            before = time.monotonic()
            result, calls = self.resolve({"https://tv.test/master.m3u8": slow}, cancel=cancel)
            self.assertTrue(started.is_set())
            self.assertLess(time.monotonic() - before, .5)
            self.assertFalse(result["quality_warning"])
        finally:
            release.set()
            timer.join()

    def test_one_wall_deadline_even_if_io_ignores_timeout(self):
        release = threading.Event()

        def slow():
            release.wait(1)
            return Response("https://tv.test/master.m3u8")

        try:
            with patch.object(quality, "DEADLINE", .08):
                before = time.monotonic()
                result, _ = self.resolve({"https://tv.test/master.m3u8": slow})
                self.assertLess(time.monotonic() - before, .5)
                self.assertFalse(result["quality_warning"])
        finally:
            release.set()

    def test_deadline_preserves_already_resolved_low_source(self):
        release = threading.Event()
        original = "https://tv.test/master.m3u8"
        low = "https://tv.test/low.m3u8"

        def slow():
            release.wait(1)
            return Response(original)

        try:
            with patch.object(quality, "DEADLINE", .08):
                result, _ = self.resolve({original: slow, low: MEDIA}, {
                    "url": original, "alternatives": [{"url": low, "height": 240}]})
                self.assertEqual(result["url"], low)
                self.assertEqual(result["quality_height"], 240)
        finally:
            release.set()

    def test_resolution_warning_boundaries(self):
        self.assertFalse(quality.quality_warning(640, 480))
        self.assertFalse(quality.quality_warning(None, None))
        self.assertIn("HD SOURCE", quality.quality_warning(641, 480))
        self.assertIn("HD SOURCE", quality.quality_warning(640, 481))
        self.assertIn("1280px wide", quality.quality_warning(1280, None))
        self.assertFalse(quality.quality_warning(640, 720, max_height=720))

    def test_redirects_are_bounded_http_only_and_do_not_read_bodies(self):
        original = "https://tv.test/start"
        redirect_body = Mock(wraps=io.BytesIO(b"x" * 1024))
        redirect = error.HTTPError(original, 302, "Found", {"Location": "/live/master.m3u8"}, redirect_body)
        result, calls = self.resolve({original: redirect, "https://tv.test/live/master.m3u8": MEDIA},
                                     {"url": original, "user_agent": "Redirect Agent"})
        self.assertEqual(result["url"], "https://tv.test/live/master.m3u8")
        self.assertEqual(calls[1][0].get_header("User-agent"), "Redirect Agent")
        redirect_body.read.assert_not_called()
        redirect_body.close.assert_called_once()
        bad = error.HTTPError(original, 302, "Found", {"Location": "file:///tmp/x"}, io.BytesIO())
        result, calls = self.resolve({original: bad}, {"url": original})
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["url"], original)

        def loop():
            raise error.HTTPError(original, 302, "Found", {"Location": original}, io.BytesIO())

        result, calls = self.resolve({original: loop}, {"url": original})
        self.assertEqual(len(calls), 5)
        self.assertFalse(result["quality_warning"])

    def test_local_source_needs_no_network(self):
        with patch.object(quality.request, "build_opener") as build:
            result = quality.resolve_station({"url": "/tmp/video.mkv"})
        build.assert_not_called()
        self.assertIsNone(result["quality_height"])


if __name__ == "__main__":
    unittest.main()
