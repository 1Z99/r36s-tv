# Install 227 TV

## Supported Handheld

This release targets **R36S, aarch64, dArkOSRE based on Debian 13 (trixie)**,
including the tested build `03082026` and kernel `4.4.189`. It is not a universal
binary for every firmware sold with an R36S. Original Ubuntu-based ArkOS,
32-bit images and other distributions require a matching playback runtime build.
The manager checks this before downloading/installing the application. The small
manager itself may already have been downloaded when this check runs.

Wi-Fi is required for live television. A fresh install also needs network access
if operating-system dependencies are missing. Python 3.10+, working firmware SDL2
KMSDRM/Mali drivers and sudo authorization must be available. Missing Python/curl
can be bootstrapped only if APT can add them without upgrades or protected changes.

## One-File Online Install

1. Download [Install 227 TV.sh](https://github.com/1Z99/r36s-tv/releases/latest/download/Install.227.TV.sh).
2. Copy it to the existing `tools` folder on your games SD card. GitHub may name
   the download `Install.227.TV.sh`; you can rename it `Install 227 TV.sh`.
3. Enable Wi-Fi, then launch it from **Tools** on the R36S.
4. Explicitly choose **SD1** (`/roms`) or **SD2** (`/roms2`), select **Install**,
   and confirm. An unmounted/unwritable card is unavailable.
5. Wait for success, then restart/reload EmulationStation yourself and open
   **227 > 227 TV**. There is no need to install 227 AI first.

No extra archives need to be copied for an online install. The script fetches
the manager, which downloads the complete application with real byte progress.
Use the generated release installer, not the source `install227tv.sh` template
or `install.sh`, which is only for complete source bundles.

The generated `Install 227 TV.sh` has the manager archive's exact size and SHA-256
pinned into it. The verified manager pins the complete application's SHA-256.
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

## Online Downloader

From v1.0.2, the generated installer checks the repository's fixed HTTPS
endpoint `releases/latest/download/release.json` at launch, validates its
format, target, version, filenames, sizes, and SHA-256 values, and uses the
matching tagged `base_url`. It also downloads a copy of the version pinned by
the launcher for offline Repair/Uninstall use. Normal users do not need to
configure a host. For an advanced mirror or a true offline-directory install,
override the base explicitly, which pins behavior to that directory:

```sh
TV227_RELEASE_BASE="file:///path/to/matching-release-files/" \
  sh 'Install 227 TV.sh' --cli --roms /roms --action install --yes
```

Both archives must match the generated script's pins. A downloaded sidecar checksum alone is
not treated as an independent trust anchor. Obtain the bootstrap through a
trusted source; there is no independent release signature yet.

## Update And Remove

Open **Tools > Install 227 TV** again:

- **Update** checks the latest published release over the fixed HTTPS endpoint,
  verifies the release record and bundle (filename, version, sizes, SHA-256),
  and refuses to downgrade. When offline, Update stops safely without changing
  TV; **Repair** stays available.
- **Repair** restores exactly the installed version, preferring the
  `installed-release.json` record stored beside the app. Without a record (older
  v1.0.0/v1.0.1 installs) the installer recovers the version from the installed
  code before fetching its matching tag; it never guesses "latest" in this case.
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

### Automatic Updates From v1.0.2

Installers of v1.0.2 and later update **TV** to the repository's newest stable
release through their Update action, verified the same strict way as a fresh
install. If the v1.0.1 release's same-file download already succeeded on this
device, only the *installer file itself* needs one replacement: download the
latest **Install 227 TV.sh** and let it copy itself into Tools. Afterwards,
future versions follow automatically.

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

Older cached 227 AI managers may not preserve other apps' menu entries. Use a
current AI installer when maintaining AI. If an older manager removes TV's menu
entry, rerun **Repair** in the TV installer. TV does not modify the AI
manager, models, or chats.

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

Bootstrap startup may prepare its verified manager before CLI dry-run; the manager
dry-run itself does not install packages, download the application or change menus.
`--es-cfg PATH` and `--themes PATH` support explicit integration/test destinations.
