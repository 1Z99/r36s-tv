"""Offline backend tests: python3 -m unittest -v test_backend."""

import io
from http.client import IncompleteRead
import json
import math
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import URLError

import tv_backend as backend


def station(key="one", **extra):
    row = dict(uuid=key, name="News " + key, url="https://example.org/" + key,
               countryCode="DE", country="Germany", language="German",
               tags="news", user_agent="", referrer="", favicon="")
    row.update(extra)
    return row


def polygon(ring, holes=()):
    return {"type": "Feature", "properties": {"name": "Test"},
            "geometry": {"type": "Polygon", "coordinates": [ring] + list(holes)}}


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        backend._atomic_json(self.assets / "countries.json", {"DE": "Germany"})
        backend._atomic_json(self.assets / "languages.json", [{"code": "deu", "name": "German"}])

    def catalog(self):
        return backend.Catalog(self.root, self.assets)

    def test_catalog_join_cache_dedup(self):
        catalog = self.catalog()
        self.assertEqual(catalog.load_cached(), [])
        channels = [dict(id="news.de", name="News", country="DE", languages=["deu"], categories=["news"], logo="logo")]
        streams = [dict(channel="news.de", url="https://example.org/live", user_agent="agent", referrer="https://example.org"),
                   dict(channel="news.de", url="https://example.org/duplicate"),
                   dict(channel="bad", url="file:///etc/passwd")]
        with patch.object(backend, "_download", side_effect=[json.dumps(channels), json.dumps(streams)]) as download:
            rows = catalog.refresh()
        self.assertEqual(download.call_count, 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), set(backend.STATION_FIELDS))
        self.assertEqual(rows[0]["language"], "German")
        self.assertEqual(rows[0]["country"], "Germany")
        self.assertEqual(rows[0]["user_agent"], "agent")
        self.assertEqual(catalog.load_cached(), rows)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_m3u_fallback_and_failed_refresh_preserves_cache(self):
        catalog = self.catalog()
        playlist = b'#EXTM3U\n#EXTINF:-1 tvg-id="x",Example\n#EXTVLCOPT:http-user-agent=TV\nhttps://example.org/live\n'
        with patch.object(backend, "_download", side_effect=[b"[]", URLError("offline"), playlist]):
            rows = catalog.refresh()
        self.assertEqual(rows[0]["uuid"], "x")
        self.assertEqual(rows[0]["user_agent"], "TV")
        with patch.object(backend, "_download", side_effect=[b"[]", b"[]"]):
            with self.assertRaises(ValueError):
                catalog.refresh()
        self.assertEqual(catalog.load_cached(), rows)
        with patch.object(backend, "_download", side_effect=URLError("offline")):
            with self.assertRaises(URLError):
                catalog.refresh()
        self.assertEqual(catalog.load_cached(), rows)

    def test_country_load_uses_only_country_playlist_and_reuses_its_cache(self):
        catalog = self.catalog()
        playlist = b'#EXTM3U\n#EXTINF:-1 tvg-id="news.de@SD" tvg-logo="https://example.org/logo.png" tvg-language="deu" group-title="News",News DE (720p)\n#EXTVLCOPT:http-user-agent=TV\nhttps://example.org/live\n'
        with patch.object(backend, "_download", return_value=playlist) as download:
            rows = catalog.country("DE")
            self.assertEqual(download.call_args.args, (backend.COUNTRY_URL.format("de"),))
            self.assertEqual(rows[0]["countryCode"], "DE")
            self.assertEqual(rows[0]["name"], "News DE")
            self.assertEqual(rows[0]["language"], "German")
            self.assertEqual(rows[0]["tags"], "news")
            self.assertEqual(rows[0]["user_agent"], "TV")
            self.assertEqual(rows[0]["height"], 720)
            self.assertEqual(rows, catalog.country("DE"))
            download.assert_called_once()
        self.assertFalse(catalog.path.exists())
        self.assertTrue((self.root / "countries/DE.json").is_file())

    def test_country_cache_fallback_and_code_validation(self):
        catalog = self.catalog()
        playlist = b'#EXTM3U\n#EXTINF:-1 tvg-id="one.de@HD",One\nhttps://example.org/live\n'
        with patch.object(backend, "_download", return_value=playlist):
            rows = catalog.country("DE")
        with patch.object(backend, "_download", side_effect=URLError("offline")):
            self.assertEqual(catalog.country("DE", refresh=True), rows)
        with patch.object(backend, "_download") as download:
            for code in ("../../etc", "D", "ZZ", "DE/other"):
                with self.assertRaises(ValueError):
                    catalog.country(code)
            download.assert_not_called()

    def test_country_without_playlist_has_an_empty_country_cache(self):
        catalog = self.catalog()
        with patch.object(backend, "_download", side_effect=backend.error.HTTPError(
                backend.COUNTRY_URL.format("de"), 404, "Not found", {}, None)):
            self.assertEqual(catalog.country("DE"), [])
        with patch.object(backend, "_download") as download:
            self.assertEqual(catalog.country("DE"), [])
            download.assert_not_called()

    def test_language_enrichment_without_optional_asset(self):
        # countries.json is the only required naming asset.
        with patch.object(backend, "_names", side_effect=[{"DE": "Germany"}, {}]):
            catalog = self.catalog()
        with patch.object(backend, "_download", side_effect=[
                b'[{"id":"one", "name":"One", "country":"DE", "languages":["deu"]}]',
                b'[{"channel":"one", "url":"https://example.org/live"}]']):
            rows = catalog.refresh()
        self.assertEqual(len(backend.filter_stations(rows, "german germany")), 1)

    def test_current_feed_schema_preserves_languages_and_variants(self):
        channels = [{"id": "news.de", "name": "News", "country": "DE"}]
        streams = [{"channel": "news.de", "feed": "DE", "url": "https://example.org/de"},
                   {"channel": "news.de", "feed": "EN", "url": "https://example.org/en"},
                   {"channel": "news.de", "feed": "EN", "url": "https://example.org/en-alt"}]
        feeds = [{"channel": "news.de", "id": "DE", "name": "German", "is_main": True, "languages": ["deu"]},
                 {"channel": "news.de", "id": "EN", "name": "English", "is_main": False, "languages": ["eng"]}]
        with patch.object(backend, "_download", side_effect=list(map(json.dumps, (channels, streams, feeds)))):
            rows = self.catalog().refresh()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["uuid"] for row in rows}, {"news.de", "news.de::EN"})
        english = backend.filter_stations(rows, "english")[0]
        self.assertEqual(english["url"], "https://example.org/en")
        self.assertEqual(english["name"], "News (English)")

    def test_bounded_https_download(self):
        class Response(io.BytesIO):
            headers = {}
        with patch.object(backend.request, "build_opener") as opener:
            opener.return_value.open.return_value = Response(b"12345")
            with self.assertRaises(ValueError):
                backend._download("https://example.org", maximum=4)
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 10)
        with self.assertRaises(ValueError):
            backend._download("http://example.org")
        with self.assertRaises(ValueError):
            backend._HTTPSRedirect().redirect_request(None, None, 302, "", {}, "http://example.org")
        with patch.object(backend.time, "monotonic", side_effect=[0, 11]):
            with patch.object(backend.request, "build_opener") as opener:
                opener.return_value.open.return_value = Response(b"data")
                with self.assertRaises(TimeoutError):
                    backend._download("https://example.org")
        with patch.object(backend.request, "build_opener") as opener:
            response = Response(b"")
            response.headers = {"Content-Length": str(backend.MAX_DOWNLOAD + 1)}
            opener.return_value.open.return_value = response
            with self.assertRaises(ValueError):
                backend._download("https://example.org")

    def test_favorites_and_history_persist(self):
        state = backend.State(self.root)
        self.assertTrue(state.favorite(station()))
        self.assertIn("one", backend.State(self.root).data["favorites"])
        self.assertFalse(state.favorite(station()))
        self.assertEqual(backend.State(self.root).data["favorites"], {})
        for i in range(backend.RECENT_LIMIT + 5):
            state.played(station(str(i)))
        state.played(station("20", name="Updated"))
        loaded = backend.State(self.root)
        self.assertEqual(len(loaded.data["recent"]), backend.RECENT_LIMIT)
        self.assertEqual(loaded.data["recent"][0]["name"], "Updated")
        self.assertEqual(sum(row["uuid"] == "20" for row in loaded.data["recent"]), 1)

    def test_invalid_state_never_overwritten(self):
        for value in ([], {"favorites": []}, {"recent": ["bad"]}, {"volume": 101}, {"crt": "yes"},
                      *({"globe_rotation_speed": speed} for speed in (-1, 3, True, "fast", None))):
            with self.subTest(value=value):
                backend._atomic_json(self.root / "state.json", value)
                before = (self.root / "state.json").read_bytes()
                with self.assertRaises(ValueError):
                    backend.State(self.root)
                self.assertEqual((self.root / "state.json").read_bytes(), before)
        with patch.object(backend.json, "load", side_effect=json.JSONDecodeError("bad", "{", 1)):
            with self.assertRaises(ValueError):
                backend.State(self.root)
        for value in ([], {"one": "bad"}, {"one": {"status": "dead"}}):
            backend._atomic_json(self.root / "health.json", value)
            before = (self.root / "health.json").read_bytes()
            with self.assertRaises(ValueError):
                backend.Health(self.root)
            self.assertEqual((self.root / "health.json").read_bytes(), before)

    def test_existing_state_defaults_to_original_rotation_speed(self):
        backend._atomic_json(self.root / "state.json", {"volume": 42})
        state = backend.State(self.root)
        self.assertEqual(state.data["globe_rotation_speed"], 1)
        self.assertEqual(state.data["volume"], 42)

    def test_atomic_write_failure_preserves_file(self):
        state = backend.State(self.root)
        state.save()
        before = state.path.read_bytes()
        with patch.object(backend.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                state.favorite(station())
        self.assertEqual(state.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_filter_tabs_search_and_snapshots(self):
        one, two, removed = station(), station("two"), station("removed")
        state = {"favorites": {"one": one, "removed": removed}, "recent": [two, one]}
        health = {"two": {"status": "dead"}}
        rows = [one, two]
        self.assertEqual(backend.filter_stations(rows, "germany german", country="de", health=health), [one])
        self.assertEqual(backend.filter_stations(rows, "missing"), [])
        self.assertEqual(backend.filter_stations(rows, health=health), [one])
        self.assertEqual(backend.filter_stations(rows, tab="Favorites", state=state), [one, removed])
        self.assertEqual(backend.filter_stations(rows, tab="Recent", state=state, health=health), [two, one])

    def test_health_lifecycle_and_fair_limit(self):
        health = backend.Health(self.root)
        with patch.object(backend.Health, "_probe", return_value=(False, "offline")):
            health.scan([station()], limit=1)
        first = health.data["one"]["deadSince"]
        with patch.object(backend.Health, "_probe", return_value=(False, "offline")):
            health.scan([station()])
        self.assertEqual(health.data["one"]["deadSince"], first)
        with patch.object(backend.Health, "_probe", return_value=(True, "")) as probe:
            health.scan([station(), station("two"), station("two")], limit=1)
            self.assertEqual(probe.call_args.args[0]["uuid"], "two")
            health.scan([station()])
            health.scan([station()])
        row = backend.Health(self.root).data["one"]
        self.assertEqual(row["status"], "alive")
        self.assertIsNone(row["deadSince"])
        self.assertTrue(row["resurrected"])
        self.assertIn("resurrectedAt", row)
        with patch.object(backend.Health, "_probe") as probe:
            health.scan([station()], limit=0)
            probe.assert_not_called()

    def test_health_concurrency_is_bounded(self):
        lock = threading.Lock()
        active, maximum = 0, 0

        def probe(row):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return True, ""

        with patch.object(backend.Health, "_probe", side_effect=probe) as mocked:
            backend.Health(self.root).scan([station(str(i)) for i in range(20)])
        self.assertEqual(mocked.call_count, 12)
        self.assertLessEqual(maximum, 4)
        self.assertGreater(maximum, 1)

    def test_probe_range_headers_timeout_and_errors(self):
        class Response(io.BytesIO):
            status = 206
        with patch.object(backend.request, "urlopen", return_value=Response(b"stream")) as urlopen:
            alive, _ = backend.Health._probe(station(user_agent="agent", referrer="https://ref.example"))
        self.assertTrue(alive)
        req = urlopen.call_args.args[0]
        self.assertEqual(req.get_header("Range"), "bytes=0-1023")
        self.assertEqual(req.get_header("User-agent"), "agent")
        self.assertEqual(req.get_header("Referer"), "https://ref.example")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 5)
        with patch.object(backend.request, "urlopen", side_effect=URLError("offline")):
            self.assertFalse(backend.Health._probe(station())[0])
        with patch.object(backend.request, "urlopen", return_value=Response(b"")):
            self.assertFalse(backend.Health._probe(station())[0])
        with patch.object(backend.request, "urlopen", side_effect=IncompleteRead(b"")):
            self.assertFalse(backend.Health._probe(station())[0])
        self.assertFalse(backend.Health._probe(station(url="file:///etc/passwd"))[0])
        self.assertFalse(backend.Health._probe(station(url="https://[invalid"))[0])

    def test_projection_roundtrip(self):
        for center in ((0, 0), (45, 175), (-70, -90), (90, 30)):
            for lat, lon in ((0, 0), (50, 170), (-80, -120), (90, 30)):
                x, y, z = backend.project(lat, lon, *center)
                self.assertAlmostEqual(x*x + y*y + z*z, 1)
                if z > 1e-8:
                    actual = backend.unproject(x, y, *center)
                    self.assertAlmostEqual(actual[0], lat)
                    if abs(lat) < 90:
                        self.assertAlmostEqual((actual[1] - lon + 180) % 360 - 180, 0)
        self.assertIsNone(backend.unproject(2, 0, 0, 0))
        self.assertIsNone(backend.unproject(math.nan, 0, 0, 0))
        self.assertEqual(backend.unproject(0, 0, 0, 0), (0, 0))

    def test_geometry_holes_largest_polygon_determinism(self):
        outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
        self.assertEqual(backend.country_center(polygon(outer)), (5, 5))
        hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
        feature = polygon(outer, [hole])
        self.assertIsNone(backend.country_at([feature], 5, 5))
        self.assertEqual(backend.country_at({"features": [feature]}, 2, 2), feature["properties"])
        self.assertIsNone(backend.country_at([feature], 20, 20))
        for key in map(str, range(30)):
            lat, lon = backend.station_location(feature, key)
            self.assertEqual((lat, lon), backend.station_location(feature, key))
            self.assertIsNotNone(backend.country_at([feature], lat, lon))
        self.assertIsNotNone(backend.country_at([feature], *backend.country_center(feature)))
        feature["geometry"] = {"type": "MultiPolygon", "coordinates": [
            [[[20, 20], [21, 20], [21, 21], [20, 21], [20, 20]]], [outer, hole]]}
        lat, lon = backend.station_location(feature, "one")
        self.assertTrue(0 <= lat <= 10 and 0 <= lon <= 10)

    def test_dateline_and_concave_geometry(self):
        feature = polygon([[170, -10], [-170, -10], [-170, 10], [170, 10], [170, -10]])
        self.assertIsNotNone(backend.country_at([feature], 0, 179))
        self.assertIsNotNone(backend.country_at([feature], 0, -179))
        self.assertIsNone(backend.country_at([feature], 0, 0))
        lat, lon = backend.station_location(feature, "date")
        self.assertGreaterEqual(abs(lon), 170)
        self.assertIsNotNone(backend.country_at([feature], lat, lon))
        concave = polygon([[0, 0], [5, 0], [5, 1], [1, 1], [1, 5], [0, 5], [0, 0]])
        self.assertIsNotNone(backend.country_at([concave], *backend.country_center(concave)))
        with self.assertRaises(ValueError):
            backend.station_location({"geometry": None}, "empty")


if __name__ == "__main__":
    unittest.main()
