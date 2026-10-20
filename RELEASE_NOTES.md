# 227 TV v1.0.4

A cleaner browse screen: the unused Archive tab is gone, tab labels are
centered, and the globe's background panel melts into the scanline backdrop.

## Interface Refinements

- **Archive tab removed**: the world tray now shows three tabs —
  **World / Favorites / Recent** — with no Archive tab. Channels that the
  health checker marks dark stay hidden from World automatically; if any still
  live in Favorites or Recent, they remain found there. The Settings item is
  now **Recheck station health** (same scan as before, renamed and with a new
  complete toast).
- **Centered tab labels**: WORLD / FAVORITES / RECENT sit centered in their
  redirected cells, and the selection pill spans the full cell.
- **Translucent globe square**: the square panel behind the Earth is now
  ~35% opacity (`9,10,12` at alpha 90), so the app's scanline backdrop shows
  through instead of a heavy dark card. Implemented in both the bundled native
  globe projector (`native/libtvglobe.so`, rebuilt for arm64 from the updated
  `globe_filter.c`) and the SDL fallback renderer.

## Automatic Updates (unchanged from v1.0.2)

- **Update** checks the fixed endpoint `releases/latest/download/release.json`,
  validates format, target, version, canonical base URL, filenames, sizes and
  SHA-256 boundaries, then installs the newest stable release with the same
  pinning and rollback guarantees. It refuses to downgrade an installed TV.
- **Repair** restores exactly the installed release from the
  `installed-release.json` record; **Uninstall TV** remains fully offline.

## Update From v1.0.2 Or v1.0.3

Nothing special: close TV, connect Wi-Fi, open **Tools > Install 227 TV**,
choose the card with TV, and select **Update**. Saved data, 227 AI and the
shared 227 system are preserved. Reload EmulationStation's menu only after an
artwork migration. Offline users need the installer and both matching v1.0.4
`.tar.gz` assets together in Tools; missing OS packages still require internet.

## Compatibility And Verification

The binary target is unchanged: **Debian 13/trixie arm64 dArkOSRE on R36S**.
Original Ubuntu-based ArkOS, other distributions and 32-bit firmware remain
unsupported by the bundled runtime.

**366 host tests passed**, including updated globe/filter and update-policy
suites, with mocked HTTP and APT. The bundled `native/libtvglobe.so` was
rebuilt for arm64 on Debian 13 aarch64 hardware, verified as a stripped ELF,
and finally smoke-checked through temporary-card install, update and uninstall.

Instructions: <https://github.com/1Z99/r36s-tv/blob/main/INSTALLATION.md>.
