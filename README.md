# 227 TV for R36S

A controller-first internet TV atlas for the R36S: explore a globe, choose a
country, and watch live channels without leaving your handheld.

**[Download Install 227 TV.sh](https://github.com/1Z99/r36s-tv/releases/latest/download/Install.227.TV.sh)**
| [All Releases](https://github.com/1Z99/r36s-tv/releases)
| [Installation Guide](INSTALLATION.md)
| [Report a Problem](https://github.com/1Z99/r36s-tv/issues)

## Easy Install

You only need **one file** to start. No SSH, terminal commands, separate AI
installation, or manual dependency downloads are needed on supported firmware.

1. Download **Install 227 TV.sh** using the link above. The release download may
   be named `Install.227.TV.sh`; rename it to `Install 227 TV.sh` for a tidy menu.
2. Put it in the existing **`tools`** folder on your games SD card. Do not extract
   a source-code ZIP or copy the repository's `install.sh` instead.
3. Safely eject the card, boot the R36S, and connect it to Wi-Fi.
4. Open **Tools > Install 227 TV** on the handheld.
5. Choose **SD1** or **SD2**, select **Install**, confirm, and let it finish.
6. Restart EmulationStation or the handheld when convenient, then open
   **227 > 227 TV**.

The installer downloads and verifies the graphical manager, app, artwork, fonts,
map, intro, and matching playback runtime. The manager shows download progress
and offers **Install**, **Update / repair**, and **Uninstall TV**. A Tools app
launcher is retained as a fallback. Do not power off during final installation.

If your computer cannot see the games partition, do **not** format the card.
Use a reader/OS that can access it. Wi-Fi hardware support is provided by your
firmware; some R36S devices need a supported USB Wi-Fi adapter.

## Compatibility

| Item | Requirement |
| --- | --- |
| Device | R36S, 640 x 480 display, aarch64 |
| Supported firmware target | **Debian 13 / trixie arm64 dArkOSRE** |
| Recorded hardware test | dArkOSRE build `03082026`, kernel `4.4.189` |
| Storage | Existing writable games card at `/roms` or `/roms2` |
| Network | Internet for installation, channel lists, and live TV |
| Runtime | Python 3.10+, working firmware SDL2/KMSDRM and audio |

**Not a universal R36S firmware package.** Original Ubuntu-based ArkOS,
AmberELEC, 32-bit images, and untested clones are not supported by the shipped
binaries. The installer refuses incompatible platforms and unsafe dependency
changes rather than replacing firmware or graphics drivers.

Working dependencies are reused. Missing approved Debian packages may be added
only after a simulated, version-pinned safety check. Noninteractive sudo access
must already be available; the installer never asks for or stores a password.
227 AI is **not required**. If AI already uses the 227 menu, choose its card so
both apps can share the existing registration.

## Features

- Interactive globe with country fly-to, zoom, thin borders, and a stick pointer.
- Country-by-country channel loading, search, favorites, and recent stations.
- Live playback with audio through a private software-rendering libmpv runtime.
- Real broadcaster-provided HLS quality choices, with automatic selection up to
  480p for single-channel playback.
- Green terminal-inspired 227 UI, animated intro, and matching ASCII cat artwork.
- Experimental four-view matrix with per-cell selection and focused audio.
- Saved preferences and TV data kept separately from installed program files.
- A downloadable controller-driven installer with update/repair and uninstall.

Stream availability, regional restrictions, and picture quality depend on each
broadcaster. HD-only channels may be slow; scaling them does not remove decoding
cost. Four-view mode is experimental, not a promise of four smooth live streams.

## Preview

![227 TV globe and channel browser](assets/readme/browse.png)

The preview uses offline demo data. It illustrates the interface, not guaranteed
channel availability or measured handheld playback performance.

## Controls

| Control | Action |
| --- | --- |
| A | Select / confirm; hold over the globe to drag with the left stick |
| B | Back / stop playback; delete in search |
| X | Open search / apply search |
| Y | Favorite a station; insert a space in search |
| D-pad | Navigate lists and menus; rotate when the globe has focus |
| Left stick | Pointer |
| Right stick | Scroll |
| L1 / R1 | Previous / next tab |
| L2 / R2 | Globe zoom; volume during playback |
| Select | Switch globe / list focus |
| Start | Settings, playback quality, and exit menu |

## Updates And Removal

Keep **Tools > Install 227 TV** for maintenance. **Update / repair** restores
the release pinned by that installer. For a newer release, replace the Tools
installer with the latest download and run it again. Close TV before updating.

**v1.0.1 fixes the black/green-box thumbnail** by using the exact same standalone
JPEG as 227 AI. Replace the v1.0.0 Tools installer with the latest download, then
choose **Update / repair**. The old installer only repairs v1.0.0. Reload/restart
EmulationStation after updating to refresh its cached artwork.

**Uninstall TV** removes TV's program and launchers while preserving favorites,
history, preferences, 227 AI, and the shared 227 system. No automatic reboot or
EmulationStation restart is performed. Reload its menu yourself after changes.

## Help

**336 host tests passed**, covering offscreen rendering, local playback fixtures,
download verification, dependency-plan auditing, v1.0.0 artwork migration, and
rollback. The initial v1.0.0 release also passed a temporary-card hardware test
of dependency checks, install, update, and uninstall preserving a sample AI entry.
v1.0.1 leaves the handheld unchanged for a manual update test; that update and
the final thumbnail display still need device-side acceptance.

Read [INSTALLATION.md](INSTALLATION.md) for offline installation, card selection,
logs, safe dependency handling, troubleshooting, and advanced CLI use.

For an issue, include your firmware/build, architecture, SD-card choice, steps
to reproduce, and relevant log output. Remove personal paths and private stream
URLs before posting. Early bootstrap failures are logged at
`~/.local/state/melkor227-tv/bootstrap.log`; the graphical manager uses
`~/.local/state/melkor227-tv/installer.log`.

## Source And Credits

Maintained by **1Z99**. Thanks to **southoz and the
[dArkOSRE-R36 contributors](https://github.com/southoz/dArkOSRE-R36)** for the
firmware used for development and testing.

See [DEVELOPMENT.md](DEVELOPMENT.md) for architecture, source installation,
native runtime builds, desktop previews, and testing, and
[PUBLISHING.md](PUBLISHING.md) for the pinned release process. This repository
contains the app, installer, tests, native helper source, runtime build recipe,
and the bundled runtime's corresponding source and license notices.

Application sources retain their MIT notices in [LICENSE](LICENSE) and
[assets/AI-LICENSE](assets/AI-LICENSE). **The full distribution is not MIT-only:**
the bundled GPL-enabled mpv runtime and fonts have separate terms. See
[CREDITS.md](CREDITS.md). No broadcast content is bundled; public playlist entries
do not grant rebroadcast rights. Use streams only where you are authorized.
