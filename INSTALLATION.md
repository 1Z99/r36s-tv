# Install 227 TV

[Quick start](README.md#easy-install) | [User guide](USER_GUIDE.md) |
[Project website](https://1z99.github.io/project-ai-r36s/projects/227-tv/)

Current release: **1.0.4**. The normal setup is one installer copied to your
games card, then a controller-driven installation from Tools. No AI installation,
SSH or terminal commands are required.

## Supported Handheld

This release targets **R36S, aarch64, dArkOSRE based on Debian 13 (trixie)**,
including the tested build `03082026` and kernel `4.4.189`. It is not a universal
binary for every firmware sold with an R36S. Original Ubuntu-based ArkOS,
AmberELEC, 32-bit images and untested clones are not supported by the shipped binaries.
The manager checks this before downloading/installing the application. The small
manager itself may already have been downloaded when this check runs.

Wi-Fi is required for live television. A fresh install also needs network access
if operating-system dependencies are missing. Python 3.10+, working firmware SDL2
KMSDRM/Mali drivers and root or existing noninteractive `sudo -n` authorization
must be available. No password is requested or stored. Missing Python/curl/CA
certificates can be bootstrapped only if APT can add them without upgrades or
protected changes; an existing Python older than 3.10 is rejected, not upgraded.

## One-File Online Install

1. Download [Install 227 TV.sh](https://github.com/1Z99/r36s-tv/releases/latest/download/Install.227.TV.sh).
2. With the handheld powered off, copy it to the existing `tools` folder on your
   games SD card. GitHub may name the download `Install.227.TV.sh`; you can rename
   it `Install 227 TV.sh`. Safely eject the card, put it back, and boot.
3. Enable Wi-Fi, then launch **Tools > Install 227 TV** on the R36S.
4. Explicitly choose **SD1** (`/roms`) or **SD2** (`/roms2`), select **Install**,
   and confirm. Use Left/Right on the destination row. An unmounted/unwritable
   card is unavailable; if 227 is already registered, choose its card.
5. Wait for success, then restart/reload EmulationStation yourself and open
   **227 > 227 TV**. There is no need to install 227 AI first.

No extra archives need to be copied for an online install. The script fetches
the manager, which downloads the complete application with real byte progress.
Use the generated release installer, not the source `install227tv.sh` template
or `install.sh`, which is only for complete source bundles.

If 227 is absent, TV creates the system automatically. If it already exists, TV
preserves its registration and other entries. The app is not installed into Ports.
If your computer cannot see the games partition, do not format it; use a reader/OS
that can access it. Wi-Fi adapter support comes from your firmware, not TV.

The script embeds size/hash pins for its own release's manager. Normal online
discovery obtains the selected release's manager metadata over GitHub HTTPS;
these are different trust paths, detailed below. The verified manager carries
the application's release record.
Runtime libraries, fonts, 19-frame intro, map data, the native globe helper and
their source/license notices are included in the application archive.

## Offline Application Bundle

Download these three matching files from the same [release](https://github.com/1Z99/r36s-tv/releases)
and put them together in the mounted games
card's `tools` directory:

- `Install 227 TV.sh` (rename the downloaded `Install.227.TV.sh` if needed)
- `melkor227-tv-manager-1.0.4.tar.gz`
- `melkor227-tv-1.0.4-darkosre-arm64.tar.gz`

Then use the handheld:

1. Enable Wi-Fi if missing system packages need downloading.
2. Open **Tools > Install 227 TV**.
3. Explicitly choose **SD1** (`/roms`) or **SD2** (`/roms2`). An unmounted card is unavailable.
4. Select **Install**, confirm **Yes**, and wait for completion.
5. Reload/restart EmulationStation's menu yourself when ready.
6. Open **227 > 227 TV**. TV does not install into Ports.

The two archives make application installation offline; they do not contain a
replacement OS or every Debian package. Missing compatible system packages still
need Debian repositories. Existing working firmware dependencies are reused.

A matching manager archive beside the script automatically selects the local,
pinned release instead of online latest discovery. The script verifies that
manager, whose embedded release record verifies the application; no separate
`release.json` download is needed. To return to normal online updates, move the
two copied archives out of the installer's folder and reopen it with Wi-Fi.

## Online Downloader

From v1.0.2, the generated installer checks the repository's fixed HTTPS
endpoint `releases/latest/download/release.json` at launch, validates its
format, target, stable version, canonical repository/tag `base_url`, filenames,
sizes, and SHA-256 values. The selected manager's hash comes from that HTTPS
response, not from immutable pins in an older script. If discovery selects a
different release, the bootstrap also caches its built-in pinned manager for
later offline Repair/Uninstall use. Normal users do not need to configure a host.
For an advanced mirror or offline-directory install, override the base explicitly,
which pins behavior to that directory rather than discovering latest:

```sh
TV227_RELEASE_BASE="file:///path/to/matching-release-files/" \
  sh 'Install 227 TV.sh' --cli --roms /roms --action install --yes
```

In pinned mode, the manager archive must match the generated script's built-in
size/hash pins; the application must match that manager's embedded release record.
Overrides do not make an old script accept arbitrary newer archives. `--source`
instead selects an explicit complete source tree.

Obtain the script from a trusted release page. HTTPS and control of the GitHub
repository remain the online trust root; sidecar checksums are not independent
authentication, and no release-signature verification is implemented. The
bootstrap re-extracts verified manager code on each normal launch rather than
trusting mutable cached Python modules. See [the technical trust model](DEVELOPMENT.md#release-trust-and-maintenance).

## Update And Remove

Open **Tools > Install 227 TV** again:

- **Update** checks the latest published release over the fixed HTTPS endpoint,
  verifies the release record and bundle (filename, version, sizes, SHA-256),
  and refuses to downgrade. An equal version returns **up-to-date** without
  auditing installed files or menu entries: use **Repair** for those. Offline
  latest-mode Install/Update stops safely without changing TV; explicit local
  bundles/mirrors compare their pinned version instead.
- **Repair** restores exactly the installed version, preferring the
  `installed-release.json` record stored beside the app. Offline Repair needs
  that record and a matching verified application cache; the bootstrap also
  needs a verified cached manager. Without a record (older v1.0.0/v1.0.1 installs),
  the installer reads the version from code without executing it, then fetches
  that exact tag's metadata, requiring network access. It never guesses latest.
- **Uninstall TV** removes TV's program and launchers, not AI, the shared 227
  registration or themes. Favorites, history, catalog caches and preferences in
  `~/.local/share/melkor227-tv` are deliberately retained.
- **B** asks to cancel. Downloads are cancellable. An in-progress APT transaction
  or final publication is allowed to finish safely before cancellation takes effect.
- In the graphical manager, failure stays visible: **A** retries, **X** shows
  details, **B** returns to the menu. Before its display starts, failures are
  written to `bootstrap.log`; a text dialog is used only when a terminal exists.

Close TV before updating. The installer does not kill the app, start TV, restart
EmulationStation, reboot, upgrade firmware or alter Wi-Fi configuration.

### Back Up Saved Data

Close TV and copy `~/.local/share/melkor227-tv/state.json` to a separate safe
location. It holds favorites, up to 50 recent station snapshots, and preferences.
Copy the entire data directory to retain country caches and health records too.
To restore, keep TV closed and restore the saved files to the same data root,
retaining an untouched backup. Use the actual `TV227_DATA` directory if overridden.
State normally lives in the user's **home**, not on the games card.

The card picker is not a migration tool: installing on the other card does not
move or delete a previous copy. Both copies share the same default home-directory
state. The bootstrap's Tools card can differ from the selected app card; keep
its `Install 227 TV.sh` shortcut and `tools/227-tv-manager/` support folder.

### Automatic Updates From v1.0.2

Installers of v1.0.2 and later discover newer stable releases through **Update**
in normal online mode; explicit mirrors and adjacent local bundles stay pinned.
For a v1.0.0/v1.0.1 installer, replace the Tools script once with the latest
**Install 227 TV.sh**. Saved data is retained. Afterwards, use Update for future
stable releases instead of replacing the script each time.

The v1.0.1 thumbnail fix remains included; a v1.0.2 installer updating an older
install migrates the menu artwork from SVG to the standalone JPEG as before,
with rollback participating as before. Reload/restart EmulationStation after a
thumbnail migration to clear its cached image.

## The Shared 227 System

If system `<name>227</name>` already exists, its directory, command, extensions,
theme and unrelated metadata are preserved. For example it can point to
`/roms/227ai/`, alongside `227 AI.sh`. TV adds only its own launcher, artwork and
gamelist entry. AI statistics and other projects survive repeated installs.

If no 227 system exists, TV creates it using `<card>/227ai/`, so a later AI
installation can share the same menu. If 227 is registered on another card, the
installer refuses to silently retarget it; choose that card instead. The existing
registration must use a direct child of the selected card for the relative launcher.

The historical TV Ports entry is migrated only when its launcher is identified
as TV's own file. Unrelated Ports games are not removed. A Tools fallback remains.

Older cached 227 AI manager backends may not preserve other apps' menu entries.
**Replacing AI's bootstrap alone can reuse the cached core**; it is not proof
that the manager backend was refreshed or its shared-menu handling was fixed.
If AI maintenance removes TV's entry, close TV and run
**Repair** in the TV manager. TV does not modify AI's manager, models, or chats.

## First Launch

Open **227 > 227 TV**. The short intro plays on normal launches; A, B or Start
skips it. An empty World list saying **Choose a country** is intentional.

1. Press **Start > Browse countries**, select a country with Up/Down, and press A.
2. Wait for the list, then select a station and press A. Left/Right pages by eight.
3. Playback is already fullscreen. A pauses/resumes; L2/R2 changes volume. B
   closes an open menu first, then stops playback from the unobscured watch view.
4. To exit, use **Start > Exit TV > Exit to EmulationStation** and confirm with A.

X searches loaded country channels or saved Favorites/Recent, not unseen worldwide
stations. Start with one channel before the experimental matrix.
[USER_GUIDE.md](USER_GUIDE.md) covers the contextual controls, quality, and filters.

## Dependencies And Safety

The bundle contains a private **software-rendering libmpv**, not the incompatible
distribution libmpv package. It loads the firmware's FFmpeg, ALSA and supporting
shared libraries. Capability checks test the actual private engine in a separate
Python process without opening a display or audio device.

Only missing, explicitly mapped Debian 13 dependencies may be installed. The
complete APT plan is simulated, audited, pinned and simulated again. Existing
broken libraries are errors, not permission to replace them. Upgrades, removals,
reinstalls and protected graphics/Mali/EGL/GLES/GBM/DRM/libc/systemd/firmware changes
are rejected. If a fresh image cannot supply missing dependencies under those
constraints, installation stops with diagnostics instead of modifying its drivers.

Added system packages remain after uninstall and are outside app-file rollback.
Handled failures attempt to restore program/menu files, but this is not a
power-loss-safe transaction. Keep the handheld powered during APT and final
publication; do not treat rollback as a substitute for a backup.

## Logs And Troubleshooting

- Early bootstrap log: `~/.local/state/melkor227-tv/bootstrap.log`.
- Graphical installer log: `~/.local/state/melkor227-tv/installer.log`.
- TV application log: `~/.local/share/melkor227-tv/app.log`.
- Verified download cache: `<card>/tools/227-tv-manager/cache`.
- Initial manager archive cache: `<card>/tools/.227-tv-downloads`.
- XML changes receive timestamped backups beside the original files.

If the installer says a dependency ABI is incompatible, use matching firmware or
rebuild the runtime. Do not replace Mali/GBM or install foreign distribution
packages. If a download fails verification, retry using the matching archive and
bootstrap from the same build. Interrupted downloads are verified before use.

If Tools does not show the installer, check that it is in the games card's
existing `tools` directory and ends in `.sh`, then reload the menu. If the system
is configured to show only scraped games, disable that filter or register the
script in your Tools list. If installation succeeds but 227 is missing, restart
EmulationStation; no forced restart is performed while the installer is open.
If TV's entry is still missing, select its installed card and run Repair.

If the bootstrap cannot reach latest, it can open only a verified built-in manager
cache. A first-ever one-file launch cannot work offline without the three-file
bundle. Malformed metadata fails closed; do not bypass verification errors.

If World is empty or search finds nothing, choose a country first. Country/query
filters remain active across tabs: use **Start > World / clear filters** before
switching to Favorites/Recent to see all saved stations. A failed country refresh
can retain an old cache. Try another channel for buffering or SIGNAL UNAVAILABLE;
health rechecks test HTTP reachability, not decoding or audio. See the
[user guide](USER_GUIDE.md#troubleshooting) for playback help.

Keep enough free space for downloads, unpacking, and rollback (at least 200 MiB
is a practical starting point). APT dependencies may need additional root
partition space. Downloads are roughly 12 MiB for this release, not counting
missing operating-system packages. Their precise sizes are in `release.json`.

Only launch TV through EmulationStation. Never use `nohup`, `&`, or a background
service to open TV alongside ES: both programs can otherwise read the controller
and compete for the direct KMS display.

## Advanced CLI

```sh
# Read-only destination and 227 registration validation:
sh 'Install 227 TV.sh' --cli --roms /roms --action install --dry-run

# Full local source tree, retaining the same manager/installer checks:
sh install227tv.sh --source /path/to/melkor227-tv \
  --cli --roms /roms --action install --yes

# Remove TV only; saved TV data and 227 AI are kept:
sh 'Install 227 TV.sh' --cli --roms /roms --action uninstall --yes
```

Bootstrap startup can log, download and replace verified manager files before CLI
dry-run; it is not a write-free bootstrap command. The manager dry-run itself only
validates the card/shared-menu destination: it does not install packages, download
the app, change menus or prove staging space. Missing bootstrap tools are not
installed in dry-run mode.
`--es-cfg PATH` and `--themes PATH` support explicit integration/test destinations.
