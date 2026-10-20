# 227 TV v1.0.1

Fix the black thumbnail with a green box in the shared **227** menu.

## Thumbnail Fix

- TV now uses the **exact same JPEG as 227 AI**, without a label or SVG wrapper.
- Both the manager and full application package contain the standalone JPEG.
- Update migrates TV's menu image reference and removes owned, unreferenced
  legacy SVG artwork. Other games' shared artwork is preserved.
- TV statistics, saved data, 227 AI, and the shared system registration survive
  the update. Artwork migration participates in installation rollback.

The former SVG embedded the cat JPEG alongside vector decoration. The reported
black/green result is consistent with EmulationStation rendering the decoration
but ignoring the embedded image. A standalone JPEG avoids that SVG limitation.

## Update From v1.0.0

1. Download **Install.227.TV.sh** below and rename it **Install 227 TV.sh**.
2. Replace the old installer in your games card's **tools** folder.
3. Close TV, connect to Wi-Fi, then open **Tools > Install 227 TV**.
4. Select the card holding TV, choose **Update / repair**, and confirm.
5. Reload/restart EmulationStation after success to refresh the cached artwork.

**The old v1.0.0 installer remains pinned to v1.0.0.** Its Update / repair action
cannot discover this release automatically; replace the installer first. The
new bootstrap verifies and replaces the cached manager before updating TV.

For a fresh installation, use **Install** instead. No separate AI installation
or SSH is required. Offline users need the installer and both matching v1.0.1
`.tar.gz` assets together in Tools; missing OS packages still require internet.

## Compatibility And Verification

The binary target is unchanged: **Debian 13/trixie arm64 dArkOSRE on R36S**.
Original Ubuntu-based ArkOS, other distributions, and 32-bit firmware are not
supported by the bundled runtime. No playback/firmware upgrade is included.

**336 host tests passed**, covering JPEG integrity, v1.0.0 SVG migration, AI/data
preservation, shared artwork, idempotence, and failure rollback. The handheld
installation is deliberately left unchanged for a manual update test; no
device-side update or EmulationStation restart is performed for this release.

Instructions: <https://github.com/1Z99/r36s-tv/blob/main/INSTALLATION.md>.
