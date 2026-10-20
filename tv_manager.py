#!/usr/bin/env python3
"""227 TV controller-driven install, update, repair and uninstall manager."""

import argparse
import ctypes as C
import json
import os
from pathlib import Path
import queue
import struct
import sys
import threading
import time

from tv_installer import Cancelled, Installer, VERSION, compatible, discover_cards

ACTIONS = ("Install", "Update", "Repair", "Uninstall TV")


class Manager:
    def __init__(self, options, cards=None, worker=Installer):
        self.options, self.worker = options, worker
        self.cards = discover_cards() if cards is None else cards
        self.card, self.focus = None, 0
        self.state, self.yes, self.running = "home", False, True
        self.detail_scroll = 0
        self.progress, self.result, self.error = {}, {}, options.get("discovery_error", "")
        self.events, self.cancel = queue.Queue(), threading.Event()
        self.thread = None

    def start(self):
        self.state, self.error, self.progress = "working", "", {}
        self.cancel.clear()
        prefix = self.cards[self.card]["path"]

        def work():
            try:
                result = self.worker(prefix, emit=self.events.put, cancel=self.cancel, **self.options).run(
                    ("install", "update", "repair", "uninstall")[self.focus-1])
                self.events.put(dict(phase="result", state="success", result=result))
            except Cancelled as exc:
                self.events.put(dict(phase="result", state="cancelled", error=str(exc)))
            except Exception as exc:
                self.events.put(dict(phase="result", state="failed", error=str(exc)))

        self.thread = threading.Thread(target=work, name="tv-install", daemon=False)
        self.thread.start()

    def update(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event["phase"] == "result":
                self.state, self.result = event["state"], event.get("result", {})
                self.error = event.get("error", "")
            else:
                self.progress = event

    def button(self, action):
        if self.state == "home":
            if action == "back":
                self.running = False
            elif action in ("up", "down"):
                self.focus = (self.focus+(1 if action == "down" else -1)) % (len(ACTIONS)+1)
            elif action in ("left", "right") or (action == "ok" and self.focus == 0):
                current = -1 if self.card is None else self.card
                self.card = (current+(1 if action != "left" else -1)) % len(self.cards)
                self.focus = 0
            elif action == "ok" and self.focus:
                if self.card is None:
                    self.error = "Choose SD1 or SD2 first."
                    self.focus = 0
                elif not self.cards[self.card]["available"]:
                    self.error = "That card is not mounted and writable."
                    self.focus = 0
                else:
                    self.state, self.yes = "confirm", False
        elif self.state in ("confirm", "cancel_confirm"):
            if action == "back":
                self.state = "working" if self.state == "cancel_confirm" else "home"
            elif action in ("up", "down", "left", "right"):
                self.yes = not self.yes
            elif action == "ok":
                if self.state == "cancel_confirm":
                    if self.yes:
                        self.cancel.set()
                    self.state = "working"
                elif self.yes:
                    self.start()
                else:
                    self.state = "home"
        elif self.state == "working":
            if action == "back":
                self.state, self.yes = "cancel_confirm", False
        elif self.state in ("failed", "cancelled", "success"):
            if action == "ok" and self.state != "success":
                self.start()
            elif action == "details" and self.state == "failed":
                self.state, self.detail_scroll = "details", 0
            elif action in ("back", "ok"):
                self.state, self.error = "home", ""
        elif self.state == "details":
            if action in ("back", "ok"):
                self.state = "failed"
            elif action in ("up", "down"):
                import textwrap
                count = len(textwrap.wrap(self.error, 66))
                self.detail_scroll = max(0, min(max(0, count-12), self.detail_scroll+(1 if action == "down" else -1)))


def draw(display, manager):
    from app import BG, DIM, EDGE, GREEN, PANEL, RED, TEXT
    d, m = display, manager
    d.copy(d.backdrop, (0, 0, 640, 480))
    d.rect(0, 0, 640, 52, BG)
    d.copy(d.logo, (20, 14, 67, 22))
    d.text("TV INSTALLER", 110, 18, TEXT, 18)
    d.text(VERSION, 525, 20, DIM, 13, 95, align="right")
    d.line(12, 51, 628, 51, EDGE)
    if m.state == "home":
        d.text("227 TV", 20, 76, GREEN, 22, 600, align="center")
        d.text("Install beside 227 AI, not in Ports", 20, 108, DIM, 13, 600, align="center")
        for i, label in enumerate(["Destination card"]+list(ACTIONS)):
            y = 140+i*48
            d.rect(30, y, 580, 42, (14, 50, 29, 255) if m.focus == i else PANEL)
            if m.focus == i:
                d.rect(30, y, 3, 42, GREEN)
            d.text(label, 48, y+13, TEXT, 15)
            if i == 0:
                card = m.cards[m.card] if m.card is not None else None
                text = "SELECT SD1 / SD2" if card is None else card["name"]+"  "+card["path"]
                d.text(text, 310, y+13, GREEN if card and card["available"] else DIM, 13, 280, align="right")
        d.text("Latest check failed. Repair / Uninstall available." if m.error and m.options.get("discovery_error") else
               m.error or "Favorites, history and 227 AI are preserved.", 20, 404, RED if m.error else DIM,
               13, 600, align="center")
    elif m.state in ("confirm", "cancel_confirm"):
        cancelling = m.state == "cancel_confirm"
        title = "Cancel this operation?" if cancelling else ACTIONS[m.focus-1]+"?"
        d.text(title, 20, 115, GREEN, 22, 600, align="center")
        text = ("Package setup/publication finishes safely first." if cancelling else
                "Target: "+m.cards[m.card]["name"]+"  "+m.cards[m.card]["path"])
        d.text(text, 20, 170, TEXT, 13, 600, align="center")
        d.text("227 AI and saved TV favorites/history are kept.", 20, 204, DIM, 13, 600, align="center")
        for yes, x, label in ((False, 90, "NO / BACK"), (True, 340, "YES / CONTINUE")):
            d.rect(x, 285, 210, 52, (14, 65, 34, 255) if m.yes == yes else PANEL)
            d.rect(x, 285, 210, 52, GREEN if m.yes == yes else EDGE, True)
            d.text(label, x, 304, TEXT, 15, 210, align="center")
    elif m.state == "working":
        p = m.progress
        d.text(p.get("label", "Preparing installation"), 24, 95, GREEN, 18, 592, align="center")
        detail = p.get("detail", "")
        d.text(detail, 30, 143, DIM, 13, 580, align="center")
        done, total = p.get("done", 0), p.get("total")
        d.rect(40, 220, 560, 16, PANEL)
        if total:
            ratio = min(1., done/total)
            d.rect(40, 220, int(560*ratio), 16, GREEN)
            d.text("%.1f / %.1f MiB" % (done/1048576, total/1048576), 20, 258, TEXT, 15, 600, align="center")
        else:
            d.rect(40+int(time.monotonic()*90) % 470, 220, 90, 16, GREEN)
        if p.get("speed"):
            d.text("%.0f KiB/s" % (p["speed"]/1024), 20, 294, DIM, 13, 600, align="center")
        d.text("B requests cancellation. Do not power off.", 20, 360, DIM, 13, 600, align="center")
    else:
        success = m.state == "success"
        d.text("COMPLETE" if success else m.state.upper(), 20, 85, GREEN if success else RED, 22, 600, align="center")
        message = ((m.result.get("outcome", "complete")+" TV "+m.result.get("version", "")+
                    ". Open 227 > 227 TV after reloading the ES menu.") if success and m.result.get("outcome") != "uninstalled" else
                   "TV removed; 227 AI and your saved TV data were kept." if success else m.error)
        import textwrap
        lines = textwrap.wrap(message, 66)
        start = m.detail_scroll if m.state == "details" else 0
        for i, line in enumerate(lines[start:start+12]):
            d.text(line, 24, 142+i*21, TEXT, 13, 592, align="center")
        d.text("B: Menu" if success else "A: Retry   X: Details   B: Menu", 20, 410, DIM, 13, 600, align="center")
    d.line(12, 445, 628, 445, EDGE)
    d.text("D-pad Navigate   A Select   B Back", 12, 458, TEXT, 13, 616, align="center")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", action="store_true")
    parser.add_argument("--roms", type=Path)
    parser.add_argument("--action", choices=("install", "update", "repair", "uninstall"), default="install")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Read-only CLI destination/system validation")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--base", default=os.environ.get("TV227_RELEASE_BASE", ""))
    parser.add_argument("--latest", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--discovery-error", default="", help=argparse.SUPPRESS)
    parser.add_argument("--release", type=Path, default=Path(__file__).parent / "release.json")
    parser.add_argument("--es-cfg", type=Path, default=Path("/etc/emulationstation/es_systems.cfg"))
    parser.add_argument("--themes", type=Path)
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args(argv)
    release = json.loads(args.release.read_text()) if args.release.exists() else None
    options = dict(source_dir=args.source, base=args.base, release=release,
                   es_cfg=args.es_cfg, themes=args.themes, development=args.desktop,
                   pinned=bool(args.base) and not args.latest, discovery_error=args.discovery_error)
    if args.latest:
        # The bootstrap's selected download directory is not a user pin.
        options["base"] = None
    if args.cli:
        if not args.roms or (not args.yes and not args.dry_run):
            parser.error("CLI changes require --roms PATH and --yes")
        if args.dry_run:
            worker = Installer(args.roms, **options)
            worker.validate_card()
            from tv_system import SystemMenu
            source = args.source or Path(__file__).parent
            system = SystemMenu(source, args.roms, args.es_cfg, args.themes).validate()
            print(json.dumps(dict(action=args.action, card=str(args.roms), system=str(system), dry_run=True)))
            return
        result = Installer(args.roms, emit=lambda event: print(json.dumps(event), flush=True), **options).run(args.action)
        print(json.dumps(result, indent=2))
        return
    if not args.desktop:
        compatible()
        from app import display_owner
        owner = display_owner()
        if owner is None:
            parser.error("Open Install 227 TV from EmulationStation Tools, not a background SSH session")
        from tv_dependencies import ensure
        ready = ensure(manager_only=True)
        if not ready["ok"]:
            raise RuntimeError("Manager dependencies unavailable: "+json.dumps(ready, indent=2))
        os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
        mali = "/usr/lib/aarch64-linux-gnu/libMali.so"
        if Path(mali).is_file():
            os.environ["SDL_VIDEO_EGL_DRIVER"] = os.environ["SDL_VIDEO_GL_DRIVER"] = mali
    from app import Display
    display = Display(args.desktop)
    manager = Manager(options)
    log_root = Path.home()/".local/state/melkor227-tv"
    log_root.mkdir(parents=True, exist_ok=True)
    log = (log_root / "installer.log").open("a", encoding="utf-8")
    event = C.create_string_buffer(64)
    buttons = {0: "back", 1: "ok", 2: "details", 8: "up", 9: "down", 10: "left", 11: "right"}
    keys = {13: "ok", 27: "back", 1073741906: "up", 1073741905: "down",
            1073741904: "left", 1073741903: "right", 120: "details"}
    try:
        while manager.running:
            if not args.desktop and display_owner() != owner:
                manager.cancel.set()
                break
            while display.sdl.SDL_PollEvent(event):
                kind = struct.unpack_from("<I", event.raw)[0]
                action = (buttons.get(event.raw[12]) if kind == 0x603 else
                          keys.get(struct.unpack_from("<i", event.raw, 20)[0]) if kind == 0x300 else None)
                if kind == 0x100:
                    if manager.thread and manager.thread.is_alive():
                        manager.cancel.set()
                    else:
                        manager.running = False
                elif action:
                    manager.button(action)
            manager.update()
            if manager.progress and manager.progress != getattr(manager, "_logged", None):
                log.write(json.dumps(manager.progress)+"\n")
                log.flush()
                manager._logged = dict(manager.progress)
            if manager.error and manager.error != getattr(manager, "_logged_error", None):
                log.write(json.dumps(dict(error=manager.error))+"\n")
                log.flush()
                manager._logged_error = manager.error
            draw(display, manager)
            if args.screenshot:
                display.screenshot(args.screenshot)
                break
            display.sdl.SDL_RenderPresent(display.renderer)
            time.sleep(.04)
    finally:
        if manager.thread and manager.thread.is_alive():
            manager.cancel.set()
            manager.thread.join()
        display.close()
        log.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print("227 TV installer: "+str(error), file=sys.stderr)
        raise SystemExit(1)
