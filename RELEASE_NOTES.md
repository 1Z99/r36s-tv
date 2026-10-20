# 227 TV v1.0.2

Adjustable idle Earth rotation, smarter idle-spin behavior, and installers that
can finally update to new releases **without a manual installer swap**.

## New Features

- **Earth rotation speed** in Settings under Start: Off, 0.5x, 1x, 2x, 4x, 8x.
  The choice is stored in `state.json` and survives launches. The default 1x
  keeps the original 2.4 deg/s spin.
- Refined pause rules for the idle world spin: moving the stick to park the
  pointer away from the globe no longer stops the rotation. The spin only
  pauses while the pointer is over the globe, during a manual drag, when using
  globe zoom, or while rotating with the crosshair focused; it eases back in
  about two seconds later and after zoom/flight end as before.

## Automatic Updates

- **Update** now checks the repository's fixed HTTPS endpoint
  `releases/latest/download/release.json`, validates format, target, version,
  canonical base URL, filenames, sizes, and SHA-256 boundaries, then installs
  the newest stable release using the same pinning and rollback guarantees as
  before. It refuses to downgrade an installed TV.
- **Repair** is now separate from Update: it restores exactly the installed
  release, using the new `installed-release.json` record beside the app. On
  older installs without a record the installer safely recovers the version
  from the installed code (without executing it) and fetches only that tag;
  it never guesses "latest" when the installed version cannot be identified.
- **Uninstall TV** remains usable without any network access. The manager now
  shows four actions: Install, Update, Repair, Uninstall TV.
- Launchers still cache a copy of the version pinned by the Tools script for
  offline Repair/Uninstall when the latest-release check fails.
- Explicit `TV227_RELEASE_BASE` / `--base` and `--source` installs keep their
  pinned, offline behavior and report `latest_checked: false`.

## Update From v1.0.0 Or v1.0.1

Existing installers remain pinned to their own version. Replace the Tools
installer **once** with this release's `Install.227.TV.sh` (renamed as before);
afterwards future releases arrive through the Update button automatically.
The v1.0.1 thumbnail fix (standalone JPEG instead of SVG artwork in the 227
menu) is retained and migrated with rollback as before.

Close TV, connect Wi-Fi, open **Tools > Install 227 TV**, choose the card with
TV, and select **Update**. Reload/restart EmulationStation afterwards if the
menu artwork was migrated; saved data, 227 AI and the shared 227 system are
preserved. No separate AI installation or SSH is required. Offline users need
the installer and both matching v1.0.2 `.tar.gz` assets together in Tools;
missing OS packages still require internet.

## Compatibility And Verification

The binary target is unchanged: **Debian 13/trixie arm64 dArkOSRE on R36S**.
Original Ubuntu-based ArkOS, other distributions and 32-bit firmware remain
unsupported by the bundled runtime. No playback/firmware upgrade is included.

**365 host tests passed**, including the new release-policy and update-flow
suites (fixed endpoint scope, schema/filename/hash validation, bounded HTTP,
downgrade refusal, network-failure behavior, installed-record recovery, legacy
migration, and GUI navigation) with mocked HTTP and APT. The one-file Tools
flow for this automatic-update behavior is deliberately awaiting a manual
handheld acceptance test; earlier install/update/uninstall behavior passed
temporary-card and handheld hardware tests.

Instructions: <https://github.com/1Z99/r36s-tv/blob/main/INSTALLATION.md>.
