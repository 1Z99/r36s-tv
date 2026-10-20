# 227 TV Development

## Download And Install

The controller-driven installer is documented in [INSTALLATION.md](INSTALLATION.md).
It downloads a verified complete application bundle, checks/sets up missing safe
dependencies on supported Debian 13 arm64 dArkOSRE, and installs **227 > 227 TV**
beside 227 AI, **not Ports**. Install, update/repair and uninstall are available
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

The idle world rotates slowly at 2.4 degrees/second, limited to 15 Hz after two
seconds without input. It pauses while the pointer hovers over the globe, while
dragging, during menus, recent input or camera flights, in country views or
close-up zoom, outside browsing, and when desktop input focus is inactive.

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

Normal startup loads only the local map and saved preferences. It does not read
the old global `catalog.json`, fetch channel indexes, or preload any station list.
Clicking a country downloads only `iptv/countries/<code>.m3u`, with a separate
24-hour cache for that country. World clears the station list; Favorites and
Recent remain available from saved snapshots without downloading the world index.
Search and Refresh operate on the selected country. The country picker works
before any channel data has loaded. Late country responses cannot replace a newer
selection. The old global cache is preserved but is not used for startup.

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
  cells use a 240p cap. The menu lists
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
- Leaving a video with B closes its decoder, audio output and pending quality
  selection. Menus alone do not stop playback. Stale network results cannot restart it.
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
  and 224x224 when stationary. Station markers,
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

A controller-first Python/SDL2 source port of the Melkor 227 desktop TV atlas
for the R36S running dArkOSRE. This is **not** a bit-for-bit reproduction of the
Qt/QML desktop plugin. The handheld UI uses SDL software rendering and libmpv's
software RGBA render API, with no Qt, external player window or GPU video surface.

**Hardware status (2026-10-19):** installed on Debian 13 trixie arm64, dArkOSRE,
kernel `4.4.189`, using the existing SDL2/SDL2_ttf and FFmpeg. The current boot's
EmulationStation Ports path is `/roms/ports`, on an exFAT ROM partition, not
`/roms2/ports`. The bundled private libmpv was built and tested on the handheld.
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
The current feed index loaded 13,498 station/feed entries with language metadata.

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
- Artwork: `images/melkor227.svg` beneath the shared 227 directory and Tools.

Shared 227 registration preserves AI/other games and statistics, backs up changed
XML, and is idempotent. Malformed or redirected configuration is rejected before
publication. Only TV's identified historical Ports entry is migrated. Current AI
managers should also preserve other 227 entries during AI maintenance; older
cached managers may need updating before use with a shared menu.
Reload the ES menu after installation when convenient; the installer does not force it.

## Deploy From PC

```sh
bash deploy.sh
```

Defaults: SSH user `ark`, host `192.168.179.3` (`darkosre-r36`), key
`~/.ssh/r36s_arkos`, and card root `/roms`. Override with `TV227_SSH_USER`,
`TV227_SSH_HOST`, `TV227_SSH_KEY`, and `TV227_PREFIX`. SSH uses the selected key
only, batch authentication and `ConnectTimeout=15`; unknown host keys must be
verified through your normal SSH setup first. Installation uses `sudo -n`, so
noninteractive sudo authorization must already be available.

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
| B | 0 | Stop channel and go back; delete while searching |
| A | 1 | Confirm / select |
| X | 2 | Search |
| Y | 3 | Favorite |
| L1 / R1 | 4 / 5 | Previous / next tab |
| L2 / R2 | 6 / 7 | Globe zoom; volume during playback |
| D-pad | 8 / 9 / 10 / 11 | Up / down / left / right navigation |
| Select | 12 | Toggle globe / list |
| Start | 13 | Menu |
| Left stick | Axes 0/1 | Local AI mouse pointer; hold A to drag the globe |
| Right stick | Axis 3 | Scroll list or menu |

## Local Verification

Run from the source directory. Unit discovery includes backend, media and
packaging tests; media integration uses ffmpeg-generated local clips when ffmpeg and
libmpv are available. A null audio output prevents opening the host sound device.
Packaging tests use a mock SSH/scp transport, test absent/invalid/custom runtimes,
check the filter module/library/source/build helper in archives and staging, and
perform two real temporary host installs when host libraries are available.
They make no device connections and do not run the native builder.

```sh
for script in install.sh deploy.sh build_globe_filter.sh 'Melkor 227 TV.sh'; do bash -n "$script" || break; done
bash build_globe_filter.sh /tmp/libtvglobe-host.so
SDL_VIDEODRIVER=dummy SDL_RENDER_DRIVER=software \
  TV227_GLOBE_FILTER=/tmp/libtvglobe-host.so TV_MEDIA_AO=null \
  python3 -B -m unittest discover -v
```

Offline screenshots, with output outside the working copy:

```sh
ls -ld /tmp
PREVIEW=$(mktemp -d /tmp/melkor227-preview.XXXXXXXX)
SDL_VIDEODRIVER=dummy SDL_RENDER_DRIVER=software TV_MEDIA_AO=null \
  python3 -B app.py --desktop --demo --scene browse --screenshot "$PREVIEW/browse.png"
```

Use `--scene search` or `--scene matrix` for the other scenes. A screenshot is
not evidence of real stream decoding, physical controller input or KMSDRM
compatibility. `--demo` uses an internal `TemporaryDirectory` for all preview
state and never opens live favorites/catalog state, even if `TV227_DATA` is set.
For a desktop window, run `python3 -B app.py --desktop --demo`.
For a full staging install without writing the actual card:

```sh
ls -ld /tmp
STAGING=$(mktemp -d '/tmp/melkor227-card with spaces.XXXXXXXX')
sh install.sh --prefix "$STAGING"
sh install.sh --prefix "$STAGING"
```

This still requires working host libraries and creates no TV state. Use a source
tree without an arm64 `runtime/` for host testing. The second install checks
idempotent gamelist registration. Hardware acceptance
still needs an explicitly authorized on-device session, including audio,
controls, single-channel playback, matrix load and clean return to ES.
