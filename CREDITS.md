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

## Documentation Screenshots

The [README gallery](assets/readme/README.md) uses native 640 x 480 PC captures
from the project's website capture run on **2026-10-21**, not handheld photos.
Watch and matrix contain real France 24 video decoded through the production
libmpv/SDL interface. The matrix shows four distinct language feeds: French,
English, Arabic and Spanish. Browser/search and installer states are scripted;
Settings and quality in the website gallery retain playback behind opaque menus.

The original 227 interface/artwork retains the notices above. **Broadcast frames,
station marks and underlying programs belong to France 24 / France Medias Monde
and the respective rights holders; they are not covered by the project's MIT
license.** No broadcaster endorsement is implied. Public stream listings do not
grant redistribution or rebroadcast rights. See
[assets/readme/NOTICE.md](assets/readme/NOTICE.md) for broadcast sources and rights,
and [captures.json](assets/readme/captures.json) for capture provenance.

The website's explicitly opted-in `dev/render_tv_captures.py --capture-streams`
workflow used real network video decoding with null/disabled audio. No handheld,
physical controller or personal saved state was accessed, and no installer action
was executed. These images do not establish audio quality, handheld performance,
or renewed hardware compatibility. Stock app `--demo` scenes are separate offline
fixtures and must not be described as real playback.

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
