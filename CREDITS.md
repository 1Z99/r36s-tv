# Credits And Licenses

## Application

227 TV for R36S is maintained by **1Z99**. The handheld Python/SDL interface,
installer, and native globe helper adapt the Melkor 227 desktop TV atlas.
The upstream MIT notice for **Akshar Patel** is retained in [LICENSE](LICENSE).
227 AI-derived code and original artwork by **1Z99**, copyright 2026, retain
their MIT notice in [assets/AI-LICENSE](assets/AI-LICENSE).

## Firmware

Thanks to **southoz and the dArkOSRE-R36 contributors**:
<https://github.com/southoz/dArkOSRE-R36>. Firmware is installed separately and
is not bundled here. The tested release target is Debian 13 arm64 dArkOSRE.

## Artwork And Fonts

- The intro frames, green background, 227 logo, and ASCII cat cover are from
  **1Z99's [227 AI](https://github.com/1Z99/project-ai-r36s)** original artwork,
  distributed under its MIT notice. TV uses the same unchanged cover JPEG as AI.
  Upstream credits: <https://github.com/1Z99/project-ai-r36s/blob/main/CREDITS.md>.
- DejaVu Sans Mono regular and bold fonts are bundled under the Bitstream Vera
  and DejaVu terms. Full notice: [assets/FONT-LICENSE](assets/FONT-LICENSE).
  Upstream: <https://dejavu-fonts.github.io/>.
- Natural Earth map data is public domain. Derived geometry and raster atlas
  retain the provenance notice in [assets/NOTICE.md](assets/NOTICE.md).
  Upstream: <https://www.naturalearthdata.com/>.
- README demo captures are generated from this app's offline demo scenes by
  1Z99 and are covered by the project artwork MIT notice.

## Playback Runtime

The private ARM64 libmpv build is based on **mpv 0.40.0**, Debian source revision
`0.40.0-3+deb13u1`, locally identified as `0.40.0-3+deb13u1+tv227sw1`.
Upstream: <https://mpv.io/> and <https://github.com/mpv-player/mpv>.

**This is a GPL-enabled build. Do not label the entire download MIT-only.**
mpv's GPL/LGPL notices, Debian copyright/changelog, and referenced common license
texts ship in `runtime/licenses/`. The corresponding upstream source archive,
Debian source patches and descriptor, and retained build recipe ship in
`runtime/sources/`, in both this repository and the application release archive.
`runtime/manifest.json` records hashes, build options, dependency versions,
and the recorded device preflight. The top-level `build_runtime.py` documents
current exFAT-compatible soname packaging; the retained original recipe uses
symlinks which were normalized to regular copies in the shipped bundle.

The runtime dynamically uses the firmware's FFmpeg, ALSA, and other supporting
libraries. It does not bundle replacement graphics drivers, glibc, or firmware.
Those separately installed components retain their own licenses. SDL2 and
SDL2_ttf are also loaded from the operating system, not redistributed here.

## Channel Listings

Channel playlists come from the community **iptv-org** project:
<https://github.com/iptv-org/iptv> and <https://github.com/iptv-org/api>.
Lists are downloaded when needed, not a bundle of television content.
Broadcasters retain all rights to their streams, logos, and programs. Listing a
URL is not permission to redistribute a broadcast or bypass regional access
restrictions. Availability and legality vary by channel and location.
