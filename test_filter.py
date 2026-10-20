#!/usr/bin/env python3
"""Standalone CPU-only tests and optional native benchmark; no SDL imports.

python3 -B test_filter.py
python3 -B test_filter.py --benchmark --iterations 100
Tests build a host library in a temporary directory with existing GCC. The
benchmark loads TV227_GLOBE_FILTER or the shipped native library without building.
"""

import argparse
import ctypes as C
import json
import math
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from tv_filter import Scaler

ROOT = Path(__file__).resolve().parent


def reference(source, sw, sh, dw, dh, pitch):
    """Scalar per-channel reference for two truncating 8-bit-weight passes."""
    result = bytearray(dw * dh * 4)
    for y in range(dh):
        fy = max(0, min((sh - 1) * 256, ((2*y + 1)*sh*128)//dh - 128))
        y0, wy = divmod(fy, 256)
        y1 = min(y0 + 1, sh - 1)
        for x in range(dw):
            fx = max(0, min((sw - 1) * 256, ((2*x + 1)*sw*128)//dw - 128))
            x0, wx = divmod(fx, 256)
            x1 = min(x0 + 1, sw - 1)
            for channel in range(4):
                top = (source[y0*pitch+x0*4+channel]*(256-wx)
                       + source[y0*pitch+x1*4+channel]*wx)//256
                bottom = (source[y1*pitch+x0*4+channel]*(256-wx)
                          + source[y1*pitch+x1*4+channel]*wx)//256
                result[(y*dw+x)*4+channel] = (top*(256-wy) + bottom*wy)//256
    return bytes(result)


def project_reference(source, sw, sh, size, latitude, longitude, zoom, pitch=None):
    """Independent double-precision geometry and scalar-channel bilinear sampling."""
    pitch = pitch or sw*4
    latitude, longitude, zoom = (C.c_float(v).value for v in (latitude, longitude, zoom))
    lat, lon = math.radians(latitude), math.radians(math.remainder(longitude, 360))
    slat, clat, slon, clon = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
    radius = 150*size/350*zoom
    result = bytearray(bytes((9, 10, 12, 255)) * (size*size))
    for y in range(size):
        py = (size/2-y-.5)/radius
        for x in range(size):
            px = (x+.5-size/2)/radius
            if px*px + py*py > 1:
                continue
            depth = math.sqrt(max(0, 1-px*px-py*py))
            north = clat*py + slat*depth
            forward_local = -slat*py + clat*depth
            east, forward = clon*px + slon*forward_local, -slon*px + clon*forward_local
            u = ((math.atan2(east, forward)/math.tau+.5)*sw-.5) % sw
            v = max(0, min(sh-1, (.5-math.asin(max(-1, min(1, north)))/math.pi)*sh-.5))
            x0, y0 = int(u), int(v)
            x1, y1 = (x0+1) % sw, min(y0+1, sh-1)
            wx, wy = int((u-x0)*256), int((v-y0)*256)
            for c in range(4):
                a = (source[y0*pitch+x0*4+c]*(256-wx) + source[y0*pitch+x1*4+c]*wx)//256
                b = (source[y1*pitch+x0*4+c]*(256-wx) + source[y1*pitch+x1*4+c]*wx)//256
                result[(y*size+x)*4+c] = (a*(256-wy) + b*wy)//256
    return bytes(result)


def xyz(latitude, longitude):
    lat, lon = math.radians(latitude), math.radians(longitude)
    return math.cos(lat)*math.sin(lon), math.sin(lat), math.cos(lat)*math.cos(lon)


def segment_buffer(*points):
    return (C.c_float * (len(points)*3))(*(value for point in points for value in point))


class FilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("gcc"):
            raise unittest.SkipTest("existing GCC required; no compiler is installed by tests")
        cls.directory = tempfile.TemporaryDirectory(prefix="tv227-filter-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.library = str(Path(cls.directory.name) / "host.so")
        subprocess.run(["bash", str(ROOT / "build_globe_filter.sh"), cls.library], check=True)

    def setUp(self):
        with patch.dict(os.environ, TV227_GLOBE_FILTER=self.library):
            self.scaler = Scaler()
        self.assertTrue(self.scaler.available, self.scaler.error)

    def check_pixels(self, sw, sh, dw, dh, pixel, padding=0):
        pitch = sw*4 + padding
        raw = bytearray([0xBD] * ((sh-1)*pitch + sw*4))
        for y in range(sh):
            for x in range(sw):
                raw[y*pitch+x*4:y*pitch+x*4+4] = bytes(pixel(x, y))
        source = (C.c_uint8 * len(raw)).from_buffer_copy(raw)
        output = self.scaler.scale(source, sw, sh, dw, dh, pitch)
        self.assertIsNotNone(output, self.scaler.error)
        self.assertEqual(bytes(output), reference(raw, sw, sh, dw, dh, pitch))
        self.assertEqual(bytes(source), raw)
        return bytes(output)

    def test_solid_colors_and_alpha(self):
        for color in ((0, 0, 0, 0), (255, 255, 255, 255), (255, 0, 0, 255),
                      (0, 255, 0, 255), (0, 0, 255, 255), (17, 83, 219, 73)):
            with self.subTest(color=color):
                data = self.check_pixels(3, 5, 13, 17, lambda x, y: color, 7)
                self.assertEqual(data, bytes(color) * (13*17))

    def test_checkerboard_gradient_and_opaque_alpha(self):
        patterns = (
            lambda x, y: (255*((x+y) % 2), 255*(x % 2), 255*(y % 2), 255),
            lambda x, y: (x*31, y*43, (x*17+y*29) % 256, (x*73+y*11) % 256),
        )
        for pixel in patterns:
            for dims in ((7, 5, 19, 17), (7, 5, 3, 2), (7, 5, 7, 5)):
                with self.subTest(dims=dims, pixel=pixel):
                    result = self.check_pixels(*dims, pixel, padding=3)
                    if pixel is patterns[0]:
                        self.assertEqual(result[3::4], b"\xff" * (dims[2]*dims[3]))

    def test_pixel_centers_edges_and_single_pixel_axes(self):
        pixel = lambda x, y: (255*x, 255*y, 0, 255)
        data = self.check_pixels(2, 2, 4, 4, pixel)
        self.assertEqual(data[:16:4], bytes((0, 63, 191, 255)))
        self.assertEqual(data[1::16], bytes((0, 63, 191, 255)))
        self.assertEqual(data[-4:], bytes((255, 255, 0, 255)))
        for dims in ((1, 1, 17, 11), (1, 2, 9, 1), (2, 1, 1, 9), (2, 2, 1, 1)):
            self.check_pixels(*dims, pixel, padding=5)

    def test_random_sizes_and_maximum_coordinates(self):
        rng = random.Random(227)
        for _ in range(40):
            dims = [rng.randint(1, 20) for _ in range(4)]
            self.check_pixels(*dims, lambda x, y: [rng.randrange(256) for _ in range(4)],
                              padding=rng.randrange(8))
        for dims in ((8192, 1, 8191, 1), (1, 8192, 1, 8191),
                     (1, 1, 8192, 1), (1, 1, 1, 8192)):
            self.check_pixels(*dims, lambda x, y: (x % 256, y % 256, 91, 255))

    def test_output_reuse_resize_and_source_alias(self):
        source = (C.c_uint8 * 16)(*range(16))
        first = self.scaler.scale(source, 2, 2, 4, 4)
        self.assertIs(first, self.scaler.scale(source, 2, 2, 4, 4))
        self.assertIs(first, self.scaler.scale(source, 2, 2, 2, 8))
        expected = reference(bytes(first), 4, 4, 4, 4, 16)
        result = self.scaler.scale(first, 4, 4, 4, 4)
        self.assertIsNot(result, first)
        self.assertEqual(bytes(result), expected)
        self.assertIsNot(result, self.scaler.scale(source, 2, 2, 3, 3))

    def test_invalid_dimensions_stride_and_buffers(self):
        source = (C.c_uint8 * 16)()
        for value in (0, -1, 8193, 2**40, 1.5, None, True, "2"):
            for axis in range(4):
                dims = [2, 2, 2, 2]
                dims[axis] = value
                with self.subTest(value=value, axis=axis):
                    self.assertIsNone(self.scaler.scale(source, *dims))
                    self.assertIn("dimensions", self.scaler.error)
        for pitch in (-1, 0, 7, 2**31, True, 8.5, "8"):
            self.assertIsNone(self.scaler.scale(source, 2, 2, 2, 2, pitch))
            self.assertIn("src_pitch", self.scaler.error)
        for buffer in (None, b"1234", C.cast(source, C.c_void_p), C.pointer(source)):
            self.assertIsNone(self.scaler.scale(buffer, 2, 2, 2, 2))
            self.assertIn("ctypes array", self.scaler.error)
        self.assertIsNone(self.scaler.scale((C.c_uint8 * 15)(), 2, 2, 2, 2))
        self.assertIn("too small", self.scaler.error)
        self.assertIsNone(self.scaler.scale(source, 2, 2, 2, 2, 12))
        self.assertIn("too small", self.scaler.error)
        self.assertTrue(self.scaler.available)
        self.assertIsNotNone(self.scaler.scale(source, 2, 2, 2, 2))
        self.assertEqual(self.scaler.error, "")

    def test_missing_bad_architecture_and_symbol(self):
        for path in (str(Path(self.directory.name) / "missing.so"), __file__):
            with patch.dict(os.environ, TV227_GLOBE_FILTER=path):
                scaler = Scaler()
            self.assertFalse(scaler.available)
            self.assertIn(path, scaler.error)
            self.assertIsNone(scaler.scale(None, 0, 0, 0, 0))
        with patch("tv_filter.C.CDLL", side_effect=OSError("wrong ELF class")):
            self.assertIn("wrong ELF class", Scaler().error)
        with patch("tv_filter.C.CDLL", return_value=object()):
            scaler = Scaler()
            self.assertFalse(scaler.available)
            self.assertIn("tv_globe_scale", scaler.error)

    def test_native_failure_and_allocation_failure(self):
        source = (C.c_uint8 * 4)()
        with patch.object(self.scaler, "_scale", return_value=-1):
            self.assertIsNone(self.scaler.scale(source, 1, 1, 1, 1))
            self.assertIn("status -1", self.scaler.error)
        with patch("tv_filter.C.sizeof", side_effect=MemoryError("out of memory")):
            self.assertIsNone(self.scaler.scale(source, 1, 1, 1, 1))
            self.assertIn("out of memory", self.scaler.error)

    def test_native_validation(self):
        fn = self.scaler._scale
        source, dest = (C.c_uint8 * 16)(), (C.c_uint8 * 16)()
        valid = [source, 2, 2, 8, dest, 2, 2, 8]
        for index in (0, 4):
            args = valid.copy()
            args[index] = None
            self.assertEqual(fn(*args), -1)
        for index in (1, 2, 5, 6):
            for value in (-2147483648, -1, 0, 8193, 2147483647):
                args = valid.copy()
                args[index] = value
                self.assertEqual(fn(*args), -1)
        for index in (3, 7):
            for value in (-2147483648, -1, 0, 7):
                args = valid.copy()
                args[index] = value
                self.assertEqual(fn(*args), -1)
        self.assertEqual(fn(source, 2, 2, 8, source, 2, 2, 8), -1)
        self.assertEqual(fn(source, 1, 1, 4, C.byref(source, 1), 1, 1, 4), -1)
        self.assertEqual(fn(C.byref(source, 1), 1, 1, 4, source, 1, 1, 4), -1)

    def test_native_unaligned_and_padded_destination(self):
        sw, sh, dw, dh = 5, 3, 11, 7
        sp, dp = sw*4+3, dw*4+5
        source = (C.c_uint8 * (sp*sh+2))(*([0xC7] * (sp*sh+2)))
        dest = (C.c_uint8 * (dp*dh+2))(*([0xA9] * (dp*dh+2)))
        for y in range(sh):
            for x in range(sw*4):
                source[1+y*sp+x] = (x*29+y*53) % 256
        original = bytes(source)
        self.assertEqual(self.scaler._scale(C.byref(source, 1), sw, sh, sp,
                                           C.byref(dest, 1), dw, dh, dp), 0)
        expected = reference(original[1:], sw, sh, dw, dh, sp)
        for y in range(dh):
            self.assertEqual(bytes(dest[1+y*dp:1+y*dp+dw*4]), expected[y*dw*4:(y+1)*dw*4])
            self.assertEqual(bytes(dest[1+y*dp+dw*4:1+(y+1)*dp]), b"\xa9" * (dp-dw*4))
        self.assertEqual((dest[0], dest[-1]), (0xA9, 0xA9))
        self.assertEqual(bytes(source), original)

    def test_borders_world_and_constant_native_width(self):
        self.assertTrue(self.scaler.borders_available, self.scaler._borders_error)
        width, height = 101, 101
        for zoom in (.8, 1.5, 4, 8):
            for vertical in (False, True):
                with self.subTest(zoom=zoom, vertical=vertical):
                    points = (xyz(-30, 0), xyz(30, 0)) if vertical else (xyz(0, -30), xyz(0, 30))
                    segments = segment_buffer(*points)
                    dest = (C.c_uint8 * (width*height*4)).from_buffer_copy(
                        bytes((0, 0, 0, 255)) * (width*height))
                    address = C.addressof(dest)
                    self.assertTrue(self.scaler.overlay_borders(dest, width, height, segments, 0, 0, zoom),
                                    self.scaler.error)
                    self.assertEqual(C.addressof(dest), address)
                    self.assertEqual(bytes(dest[(50*width+50)*4:(50*width+51)*4]), bytes((64, 196, 104, 255)))
                    cross = [dest[(50*width+i)*4] if vertical else dest[(i*width+50)*4]
                             for i in range(101)]
                    self.assertEqual(sum(value > 0 for value in cross), 1)
                    self.assertEqual(bytes(dest)[3::4], b"\xff" * (width*height))

    def test_borders_camera_rotation_and_after_scale(self):
        lat, lon = 28, -73
        slat, clat = math.sin(math.radians(lat)), math.cos(math.radians(lat))
        slon, clon = math.sin(math.radians(lon)), math.cos(math.radians(lon))
        points = []
        for x in (-.5, .5):
            z = math.sqrt(1-x*x)
            points.append((clon*x+slon*clat*z, slat*z, -slon*x+clon*clat*z))
        segments = segment_buffer(*points)
        source = (C.c_uint8 * 4)(0, 0, 0, 255)
        dest = self.scaler.scale(source, 1, 1, 101, 101)
        self.assertTrue(self.scaler.overlay_borders(dest, 101, 101, segments, lat, lon, .8))
        self.assertGreater(dest[(50*101+50)*4+1], 70)
        self.assertEqual(dest[(45*101+50)*4], 0)
        normalized = self.scaler.scale(source, 1, 1, 101, 101)
        self.assertIs(dest, normalized)
        self.assertTrue(self.scaler.overlay_borders(dest, 101, 101, segments, lat, lon+360*1000, .8))
        self.assertGreater(dest[(50*101+50)*4+1], 70)

    def test_borders_behind_and_normalized_horizon_clipping(self):
        def render(points):
            dest = (C.c_uint8 * (101*101*4))()
            self.assertTrue(self.scaler.overlay_borders(dest, 101, 101, segment_buffer(*points), 0, 0, .8))
            return bytes(dest)
        self.assertFalse(any(render((xyz(0, 140), xyz(0, 160)))))
        crossing = (xyz(0, 80), xyz(0, 100))
        result = render(crossing)
        self.assertTrue(any(result))
        self.assertEqual(result, render(crossing[::-1]))
        for y in range(101):
            self.assertFalse(any(result[y*101*4:(y*101+83)*4]))
        # Antipodal endpoints have no well-defined horizon direction, not a diameter.
        self.assertFalse(any(render((xyz(0, 0), xyz(0, 180)))))

    def test_borders_aa_coverage_and_alpha(self):
        segments = segment_buffer(xyz(0, -30), xyz(0, 30))
        for alpha in (0, 255):
            dest = (C.c_uint8 * (100*100*4)).from_buffer_copy(bytes((0, 0, 0, alpha)) * 10000)
            self.assertTrue(self.scaler.overlay_borders(dest, 100, 100, segments, 0, 0, .8))
            for y in (49, 50):
                self.assertEqual(bytes(dest[(y*100+50)*4:(y*100+51)*4]),
                                 bytes((32, 98, 52, 127 if alpha == 0 else 255)))
            self.assertEqual(dest[(48*100+50)*4], 0)
            self.assertEqual(dest[(51*100+50)*4], 0)

    def test_borders_viewport_clipping_padding_and_unaligned(self):
        width, height, pitch = 37, 29, 37*4+7
        storage = (C.c_uint8 * 25)()
        segments = (C.c_float * 6).from_buffer(storage, 1)
        segments[:] = segment_buffer(xyz(0, -70), xyz(0, 70))[:]
        dest = (C.c_uint8 * (height*pitch+2))(*([0xA9] * (height*pitch+2)))
        for y in range(height):
            for x in range(width):
                for c, value in enumerate((0, 0, 0, 255)):
                    dest[1+y*pitch+x*4+c] = value
        self.assertEqual(self.scaler._borders(segments, 1, C.byref(dest, 1), width, height, pitch,
                                              0, 0, 64), 0)
        for y in range(height):
            row = bytes(dest[1+y*pitch:1+y*pitch+width*4])
            if y == height//2:
                self.assertTrue(all(value > 0 for value in row[::4]))
            else:
                self.assertFalse(any(row[::4]))
            self.assertEqual(bytes(dest[1+y*pitch+width*4:1+(y+1)*pitch]), b"\xa9" * 7)
        self.assertEqual((dest[0], dest[-1]), (0xA9, 0xA9))
        invisible = segment_buffer(xyz(60, -20), xyz(60, 20))
        tight = (C.c_uint8 * (width*height*4))()
        self.assertTrue(self.scaler.overlay_borders(tight, width, height, invisible, 0, 0, 8))
        self.assertFalse(any(tight))

    def test_borders_invalid_buffers_and_transactional_input(self):
        dest = (C.c_uint8 * (21*21*4))()
        segments = segment_buffer(xyz(0, -30), xyz(0, 30))
        valid = [dest, 21, 21, segments, 0, 0, .8]
        for index, values in ((0, (None, b"data", C.cast(dest, C.c_void_p), (C.c_uint8 * 3)())),
                              (1, (0, -1, 8193, True, 1.5)), (2, (0, -1, 8193, None)),
                              (3, (None, C.cast(segments, C.POINTER(C.c_float)), (C.c_double * 6)(),
                                   (C.c_float * 5)(), (C.c_float * (200001*6))())),
                              (4, (-91, 91, math.inf, math.nan, True)),
                              (5, (math.nan, math.inf, "0", 10**1000)),
                              (6, (0, -1, 65, math.nan, math.inf, 1e-300))):
            for value in values:
                args = valid.copy()
                args[index] = value
                self.assertFalse(self.scaler.overlay_borders(*args), (index, type(value)))
                self.assertTrue(self.scaler.error)
        for invalid in (math.nan, math.inf, -math.inf):
            points = (C.c_float * 12)(*segments, *segments)
            points[-1] = invalid
            self.assertFalse(self.scaler.overlay_borders(dest, 21, 21, points, 0, 0, .8))
            self.assertFalse(any(dest))
        with patch.object(self.scaler, "_borders", return_value=-1):
            self.assertFalse(self.scaler.overlay_borders(*valid))
            self.assertIn("status -1", self.scaler.error)
        self.assertTrue(self.scaler.overlay_borders(dest, 21, 21, (C.c_float * 0)(), 0, 0, .8))
        self.assertFalse(any(dest))
        self.assertEqual(self.scaler.error, "")
        self.assertTrue(self.scaler.overlay_borders(*valid))

    def test_borders_native_validation(self):
        fn = self.scaler._borders
        dest = (C.c_uint8 * 16)()
        segments = segment_buffer(xyz(0, -30), xyz(0, 30))
        valid = [segments, 1, dest, 2, 2, 8, 0, 0, .8]
        for index, values in ((0, (None,)), (1, (-1, 200001)), (2, (None,)),
                              (3, (0, -1, 8193, 2147483647)), (4, (0, -1, 8193)),
                              (5, (-1, 0, 7)), (6, (-91, 91, math.nan, math.inf)),
                              (7, (math.nan, math.inf)), (8, (0, -1, 65, math.nan, math.inf))):
            for value in values:
                args = valid.copy()
                args[index] = value
                self.assertEqual(fn(*args), -1, (index, value))
        self.assertEqual(fn(None, 0, dest, 2, 2, 8, 0, 0, .8), 0)
        self.assertEqual(fn(segments, 1, segments, 2, 2, 8, 0, 0, .8), -1)
        self.assertEqual(fn(segments, 1, C.byref(segments, 1), 2, 2, 8, 0, 0, .8), -1)

    def test_borders_optional_abi_independent_of_projection(self):
        libraries = (
            Mock(spec=["tv_globe_scale", "tv_globe_project_abi", "tv_globe_project"],
                 tv_globe_scale=self.scaler._scale,
                 tv_globe_project_abi=Mock(return_value=1), tv_globe_project=self.scaler._project),
            Mock(tv_globe_scale=self.scaler._scale, tv_globe_project_abi=Mock(return_value=1),
                 tv_globe_project=self.scaler._project, tv_globe_borders_abi=Mock(return_value=2)),
        )
        for library in libraries:
            with patch("tv_filter.C.CDLL", return_value=library):
                scaler = Scaler()
            self.assertTrue(scaler.available)
            self.assertTrue(scaler.projection_available)
            self.assertFalse(scaler.borders_available)
            self.assertFalse(scaler.overlay_borders(None, 1, 1, None, 0, 0, .8))
            self.assertIn("unavailable", scaler.error)
        library = Mock(spec=["tv_globe_scale", "tv_globe_borders_abi", "tv_globe_borders"],
                       tv_globe_scale=self.scaler._scale, tv_globe_borders=self.scaler._borders,
                       tv_globe_borders_abi=Mock(return_value=1))
        with patch("tv_filter.C.CDLL", return_value=library):
            scaler = Scaler()
        self.assertTrue(scaler.borders_available)
        self.assertFalse(scaler.projection_available)
        with patch("tv_filter.C.CDLL", side_effect=OSError("wrong ELF class")):
            scaler = Scaler()
        self.assertFalse(scaler.borders_available)
        self.assertFalse(scaler.overlay_borders(None, 1, 1, None, 0, 0, .8))
        self.assertIn("wrong ELF class", scaler.error)

    def test_projection_known_centers_and_dateline(self):
        source = (C.c_uint8 * (8*4*4))(*[
            value for y in range(4) for x in range(8) for value in (x*20, y*40, 80, 255)])
        self.assertTrue(self.scaler.projection_available, self.scaler._projection_error)
        for lat, lon, expected in ((0, 0, (70, 60, 80, 255)), (45, 90, (110, 20, 80, 255)),
                                   (-45, -90, (30, 100, 80, 255))):
            result = self.scaler.project(source, 8, 4, 1, lat, lon, .8)
            self.assertIsNotNone(result, self.scaler.error)
            self.assertLessEqual(max(abs(a-b) for a, b in zip(result, expected)), 1)
        seam = (C.c_uint8 * 16)(0, 10, 20, 255, 30, 10, 20, 255,
                                 30, 10, 20, 255, 200, 10, 20, 255)
        samples = [bytes(self.scaler.project(seam, 4, 1, 1, 0, lon, .8))
                   for lon in (179.99, 180, -180, -179.99, 540, -540)]
        self.assertTrue(all(abs(pixel[0]-100) <= 1 for pixel in samples), samples)
        self.assertTrue(all(pixel[1:] == bytes((10, 20, 255)) for pixel in samples))
        for lon in (1e308, -1e308, 360000090):
            a = bytes(self.scaler.project(source, 8, 4, 3, 18, lon, .8))
            b = bytes(self.scaler.project(source, 8, 4, 3, 18, math.remainder(lon, 360), .8))
            self.assertEqual(a, b)

    def test_projection_geometry_reference_alpha_and_poles(self):
        sw, sh = 17, 9
        source = (C.c_uint8 * (sw*sh*4))(*[
            value for y in range(sh) for x in range(sw)
            for value in (x*13, y*29, (x*7+y*11) % 256, (x*17+y*19) % 256)])
        original = bytes(source)
        for size, lat, lon, zoom in ((31, 18, -73, .8), (32, -53, 179.9, 1.2),
                                     (17, 89, -179, .8), (11, -89, 40, 2), (9, 0, 0, 64)):
            with self.subTest(size=size, lat=lat, lon=lon, zoom=zoom):
                result = self.scaler.project(source, sw, sh, size, lat, lon, zoom)
                self.assertIsNotNone(result, self.scaler.error)
                expected = project_reference(source, sw, sh, size, lat, lon, zoom)
                self.assertLessEqual(max(abs(a-b) for a, b in zip(result, expected)), 2)
        self.assertEqual(bytes(source), original)
        poles = (C.c_uint8 * 16)(12, 23, 34, 45, 60, 70, 80, 90,
                                  100, 110, 120, 130, 140, 150, 160, 170)
        for lat, expected in ((90, bytes(poles[:4])), (-90, bytes(poles[-4:]))):
            self.assertEqual(bytes(self.scaler.project(poles, 1, 4, 1, lat, 123, .8)), expected)

    def test_projection_disk_mask_tiny_zoom_and_limits(self):
        color, outside = bytes((123, 211, 50, 73)), bytes((9, 10, 12, 255))
        source = (C.c_uint8 * 4).from_buffer_copy(color)
        size, zoom = 21, .8
        output = self.scaler.project(source, 1, 1, size, 23, 56, zoom)
        radius = 150*size/350*C.c_float(zoom).value
        for y in range(size):
            for x in range(size):
                inside = (x+.5-size/2)**2 + (y+.5-size/2)**2 <= radius**2
                self.assertEqual(bytes(output[(y*size+x)*4:(y*size+x+1)*4]),
                                 color if inside else outside)
        tiny = C.c_float(1e-45).value
        self.assertEqual(bytes(self.scaler.project(source, 1, 1, 1, 0, 0, tiny)), color)
        output = self.scaler.project(source, 1, 1, 2048, 0, 0, tiny)
        self.assertEqual(bytes(output), outside * (2048*2048))
        for width, height in ((8192, 1), (1, 8192)):
            atlas = (C.c_uint8 * (width*height*4))()
            self.assertIsNotNone(self.scaler.project(atlas, width, height, 1, 0, 0, .8))

    def test_projection_reuse_separate_buffer_and_alias(self):
        source = (C.c_uint8 * 16)(*range(16))
        projected = self.scaler.project(source, 2, 2, 5, 0, 0, .8)
        self.assertIs(projected, self.scaler.project(source, 2, 2, 5, 18, 90, .8))
        original = bytes(projected)
        scaled = self.scaler.scale(source, 2, 2, 5, 5)
        self.assertIsNot(scaled, projected)
        self.assertEqual(bytes(projected), original)
        scaled_bytes = bytes(scaled)
        result = self.scaler.project(projected, 5, 5, 5, 0, 0, .8)
        self.assertIsNot(result, projected)
        self.assertEqual(bytes(scaled), scaled_bytes)
        self.assertIsNot(result, self.scaler.project(source, 2, 2, 7, 0, 0, .8))

    def test_projection_invalid_inputs_and_native_errors(self):
        source = (C.c_uint8 * 16)()
        valid = [source, 2, 2, 5, 0, 0, .8]
        for index, values in ((1, (0, -1, 8193, True, 2.5)), (2, (0, -1, 8193, None)),
                              (3, (0, -1, 2049, 2**40, True, 3.5)),
                              (4, (-91, 91, math.inf, math.nan, None, True)),
                              (5, (math.inf, -math.inf, math.nan, "0", 10**1000)),
                              (6, (0, -1, 65, 1e-300, math.nan, math.inf, True))):
            for value in values:
                args = valid.copy()
                args[index] = value
                self.assertIsNone(self.scaler.project(*args), (index, value))
                self.assertTrue(self.scaler.error)
        for buffer in (None, b"1234", C.cast(source, C.c_void_p), (C.c_uint8 * 15)()):
            self.assertIsNone(self.scaler.project(buffer, 2, 2, 5, 0, 0, .8))
            self.assertTrue(self.scaler.error)
        with patch.object(self.scaler, "_project", return_value=-1):
            self.assertIsNone(self.scaler.project(*valid))
            self.assertIn("status -1", self.scaler.error)
        self.assertTrue(self.scaler.available)
        self.assertTrue(self.scaler.projection_available)
        self.assertIsNotNone(self.scaler.project(*valid))
        self.assertEqual(self.scaler.error, "")

    def test_projection_optional_symbol_and_abi_fallback(self):
        for library in (Mock(spec=["tv_globe_scale"], tv_globe_scale=self.scaler._scale),
                        Mock(tv_globe_scale=self.scaler._scale, tv_globe_project_abi=Mock(return_value=2))):
            with patch("tv_filter.C.CDLL", return_value=library):
                scaler = Scaler()
            self.assertTrue(scaler.available)
            self.assertFalse(scaler.projection_available)
            source = (C.c_uint8 * 4)()
            self.assertIsNone(scaler.project(source, 1, 1, 1, 0, 0, 1))
            self.assertIn("unavailable", scaler.error)
            self.assertIsNotNone(scaler.scale(source, 1, 1, 1, 1))
        with patch("tv_filter.C.CDLL", side_effect=OSError("wrong ELF class")):
            scaler = Scaler()
        self.assertFalse(scaler.projection_available)
        self.assertIsNone(scaler.project(None, 1, 1, 1, 0, 0, .8))
        self.assertIn("wrong ELF class", scaler.error)

    def test_projection_native_validation_and_strides(self):
        fn = self.scaler._project
        source, dest = (C.c_uint8 * 16)(), (C.c_uint8 * 16)()
        valid = [source, 2, 2, 8, dest, 2, 8, 0, 0, .8]
        for index, values in ((0, (None,)), (4, (None,)), (1, (0, -1, 8193, 2147483647)),
                              (2, (0, -1, 8193)), (3, (-1, 0, 7)), (5, (-1, 0, 2049)),
                              (6, (-1, 0, 7)), (7, (-91, 91, math.nan, math.inf)),
                              (8, (math.nan, math.inf)), (9, (0, -1, 65, math.nan, math.inf))):
            for value in values:
                args = valid.copy()
                args[index] = value
                self.assertEqual(fn(*args), -1, (index, value))
        self.assertEqual(fn(source, 2, 2, 8, source, 2, 8, 0, 0, .8), -1)
        self.assertEqual(fn(source, 1, 1, 4, C.byref(source, 1), 1, 4, 0, 0, .8), -1)
        sw, sh, size = 7, 5, 11
        sp, dp = sw*4+3, size*4+5
        source = (C.c_uint8 * (sp*sh+2))(*([0xBD] * (sp*sh+2)))
        dest = (C.c_uint8 * (dp*size+2))(*([0xA9] * (dp*size+2)))
        for y in range(sh):
            for x in range(sw*4):
                source[1+y*sp+x] = (x*17+y*23) % 256
        original = bytes(source)
        self.assertEqual(fn(C.byref(source, 1), sw, sh, sp, C.byref(dest, 1), size, dp,
                            18, 73, .8), 0)
        expected = project_reference(original[1:], sw, sh, size, 18, 73, .8, sp)
        for y in range(size):
            row = bytes(dest[1+y*dp:1+y*dp+size*4])
            self.assertLessEqual(max(abs(a-b) for a, b in zip(row, expected[y*size*4:(y+1)*size*4])), 2)
            self.assertEqual(bytes(dest[1+y*dp+size*4:1+(y+1)*dp]), b"\xa9" * (dp-size*4))
        self.assertEqual((dest[0], dest[-1]), (0xA9, 0xA9))
        self.assertEqual(bytes(source), original)


def benchmark(iterations):
    scaler = Scaler()
    if not scaler.available:
        raise SystemExit(scaler.error)
    if not scaler.projection_available:
        raise SystemExit(scaler._projection_error)
    atlas_width, atlas_height = 1024, 512
    atlas = (C.c_uint8 * (atlas_width*atlas_height*4))()
    C.memset(atlas, 127, C.sizeof(atlas))
    for size in (160, 224):
        source = (C.c_uint8 * (size*size*4))()
        C.memset(source, 127, C.sizeof(source))
        for operation in ("project", "scale", "project+scale"):
            samples = []
            for index in range(iterations+10):
                start = time.perf_counter()
                if operation != "scale":
                    result = scaler.project(atlas, atlas_width, atlas_height, size, 18, 38+index*.2, .8)
                    if result is None:
                        raise SystemExit(scaler.error)
                if operation != "project":
                    result = scaler.scale(result if operation == "project+scale" else source,
                                          size, size, 406, 406)
                    if result is None:
                        raise SystemExit(scaler.error)
                elapsed = (time.perf_counter() - start)*1000
                if index >= 10:
                    samples.append(elapsed)
            print("{} {}x{} zoom=.8 atlas={}x{}: n={} mean={:.3f} ms median={:.3f} ms p95={:.3f} ms".format(
                operation, size, size, atlas_width, atlas_height, iterations,
                statistics.mean(samples), statistics.median(samples),
                sorted(samples)[max(0, (95*iterations+99)//100 - 1)]))


def benchmark_borders(iterations, path):
    scaler = Scaler()
    if not scaler.borders_available:
        raise SystemExit(scaler.error or scaler._borders_error)
    features = json.loads(Path(path).read_text())["features"]
    points = []
    for feature in features:
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        for polygon in polygons:
            for ring in polygon:
                vertices = [xyz(lat, lon) for lon, lat in ring]
                if vertices and ring[0] != ring[-1]:
                    vertices.append(vertices[0])
                for a, b in zip(vertices, vertices[1:]):
                    points.extend((a, b))
    segments = segment_buffer(*points)
    if len(segments)//6 > 200000:
        raise SystemExit("geographic fixture exceeds native segment limit")
    dest = (C.c_uint8 * (406*406*4))()
    for zoom in (.8, 1.5, 4, 8):
        samples = []
        for index in range(iterations+10):
            # Reset outside the timed call, just as a fresh scale() output would.
            C.memset(dest, 0, C.sizeof(dest))
            start = time.perf_counter()
            ok = scaler.overlay_borders(dest, 406, 406, segments, 18, 38+index*.2, zoom)
            elapsed = (time.perf_counter() - start)*1000
            if not ok:
                raise SystemExit(scaler.error)
            if index >= 10:
                samples.append(elapsed)
        print("borders 406x406 zoom={} segments={} n={} mean={:.3f} ms median={:.3f} ms p95={:.3f} ms".format(
            zoom, len(segments)//6, iterations, statistics.mean(samples), statistics.median(samples),
            sorted(samples)[max(0, (95*iterations+99)//100 - 1)]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--benchmark-borders", action="store_true")
    parser.add_argument("--geography", type=Path, default=ROOT / "assets" / "globe.json")
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()
    if args.benchmark or args.benchmark_borders:
        if args.iterations < 1:
            parser.error("--iterations must be positive")
        if args.benchmark:
            benchmark(args.iterations)
        if args.benchmark_borders:
            benchmark_borders(args.iterations, args.geography)
    else:
        unittest.main(argv=[__file__], verbosity=2)
