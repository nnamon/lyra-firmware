#!/usr/bin/env bash
# Pack this directory into an update.img.
set -euo pipefail
cd "$(dirname "$0")"

OUT="${1:-update.img}"

# rkimage lives in the flasher repo, because it is the same container parser
# the web tool uses. Prefer one on PATH, then a sibling checkout.
find_rkimage() {
  if command -v rkimage >/dev/null 2>&1; then
    RK=(rkimage)
    return 0
  fi
  local c
  for c in ../lyra_webtool ../../lyra_webtool "${LYRA_WEBTOOL:-}"; do
    [ -n "$c" ] && [ -f "$c/Cargo.toml" ] || continue
    RK=(cargo run --quiet --manifest-path "$c/Cargo.toml" -p rockusb --bin rkimage --)
    return 0
  done
  cat >&2 <<MSG
error: rkimage not found.

It is part of the flasher repository, which owns the container format:
  https://github.com/nnamon/lyra_webtool

Install it:
  cargo install --git https://github.com/nnamon/lyra_webtool rockusb

or check that repo out beside this one, or set LYRA_WEBTOOL to its path.
MSG
  exit 1
}
find_rkimage

for f in images/MiniLoaderAll.bin images/uboot.img images/boot.img images/rootfs.img; do
  [ -f "$f" ] || { echo "missing $f; see images/README.md" >&2; exit 1; }
done

# package-file and parameter.txt are packed from this directory, so the copies
# in images/ are what the packer reads.
cp -f package-file parameter.txt images/

"${RK[@]}" pack . "$OUT"
"${RK[@]}" info "$OUT"
