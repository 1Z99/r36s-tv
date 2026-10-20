# 227 TV v1.0.0

First public release of 227 TV for R36S on **Debian 13 arm64 dArkOSRE**.

## Install With One File

1. Download **Install.227.TV.sh** below; optionally rename it **Install 227 TV.sh**.
2. Copy it into the games SD card's existing **tools** folder.
3. Connect the R36S to Wi-Fi and open the installer from **Tools**.
4. Choose SD1/SD2, select Install, confirm, and wait.
5. Restart EmulationStation when ready and launch **227 > 227 TV**.

No SSH or separate AI installation is needed. The installer downloads the
graphical manager and full app/runtime with pinned size and SHA-256 checks.
Do not download GitHub's source-code ZIP for this workflow.

## Included

- Country globe, search, favorites, recent channels, and live playback with audio.
- Automatic up-to-480p source selection and available broadcaster HLS renditions.
- Experimental four-view matrix and controller-first green 227 interface.
- ASCII cat thumbnail matching 227 AI, with a 227 TV label.
- Install, update/repair, and uninstall; saved TV data and other 227 apps preserved.
- ARM64 software-rendering libmpv, native globe helper, fonts, map, artwork,
  original notices, and corresponding runtime source/build materials.

## Requirements And Limits

The recorded source-app hardware test used R36S dArkOSRE build `03082026`,
Debian 13/trixie arm64, kernel `4.4.189`. Original Ubuntu-based ArkOS, other
distributions, and 32-bit firmware are not supported by these binaries.
Use working firmware graphics/audio and a Wi-Fi connection. Missing approved
Debian dependencies may be installed only when no upgrades, removals, or
protected driver/base-system replacements are required.

**Verification:** 320 host regression tests passed. A temporary-card release test
on the supported R36S passed read-only dependency preflight, install, update,
uninstall, and shared AI-entry preservation. It did not open a display, install
system packages, stop the running TV app, or modify the real ES configuration.
The final public one-file Tools GUI flow has not yet been manually re-tested
for this release; historical app playback tests are not a fresh-image installer
certification. HD-only stations can be slow, channel access is not guaranteed,
and four-view live playback remains experimental.

For offline app installation, keep the installer and both `.tar.gz` assets from
this same release together in Tools. Missing OS packages still need internet.
For a newer release, download its new installer; update/repair is release-pinned.

Full instructions and troubleshooting:
<https://github.com/1Z99/r36s-tv/blob/main/INSTALLATION.md>.
