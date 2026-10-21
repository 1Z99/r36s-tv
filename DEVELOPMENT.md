# 227 TV Development

[Overview](README.md) | [Installation](INSTALLATION.md) | [User guide](USER_GUIDE.md)

227 TV is a controller-first Python/SDL2 source port of the Melkor 227 desktop
atlas, not a bit-for-bit Qt/QML reproduction. It uses ctypes, SDL software
rendering, and libmpv's software RGBA API: no Qt, external player window, GPU video
surface, pip packages, or project-hosted playback proxy.

## Download And Install

The controller-driven installer is documented in [INSTALLATION.md](INSTALLATION.md).
It downloads a verified complete application bundle, checks/sets up missing safe
dependencies on supported Debian 13 arm64 dArkOSRE, and installs **227 > 227 TV**
with or without 227 AI, **not Ports**. Install, update/repair and uninstall are available
from Tools. Your saved TV data and the AI app are preserved.

Public repository: [1Z99/r36s-tv](https://github.com/1Z99/r36s-tv).
See [README.md](README.md) for the single-file Tools installer and
[PUBLISHING.md](PUBLISHING.md) to rebuild the pinned release assets.
This supports the tested dArkOSRE ABI, not every firmware running on an R36S.

## Display Ownership

Open handheld TV from **EmulationStation > 227 > 227 TV** (Tools fallback is retained).
Do not start it remotely with `nohup`, `&`, or a background service while ES
is running: KMSDRM is not a desktop compositor, and competing processes can
both render and respond to controller input.

The application now refuses unowned KMSDRM launches before creating any SDL
window. It records the real EmulationStation ancestor PID and start time,
and exits if that owning process disappears or the launcher is orphaned.
KMSDRM startup can queue misleading hide/focus-loss events, so handheld ownership
is determined by that ES process relationship, not window-manager focus. Desktop
previews ignore input while unfocused. Background joystick delivery is disabled.
Exiting releases players, audio and SDL resources.

Desktop previews use `--desktop`. Explicit handheld diagnostics require
`--device-test --run-seconds N` with `0 < N <= 60`; do not run display tests
against a simultaneously rendering ES session. Normal deployment does not
start the app remotely. Leave it closed for the user to launch from the menu.

## Handheld Update

### Thin Map Borders

Country close-ups switch to a cached geographic polygon fill once movement
stops, instead of magnifying the small world texture. Lakes and polygon holes
are retained. Coastlines/country outlines are drawn afterwards at one native
screen pixel, so zoom and low globe-resolution settings do not thicken them.
The Earth rim is also drawn at native resolution. The world atlas marks only
one side of each shared border, removing its former two-pixel-wide border band.
Flights, dragging and held zoom controls retain the fast motion renderer;
the detailed close-up is built once and reused for list/text redraws.

### Smooth Globe Presentation

Globe images use smooth CPU-filtered presentation at native display size, cached
until the camera, selection or quality changes. Moving/stationary detail and
mesh/vector quality changes crossfade over 200 ms rather than popping between
images. Manual zoom is eased. The selected country's dark-green border trace
uses cached geometry and animates at 12 Hz without rebuilding the globe image.
The world/movement path also uses a native bilinear orthographic projector when
available, bypassing SDL's nearest-sampled triangle texture path. Coastlines and
country borders remain visible at every zoom, including x0.8 and during spin,
dragging and flights. They are antialiased one-native-pixel geographic lines
overlaid after scaling, not enlarged atlas border texels. Close-ups retain their
crisp vector outlines and dark-green selected border trace. A cached SDL line
fallback preserves world borders if the optional native overlay is unavailable.

Settings offers **Earth rotation speed**: Off, 0.5x, 1x, 2x, 4x and 8x.
Use Left/Right to change speed or A to cycle; the choice persists between launches.
The default 1x preserves the original 2.4 degrees/second rotation.
The idle world rotates at the selected speed, limited to 15 Hz, with an eased
start after two seconds without **globe interaction**. It requires world browsing,
no modal/country/flight, zoom at most 1.5x, active input focus, no drag, and a
pointer away from the globe. List navigation and pointer motion away from the
globe do not themselves reset that timer or stop rotation.

`tv_filter.py` wraps the optional native CPU projector/scaler in `native/libtvglobe.so`.
The shipped library was compiled natively for ARM64. Missing or wrong-architecture
libraries fall back to SDL's filtered scaler, so default desktop previews also
work without a usable native `.so`. No runtime compilation or dependency
installation occurs. The native filter's recorded handheld benchmark for
224x224 to 406x406 was about 6.8 ms, compared with roughly 25 ms for SDL filtering;
these are scaling timings, not whole-frame performance guarantees. The standalone
benchmark helper is `python3 -B test_filter.py --benchmark --iterations 100`.
The native 160x160 world projector measured about 6.5 ms before scaling/upload;
complete interface frame time also includes SDL composition and presentation.

Rebuild for another architecture using an existing native compiler on `PATH`
(`gcc` by default, or set `CC`), with an existing output parent directory:

```sh
bash build_globe_filter.sh native/libtvglobe.so
```

The helper accepts an optional output path; without one it writes the same
`native/libtvglobe.so`. `globe_filter.c` and `build_globe_filter.sh` ship with the
library. This app-local `native/` directory is separate from the private mpv
`runtime/` manifest and library path. Rebuilding does not replace firmware or
system libraries and does not install build tools or other system dependencies.

### Country-Only Loading

Normal startup loads bundled geography, saved state and health records. It does not read
the old global `catalog.json`, fetch channel indexes, or preload any station list.
Clicking a country downloads only `iptv/countries/<code>.m3u`, with a separate
24-hour cache for that country. **World / clear filters** clears the station list; Favorites and
Recent remain available from saved snapshots without downloading the world index.
Search and Refresh operate on the selected country. The country picker works
before any channel data has loaded. Late country responses cannot replace a newer
selection. A failed fetch can reuse an existing cache regardless of age; a
first-time 404 produces an empty country cache. Country downloads are bounded
to 8 MiB. The old global cache is preserved but is not used for startup.

`Catalog.refresh()` retains a separate global-index API (channel/stream/feed
metadata, with a master-playlist fallback). Normal browsing does not call it.
Do not describe the historical global feed count as a current shipped channel
total, or search as worldwide discovery. Country/query filters persist across
World, Favorites and Recent; opening a country resets the query and selects World.

### Country Fly-To And Matrix Reveal

Selecting a country by pointer, D-pad or the country list now starts an eased
camera flight. It rotates across the shortest longitude path, pulls back for
long moves from a close-up view, and zooms to frame the country's largest
landmass. This animates the existing map; it does not download satellite imagery.
Manual rotation/zoom or opening a menu cancels the flight. World/Back flies out
to the normal world camera. The fast motion renderer stays active during flight.

Visible country channel names use Local AI's original deterministic Matrix glyph
scramble and left-to-right reveal, strictly one row at a time from 1 through 8.
Each name finishes before the next begins; waiting rows show an ellipsis instead
of scrambling together. The effect runs at 20 ticks/second, settles to normal
text, and stops requesting redraws afterwards. It is triggered only by country
selection/loading, not by scrolling or switching tabs.

### Compact Layout And Quality

- Catalog progress and short status messages are integrated into the top bar,
  immediately beside the 227 logo. There is no bottom notification box.
- High-quality-source notices use borderless grey text beneath the centered
  227 loading animation, not a popup or header notification.
- The channel panel sits farther right and shows eight 42-pixel rows. Channel
  names use 13-pixel text and are limited to 15 characters in the list; full
  names remain in settings and playback. The globe is enlarged proportionally.
- Start opens full-screen settings. While watching, the first item is
  **Resolution / frame rate**. Choose a broadcaster-provided rendition or
  **Auto / up to 480p** to restore automatic selection. Auto prefers the highest
  available source at or below 480p, not the lowest-quality rendition. Matrix
  cells prefer up to 240p. These are soft source-selection targets, not HD
  decoding blocks. The menu lists
  real resolution/FPS combinations; it does not fabricate frame-rate controls
  for single-format channels or transcode video on the handheld.
- Pinned HLS choices preserve separate audio/subtitle groups through a private
  one-rendition master, removed when the player closes. Quality changes affect
  the focused matrix cell only and do not change saved favorites or history.

- Startup repaints on window exposure and repeatedly during the initial KMS handoff;
  the same 19-photo intro as 227 Local AI plays without input (1.67 seconds, 0.8-second fade).
- The header uses Local AI's actual logo, without the Television Atlas subtitle.
- Search uses Local AI's bold keyboard and cached green cursor. Left-stick pointer
  speed and deadzone match Local AI; right stick scrolls lists. Hold A over the
  globe and move the pointer to rotate it, or Select globe focus and use the D-pad.
- Search controls: A types, B deletes (closes when empty), Y inserts a space,
  X applies the search. FIND and CLOSE keys are available too.
- Footer controls use centered, measured button groups and contextual labels.
- B dismisses an open modal first (quality returns to Settings). From the
  unobscured watch view it closes all decoders, audio output and pending quality
  work. Menus alone do not stop playback. Stale network results cannot restart it.
- Channel selection checks HLS renditions and catalog alternatives in the background,
  selecting the best source within the handheld cap before decoding. Separate audio
  groups are retained in a one-rendition master. Unknown sources are checked
  for actual dimensions once decoding begins; HD-only streams show a warning and
  continue playing, rather than being blocked. Scaling cannot make every HD-only
  stream smooth on this hardware.
- A centered 227 logo with an animated trace replaces the old tuning message.
- Globe geography is rendered with one native SDL triangle batch and cached map,
  not Python polygon scanlines per frame. Globe zoom starts at x0.8. Settings
  offers 128, 160, 192, 224, 256, 320, 384, 448 and 512 px globe resolution;
  the choice persists, with lower
  internal resolution while moving. The default uses 160x160 during movement
  and 224x224 when stationary. Country-count labels,
  cursor, text and other UI stay at native resolution. Cached CPU-filtered
  presentation uses the native helper when available, with SDL filtering as fallback.
  Movement detail is capped at 224 px, even with higher stationary resolutions.
- Settings stays open when adjusting CRT or globe detail. D-pad Up/Down selects
  rows; Left/Right sets CRT off/on or cycles globe resolution. A toggles/cycles
  the selected setting. Country name/info is drawn without its former black box.

Developer checks: `--skip-intro`, `--run-seconds N`, and `--capture-after PATH`
support bounded startup tests. Use `--desktop` when running them on a PC.
Intro/logo/background and font assets retain their original licenses in
`assets/AI-LICENSE` and `assets/FONT-LICENSE`; see [CREDITS.md](CREDITS.md)
for artwork provenance and the separate GPL-enabled playback runtime.

## Verification Record

**Hardware status (2026-10-19):** installed on Debian 13 trixie arm64, dArkOSRE,
kernel `4.4.189`, using the existing SDL2/SDL2_ttf and FFmpeg. That historical run's
EmulationStation Ports path is `/roms/ports`, on an exFAT ROM partition, not
`/roms2/ports`. This is not the current installation destination: TV now uses
the shared 227 system. The bundled private libmpv was built and tested on the handheld.
The unmodified Debian `libmpv2 0.40.0-3+deb13u1` failed preflight with
`undefined symbol: gbm_surface_create_with_modifiers`: firmware `libgbm.so.1`
points to Mali and lacks the newer GBM ABI. This is not an observed kernel
syscall failure. The custom build avoids this dependency without replacing drivers.
Verified on-device: KMSDRM globe/country/search rendering, local video, live France
24 video with active ALSA audio output, and returning from playback to the globe.
Four simultaneous 320x180 test decoders ran at approximately 14 frames/s each,
including an audio-focus handoff; this was a decoder test, not four live network
streams or a matrix display benchmark. High-resolution TV streams can be slow.
The physical B button was observed cancelling the device test; the remaining
controls use the previously measured canonical mapping. Matrix mode remains
experimental. Stream availability and regional access depend on the broadcaster.
That historical global-index load observed 13,498 station/feed entries with
language metadata, not a guaranteed or current channel total.

**Host audit (2026-10-21):** at source revision
[`df66d40`](https://github.com/1Z99/r36s-tv/commit/df66d40), unittest discovery ran
**366 tests in 48.2 seconds, OK with one skip**. The skipped
`test_native_world_projector_avoids_mesh_rasterization` lacked a matching host
projector; the shipped ARM64 helper is not a PC binary. This is a host regression
record, separate from the historical release notes and hardware acceptance.
HTTP/APT are mocked in maintenance tests; media tests use local generated clips
and null audio; packaging tests use mock transport and temporary destinations.

**PC captures (2026-10-21):** the [gallery](assets/readme/README.md) documents
production SDL views and real France 24 decoding on a PC, with null audio and no
handheld/controller access. Watch shows French; matrix shows French, English,
Arabic and Spanish, not four unrelated stations. The original capture record
observed 151 watch frames and 151-153 per matrix feed from 640 x 360, 25 fps sources.
These count nonempty production `Player.frame()` RGBA renders, not internal decoder
totals or performance measurements. Settings/quality retain active playback behind
opaque menus; browser and installer states are scripted.

No new hardware acceptance was performed for the October 21 documentation audit.
Physical controls, audible output, KMSDRM, network playback and matrix load still
require a separately authorized hardware session. Do not infer them from PC images
or temporary-card installer tests.

## Runtime

- Python 3.10+, using the standard library only (no pip packages).
- SDL2 and SDL2_ttf shared libraries and `flock` (util-linux).
- A firmware-compatible libmpv with `api_type=sw` and RGBA software rendering.
  Software rendering was introduced around mpv 0.33; use 0.35+ as a practical
  baseline, but the actual capability probe is authoritative, not a version check.
- A working SDL KMSDRM driver for handheld use; a desktop SDL driver for local use.
- Network access for catalog refresh and live stations. Demo scenes are offline.

The graphical downloader can add missing safe system dependencies after auditing
and pinning its APT plan; the source `install.sh` only checks capabilities. Neither
upgrades firmware/graphics libraries, restarts ES, or starts the TV application.
The installer preserves an existing 227 registration, creating it only if absent.
The shipped playback runtime targets Debian 13 arm64; do not mix arbitrary foreign
packages into dArkOSRE. See INSTALLATION.md for exact compatibility/safety limits.

`runtime/` is optional. With no such directory, host preflight and installation
use system libmpv. If present, it must contain a valid `manifest.json`; a missing
or invalid manifest fails closed, even if system libmpv is available. The
installer checks package versions/source, file hashes, the library/source/license
allowlist and relative soname symlinks. `sh install.sh --list-runtime` prints
validated relative package paths, or **zero bytes** when `runtime/` is absent.
The launcher and installer prepend `runtime/lib` to `LD_LIBRARY_PATH` before
starting a new Python process, as required by ctypes soname loading.

### Private Native Build

`build_runtime.py` is an explicit, device-verified native build recipe, not
part of normal installation/deployment. It requires an existing Debian 13 arm64
native compiler, Meson, pkg-config, curl, tar, patch and binutils. It never uses
sudo, apt install/update/upgrade, pip or system library writes. Missing tools
cause an error rather than installation. Do not run it on an unrelated distro.

The recipe downloads `libmpv2=0.40.0-3+deb13u1` for documentation and dev/tool
packages with `apt download`, pins every candidate version into
`packages.lock.json`, and extracts with `dpkg-deb -x` into a new private work
directory. It downloads mpv 0.40.0 plus Debian `0.40.0-3+deb13u1` source/patches
over HTTPS and verifies archive SHA256 values against the `.dsc`. The `.dsc`
comes from Debian HTTPS; the script does not perform independent OpenPGP trust
verification. Build headers/tool versions and installed runtime versions are
recorded separately; an unavailable locked package causes failure, not substitution.

Build options are:

```text
-Dauto_features=disabled -Dlibmpv=true -Dcplayer=false -Dalsa=enabled -Dgl=disabled -Dlua=lua52
-Dbuildtype=release -Dstrip=true -Dbuild-date=false
```

This retains the software RGBA API and ALSA audio while disabling mpv's optional
DRM/GBM/EGL/GL/Vulkan/Wayland/X11 display backends. Required shared dependencies
link to the **existing firmware** libraries, not static archives or downloaded
runtime replacements. Only the locally built `libmpv.so.2` and its versioned
target ship. Soname files are regular copies, not symlinks, for exFAT support.
No SDL/EGL/GLES/Mali/DRM/GBM, glibc or systemd libraries ship.
`ldd -r` must resolve all symbols, then a **new Python process** runs
`tv_media.availability()` from source on stdin without a display or audio output.
Any failure retains the private work tree and does not publish the output.
If a later probe exits by signal or reports `ENOSYS`, investigate kernel/syscall
compatibility separately; do not upgrade firmware or replace libc as a workaround.

Rebuild procedure, if the bundled runtime needs replacing:

1. Put `build_runtime.py` and `tv_media.py` in a private temporary device directory.
   Existing private build directories can be resumed with `--reuse-work PATH`.
   The recipe selects FFmpeg headers matching installed runtime versions, using
   Debian Snapshot when those versions have left the main repository.
2. In the directory containing those two files, run the native build and archive:

```sh
ls -ld /tmp
OUTPUT=$(mktemp -d /tmp/melkor227-bundle.XXXXXXXX)
python3 -B build_runtime.py --work-parent /tmp --output "$OUTPUT/runtime" \
  --media-probe ./tv_media.py --jobs 2
# Continue only if the build exited successfully and printed a passing probe.
tar -czf "$OUTPUT/runtime.tar.gz" -C "$OUTPUT" runtime
```

3. Transfer that tarball to the PC with the established SSH key. Inspect its
   contents, then extract its single `runtime/` directory into this source tree
    (which must not already have `runtime/`).
4. Run `sh install.sh --list-runtime` locally to validate packaging without
   loading arm64 libraries. Inspect `runtime/manifest.json`, including exact
   versions, sources, build options, dependency diagnostics and preflight result.
5. The primary operator can then run `bash deploy.sh`. Deployment repeats the
   headless preflight against staged runtime files before touching installed code.
   Physical playback/display tests remain a separate authorized device step.

The finished runtime includes Debian copyright/changelogs, upstream licenses,
referenced common license texts, both corresponding source archives, `.dsc`,
and this build recipe. The custom binary version is recorded as
`0.40.0-3+deb13u1+tv227sw1`, not misrepresented as the unmodified Debian binary.
Rebuild with `--lock PATH` using the retained `packages.lock.json` or the finished
`runtime/manifest.json`. This pins inputs/configuration, not bit-identical output
across different compilers. The bundled manifest records the successful native
build, its sources, exact dependency versions and software-render preflight.

## Bundle Assets

The checked-in assets are sufficient for normal builds and tests. Regeneration
is optional and requires explicit upstream asset/font directories:

```sh
python3 -B package_assets.py --source /path/to/custom.melkor227 \
  --font-dir /path/to/font-assets --ai-source /path/to/project-ai-r36s
```

Packaging copies the upstream GeoJSON to `assets/globe.json`, derives a country
code-to-name map in `assets/countries.json`, and copies the upstream `LICENSE`
and `assets/NOTICE.md` unchanged. The notice's original `countries.json` filename
refers to the map now bundled as `globe.json`. Natural Earth map data is public
domain. The bundled DejaVu Sans Mono and `assets/FONT-LICENSE` come from the font
directory's `DejaVuSansMono.ttf` and `copyright`. Packaging does not touch TV state.

Documentation captures are separate from application assets. The website repository
provides `dev/render_tv_captures.py`; see the [capture workflow](assets/readme/README.md#regeneration).
Stock `--demo` screenshots are offline UI fixtures, not these real playback captures.

## Release Trust And Maintenance

The generated bootstrap embeds its own manager filename, byte size and SHA-256,
plus the release-policy helper. Those immutable pins attest that **built-in
manager**, not all future releases. In normal online mode it fetches
`https://github.com/1Z99/r36s-tv/releases/latest/download/release.json` and accepts
the selected manager's size/hash from that HTTPS response. Validation requires
format 1, target `debian13-arm64-darkosre`, a stable version, canonical repository/tag
base URL, exact versioned names, bounded positive sizes, and valid hash syntax.
Manager archive bytes must match before extraction.

The verified manager embeds the application's release record. Its bundle fields
must agree with discovered metadata and its code version must match. The app
archive is size/hash-checked, extracted with path/type/count/unpacked-size limits,
then checked for its exact file set and individual hashes before publication.
**GitHub HTTPS/repository control is the online trust root.** There is no
independent publisher signature, and sidecar checksums are not authentication.

An explicit `TV227_RELEASE_BASE`/`--base`, or a matching adjacent manager archive,
selects pinned mode. Its manager must match the script's built-in pins and the
app must match that manager's embedded record. The three-file offline bundle
needs no separate `release.json`. `--source` instead selects an explicit local tree.
Normal bootstrap launches re-extract verified manager code instead of trusting
mutable extracted modules, retaining downloads. If latest selects a different
release, the bootstrap also caches the original script's built-in manager.

Failed network discovery can fall back only to that verified built-in manager
cache; malformed metadata fails closed. Latest-mode Install/Update checks the
endpoint again at action time and fails without changing TV if unavailable.
Update rejects downgrades but returns `up-to-date` on equal versions **without
hashing installed files or auditing menus**. Repair restores the version in
`installed-release.json` from a matching cache or recorded base, never latest.
Legacy recovery without a record reads the installed version via AST parsing,
without execution, then fetches its exact tag metadata; this needs network access.

Maintenance holds the data-root app lock, a destination install lock, and
serialized shared-menu work. Use the same `TV227_DATA` override as the launcher.
Publication stages on the card, backs up the previous app and owned launcher/art,
and attempts restoration on handled failure. Failed recovery retains a reported
recovery directory. Cancellation waits through APT, final publication/rollback,
and removal. This is not power-loss-safe storage, and app rollback does not undo
added system packages. Uninstall retains saved state, manager storage, shared
system/themes, and other apps; it is not secure erasure.

Only absent approved dependencies can be installed after audited, version-pinned
APT simulations. Existing broken libraries, upgrades, reinstalls, removals and
protected graphics/base-system changes fail. The source `install.sh` only probes
capabilities, while the manager can set up missing safe packages. The launcher
sets the private library path; it does not reverify every runtime hash on each launch.

## Install

On the device, from a complete source bundle:

```sh
sh install.sh --check-only
sudo sh install.sh --prefix /roms
```

Preflight uses ctypes to load SDL2/SDL2_ttf, and `tv_media.availability()` to
create and exercise a tiny libmpv software-render context without an SDL display
or audio output. `--check-only` performs no writes and needs `tv_media.py` beside
the installer. There is deliberately no bypass for missing runtime capabilities.
The probe does not import the globe renderer or load its optional native filter;
an ARM64 `native/libtvglobe.so` does not interfere with system-libmpv host preflight.
The card root must already exist and be mounted; the installer never creates
`/roms`. `--prefix` accepts another existing, writable directory for staging
tests. Paths containing spaces are supported.

Installed paths:

- App: `/roms/tools/melkor227-tv/app.py`, backend/media/globe/filter modules, assets and license.
- Globe helper: app-local `native/libtvglobe.so`, `globe_filter.c` and executable `build_globe_filter.sh`.
- Main launcher: `/roms/227ai/Melkor 227 TV.sh`, or the existing 227 system's registered directory.
- Fallback: `/roms/tools/Melkor 227 TV.sh` (always installed).
- Artwork: `images/melkor227.jpg` beneath the shared 227 directory and Tools.
  This is the unchanged 227 AI cover JPEG; v1.0.0 SVG artwork is migrated on update.

Shared 227 registration preserves AI/other games and statistics, backs up changed
XML, and is idempotent. Malformed or redirected configuration is rejected before
publication. Only TV's identified historical Ports entry is migrated. Older AI
backends can remove other shared entries; replacing AI's bootstrap can reuse cached
core modules and is not proof of updated shared-menu handling. Recover a lost TV
entry with TV's Repair action; TV does not patch AI.
Reload the ES menu after installation when convenient; the installer does not force it.

## Deploy From PC

`deploy.sh` is a maintainer tool for a separately authorized device deployment,
not a validation step or the recommended user installer. Supply your own explicit
`TV227_SSH_USER`, `TV227_SSH_HOST`, `TV227_SSH_KEY`, and `TV227_PREFIX`; do not rely
on another operator's defaults. Authenticate and verify the host through your
own setup. Installation requires existing noninteractive `sudo -n` authorization.
No device connection is needed for documentation work or host regression tests.

Deployment first validates local Python syntax and any private runtime manifest,
builds a temporary tarball, verifies the remote `/tmp` parent, creates a private
staging directory, uploads with scp, and extracts. It then runs the staged
installer's headless `--check-only` with the staged private library path (or
system libmpv when no runtime is packaged). Only after success does it invoke
the actual installer with `sudo -n`. Staging is cleaned on success or failure.
The upload allowlist includes `app.py`, `tv_backend.py`, `tv_media.py`,
`tv_quality.py`, `tv_style.py`, `tv_globe.py`, `tv_filter.py`, `globe_filter.c`,
`build_globe_filter.sh`, `native/libtvglobe.so`, the launcher, `install.sh`, bundled
assets, and `LICENSE`, plus exactly the validated optional runtime files, licenses
and source. The helper's native directory is not added to the mpv runtime manifest.
It never uploads state, caches, tests, `.git` or `__pycache__`.
Deployment does not restart ES, stop an existing app, or run foreground display
tests. Exit any running TV session before upgrading.

## Data And Launcher

State defaults to `~/.local/share/melkor227-tv`; `TV227_DATA` overrides that
directory. Catalog cache, favorites, history and preferences are separate from
installed code. Neither installer nor deployment copies, replaces or deletes
saved state. Keep `TV227_DATA` separate from the installed application directory.
`state.json` holds favorites, up to 50 Recent snapshots and preferences;
`health.json` and `countries/*.json` hold reachability records and country caches.
Close TV before backing up/restoring files. State normally lives in the user's
home, and both card installs share it by default. Invalid JSON fails visibly
rather than being silently replaced with defaults. See [storage and backup](USER_GUIDE.md#storage-and-backup).
The launcher appends stdout and stderr to `$TV227_DATA/app.log` and uses
`$TV227_DATA/app.lock` with `flock` to reject duplicate launches for that data
directory. It does not truncate existing logs.

The same launcher resolves the app relative to the 227 directory, Tools, or the source
directory, including card roots with spaces. Handheld launches default to
`SDL_VIDEODRIVER=kmsdrm` and `SDL_RENDER_DRIVER=software`. When present, the verified
firmware `/usr/lib/aarch64-linux-gnu/libMali.so` is selected for both SDL EGL and
GL drivers. No guessed Mali directory or private graphics replacement is used.
An existing valid `XDG_RUNTIME_DIR` is retained; otherwise a private temporary
runtime directory is created and removed on exit. `--desktop` skips device-only
environment setup. `SDL_VIDEODRIVER=dummy` can override KMSDRM for headless runs.
The launcher waits for Python, handles termination, then returns to its caller
(normally ES); it does not start another ES instance or restart its service.

## Controls

Raw SDL joystick events are canonical; do not also process the GameController
stream for the same press.

| Physical Control | Raw Button | Action |
| --- | --- | --- |
| B | 0 | Dismiss modal first; otherwise stop playback / clear browsing filters / exit prompt; delete in search |
| A | 1 | Confirm/select; pause playback; pointer-active matrix selects a cell |
| X | 2 | Search |
| Y | 3 | Favorite |
| L1 / R1 | 4 / 5 | Previous / next tab |
| L2 / R2 | 6 / 7 | Globe zoom; volume during playback |
| D-pad | 8 / 9 / 10 / 11 | Up / down / left / right navigation |
| Select | 12 | Toggle globe / list |
| Start | 13 | Menu |
| L3 | 14 | Random station from the filtered browsing list |
| R3 | 15 | Pause/resume focused player |
| FN | 16 | Menu |
| Left stick | Axes 0/1 | Local AI mouse pointer; hold A to drag the globe |
| Right stick | Axis 3 | Scroll list/menu while browsing, not playback menus |

The [user guide](USER_GUIDE.md#controls) is the contextual action reference.
Recent records single-channel selections, including unsuccessful tunes; matrix
starts do not populate it. Matrix slots and the search query are session-only.

## Local Verification

Run from the source directory. Unit discovery includes backend, media and
packaging tests; media integration uses ffmpeg-generated local clips when ffmpeg and
libmpv are available. A null audio output prevents opening the host sound device.
Packaging tests use a mock SSH/scp transport, test absent/invalid/custom runtimes,
check the filter module/library/source/build helper in archives and staging, and
perform two real temporary host installs when host libraries are available.
They make no device connections and do not run the native builder.

```sh
for script in install.sh install227tv.sh deploy.sh build_globe_filter.sh 'Melkor 227 TV.sh'; do bash -n "$script" || break; done
sh install.sh --list-runtime
PYTHONDONTWRITEBYTECODE=1 SDL_VIDEODRIVER=dummy SDL_RENDER_DRIVER=software \
  TV_MEDIA_AO=null python3 -B -m unittest discover -v
```

For native projector integration coverage, build a **host** helper at a temporary
path. Never overwrite the shipped ARM64 helper for a PC test:

```sh
ls -ld /tmp
HOST_HELPER=$(mktemp -d /tmp/tv227-helper.XXXXXXXX)
bash build_globe_filter.sh "$HOST_HELPER/libtvglobe-host.so"
SDL_VIDEODRIVER=dummy SDL_RENDER_DRIVER=software \
  TV227_GLOBE_FILTER="$HOST_HELPER/libtvglobe-host.so" TV_MEDIA_AO=null \
  python3 -B -m unittest discover -v
```

Offline screenshots, with output outside the working copy:

```sh
ls -ld /tmp
PREVIEW=$(mktemp -d /tmp/melkor227-preview.XXXXXXXX)
SDL_VIDEODRIVER=dummy SDL_RENDER_DRIVER=software TV_MEDIA_AO=null \
  python3 -B app.py --desktop --demo --scene browse --screenshot "$PREVIEW/browse.png"
```

Other supported scenes are `country`, `search`, `matrix`, `settings`, `quality`
and `loading`. Screenshot mode requires `--demo` and an existing output parent.
There is no built-in `watch` scene; the stock matrix demo shows unavailable-signal
cells, not decoded broadcasts. A screenshot is
not evidence of real stream decoding, physical controller input or KMSDRM
compatibility. `--demo` uses an internal `TemporaryDirectory` for all preview
state and never opens live favorites/catalog state, even if `TV227_DATA` is set.
For a desktop window, run `python3 -B app.py --desktop --demo`.
For isolated host staging and shared-menu registration checks:

```sh
PYTHONDONTWRITEBYTECODE=1 TV_MEDIA_AO=null \
  python3 -B -m unittest test_packaging test_system
```

These fixtures provide temporary card/configuration paths and, where needed, a
source fixture without the ARM64 runtime and with working host SDL/libmpv.
`--prefix /tmp/...` alone does not isolate every integration path: do not point
a manual staging install at live EmulationStation configuration. Host capability
probes likewise need a disposable fixture without the device runtime. Tests do
not install OS packages, rebuild the playback runtime or connect to a handheld.
Hardware acceptance still needs an explicitly authorized on-device session, including audio,
controls, single-channel playback, matrix load and clean return to ES.
