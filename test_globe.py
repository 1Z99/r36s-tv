#!/usr/bin/env python3
"""Atlas/SDL regression tests; --benchmark runs the same software path on R36S.

python3 -B test_globe.py --benchmark --frames 120 --screenshot /tmp/globe.png
Runtime packaging needs tv_globe.py and assets/globe-atlas.z. The generator
and this test runner are development tools, not runtime dependencies.
"""

import argparse
import ctypes as C
import json
import math
import os
from pathlib import Path
import statistics
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from generate_globe import encode_atlas, rasterize
from tv_globe import Color, Globe, Point, Rect, load_atlas

ROOT = Path(__file__).resolve().parent


def feature(code, rings, multi=False):
    return {"properties": {"code": code}, "geometry": {
        "type": "MultiPolygon" if multi else "Polygon", "coordinates": rings}}


class AtlasTests(unittest.TestCase):
    def sample(self, atlas, lat, lon):
        w, h, codes, ids, _ = atlas
        return codes[ids[min(h-1, int((90-lat)*h/180))*w+int((lon+180)*w/360) % w]]

    def test_islands_holes_and_deterministic_ids(self):
        outer = [[-30, -20], [30, -20], [30, 20], [-30, 20], [-30, -20]]
        hole = [[-10, -10], [-10, 10], [10, 10], [10, -10], [-10, -10]]
        island = [[70, -5], [80, -5], [80, 5], [70, 5], [70, -5]]
        tiny = [[1, 1], [4, 1], [4, 4], [1, 4], [1, 1]]
        features = [feature("ZZ", [[outer, hole], [island]], True), feature("AA", [tiny])]
        atlas = rasterize(features, 360, 180, grid=False)
        self.assertEqual(self.sample(atlas, 15, 0), "ZZ")
        self.assertEqual(self.sample(atlas, 0, 0), "")
        self.assertEqual(self.sample(atlas, 2, 2), "AA")
        self.assertEqual(self.sample(atlas, 0, 75), "ZZ")
        self.assertEqual(self.sample(atlas, 0, 60), "")
        self.assertEqual(encode_atlas(atlas), encode_atlas(rasterize(features[::-1], 360, 180, False)))

    def test_dateline_with_crossing_hole(self):
        outer = [[170, -15], [-170, -15], [-170, 15], [170, 15], [170, -15]]
        hole = [[175, -5], [-175, -5], [-175, 5], [175, 5], [175, -5]]
        atlas = rasterize([feature("DL", [outer, hole])], 360, 180, False)
        for lon in (-179, 179):
            self.assertEqual(self.sample(atlas, 10, lon), "DL")
            self.assertEqual(self.sample(atlas, 0, lon), "")
        self.assertEqual(self.sample(atlas, 0, 172), "DL")
        self.assertEqual(self.sample(atlas, 10, 0), "")
        # Longitude neighbors wrap for borders, rather than outlining the seam.
        w, _, _, _, styles = atlas
        self.assertEqual(styles[80*w], styles[80*w+w-1])

    def test_atlas_marks_only_one_side_of_shared_borders(self):
        west = [[-20, -10], [0, -10], [0, 10], [-20, 10], [-20, -10]]
        east = [[0, -10], [20, -10], [20, 10], [0, 10], [0, -10]]
        w, _, _, _, styles = rasterize([feature("AA", [west]), feature("BB", [east])], 360, 180, False)
        self.assertEqual(styles[90*w+179], 1)
        self.assertEqual(styles[90*w+180], 2)
        self.assertEqual(styles[90*w+159], 0)
        self.assertEqual(styles[90*w+160], 2)

    def test_polar_cap(self):
        ring = [[lon, -80] for lon in (-180, -90, 0, 90, 180, -180)]
        atlas = rasterize([feature("SP", [ring])], 360, 180, False)
        for lon in (-179, -90, 0, 90, 179):
            self.assertEqual(self.sample(atlas, -89, lon), "SP")
            self.assertEqual(self.sample(atlas, -70, lon), "")

    def test_generated_asset_roundtrip_and_real_geography(self):
        features = json.loads((ROOT / "assets/globe.json").read_text())["features"]
        atlas = load_atlas(ROOT / "assets/globe-atlas.z")
        self.assertEqual(encode_atlas(rasterize(features)), (ROOT / "assets/globe-atlas.z").read_bytes())
        for code, lat, lon in (("SY", 35, 38), ("DE", 51, 10), ("BR", -10, -55),
                               ("AU", -25, 135), ("AQ", -89, 30), ("", 0, -140)):
            self.assertEqual(self.sample(atlas, lat, lon), code)

    def test_invalid_atlas_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.z"
            import zlib
            path.write_bytes(zlib.compress(struct.pack(">4sHHH", b"GAT1", 8, 4, 4)+b'[""]'+b'\xff'*64))
            with self.assertRaises(ValueError):
                load_atlas(path)


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ["SDL_RENDER_DRIVER"] = "software"
        import app
        cls.display = app.Display(desktop=True)

    @classmethod
    def tearDownClass(cls):
        cls.display.close()

    def setUp(self):
        fades = patch("tv_globe.FADE_SECONDS", 0.)
        fades.start()
        self.addCleanup(fades.stop)
        self.globe = Globe(self.display, [])
        self.native_project = self.globe.scaler.project
        projection = patch.object(self.globe.scaler, "project", return_value=None)
        projection.start()
        self.addCleanup(projection.stop)
        self.addCleanup(self.globe.close)

    def pixels(self):
        buffer = (C.c_ubyte * (640*480*4))()
        self.assertEqual(self.display.sdl.SDL_RenderReadPixels(
            self.display.renderer, None, 0x16762004, buffer, 640*4), 0)
        return bytes(buffer)

    def test_ctypes_layout(self):
        self.assertEqual(C.sizeof(Color), 4)
        self.assertEqual(C.sizeof(Point), 8)
        self.assertEqual(C.sizeof(Rect), 16)
        self.assertEqual(Color.a.offset, 3)
        self.assertEqual(Rect.w.offset, 8)

    def test_world_palette_does_not_enlarge_grid_or_political_border_texels(self):
        g = self.globe
        for style, color in ((0, (5, 19, 12, 255)), (1, (13, 62, 34, 255)),
                              (2, (13, 62, 34, 255)), (3, (5, 19, 12, 255)), (4, (13, 62, 34, 255))):
            index = g.styles.index(style)
            self.assertEqual(g._base[index*4:index*4+4], bytes(color))

    def test_native_world_projector_avoids_mesh_rasterization(self):
        g = self.globe
        if not g.scaler.projection_available:
            self.skipTest("matching native projector is unavailable")
        with patch.object(g.scaler, "project", wraps=self.native_project) as project_world, \
                patch.object(g, "_mesh", side_effect=AssertionError("native world used SDL mesh")):
            g.draw()
            before = self.pixels()
            g.rotate(2, 0)
            g.draw()
            self.assertEqual(project_world.call_count, 2)
            self.assertNotEqual(before, self.pixels())
            outside = (88*640)*4
            pixel = self.pixels()[outside:outside+4]
            # Blended square lets the scanline backdrop through; composited alpha stays 255.
            self.assertEqual(pixel[3], 255)
            self.assertNotEqual(pixel[:3], bytes((9, 10, 12)))

    def test_world_borders_are_visible_below_closeup_threshold_and_while_moving(self):
        west = [[-15, -12], [0, -12], [0, 12], [-15, 12], [-15, -12]]
        east = [[0, -12], [15, -12], [15, 12], [0, 12], [0, -12]]
        g = Globe(self.display, [feature("AA", [west]), feature("BB", [east])])
        self.addCleanup(g.close)
        g.lat, g.lon = 0., 0.
        for zoom, moving in ((.8, False), (1., False), (1.5, False), (1.99, False), (4., True), (8., True)):
            with self.subTest(zoom=zoom, moving=moving):
                g.zoom = zoom
                g.moving_until = float("inf") if moving else 0
                g.draw()
                pixels = self.pixels()
                visible = 0
                for x in range(171, 179):
                    r, green, b, _ = pixels[(263*640+x)*4:(263*640+x)*4+4]
                    visible += green >= 120 and green > b > r
                self.assertGreater(visible, 0, "Border missing at zoom %s" % zoom)
                self.assertLessEqual(visible, 2, "World border became thick at zoom %s" % zoom)

    def test_native_world_borders_are_cached_and_fallback_still_draws_them(self):
        shape = [[-15, -12], [0, -12], [0, 12], [-15, 12], [-15, -12]]
        g = Globe(self.display, [feature("AA", [shape])])
        self.addCleanup(g.close)
        g.lat, g.lon = 0., 0.
        if g.scaler.borders_available:
            with patch.object(g.scaler, "overlay_borders", wraps=g.scaler.overlay_borders) as overlay:
                g.draw()
                g.draw()
                self.assertEqual(overlay.call_count, 1)
                self.assertTrue(g.world_borders_cached)
        with patch.object(g.scaler, "overlay_borders", return_value=False):
            g.key = None
            g.draw()
            paths = g.world_lines
            g.draw()
            self.assertFalse(g.world_borders_cached)
            self.assertTrue(paths)
            self.assertIs(paths, g.world_lines)
            pixels = self.pixels()
            self.assertTrue(any(pixels[(263*640+x)*4:(263*640+x)*4+4] == bytes((64, 196, 104, 255))
                                for x in range(173, 178)))

    def test_initial_zoom_and_switchable_globe_resolution(self):
        g, sdl = self.globe, self.display.sdl
        self.assertEqual(g.zoom, .8)
        for resolution, motion in ((128, 96), (160, 112), (192, 128), (224, 160), (256, 176),
                                   (320, 224), (384, 224), (448, 224), (512, 224)):
            g.set_resolution(resolution)
            for texture, expected in ((g.cached, resolution), (g.motion_texture, motion)):
                w, h = C.c_int(), C.c_int()
                self.assertEqual(sdl.SDL_QueryTexture(texture, None, None, C.byref(w), C.byref(h)), 0)
                self.assertEqual((w.value, h.value), (expected, expected))
            g.draw("SY")
        with self.assertRaises(ValueError):
            g.set_resolution(999)

    def test_country_flight_has_smooth_endpoints_and_size_aware_zoom(self):
        g = self.globe
        small = feature("SM", [[[-5, -3], [5, -3], [5, 3], [-5, 3], [-5, -3]]])
        large = feature("LG", [[[-40, -25], [40, -25], [40, 25], [-40, 25], [-40, -25]]])
        origin = g.lat, g.lon, g.zoom
        g.fly_to(small, now=100.)
        target, duration = g.flight["target"], g.flight["duration"]
        g.advance_flight(100.)
        self.assertEqual((g.lat, g.lon, g.zoom), origin)
        g.advance_flight(100.+duration/2)
        self.assertGreater(g.zoom, origin[2])
        self.assertLess(g.zoom, target[2])
        g.advance_flight(100.+duration)
        self.assertEqual((g.lat, g.lon, g.zoom), target)
        self.assertIsNone(g.flight)
        self.assertFalse(g.advance_flight(105.))
        small_zoom = g.zoom
        g.fly_to(large, now=106.)
        self.assertLess(g.flight["target"][2], small_zoom)

    def test_flight_crosses_dateline_by_shortest_path_and_can_be_cancelled(self):
        g = self.globe
        g.lat, g.lon = 0., 179.
        near = feature("DL", [[[-179, -2], [-177, -2], [-177, 2], [-179, 2], [-179, -2]]])
        g.fly_to(near, now=100.)
        self.assertAlmostEqual(g.flight["longitude_delta"], 3.)
        g.advance_flight(100.+g.flight["duration"]/2)
        self.assertGreater(abs(g.lon), 175.)
        g.rotate(1., 0.)
        pose = g.lat, g.lon, g.zoom
        self.assertIsNone(g.flight)
        self.assertFalse(g.advance_flight(110.))
        self.assertEqual((g.lat, g.lon, g.zoom), pose)

    def test_home_flight_restores_world_camera_and_far_flight_pulls_back(self):
        g = self.globe
        g.lat, g.lon, g.zoom = -30., 140., 7.
        g.fly_to(None, now=100.)
        self.assertTrue(g.flight["pullback"])
        duration = g.flight["duration"]
        g.advance_flight(100.+duration*.28)
        self.assertAlmostEqual(g.zoom, .8)
        g.advance_flight(100.+duration+.001)
        self.assertEqual((g.lat, g.lon, g.zoom), (18., -20., .8))

    def test_retargeting_in_flight_starts_at_current_camera(self):
        g = self.globe
        target = feature("SM", [[[35, 32], [43, 32], [43, 38], [35, 38], [35, 32]]])
        g.fly_to(target, now=100.)
        g.advance_flight(100.4)
        pose = g.lat, g.lon, g.zoom
        g.fly_to(None, now=100.4)
        self.assertEqual(g.flight["origin"], pose)

    def test_rotation_zoom_and_cache(self):
        g = self.globe
        with patch.object(g, "_mesh", wraps=g._mesh) as mesh, patch.object(g, "_colorize", wraps=g._colorize) as color:
            g.draw()
            texture = g.cached
            g.draw(label=("a", 18, -20))
            g.draw(label=("a", 20, -15))
            self.assertEqual(mesh.call_count, 1)
            self.assertEqual(color.call_count, 1)
            g.rotate(6, 5)
            g.draw()
            self.assertEqual(mesh.call_count, 2)
            self.assertEqual(color.call_count, 1)
            g.zoom = 2
            g.draw()
            g.draw("SY")
            self.assertEqual(mesh.call_count, 4)
            self.assertEqual(color.call_count, 2)
            self.assertEqual(g.cached, texture)
            self.assertEqual(g.key, (g.lat, g.lon, 2, "SY"))
        g.lat, g.lon, g.zoom = 77, 179, 2
        g.rotate(8, 8)
        self.assertEqual((g.lat, g.lon), (78, -177))

    def test_longitude_seams_and_poles(self):
        g = self.globe
        for lat in (-90, -78, -45, -18, -.001, 0, .001, 18, 45, 78, 90):
            for lon in (-180, -179.99, -90, 0, 90, 179.99, 180):
                with self.subTest(lat=lat, lon=lon):
                    a, b = math.radians(lat), math.radians(lon)
                    g._mesh(math.sin(a), math.cos(a), math.sin(b), math.cos(b), 150)
                    self.assertLess(g._vertex_count*2, len(g._uv))
                    self.assertLessEqual(g._index_count, len(g._indices))
                    for i in range(0, g._index_count, 3):
                        indices = g._indices[i:i+3]
                        us = [g._uv[j*2] for j in indices]
                        self.assertGreaterEqual(min(us), 0)
                        self.assertLessEqual(max(us), 1)
                        self.assertLessEqual(max(us)-min(us), .250001)
                    # The center normal inverts to exactly the camera coordinates.
                    self.assertAlmostEqual(g._uv[1], (90-lat)/180, places=5)

    def test_screenshot_style_selection_and_country_label(self):
        g = self.globe
        g.lat, g.lon = 51, 10
        self.display.rect(0, 0, 640, 480, (3, 4, 5, 255))
        g.draw()
        pixels = self.pixels()
        center = (263*640+175)*4
        self.assertEqual(pixels[center:center+4], bytes((13, 62, 34, 255)))
        for x, y in ((0, 87), (350, 263), (175, 438)):
            offset = (y*640+x)*4
            self.assertEqual(pixels[offset:offset+4], bytes((3, 4, 5, 255)))
        g.draw("DE")
        self.assertEqual(self.pixels()[center:center+4], bytes((158, 120, 32, 255)))
        with patch.object(g, "_mesh", wraps=g._mesh) as mesh, \
                patch.object(self.display, "text", wraps=self.display.text) as drawn:
            g.draw("DE", label=("3", 51, 10))
            self.assertEqual(mesh.call_count, 0)
            calls = [call for call in drawn.call_args_list if call.args[0] == "3"]
            # One plain green numeral, the same raster as the country name.
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].args[3], (242, 201, 76, 255))
            self.assertEqual(calls[0].args[4], 18)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "globe.png"
            self.display.screenshot(path)
            self.assertTrue(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertGreater(path.stat().st_size, 5000)

    def test_native_dateline_poles_and_zoom_clipping(self):
        g = self.globe
        for lat in (-90, -78, 0, 78, 90):
            # The app paints the backdrop every frame; mirror that so the
            # translucent square never inherits the previous frame's residue.
            self.display.rect(0, 0, 640, 480, (3, 4, 5, 255))
            g.lat, g.lon = lat, -180
            g.draw()
            west = self.pixels()
            self.display.rect(0, 0, 640, 480, (3, 4, 5, 255))
            g.lon = 180
            g.draw()
            east = self.pixels()
            self.assertLess(sum(a != b for a, b in zip(west, east)), 100)
        for zoom in (.72, 2, 8):
            self.display.rect(0, 0, 640, 480, (3, 4, 5, 255))
            g.zoom = zoom
            g.draw(label=("9", 90, -180))
            pixels = self.pixels()
            for x, y in ((0, 87), (350, 263), (175, 438)):
                offset = (y*640+x)*4
                self.assertEqual(pixels[offset:offset+4], bytes((3, 4, 5, 255)))

    def test_vector_closeup_preserves_holes_and_one_pixel_borders(self):
        outer = [[-8, -6], [8, -6], [8, 6], [-8, 6], [-8, -6]]
        hole = [[-1, -1], [-1, 1], [1, 1], [1, -1], [-1, -1]]
        east = [[8, -6], [20, -6], [20, 6], [8, 6], [8, -6]]
        g = Globe(self.display, [feature("AA", [outer, hole]), feature("BB", [east])])
        self.addCleanup(g.close)
        g.lat, g.lon = 0., 0.
        for zoom in (4, 8):
            for resolution in (128, 512):
                with self.subTest(zoom=zoom, resolution=resolution):
                    g.zoom = zoom
                    g.set_resolution(resolution)
                    g.draw("AA")
                    pixels = self.pixels()
                    center = (263*640+175)*4
                    self.assertEqual(pixels[center:center+4], bytes((5, 19, 12, 255)))
                    inside = ((263-round(150*zoom*math.sin(math.radians(3))))*640+175)*4
                    self.assertEqual(pixels[inside:inside+4], bytes((158, 120, 32, 255)))
                    edge = round(175+150*zoom*math.cos(math.radians(6))*math.sin(math.radians(8)))
                    colors = {bytes((*color, 255)) for color in ((242, 201, 76), (120, 84, 18),
                                                                (160, 116, 26), (203, 152, 34))}
                    green = sum(pixels[(263*640+x)*4:(263*640+x)*4+4] in colors
                                for x in range(edge-3, edge+4))
                    self.assertGreater(green, 0)
                    self.assertLessEqual(green, 2)

    def test_vector_closeup_is_cached_and_respects_viewport_clip(self):
        shape = [[30, 30], [45, 30], [45, 40], [30, 40], [30, 30]]
        g = Globe(self.display, [feature("SY", [shape])])
        self.addCleanup(g.close)
        g.lat, g.lon, g.zoom = 35., 38., 8.
        self.display.rect(0, 0, 640, 480, (3, 4, 5, 255))
        with patch.object(g, "_vector_map", wraps=g._vector_map) as render:
            for label in (("one", 35., 38.), None):
                g.draw("SY", label=label,
                       viewport=(4, 60, 406, 406), bounds=(0, 84, 414, 354))
            self.assertEqual(render.call_count, 1)
        pixels = self.pixels()
        for x, y in ((0, 83), (415, 263), (207, 438)):
            offset = (y*640+x)*4
            self.assertEqual(pixels[offset:offset+4], bytes((3, 4, 5, 255)))

    def test_vector_closeup_wraps_dateline_without_a_spanning_border(self):
        ring = [[170, -15], [-170, -15], [-170, 15], [170, 15], [170, -15]]
        g = Globe(self.display, [feature("DL", [ring])])
        self.addCleanup(g.close)
        g.lat, g.lon, g.zoom = 0., -180., 4.
        with patch("tv_globe.time.monotonic", return_value=100.):
            g.draw("DL")
            west = self.pixels()
            center = (263*640+175)*4
            self.assertEqual(west[center:center+4], bytes((158, 120, 32, 255)))
            g.lon = 180.
            g.draw("DL")
            self.assertLess(sum(a != b for a, b in zip(west, self.pixels())), 100)

    def test_manual_zoom_eases_and_accumulates_repeated_steps(self):
        g = self.globe
        g.zoom_by(1.2, now=100.)
        self.assertEqual(g.zoom, .8)
        self.assertAlmostEqual(g.flight["target"][2], .96)
        g.advance_flight(100.12)
        self.assertGreater(g.zoom, .8)
        self.assertLess(g.zoom, .96)
        g.zoom_by(1.2, now=100.12)
        self.assertAlmostEqual(g.flight["target"][2], 1.152)
        g.advance_flight(100.37)
        self.assertAlmostEqual(g.zoom, 1.152)
        self.assertIsNone(g.flight)

    def test_renderer_mode_change_crossfades_cached_native_surfaces(self):
        shape = [[30, 30], [45, 30], [45, 40], [30, 40], [30, 30]]
        g = Globe(self.display, [feature("SY", [shape])])
        self.addCleanup(g.close)
        g.lat, g.lon, g.zoom = 35., 38., 8.
        with patch("tv_globe.FADE_SECONDS", .2), patch.object(g, "_smooth_surface", wraps=g._smooth_surface) as scale:
            with patch("tv_globe.time.monotonic", return_value=100.):
                g.draw("SY")
                first = g.surface_index
                g.moving_until = 110.
                g.draw("SY")
                self.assertNotEqual(g.surface_index, first)
                self.assertEqual(g.fade_from, first)
                self.assertAlmostEqual(g.fade_until, 100.2)
            with patch("tv_globe.time.monotonic", return_value=100.1):
                g.draw("SY")
                self.assertIsNotNone(g.fade_from)
            with patch("tv_globe.time.monotonic", return_value=100.3):
                g.draw("SY")
                self.assertIsNone(g.fade_from)
            self.assertEqual(scale.call_count, 2)
            self.assertEqual(g.surface_size, (350, 350))

    def test_dark_matrix_trace_moves_without_rasterizing_again(self):
        shape = [[35, 32], [42, 32], [42, 38], [35, 38], [35, 32]]
        g = Globe(self.display, [feature("SY", [shape])])
        self.addCleanup(g.close)
        g.lat, g.lon, g.zoom = 35., 38., 8.
        with patch.object(g, "_vector_map", wraps=g._vector_map) as render:
            with patch("tv_globe.time.monotonic", return_value=100.):
                g.draw("SY")
                before = self.pixels()
                segments = g.trace_segments
            with patch("tv_globe.time.monotonic", return_value=100.5):
                g.draw("SY")
                after = self.pixels()
            self.assertTrue(g.trace_active)
            self.assertIs(g.trace_segments, segments)
            self.assertEqual(render.call_count, 1)
            self.assertNotEqual(before, after)

    def test_target_and_clip_restoration_and_close(self):
        d, sdl, g = self.display, self.display.sdl, self.globe
        target = d.texture(640, 480, target=True)
        try:
            self.assertEqual(sdl.SDL_SetRenderTarget(d.renderer, target), 0)
            clip = Rect(10, 100, 200, 200)
            sdl.SDL_RenderSetClipRect(d.renderer, C.byref(clip))
            g.draw(label=("a", 18, -20))
            self.assertEqual(sdl.SDL_GetRenderTarget(d.renderer), target)
            actual = Rect()
            sdl.SDL_RenderGetClipRect(d.renderer, C.byref(actual))
            self.assertEqual((actual.x, actual.y, actual.w, actual.h), (10, 100, 200, 200))
            g.close()
            g.close()
            self.assertIsNone(g.cached)
            self.assertIsNone(g.atlas_texture)
        finally:
            sdl.SDL_RenderSetClipRect(d.renderer, None)
            sdl.SDL_SetRenderTarget(d.renderer, None)
            sdl.SDL_DestroyTexture(target)


def benchmark(args):
    if args.app_root:
        sys.path.insert(0, str(args.app_root))
    os.environ["SDL_VIDEODRIVER"] = args.driver
    os.environ["SDL_RENDER_DRIVER"] = "software"
    import app
    d = app.Display(desktop=args.driver != "kmsdrm")
    g = None
    try:
        started = time.perf_counter()
        g = Globe(d, [])
        g.draw()
        startup = (time.perf_counter()-started)*1000
        rotations, cached = [], []
        label = ("42", 18., -20.)
        for frame in range(args.frames+5):
            g.rotate(3, .1 if frame % 60 < 30 else -.1)
            started = time.perf_counter()
            # Text overlay forces the redraw path of the old per-channel dots.
            g.draw(label=label if frame % 2 else None)
            # Flush the software render queue, without display/vsync latency.
            sdl = d.sdl
            sdl.SDL_SetRenderTarget(d.renderer, g.cached)
            sdl.SDL_SetRenderTarget(d.renderer, None)
            elapsed = (time.perf_counter()-started)*1000
            if frame >= 5:
                rotations.append(elapsed)
            started = time.perf_counter()
            g.draw(label=label)
            sdl.SDL_SetRenderTarget(d.renderer, g.cached)
            sdl.SDL_SetRenderTarget(d.renderer, None)
            if frame >= 5:
                cached.append((time.perf_counter()-started)*1000)
        started = time.perf_counter()
        g.draw("RU", label)
        selection = (time.perf_counter()-started)*1000
        if args.screenshot:
            d.screenshot(args.screenshot)
        print(json.dumps({"driver": args.driver, "renderer": "software", "frames": args.frames,
            "startup_ms": round(startup, 3), "rotation_median_ms": round(statistics.median(rotations), 3),
            "rotation_p95_ms": round(sorted(rotations)[math.ceil(len(rotations)*.95)-1], 3),
            "rotation_max_ms": round(max(rotations), 3),
            "cached_median_ms": round(statistics.median(cached), 3),
            "country_change_ms": round(selection, 3), "vertices": g._vertex_count,
            "triangles": g._index_count//3}, indent=2))
    finally:
        if g:
            g.close()
        d.close()


if __name__ == "__main__":
    if "--benchmark" in sys.argv:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--benchmark", action="store_true")
        parser.add_argument("--frames", type=int, default=120)
        parser.add_argument("--driver", choices=("dummy", "offscreen", "kmsdrm"), default="dummy")
        parser.add_argument("--app-root", type=Path)
        parser.add_argument("--screenshot", type=Path)
        args = parser.parse_args()
        if args.frames < 1:
            parser.error("--frames must be positive")
        benchmark(args)
    else:
        unittest.main()
