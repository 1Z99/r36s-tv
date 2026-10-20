#!/usr/bin/env python3
"""Check country animations offscreen without taking the handheld display."""

import argparse
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--country", default="SY")
    parser.add_argument("--live-country", action="store_true", help="Test zero-data startup and a single country download")
    parser.add_argument("--effects", action="store_true", help="Also check idle rotation, hover pause and eased zoom")
    args = parser.parse_args()
    sys.path.insert(0, str(args.app_root))
    os.environ["SDL_VIDEODRIVER"] = "offscreen"
    os.environ["SDL_RENDER_DRIVER"] = "software"
    mali = "/usr/lib/aarch64-linux-gnu/libMali.so"
    if Path(mali).is_file():
        os.environ["SDL_VIDEO_EGL_DRIVER"] = mali
        os.environ["SDL_VIDEO_GL_DRIVER"] = mali
    import app

    display, ui = app.Display(True), None
    try:
        with tempfile.TemporaryDirectory(prefix="tv227-animation-") as data:
            ui = app.App(display, Path(data), demo=not args.live_country)
            startup_count = len(ui.stations)
            downloads = []
            if args.live_country:
                import tv_backend
                original_download = tv_backend._download

                def download(url, *values, **options):
                    downloads.append(url)
                    return original_download(url, *values, **options)

                tv_backend._download = download
                assert startup_count == 0 and not ui.workers
            else:
                cached = app.Catalog(Path.home()/".local/share/melkor227-tv", app.ASSETS).load_cached()
                if cached:
                    ui.stations = cached
                    ui.refilter()
            ui.notice_until = 0
            ui.select_country(args.country)
            globe_times = []
            vector_times = []
            original_draw = ui.globe.draw
            original_vector = ui.globe._vector_map

            def vector_draw(*values, **options):
                begin = time.monotonic()
                result = original_vector(*values, **options)
                vector_times.append(time.monotonic()-begin)
                return result

            def globe_draw(*values, **options):
                begin = time.monotonic()
                result = original_draw(*values, **options)
                globe_times.append(time.monotonic()-begin)
                return result

            ui.globe.draw = globe_draw
            ui.globe._vector_map = vector_draw
            started, times, captured = time.monotonic(), [], set()
            maximum_reveals = 0
            for frame in range(600):
                tick = time.monotonic()
                ui.update()
                active = sum(item["start"] is not None and item["reveal"] < len(item["content"])
                             for item in ui.channel_reveals.values())
                maximum_reveals = max(maximum_reveals, active)
                if args.live_country:
                    assert active <= 1, "Multiple names animated together"
                ui.draw()
                elapsed = time.monotonic()-started
                settled = all(item["reveal"] == len(item["content"]) for item in ui.channel_reveals.values())
                pending = any(name.startswith("country:") for name in ui.workers)
                stage = "start" if frame == 0 else "arrival" if ui.globe.flight is None and settled and not pending else "approach" if elapsed >= .6 else None
                if args.output and stage and stage not in captured:
                    display.screenshot(args.output / ("country-"+stage+".png"))
                    captured.add(stage)
                display.sdl.SDL_RenderPresent(display.renderer)
                times.append(time.monotonic()-tick)
                if ui.modal == "error":
                    raise RuntimeError(ui.error)
                if (ui.globe.flight is None and settled and not pending
                        and time.monotonic() > max(ui.globe.moving_until, ui.globe.fade_until)):
                    break
                time.sleep(max(0., 1/30-(time.monotonic()-tick)))
            else:
                raise RuntimeError("Animation did not finish")
            # Border tracing keeps presenting, but must not rerasterize geography.
            builds, camera = len(vector_times), ui.globe.key
            for _ in range(3):
                ui.update()
                ui.draw()
                display.sdl.SDL_RenderPresent(display.renderer)
            assert len(vector_times) == builds and ui.globe.key == camera, "Border animation rebuilt geography"
            assert .8 <= ui.globe.zoom <= 8
            if args.output:
                ui.draw()
                display.screenshot(args.output / "country-settled.png")
            if args.live_country:
                assert downloads == [tv_backend.COUNTRY_URL.format(args.country.lower())]
                assert all(row["countryCode"] == args.country for row in ui.stations)
                assert not ui.catalog.path.exists(), "World catalog was created"
            country_count = len(ui.stations)
            settled_zoom, revealed_names = ui.globe.zoom, len(ui.channel_reveals)
            effects = {}
            if args.effects:
                ui.country, ui.query, ui.tab = "", "", 0
                ui.stations, ui.channel_reveals = [], {}
                ui.refilter()
                ui.globe.cancel_flight()
                ui.globe.lat, ui.globe.lon, ui.globe.zoom = 18., -20., .8
                ui.globe.moving_until = 0
                ui.pointer_x, ui.pointer_y = 620., 440.
                ui.last_input = ui.last_globe_touch = time.monotonic()-3
                started_spin, longitude, samples = time.monotonic(), ui.globe.lon, []
                while time.monotonic()-started_spin < 1.2:
                    tick = time.monotonic()
                    ui.update()
                    if ui.dirty:
                        ui.draw()
                        display.sdl.SDL_RenderPresent(display.renderer)
                        ui.dirty = False
                        samples.append((time.monotonic()-tick)*1000)
                    time.sleep(max(.001, 1/30-(time.monotonic()-tick)))
                assert ui.globe.lon > longitude and ui.idle_spinning
                effects["idle_rotation"] = True
                effects["world_render_median_ms"] = round(statistics.median(samples), 2)
                ui.move_pointer(app.MAP_CENTER_X, app.MAP_CENTER_Y)
                ui.last_input -= 3
                ui.last_globe_touch -= 3
                paused = ui.globe.lon
                for _ in range(3):
                    ui.update()
                    ui.draw()
                    display.sdl.SDL_RenderPresent(display.renderer)
                assert not ui.idle_spinning and ui.globe.lon == paused
                effects["hover_pause"] = True
                assert ui.globe.scaler.available, ui.globe.scaler.error
                assert ui.globe.scaler.projection_available
                assert ui.globe.scaler.borders_available and ui.globe.world_borders_cached
                effects["native_filter"] = True
                effects["native_world_projection"] = True
                effects["world_borders_visible"] = True
                if args.output:
                    display.screenshot(args.output / "smooth-world.png")
                origin = ui.globe.zoom
                ui.action("plus")
                assert ui.globe.zoom == origin and ui.globe.flight is not None
                while ui.globe.flight is not None:
                    ui.update()
                    ui.draw()
                    display.sdl.SDL_RenderPresent(display.renderer)
                    time.sleep(.01)
                assert ui.globe.zoom > origin
                effects["eased_zoom"] = True
            import json
            print(json.dumps(dict(country=args.country, frames=len(times), elapsed_seconds=round(elapsed, 2),
                                  median_frame_ms=round(statistics.median(times)*1000, 2),
                                  median_globe_ms=round(statistics.median(globe_times)*1000, 2),
                                  vector_closeup_build_ms=round(max(vector_times, default=0)*1000, 2),
                                  target_zoom=round(settled_zoom, 2), revealed_names=revealed_names,
                                  startup_stations=startup_count, country_stations=country_count, downloads=downloads,
                                  max_simultaneous_name_reveals=maximum_reveals,
                                  animation_settled=True, map_raster_cached=True,
                                  effects=effects, display="offscreen"), indent=2))
    finally:
        if ui:
            ui.close()
        display.close()


if __name__ == "__main__":
    main()
