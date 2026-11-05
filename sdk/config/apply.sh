#!/bin/sh
# Reapply this repo's SDK changes after ./sdk.sh init. Safe to run twice.
set -e

DTS=kernel-6.1/arch/arm/boot/dts/rk3506b-luckfox-lyra-zero-w.dts
DEFCONFIG=buildroot/configs/rockchip_rk3506_luckfox_defconfig

cd /sdk

# SPI1 on the 40-pin header, for the e-paper HAT.
if grep -q "rm_io8_spi1_clk" "$DTS"; then
  echo "dts: spi1 already routed, skipping"
else
  [ -f "$DTS.orig" ] || cp "$DTS" "$DTS.orig"
  patch -p0 "$DTS" < /config/0001-spi1-epaper.patch
  echo "dts: spi1 routed onto the HAT's pins"
fi

# Buildroot mirrors and the root filesystem overlay.
if grep -q "ftpmirror.gnu.org" "$DEFCONFIG"; then
  echo "defconfig: already patched, skipping"
else
  cat /config/defconfig.fragment >> "$DEFCONFIG"
  echo "defconfig: mirrors and overlay set"
fi

# Earlier versions of the fragment used = and dropped the vendor overlays.
if grep -q '^BR2_ROOTFS_OVERLAY="/sdk/lyra-overlay"' "$DEFCONFIG"; then
  sed -i 's|^BR2_ROOTFS_OVERLAY="/sdk/lyra-overlay"|BR2_ROOTFS_OVERLAY+="/sdk/lyra-overlay"|' "$DEFCONFIG"
  echo "defconfig: overlay now appends to the vendor list"
fi

mkdir -p /sdk/lyra-overlay
if [ -d /config/overlay ]; then
  cp -a /config/overlay/. /sdk/lyra-overlay/
  echo "overlay: copied $(find /config/overlay -type f | wc -l) file(s)"
fi

echo
echo "Build with ./sdk.sh build"
