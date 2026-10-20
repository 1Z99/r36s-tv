#!/usr/bin/env python3
"""Bounded on-device rendering/playback check; never writes user favorites."""

import argparse
import ctypes as C
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, default=Path("/roms/tools/melkor227-tv"))
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--url")
    parser.add_argument("--name", default="Playback verification")
    parser.add_argument("--seconds", type=int, default=12)
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--serve-dir", type=Path, help="Serve local HLS fixtures on loopback")
    parser.add_argument("--exercise-quality", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("/tmp"))
    args = parser.parse_args()
    server = None
    if args.serve_dir:
        from functools import partial
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        import threading
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(args.serve_dir)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        args.url = "http://127.0.0.1:%s/master.m3u8" % server.server_port
    sys.path.insert(0, str(args.app_root))
    if args.headless:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    else:
        os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
        mali = "/usr/lib/aarch64-linux-gnu/libMali.so"
        if Path(mali).is_file():
            os.environ.setdefault("SDL_VIDEO_EGL_DRIVER", mali)
            os.environ.setdefault("SDL_VIDEO_GL_DRIVER", mali)
    os.environ["SDL_RENDER_DRIVER"] = "software"
    import app

    display, ui = app.Display(desktop=args.headless), None
    report = {"driver": os.environ["SDL_VIDEODRIVER"], "matrix": args.matrix}
    try:
        with tempfile.TemporaryDirectory(prefix="tv227-smoke-") as data:
            ui = app.App(display, Path(data), demo=True)
            ui.demo = False
            ui.notice_until = 0
            if args.catalog:
                ui.stations = json.loads(args.catalog.read_text())
                ui.refilter()
            ui.draw()
            display.screenshot(args.output / "tv227-device-browse.png")
            display.sdl.SDL_RenderPresent(display.renderer)
            report["stations"] = len(ui.stations)
            report["joystick_axes"] = display.axes
            ui.action("search")
            ui.draft = "arabic"
            ui.draw()
            display.screenshot(args.output / "tv227-device-search.png")
            ui.action("search")
            report["search_results"] = len(ui.rows)
            ui.select_country("SY")
            ui.draw()
            display.screenshot(args.output / "tv227-device-country.png")
            report["country_results"] = len(ui.rows)
            if args.url:
                station = dict(uuid="verification", name=args.name, url=args.url,
                               countryCode="", country="", language="", tags="")
                if args.matrix:
                    ui.slots = [dict(station, uuid="verification-%s" % i) for i in range(4)]
                    ui.matrix()
                else:
                    ui.play(station)
                wait = time.monotonic()
                while ui.pending_streams and time.monotonic()-wait < 9:
                    ui.update()
                    ui.draw()
                    display.sdl.SDL_RenderPresent(display.renderer)
                    time.sleep(.02)
                if not ui.players:
                    raise RuntimeError(ui.error or "No player initialized")
                counts = {cell: 0 for cell in ui.players}
                first_frames = {}
                for cell, player in ui.players.items():
                    original = player.frame

                    def counted(cell=cell, original=original):
                        frame = original()
                        if frame is not None:
                            counts[cell] += 1
                            first_frames.setdefault(cell, time.monotonic())
                        return frame

                    player.frame = counted
                started, switched, captured = time.monotonic(), False, False
                timings = {"update": [], "draw": [], "present": []}
                event = C.create_string_buffer(64)
                while time.monotonic()-started < args.seconds:
                    while display.sdl.SDL_PollEvent(event):
                        if struct.unpack_from("<I", event.raw)[0] == 0x603 and event.raw[12] == 0:
                            raise KeyboardInterrupt("Stopped by handheld B button")
                    tick = time.perf_counter()
                    ui.update()
                    timings["update"].append(time.perf_counter()-tick)
                    if args.matrix and not switched and time.monotonic()-started > args.seconds/2:
                        ui.audio_focus(1)
                        switched = True
                    tick = time.perf_counter()
                    ui.draw()
                    timings["draw"].append(time.perf_counter()-tick)
                    if not captured and time.monotonic()-started > args.seconds*.75:
                        display.screenshot(args.output / ("tv227-device-matrix.png" if args.matrix else "tv227-device-video.png"))
                        captured = True
                    tick = time.perf_counter()
                    display.sdl.SDL_RenderPresent(display.renderer)
                    timings["present"].append(time.perf_counter()-tick)
                    time.sleep(.01)
                report["frames"] = counts
                import statistics
                report["median_ms"] = {key: round(statistics.median(values)*1000, 2) for key, values in timings.items()}
                report["elapsed"] = round(time.monotonic()-started, 2)
                report["status"] = {cell: player.poll() for cell, player in ui.players.items()}
                report["selected_sources"] = {cell: row.get("url") for cell, row in ui.playing.items()}
                report["first_frame_seconds"] = {cell: round(stamp-started, 2) for cell, stamp in first_frames.items()}
                if any(status.get("error") for status in report["status"].values()):
                    raise RuntimeError("Playback reported an error")
                if not all(count > 2 for count in counts.values()):
                    raise RuntimeError("No sustained decoded video frames")
                if args.exercise_quality:
                    ui.action("menu")
                    ui.draw()
                    display.screenshot(args.output / "tv227-device-settings.png")
                    ui.action("ok")
                    ui.draw()
                    display.screenshot(args.output / "tv227-device-quality.png")
                    old = ui.statuses[ui.cell]
                    wanted = next(((i, row) for i, row in enumerate(ui.quality_rows)
                                   if row and row.get("height") and row["height"] != old.get("source_height")), None)
                    if wanted is None:
                        raise RuntimeError("Fixture has no alternate resolution")
                    ui.menu_index, row = wanted
                    ui.action("ok")
                    deadline = time.monotonic()+10
                    while time.monotonic() < deadline:
                        ui.update()
                        ui.draw()
                        display.sdl.SDL_RenderPresent(display.renderer)
                        current = ui.statuses.get(ui.cell, {})
                        if (current.get("source_height") == row["height"] and current.get("playing")
                                and ui.cell in ui.frame_cells and current.get("audio_active")):
                            break
                        time.sleep(.02)
                    else:
                        raise RuntimeError("Manual quality selection did not take effect: %s" % current)
                    report["quality_switch"] = {"before": old, "after": current, "requested": row}
                    if row.get("fps") and abs((current.get("source_fps") or 0)-row["fps"]) > .1:
                        raise RuntimeError("Manual frame-rate selection did not take effect")
                ui.action("back")
                report["back_to_globe"] = ui.view == "browse"
                report["all_players_closed"] = not ui.players and not ui.pending_streams and not ui.video_textures
                if not report["all_players_closed"]:
                    raise RuntimeError("B left a decoder or stream running")
            ui.close()
            ui = None
    finally:
        if ui:
            ui.close()
        display.close()
        if server:
            server.shutdown()
            server.server_close()
        print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
