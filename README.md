# lyra-firmware

Build and modify the firmware that runs on a Luckfox Lyra Zero W (Rockchip
RK3506B). Everything here runs in Docker, so none of it needs a Linux machine.

Flashing is a separate problem, solved by
[lyra_webtool](https://github.com/nnamon/lyra_webtool), which writes these
images to a board from a browser over WebUSB.

## The rule

**The Luckfox SDK is the source of truth.** A tool here exists either to run
the SDK, or to do something the SDK cannot. Anything that reimplements SDK
behaviour for convenience says so and defers to it.

Luckfox's own pages are the reference:
[SDK image compilation](https://wiki.luckfox.com/Luckfox-Lyra/SDK-Image-Compilation),
[Buildroot configuration](https://wiki.luckfox.com/Luckfox-Lyra/Buildroot-Configuration),
[Kernel configuration](https://wiki.luckfox.com/Luckfox-Lyra/Kernel-Configuration),
[Device tree](https://wiki.luckfox.com/Luckfox-Lyra/Device-Tree).

## What is here

| Directory | Does | Wraps the SDK? |
| --- | --- | --- |
| `sdk/` | runs the Luckfox SDK in Docker | yes |
| `rootfs/` | unpacks and repacks `rootfs.img`, a UBI image | no: the SDK cannot edit a built filesystem |
| `rootfs/cross/` | cross-compiles for the board and checks the ABI | no: should prefer the SDK toolchain when one is present |
| `rootfs/epaper/` | a 2.7inch e-Paper HAT demo that starts at boot | no |
| `build.sh`, `image.conf` | packs an `update.img` from loose partition images | duplicates `sdk.sh build firmware`, which is authoritative |

[docs/linux.md](docs/linux.md) compares the routes and says which to use when.

## Start here

The SDK, once:

```sh
cd sdk
./sdk.sh init ~/Downloads/luckfox-lyra-<date>.tar.gz   # 2 GB, hours
./sdk.sh build
./sdk.sh fetch
```

It must be Ubuntu 22.04 on x86_64, which the container pins, because the SDK
ships prebuilt x86_64 host tools and Buildroot needs a host compiler that can
emit 32-bit binaries. The source tree also has to sit on ext4, so it lives in
a Docker volume rather than a directory. See [sdk/README.md](sdk/README.md).

## Before changing firmware, check whether you need to

A pin that no enabled device tree node claims is already usable as GPIO. A
userspace driver on those pins needs no firmware change and cannot stop the
board booting. `rootfs/epaper/` works that way, which is why it runs on an
unmodified vendor `boot.img`.

Read `/sys/kernel/debug/pinctrl/` on a running board to see what is claimed.

## rkimage

`build.sh` needs `rkimage`, which lives in the flasher repository because it
is the same container parser the web tool uses:

```sh
cargo install --git https://github.com/nnamon/lyra_webtool rockusb
```

Checking that repository out beside this one works too.

## Verification status

Checked against real hardware or real vendor artefacts:

- `rootfs/ubifs.sh` round-trips a real vendor `rootfs.img`: 7263 entries
  identical, including modes, owners, link counts, device numbers and
  contents, and the repacked image is the same size.
- `rootfs/cross/cross.sh` produces binaries whose ELF flags and interpreter
  match the board's own `busybox`, and they run under emulation against the
  board's real glibc 2.38.
- The SDK's own `check-sdk.sh` and `check-buildroot.sh` both pass inside the
  `sdk/` container.

Not checked:

- A full `./sdk.sh build` has never been run. Expect to add a missing apt
  package or two on the first attempt.
- `rootfs/epaper/` has never met a panel. The command sequences are
  Waveshare's and the pin mapping comes from the datasheet and the board
  pinout, but no frame has been drawn on glass.

Previously tried and removed: a tool that repacked the `boot.img` FIT by hand
to enable SPI. The image it produced did not boot. Device tree changes belong
to the SDK, which packs the FIT the way the vendor's was.

## Licence

MIT.
