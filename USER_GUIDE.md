# 227 TV User Guide

[Overview](README.md) | [Installation and recovery](INSTALLATION.md) |
[Screenshots](assets/readme/README.md) |
[Online manual](https://1z99.github.io/project-ai-r36s/projects/227-tv/reference.html)

This guide describes **227 TV 1.0.4**. Launch the handheld app from
**EmulationStation > 227 > 227 TV**, or its Tools fallback. Never open it in the
background alongside EmulationStation: the direct KMS display and controller
need exclusive ownership.

## First Channel

1. Open TV. A, B or Start skips the short intro. **Choose a country** in an empty
   World list is normal; no worldwide channel list is loaded at startup.
2. Press **Start > Browse countries**, move Up/Down, and confirm with **A**.
   This picker works before any channels have loaded.
3. Wait for the country's list. Up/Down selects a station, Left/Right pages by
   eight, and A starts playback. Try another station if one is unavailable.
4. A pauses/resumes. L2 lowers volume and R2 raises it. B dismisses an open menu
   first; with no menu open, B stops playback and returns to browsing.
5. To quit, choose **Start > Exit TV**, select **Exit to EmulationStation**, and
   confirm with A. B cancels the exit prompt.

Playback is already fullscreen. The header, footer and pointer hide after about
2.5 seconds without input. There is no separate fullscreen button.

## Controls

Read the footer for the current context. With the pointer active, A activates
its target rather than blindly confirming the highlighted list item.

| Control | Browsing and Navigation |
| --- | --- |
| A | Watch selected station in list focus; choose crosshair country in globe focus; activate pointer target |
| B | Clear country/search filters first; from the unfiltered world, open exit confirmation; close an open menu |
| D-pad | Up/Down selects list rows, Left/Right pages by eight; rotate atlas in globe focus |
| Left stick | Move pointer; hold A over globe and drag to rotate; release without dragging to choose a country |
| Right stick, vertical | Scroll the list or a menu opened from browsing |
| Select | Toggle globe / list focus |
| L1 / R1 | Previous / next tab |
| L2 / R2 | Zoom out / in |
| X / Y | Open search / toggle selected station as favorite |
| Start / FN | Open Settings |
| L3 | Play a random station from the currently filtered list |

| Control | Playback and Matrix |
| --- | --- |
| A | Pause/resume single video or focused matrix player with button navigation; with pointer in matrix, select that cell instead |
| R3 | Pause/resume focused player, including when the pointer is active |
| B | Dismiss an open menu first; with none open, stop all players and return to browsing |
| L2 / R2 | Volume down / up by 5, between 0 and 100; saved across launches |
| D-pad | Move matrix focus between cells |
| L1 / R1 | Cycle matrix focus backward / forward |
| Start / FN | Open Settings without stopping playback |

Right-stick scrolling is not available in playback menus; use the D-pad.
X/Y browsing shortcuts are inactive during playback. In the quality submenu,
B returns to Settings before another B dismisses Settings. Stopping playback
closes decoders/audio and cancels pending quality work; opening a menu does not.

## World, Favorites And Recent

**World** loads a country's playlist only when you select that country. The
camera flies to it and channel names reveal one row at a time. The globe numeral
counts loaded entries, not verified reception or search results. The list shows
eight rows and truncates names to 15 characters; full names appear in playback
settings.

**Favorites** stores station snapshots toggled with Y while browsing. **Recent**
holds up to 50 distinct stations selected for single-channel playback, newest
first. A failed tune can still appear there; starting matrix slots does not add
Recent entries. There is no Archive tab.

L1/R1 switches tabs but **retains country and search filters**. To see all saved
stations, use **Start > World / clear filters**, then switch to Favorites or
Recent. Merely switching to World does not clear the selected country. Saved
lists can open offline, but playing their streams still needs internet.

Country caches are fresh for 24 hours. **Refresh selected country** requests a
new playlist; a failed fetch can reuse older cached data. Some countries have no
playlist. Stations marked dead by the health checker are hidden from World, not
deleted from Favorites/Recent. A country can contain duplicates or alternatives,
and a listed station can be offline or region-restricted.

## Search

X opens the keyboard. Search filters **the loaded country list or saved
Favorites/Recent**, not a worldwide service, and does not find countries or fetch
unseen channels. Use **Start > Browse countries** for country selection.

| Control or Key | Action |
| --- | --- |
| D-pad / pointer | Select a keyboard key |
| A | Type / activate selected key |
| B / DEL | Delete; B closes the keyboard when the draft is empty |
| Y / SPACE | Insert a space |
| X / FIND | Apply the draft and close the keyboard |
| CLEAR | Empty the draft; still press FIND/X to apply it |
| CLOSE | Discard draft edits and close |

All words must match, case-insensitively, across station name, country/code,
language or tags. Metadata varies by playlist; missing language fields cannot
match a language query. The draft limit is 80 characters. Search sends no query
to a remote search service. Opening a country resets the query and selects World.

## Playback Quality

During playback, open **Start > Resolution / frame rate**. Choose a real
broadcaster-provided format, or **Auto / up to 480p**. Auto prefers the highest
available rendition at or below **480p** for single video and **240p** in matrix.
These are **soft selection targets**, not a hard block on HD.

TV does not transcode or invent lower-resolution/FPS streams. If a broadcaster
offers only HD or unknown dimensions, playback can continue with a high-quality
warning and may be slow. A small display or matrix surface does not reduce source
decoding cost. A lower advertised source is the useful choice when available.

Quality selection restarts only the focused player. It is not saved as a
per-station preference and does not rewrite favorites/history. HLS sources with
separate audio retain their associated media groups through a temporary private
playlist, removed when the player closes.

## Watch Matrix

Four-view mode is **experimental**. Start with single-channel playback; not every
combination of streams will be usable on the handheld.

1. Select a station while browsing and choose **Start > Add selected to matrix**.
2. Repeat for up to four slots, then choose **Start > Watch matrix**.
3. Move focus with the D-pad or L1/R1, or point at a cell and press A. Only the
   focused cell is audible; an empty focused cell is silent.
4. Press R3 to pause/resume that player. B from the unobscured matrix stops all
   players. To watch one channel fullscreen, stop and select it normally.

Slots are session-only, separate from favorites, and may include duplicates.
There is no per-slot remove/replace or maximize command. **Clear matrix** clears
the queued slots, not players already running; stop playback separately. All
populated cells decode independently, even when not focused. The PC screenshot
shows four France 24 language feeds, not proof of handheld throughput.

## Settings

Start or FN opens Settings. Up/Down selects rows; Left/Right or A changes values.
The menu stays open while adjusting globe/CRT settings. During playback,
**Resolution / frame rate** is the first item.

| Setting or Action | Behavior |
| --- | --- |
| Browse countries | Choose a country without pointing at the globe |
| World / clear filters | Clear country list and search filters |
| Random station | Choose from filtered rows, preferring stations outside Recent when possible; no worldwide fetch |
| Matrix actions | Add selected, watch queued slots, or clear the queue |
| Globe resolution | 128, 160, 192, 224, 256, 320, 384, 448 or 512 px internal stationary detail; movement is capped at 224 px |
| CRT overlay / intensity / animation | Off/On overlay, Normal/High effect, Off/On moving refresh band |
| Earth rotation speed | Off, 0.5x, 1x, 2x, 4x or 8x idle world spin |
| Recheck station health | HTTP reachability for up to 12 loaded country entries, not decoding/audio verification |
| Refresh selected country | Fetch that country's playlist again |
| Stop playback / Exit TV | Stop players / open exit confirmation |

Defaults: volume **70**, stationary globe **224 px** (160 px while moving),
rotation **1x / 2.4 degrees per second**, CRT Off, Normal intensity, animation Off.
Accepted preferences save immediately.

Idle spin resumes after globe interaction settles; it pauses over the globe,
during dragging, menus/flights, country/close-up views, playback, or inactive PC
focus. List input and pointer motion away from the globe do not themselves stop it.

## Storage And Backup

Saved data normally lives in **`~/.local/share/melkor227-tv/`**, in the launching
user's home, not on the games SD card. `TV227_DATA` overrides the root; keep that
directory outside managed app files and use it consistently for maintenance.
Both card installations share the default state for the same user.

| File | Contents |
| --- | --- |
| `state.json` | Favorites, up to 50 Recent snapshots, volume, globe and CRT preferences |
| `health.json` | Station HTTP reachability records |
| `countries/*.json` | Parsed per-country playlist caches |
| `catalog.json` | Legacy global cache, if present; not read on normal startup |
| `app.log` / `app.lock` | Appended launcher log / duplicate-launch lock |

**To back up:** close TV and copy at least `state.json` to a separate safe
location. Copy the entire data root to include caches/health. **To restore:**
keep TV closed and restore the saved files to the same root, retaining an
untouched backup. Invalid JSON raises an error rather than silently overwriting
your data. Matrix slots and queries are not persisted.

The managed program is `<card>/tools/melkor227-tv/`; its
`installed-release.json` supports Repair and is not your preferences file.
Update, Repair and Uninstall retain saved data. XML menu changes get timestamped
backups beside the originals. See [maintenance](INSTALLATION.md#update-and-remove)
for exact-version offline recovery limits and same-version Update behavior.

## Privacy And Rights

TV has no account, project-hosted stream proxy or telemetry service. Country
lists come from iptv-org's GitHub Pages host. Tuning can contact broadcaster/CDN
alternatives and HLS playlists before displaying video; health checks also make
network requests. These hosts see your IP and normal connection metadata.
Streams/redirects may use **HTTP as well as HTTPS**; an HTTPS catalog does not
authenticate or encrypt every broadcast.

Installation separately contacts GitHub release hosts and, for missing packages,
configured APT repositories. Search is local. Favorites/history are unencrypted
JSON and are not uploaded by TV; anyone with filesystem/backup access can read
them. Logs can contain URLs and paths. There is no encrypted vault, cloud backup,
remote wipe or secure-erasure feature.

Use broadcasts only where authorized. Public listings do not grant rebroadcast
rights or permission to bypass regional restrictions. See [CREDITS.md](CREDITS.md)
for component licenses and [the capture notice](assets/readme/NOTICE.md) for
France 24 documentation images, which are not MIT-licensed broadcast content.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Empty World / no search results | Choose a country first; clear query/country filters before viewing all saved entries |
| Stale or empty country | Use Refresh selected country online; an old cache may survive failed fetches, and some countries have no playlist |
| SIGNAL UNAVAILABLE / buffering | Check Wi-Fi and try another station; broadcaster URLs and regional access can fail independently of TV |
| Slow picture | Choose a lower advertised source; Auto cannot manufacture SD for an HD-only channel; avoid matrix until single playback works |
| No sound | Resume with A/R3, raise volume with R2, and focus a populated matrix cell |
| B does not stop immediately | Dismiss the open menu first; quality returns to Settings before closing |
| Lost TV menu entry after AI maintenance | Close TV and use TV's Repair; a replaced AI bootstrap can still reuse an older cached core |
| Immediate exit / display contention | Inspect `app.log`; launch through EmulationStation, not alongside it; do not disable the ownership guard |

For installer errors and log paths, see [INSTALLATION.md](INSTALLATION.md#logs-and-troubleshooting).
Back up malformed state before recovery; deleting favorites is not a first step.
When reporting an [issue](https://github.com/1Z99/r36s-tv/issues), include firmware,
architecture, card, version, exact action/error and sanitized logs. Label any
image as PC preview, PC playback or an actual device capture.
