"""Asset provenance, Local AI geometry/timing, and real headless SDL rendering."""

import ctypes as C
import hashlib
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile
import zlib

import tv_style as style


ASSETS = Path(__file__).resolve().parent / 'assets'
HASHES = {
    'intro-frames.zip': '21606bfe3c46d72648f9fefa9c5a9e6866efc466143cbfddf3d9725f078e90b6',
    'logo.rgba': 'af6eba97a35fac1f00a17d3dcc26dc5de3edbbec373a5bcf3b0817e807fe0e11',
    'intro-background.rgba': '862a1b573a058eeb373275674aaff92e355f5066ebf8670054cdef78707801e6',
    'DejaVuSansMono-Bold.ttf': '0d3c03d1b667192f91223660a3163325cf83132662fe4d9f7d6e596bf7a995c2',
    'AI-LICENSE': '7810348e60773b82f9601339763343fbe1ec9e501d00f3fcc190166188c496c8',
}


def fake_display():
    display = Mock()
    display.sdl.SDL_RWFromConstMem.return_value = 1
    display.sdl.SDL_LoadBMP_RW.return_value = 2
    display.sdl.SDL_CreateTextureFromSurface.side_effect = range(100, 228)
    display.sdl.SDL_SetTextureBlendMode.return_value = 0
    display.sdl.SDL_UpdateTexture.return_value = 0
    display.sdl.SDL_SetTextureAlphaMod.return_value = 0
    display.sdl.SDL_GetRenderTarget.return_value = None
    display.sdl.SDL_SetRenderTarget.return_value = 0
    display.sdl.SDL_RenderClear.return_value = 0
    display.texture.return_value = 900
    return display


class StyleTests(unittest.TestCase):
    def test_local_ai_matrix_scramble_preserves_spaces_and_reveals_fully(self):
        self.assertEqual(style.scramble_text("Syria TV", 7), "S+=34 IJ")
        text = "Syria TV  HD"
        a, b = style.scramble_text(text, 1), style.scramble_text(text, 2)
        self.assertEqual(len(a), len(text))
        self.assertNotEqual(a, b)
        self.assertEqual([i for i, ch in enumerate(a) if ch.isspace()], [i for i, ch in enumerate(text) if ch.isspace()])
        self.assertTrue(all(ch.isspace() or ch in style.SCRAMBLE_CHARS for ch in a))
        item = dict(content=text, reveal=0, pending=False)
        for _ in range(20):
            style.advance_scramble(item, style.SCRAMBLE_TICK)
        self.assertEqual(item["reveal"], len(text))
        self.assertFalse(style.advance_scramble(item, .1))

    def test_assets_match_release_hashes(self):
        for name, digest in HASHES.items():
            with self.subTest(asset=name):
                self.assertEqual(hashlib.sha256((ASSETS / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((ASSETS / 'logo.rgba').stat().st_size, 108 * 36 * 4)
        self.assertEqual((ASSETS / 'intro-background.rgba').stat().st_size, 640 * 480 * 4)

    def test_intro_archive_is_exactly_19_ordered_frames(self):
        with zipfile.ZipFile(ASSETS / 'intro-frames.zip') as archive:
            manifest = json.loads(archive.read('manifest.json'))
            names = ['frame-%03d.bmp' % i for i in range(1, 20)]
            self.assertEqual(manifest['frames'], names)
            self.assertEqual(set(archive.namelist()), set(names + ['manifest.json']))
            self.assertEqual((manifest['duration'], manifest['fade_seconds']), (1.67, 0.8))
            for name in names:
                data = archive.read(name)
                self.assertEqual(data[:2], b'BM')
                self.assertEqual(struct.unpack_from('<ii', data, 18), (640, 480))

    def test_import_does_not_load_sdl_or_ai(self):
        spec = importlib.util.spec_from_file_location('isolated_tv_style', style.__file__)
        module = importlib.util.module_from_spec(spec)
        with patch('ctypes.CDLL', side_effect=AssertionError('global SDL load')):
            spec.loader.exec_module(module)
        self.assertFalse(any(name in vars(module) for name in ('SDL', 'TTF', 'app', 'project_ai_r36s')))

    def test_exact_keyboard_and_geometry(self):
        self.assertEqual(style.KEYS, ('1234567890-+*/=:', 'qwertyuiop', 'asdfghjkl',
                                     'zxcvbnm.,!?', ('SPACE', 'DEL', 'CLEAR', 'FIND', 'CLOSE')))
        expected_x = (
            (12, 51, 90, 128, 167, 206, 245, 284, 322, 361, 400, 439, 478, 516, 555, 594),
            (12, 75, 137, 200, 263, 325, 388, 451, 513, 576),
            (12, 82, 153, 224, 294, 364, 435, 506, 576),
            (12, 68, 125, 181, 238, 294, 350, 407, 463, 520, 576),
            (12, 153, 294, 435, 576),
        )
        for row, xs in enumerate(expected_x):
            boxes = style.key_geometry(row)
            self.assertEqual([box[0] for box in boxes], list(xs))
            self.assertEqual(len(boxes), len(style.KEYS[row]))
            self.assertEqual(boxes[0][0], 12)
            self.assertEqual(boxes[-1][0] + boxes[-1][2], 628)
            for x, y, w, h in boxes:
                self.assertEqual((y, w, h), (258 + row * 38, 34 if row == 0 else 52, 30))
            for left, right in zip(boxes, boxes[1:]):
                self.assertGreaterEqual(right[0] - left[0] - left[2], 4)

    def test_keyboard_uses_bold_centered_labels_and_selected_colors(self):
        display = fake_display()
        style.keyboard(display, 4, 3)
        self.assertEqual(display.text.call_count, sum(map(len, style.KEYS)))
        for call in display.text.call_args_list:
            self.assertEqual(call.kwargs, dict(size=15, width=34 if call.args[2] == 264 else 52,
                                               bold=True, align='center'))
        display.text.assert_any_call('FIND', 435, 416, style.BG, size=15,
                                     width=52, bold=True, align='center')
        display.rect.assert_any_call(435, 410, 52, 30, style.GREEN)
        display.rect.assert_any_call(435, 410, 52, 30, style.GREEN, outline=True)

    def test_pointer_deadzone_and_exact_ai_curve(self):
        for raw in (-5000, -1, 0, 1, 5000):
            self.assertEqual(style.pointer_motion(raw), 0)
        for raw in (5001, 17000, 32767, -5001, -17000, -32767, -32768):
            expected = (abs(raw) - 5000) / 27767
            self.assertEqual(style.pointer_motion(raw), -expected if raw < 0 else expected)
        for mx, my in ((0, 0), (0.5, -0.25), (-1, 1), (0, -0.75)):
            self.assertEqual(style.pointer_speed(mx, my), 260 + 1000 * max(abs(mx), abs(my)))
            self.assertEqual(style.pointer_speed(mx, my, keyboard=True),
                             210 + 800 * max(abs(mx), abs(my)))

    def test_rgba_upload_and_failure_cleanup(self):
        display = fake_display()
        texture = style.load_rgba(display, ASSETS / 'logo.rgba', 108, 36)
        self.assertEqual(texture, 900)
        display.texture.assert_called_once_with(108, 36)
        call = display.sdl.SDL_UpdateTexture.call_args
        self.assertEqual(C.string_at(call.args[2], 108 * 36 * 4), (ASSETS / 'logo.rgba').read_bytes())
        self.assertEqual(call.args[3], 432)
        display.sdl.SDL_SetTextureBlendMode.assert_called_once_with(900, 1)
        for function in ('SDL_UpdateTexture', 'SDL_SetTextureBlendMode'):
            display = fake_display()
            getattr(display.sdl, function).return_value = -1
            with self.assertRaises(RuntimeError):
                style.load_rgba(display, ASSETS / 'logo.rgba', 108, 36)
            display.sdl.SDL_DestroyTexture.assert_called_once_with(900)
        display = fake_display()
        with self.assertRaises(ValueError):
            style.load_rgba(display, ASSETS / 'logo.rgba', 1, 1)
        display.texture.assert_not_called()

    def test_intro_clock_starts_on_draw_and_all_frames_are_reachable(self):
        display, clock = fake_display(), Mock(return_value=100.0)
        loaded = []

        def create_frame(renderer, surface):
            if loaded:
                display.sdl.SDL_RenderPresent.assert_called_once_with(display.renderer)
            loaded.append(100 + len(loaded))
            return loaded[-1]

        display.sdl.SDL_CreateTextureFromSurface.side_effect = create_frame
        intro = style.Intro(display, ASSETS, clock=clock)
        self.addCleanup(intro.close)
        self.assertTrue(intro.active, intro.error)
        self.assertEqual(len(intro.textures), 19)
        self.assertTrue(intro.covers_ui)
        self.assertIsNone(intro.started)
        clock.assert_not_called()
        display.copy.assert_called_once_with(100, (0, 0, 640, 480))
        display.sdl.SDL_RenderPresent.assert_called_once_with(display.renderer)
        intro.draw()
        self.assertEqual(intro.started, 100)
        self.assertEqual(intro.frame_index, 0)
        for index in range(1, 19):
            clock.return_value = 100 + (index + 0.25) * 1.67 / 19
            intro.draw()
            self.assertEqual(intro.frame_index, index)
        clock.return_value = 101.671
        intro.draw()
        self.assertFalse(intro.active)
        self.assertFalse(intro.covers_ui)
        self.assertEqual(display.sdl.SDL_DestroyTexture.call_count, 19)
        intro.close()
        intro.draw()
        self.assertEqual(display.sdl.SDL_DestroyTexture.call_count, 19)

    def test_intro_coverage_fade_and_exact_end(self):
        display, clock = fake_display(), Mock(return_value=0.0)
        intro = style.Intro(display, ASSETS, clock=clock)
        self.addCleanup(intro.close)
        intro.draw()
        for elapsed, covers, blend, alpha in ((0.81, True, 0, 255), (0.83, False, 0, 255),
                                              (0.86, False, 0, 255), (0.88, False, 1, 252),
                                              (1.27, False, 1, 127),
                                              (1.669, False, 1, 0)):
            clock.return_value = elapsed
            self.assertEqual(intro.covers_ui, covers)
            intro.draw()
            display.sdl.SDL_SetTextureBlendMode.assert_called_with(intro.texture, blend)
            display.sdl.SDL_SetTextureAlphaMod.assert_called_with(intro.texture, alpha)
        clock.return_value = 1.67
        intro.draw()
        self.assertFalse(intro.active)
        self.assertIsNone(intro.texture)

    def test_missing_intro_is_inactive(self):
        with tempfile.TemporaryDirectory() as directory:
            intro = style.Intro(fake_display(), directory)
            self.assertFalse(intro.active)
            self.assertFalse(intro.covers_ui)
            intro.draw()
            intro.close()

    def test_intro_without_fade_remains_opaque_until_end(self):
        display, clock = fake_display(), Mock(return_value=0.0)
        intro = style.Intro(display, ASSETS, clock=clock)
        self.addCleanup(intro.close)
        intro.fade_seconds = 0
        intro.draw()
        clock.return_value = 1.66
        intro.draw()
        display.sdl.SDL_SetTextureBlendMode.assert_called_with(intro.texture, 0)
        display.sdl.SDL_SetTextureAlphaMod.assert_called_with(intro.texture, 255)
        clock.return_value = 1.67
        intro.draw()
        self.assertFalse(intro.active)

    def test_intro_decode_failure_releases_partial_preload(self):
        display = fake_display()
        display.sdl.SDL_LoadBMP_RW.side_effect = (2, None)
        with patch('sys.stderr'):
            intro = style.Intro(display, ASSETS)
        self.assertFalse(intro.active)
        self.assertIn('decode', intro.error)
        self.assertFalse(intro.textures)
        display.sdl.SDL_FreeSurface.assert_called_once_with(2)
        display.sdl.SDL_DestroyTexture.assert_called_once_with(100)

    def test_intro_rejects_invalid_manifest_and_bitmap(self):
        with zipfile.ZipFile(ASSETS / 'intro-frames.zip') as archive:
            manifest = json.loads(archive.read('manifest.json'))
        for change in ({'width': 1}, {'duration': float('nan')}, {'duration': 0},
                       {'fade_seconds': 2}, {'frames': []}, {'frames': [None]}, {}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                with zipfile.ZipFile(Path(directory) / 'intro-frames.zip', 'w') as archive:
                    archive.writestr('manifest.json', json.dumps(dict(manifest, **change)))
                    archive.writestr('frame-001.bmp', bytes(54))
                with patch('sys.stderr'):
                    intro = style.Intro(fake_display(), directory)
                self.assertFalse(intro.active)
                self.assertTrue(intro.error)

    def test_cursor_builds_once_restores_target_and_closes_once(self):
        display = fake_display()
        display._tv_style_cursor = None
        display.sdl.SDL_GetRenderTarget.return_value = 123
        pointer = style.cursor(display, 24, 60)
        display.texture.assert_called_once_with(21, 25, target=True)
        display.sdl.SDL_SetRenderTarget.assert_called_with(display.renderer, 123)
        display.line.reset_mock()
        for i in range(100):
            self.assertIs(style.cursor(display, i, 70), pointer)
        display.line.assert_not_called()
        self.assertEqual(display.texture.call_count, 1)
        display.copy.assert_called_with(900, (99, 70, 21, 25))
        pointer.close()
        pointer.close()
        pointer.draw(0, 0)
        display.sdl.SDL_DestroyTexture.assert_called_once_with(900)

    def test_cursor_build_failure_restores_target_and_frees_texture(self):
        display = fake_display()
        display.sdl.SDL_GetRenderTarget.return_value = 123
        display.line.side_effect = RuntimeError('renderer failed')
        with self.assertRaisesRegex(RuntimeError, 'renderer failed'):
            style.Cursor(display)
        display.sdl.SDL_SetRenderTarget.assert_called_with(display.renderer, 123)
        display.sdl.SDL_DestroyTexture.assert_called_once_with(900)


def reference_cursor(display, x, y):
    """Original Local AI scanlines, rendered directly rather than to a cache."""
    points = [(0, 0), (0, 20), (5, 15), (10, 23), (14, 21), (9, 13), (18, 13)]
    for ox, oy, color in ((2, 2, style.SHADOW), (0, 0, style.GREEN)):
        for row in range(23):
            scan_y = row + 0.5
            intersections = []
            for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
                if y1 == y2 or not min(y1, y2) <= scan_y < max(y1, y2):
                    continue
                intersections.append(x1 + (scan_y - y1) * (x2 - x1) / (y2 - y1))
            intersections.sort()
            for index in range(0, len(intersections) - 1, 2):
                left, right = math.ceil(intersections[index]), math.floor(intersections[index + 1])
                if right >= left:
                    display.line(x + ox + left, y + oy + row, x + ox + right, y + oy + row, color)
    for start, end in zip(points, points[1:] + points[:1]):
        display.line(x + start[0], y + start[1], x + end[0], y + end[1], style.SHADOW)


class HeadlessRenderTests(unittest.TestCase):
    def setUp(self):
        from app import Display
        env = patch.dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_RENDER_DRIVER='software')
        env.start()
        self.addCleanup(env.stop)
        self.d = Display(desktop=True)
        self.addCleanup(self.d.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def pixels(self):
        buffer = (C.c_ubyte * (640 * 480 * 4))()
        result = self.d.sdl.SDL_RenderReadPixels(self.d.renderer, None, 0x16762004, buffer, 2560)
        self.assertEqual(result, 0)
        return bytes(buffer)

    def screenshot(self, name):
        path = Path(self.temp.name) / (name + '.png')
        self.d.screenshot(path)
        raw = path.read_bytes()
        self.assertEqual(raw[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack_from('>II', raw, 16), (640, 480))
        compressed, offset = b'', 8
        while offset < len(raw):
            length = struct.unpack_from('>I', raw, offset)[0]
            if raw[offset + 4:offset + 8] == b'IDAT':
                compressed += raw[offset + 8:offset + 8 + length]
            offset += length + 12
        scanlines = zlib.decompress(compressed)
        self.assertEqual(len(scanlines), 480 * 2561)
        self.assertTrue(all(scanlines[y * 2561] == 0 for y in range(480)))
        return b''.join(scanlines[y * 2561 + 1:(y + 1) * 2561] for y in range(480))

    def test_cached_cursor_matches_original_pixels_and_screenshot(self):
        pointer = style.Cursor(self.d)
        self.addCleanup(pointer.close)
        for x, y in ((100, 100), (0, 0), (618, 450)):
            self.d.rect(0, 0, 640, 480, style.PANEL)
            reference_cursor(self.d, x, y)
            expected = self.pixels()
            self.d.rect(0, 0, 640, 480, style.PANEL)
            pointer.draw(x, y)
            self.assertEqual(self.pixels(), expected, 'Cursor differs at %s,%s' % (x, y))
        self.assertEqual(self.screenshot('cursor'), expected)

    def test_cursor_clips_its_cached_pixels_at_screen_edges(self):
        pointer = style.Cursor(self.d)
        self.addCleanup(pointer.close)
        self.d.rect(0, 0, 640, 480, style.PANEL)
        reference_cursor(self.d, 100, 100)
        reference = self.pixels()
        for x, y in ((629, 470), (-4, -3)):
            expected = bytearray(bytes(style.PANEL) * (640 * 480))
            for cy in range(25):
                for cx in range(21):
                    if 0 <= x + cx < 640 and 0 <= y + cy < 480:
                        src = ((100 + cy) * 640 + 100 + cx) * 4
                        dst = ((y + cy) * 640 + x + cx) * 4
                        expected[dst:dst + 4] = reference[src:src + 4]
            self.d.rect(0, 0, 640, 480, style.PANEL)
            pointer.draw(x, y)
            self.assertEqual(self.pixels(), bytes(expected))

    def test_logo_texture_preserves_rgba_colors(self):
        texture = style.load_rgba(self.d, ASSETS / 'logo.rgba', 108, 36)
        self.addCleanup(self.d.sdl.SDL_DestroyTexture, texture)
        self.d.rect(0, 0, 640, 480, style.BG)
        self.d.copy(texture, (0, 0, 108, 36))
        pixels = self.screenshot('logo')
        raw = (ASSETS / 'logo.rgba').read_bytes()
        opaque = 0
        for y in range(36):
            for x in range(108):
                source = raw[(y * 108 + x) * 4:(y * 108 + x + 1) * 4]
                actual = pixels[(y * 640 + x) * 4:(y * 640 + x + 1) * 4]
                if source[3] == 255:
                    opaque += 1
                    self.assertEqual(actual[:3], source[:3])
                elif source[3] == 0:
                    self.assertEqual(actual[:3], bytes(style.BG[:3]))
        self.assertGreater(opaque, 100)

    def test_intro_first_frame_pixels_and_fade_screenshots(self):
        clock = Mock(return_value=0.0)
        intro = style.Intro(self.d, ASSETS, clock=clock)
        self.addCleanup(intro.close)
        self.assertTrue(intro.active, intro.error)
        intro.draw()
        first = self.screenshot('intro-first')
        with zipfile.ZipFile(ASSETS / 'intro-frames.zip') as archive:
            bmp = archive.read('frame-001.bmp')
        self.assertEqual(struct.unpack_from('<H', bmp, 28)[0], 24)
        offset = struct.unpack_from('<I', bmp, 10)[0]
        expected = bytearray(640 * 480 * 4)
        for y in range(480):
            for x in range(640):
                index = offset + ((479 - y) * 640 + x) * 3
                b, g, r = bmp[index:index + 3]
                start = (y * 640 + x) * 4
                expected[start:start + 4] = bytes((r, g, b, 255))
        self.assertEqual(first, bytes(expected))
        self.d.rect(0, 0, 640, 480, style.PANEL)
        clock.return_value = 1.27
        intro.draw()
        faded = self.screenshot('intro-fade')
        self.assertNotEqual(first, faded)
        self.assertFalse(intro.covers_ui)
        clock.return_value = 1.67
        self.d.rect(0, 0, 640, 480, style.PANEL)
        intro.draw()
        self.assertEqual(self.pixels()[:4], bytes(style.PANEL))
        self.assertFalse(intro.active)

    def test_keyboard_headless_screenshot(self):
        parameters = inspect.signature(self.d.text).parameters
        if 'bold' not in parameters or 'align' not in parameters:
            self.skipTest('Primary Display.text bold/align integration is not available yet')
        self.d.rect(0, 0, 640, 480, style.BG)
        style.keyboard(self.d, 4, 3)
        pixels = self.screenshot('keyboard')
        self.assertEqual(pixels[(411 * 640 + 436) * 4:(411 * 640 + 436) * 4 + 4], bytes(style.GREEN))
        self.assertEqual(pixels[(259 * 640 + 13) * 4:(259 * 640 + 13) * 4 + 4], bytes(style.PANEL_3))
        x, y, w, h = style.key_geometry(4)[3]
        colors = {pixels[(py * 640 + px) * 4:(py * 640 + px) * 4 + 3]
                  for py in range(y + 7, y + 23) for px in range(x + 3, x + w - 3)}
        self.assertIn(bytes(style.BG[:3]), colors)


if __name__ == '__main__':
    unittest.main()
