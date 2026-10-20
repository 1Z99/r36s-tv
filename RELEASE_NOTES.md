# 227 TV v1.0.3

A full crystal-green globe restyle, channel-count numbers instead of station
dots, and a full-screen exit prompt — delivered to v1.0.2 users through the
Update button.

## Visual Restyle

- **Crystal-green Earth**: new ocean `(5,19,12)` and land `(13,62,34)` palette
  replacing the previous blue/teal look.
- **Mint-green country borders** `(64,196,104)` and a matching mint rim line
  around the globe, in both the native globe filter (`globe_filter.c`, bundled
  prebuilt for arm64) and the Python fallback renderer. The native filter is
  rebuilt and shipped so the borders are mint regardless of which renderer
  draws.
- **Amber selection**: the selected country now renders in amber — fill
  `(158,120,32)`, bright edge `(242,201,76)` — with an amber trace-gradient
  while the globe rotates.
- **Channel counts instead of dots**: the country markers are gone; countries
  now show a channel-count numeral at their centroid, in the same size-18 font
  as country names, with no halo, always amber.
- **Full-screen exit prompt**: the old bordered "LEAVE THE ATLAS?" dialog is
  replaced by a full-screen screen in the Settings style: header `Exit`, title
  `LEAVE 227 TV?`, the note "Your favorites and preferences stay saved", and
  the two choices Keep watching / Exit to EmulationStation. Key handling is
  unchanged (B still cancels; A on Exit quits).

## Automatic Updates (unchanged from v1.0.2)

- **Update** checks the fixed endpoint `releases/latest/download/release.json`,
  validates format, target, version, canonical base URL, filenames, sizes and
  SHA-256 boundaries, and refuses to downgrade an installed TV.
- **Repair** restores exactly the installed release from the
  `installed-release.json` record; **Uninstall TV** remains fully offline.

## Update From v1.0.2

Nothing special: close TV, connect Wi-Fi, open **Tools > Install 227 TV**,
choose the card with TV, and select **Update**. Saved data, 227 AI and the
shared 227 system are preserved. Offline users need the installer and both
matching v1.0.3 `.tar.gz` assets together in Tools; missing OS packages still
require internet.

## Compatibility And Verification

The binary target is unchanged: **Debian 13/trixie arm64 dArkOSRE on R36S**.
Original Ubuntu-based ArkOS, other distributions and 32-bit firmware remain
unsupported by the bundled runtime.

**366 host tests passed**, including the restyled palette/border/label
expectations (`test_globe.py`, `test_filter.py`, `test_app.py`) and the
release-policy and update-flow suites with mocked HTTP and APT. The bundled
`native/libtvglobe.so` was rebuilt for arm64 from the updated `globe_filter.c`
on Debian 13 aarch64 hardware, verified as a stripped ELF shared object, and
finally smoke-checked through temporary-card install, update and uninstall.

Instructions: <https://github.com/1Z99/r36s-tv/blob/main/INSTALLATION.md>.
