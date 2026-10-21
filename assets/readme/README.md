# 227 TV Screenshots

[Project README](../../README.md) | [User guide](../../USER_GUIDE.md) |
[Full website gallery](https://1z99.github.io/project-ai-r36s/projects/227-tv/gallery.html)

Native **640 x 480 lossless PNGs**, captured on a **PC on 2026-10-21** using
the production SDL interface. Watch and matrix contain real decoded France 24
broadcasts. Browser and installer views are scripted states, not device captures
or a completed installation.

## Gallery

### Fullscreen Playback

<img src="watch.png" width="640" alt="PC playback capture: live France 24 French in the native 227 TV fullscreen watch view">

France 24 French playing through libmpv's software RGBA renderer, with controls
visible in the native application.

| World Atlas | Experimental Matrix |
| --- | --- |
| <img src="browse.png" width="320" alt="PC browser fixture: world globe and four authentic France 24 language feeds under France"> | <img src="matrix.png" width="320" alt="PC playback capture: four distinct France 24 French, English, Arabic and Spanish feeds in the matrix"> |
| Scripted list of four authentic France 24 feeds; not a downloaded global catalog. | Real four-feed PC playback. Focus is outlined in green; not a handheld benchmark. |

| Search | Installer |
| --- | --- |
| <img src="search.png" width="320" alt="PC browser fixture: country-scoped search keyboard with france typed over the loaded France entries"> | <img src="installer.png" width="320" alt="PC fixture of Install 227 TV 1.0.4 with sample SD1 selected and four maintenance actions; nothing installed"> |
| Scripted `france` query over the loaded four-feed list, not remote search. | Production manager UI, sample destination, no installation executed. |

### All Nine Views

The website capture set contains **seven app views and two manager views**.
Five are used locally above; the remaining four are available in the website
gallery without adding more images to the main README.

| Website Capture | GitHub Asset or Website View | What It Shows |
| --- | --- | --- |
| `world.png` | [`browse.png`](browse.png) | World atlas; scripted France list |
| `country.png` | [Country](https://1z99.github.io/project-ai-r36s/media/227-tv/country.png) | France close-up and loaded-entry count |
| `search.png` | [`search.png`](search.png) | Local keyboard/filter fixture |
| `watch.png` | [`watch.png`](watch.png) | Real France 24 French playback |
| `matrix.png` | [`matrix.png`](matrix.png) | Real French, English, Arabic and Spanish playback |
| `settings.png` | [Settings](https://1z99.github.io/project-ai-r36s/media/227-tv/settings.png) | Production menu, active playback behind opaque UI |
| `quality.png` | [Quality](https://1z99.github.io/project-ai-r36s/media/227-tv/quality.png) | Broadcaster source choices, playback behind opaque UI |
| `installer.png` | [`installer.png`](installer.png) | Manager home with sample card |
| `confirm.png` | [Confirmation](https://1z99.github.io/project-ai-r36s/media/227-tv/confirm.png) | Sample destination, No / Back selected, no action run |

## Provenance

[captures.json](captures.json) records the original capture run: source URLs,
timestamps, observed frames, source formats, source/asset hashes and PNG hashes.
[NOTICE.md](NOTICE.md) identifies broadcast sources and rights. The source website
manifest is
[`projects/227-tv/screenshots.json`](https://1z99.github.io/project-ai-r36s/projects/227-tv/screenshots.json).

The website's **`world.png` is named `browse.png` here** for the existing GitHub
asset path. It is a filename mapping, not a new render: the copied image bytes
and SHA-256 must remain unchanged. The original record covers all nine website
views; local filenames must be interpreted using the table above, including any
website-only entries. The other four images are not bundled in this directory.

The generator replaces the stock demo stations with four authentic France 24
language-feed entries under France. Browser screens are deliberately scripted,
not evidence that a four-entry list is the complete French country playlist.
Watch decodes the French feed; matrix decodes French, English, Arabic and Spanish
together, **not four unrelated broadcasters**. Settings and quality continue the
same single-feed playback behind opaque menus; source formats are not invented.

Video enters through `App.finish_player`; production `App.update` uploads real
`Player.frame()` RGBA output and `App.draw` renders the interface. Installer
screens use `tv_manager.draw`. Observed frame counts are nonempty RGBA renders,
not the decoder's internal frame total, a frame-rate benchmark, or proof of
sustained availability. The record's 640 x 360, 25 fps sources describe that
capture run, not every broadcaster's current offerings.

The run accessed the network and decoded video with **null/disabled audio**.
It did not access a handheld, controller, audio device, or personal app state,
save preferences, or execute an installation. These are not handheld performance,
audio, physical-input, or KMSDRM tests. The separate historical hardware record
is dated **2026-10-19** in [DEVELOPMENT.md](../../DEVELOPMENT.md#verification-record).
Viewing these static files does not tune a live stream.

**Broadcast frames and station marks belong to France 24 / France Medias Monde
and the respective rights holders, not the project's MIT license.** No endorsement
or broad redistribution permission is implied. Read [NOTICE.md](NOTICE.md) and
[CREDITS.md](../../CREDITS.md) before reuse.

## Regeneration

The capture generator belongs to the
[227 AI website repository](https://github.com/1Z99/project-ai-r36s), at
`dev/render_tv_captures.py`, not to the TV application or normal installer.
Run it from that repository against an explicitly trusted TV source tree.

```sh
# Pure plan: no app/native-library loading, output writes, or network access.
python3 -B dev/render_tv_captures.py --validate-only \
  --source-dir /path/to/melkor227-tv

# Explicit opt-in: network access and real video decoding; writes website captures.
python3 -B dev/render_tv_captures.py --capture-streams \
  --source-dir /path/to/melkor227-tv
```

Capture requires compatible **host** SDL2/SDL2_ttf and libmpv libraries, not an
FFmpeg executable. The TV checkout's private runtime must resolve to a known
compatible host library for this workflow, not the shipped ARM64 binary. Do not
replace the device runtime or rebuild release assets just to refresh documentation.
The generator guards against controller, personal persistence and installation
access. Network/playback is opt-in through `--capture-streams`.

Stock `app.py --desktop --demo --scene ... --screenshot ...` is a separate offline
fixture workflow. It has no `watch` scene, and its matrix does not decode live
video. Do not substitute those scenes for the real playback images or reuse
provenance for changed bytes. When refreshing images, retain the matching capture
record/notice and recheck the `world.png` to `browse.png` mapping.
