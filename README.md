# lyra-firmware

Build and modify firmware for the Luckfox Lyra Zero W (Rockchip RK3506B). Every
tool runs in Docker, so you don't need a Linux machine.

[lyra_webtool](https://github.com/nnamon/lyra_webtool) flashes the images from
a browser over WebUSB.

## SDK first

The Luckfox SDK is the reference build. Tools here either run the SDK or do
something it can't. A tool that duplicates SDK behaviour says so.

Luckfox's pages:
[SDK image compilation](https://wiki.luckfox.com/Luckfox-Lyra/SDK-Image-Compilation),
[Buildroot configuration](https://wiki.luckfox.com/Luckfox-Lyra/Buildroot-Configuration),
[Kernel configuration](https://wiki.luckfox.com/Luckfox-Lyra/Kernel-Configuration),
[Device tree](https://wiki.luckfox.com/Luckfox-Lyra/Device-Tree).

## Contents

| Directory | Does | Wraps the SDK |
| --- | --- | --- |
| `sdk/` | runs the Luckfox SDK in Docker | yes |
| `sdk/config/` | this repo's changes to the SDK tree, reapplied by `./sdk.sh apply` | yes |
| `rootfs/` | unpacks `rootfs.img` for inspection | no |
| `rootfs/cross/` | cross-compiles for the board and checks the ABI | no |
| `epaper/` | drives a 2.7inch e-Paper HAT with Waveshare's driver | no |
| `build.sh`, `image.conf` | packs an `update.img` from loose partition images | duplicates `./sdk.sh build firmware` |

[docs/linux.md](docs/linux.md) covers the ways to change what runs on the
board.

## Building

The first build takes hours and needs the 2 GB SDK tarball from Luckfox:

```sh
cd sdk
./sdk.sh init ~/Downloads/Luckfox_Lyra_SDK_<date>.tar.gz
./sdk.sh apply
./sdk.sh build
./sdk.sh fetch
```

The container is Ubuntu 22.04 on amd64, because the SDK ships x86_64 host
tools. The source tree lives in a Docker volume, because the SDK only builds
on ext4, f2fs or btrfs. [sdk/README.md](sdk/README.md) has the details.

## GPIO without a firmware change

A pin that no enabled device tree node claims is already usable as GPIO. A
userspace driver on those pins needs no new firmware and can't stop the board
booting. `epaper/epaper-demo.py` runs this way on the vendor `boot.img`.

Read `/sys/kernel/debug/pinctrl/` on the running board to see which pins are
claimed.

## rkimage

`build.sh` needs `rkimage`, which lives in the flasher repo because it shares
the web tool's container parser:

```sh
cargo install --git https://github.com/nnamon/lyra_webtool rockusb
```

A checkout of lyra_webtool beside this repo also works.

## Status

Working on a real board:

- An SDK-built `boot.img`, written to sector `0x4000`, boots the SDK kernel.
- `sdk/config/0001-spi1-epaper.patch` gives `/dev/spidev1.0` and claims the
  three `rm_io` pin groups.
- `epaper/` draws on a 2.7inch HAT in 1-bit and 4 grey levels.
- `rootfs/cross/cross.sh` binaries match the ELF flags and interpreter of the
  board's `busybox` and run against its glibc 2.38.

Broken:

- A full SDK-built `update.img` doesn't boot. `boot.img` alone is fine, so the
  fault is in `uboot.img` or `rootfs.img`.
- `rootfs/ubifs.sh pack` output doesn't boot. Use `ubifs.sh` for inspection
  only.
- `build.sh` packs `update.img` by hand. Use `./sdk.sh build firmware`
  instead.

## Licence

MIT.
