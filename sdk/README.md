# Building from the Luckfox SDK

`sdk.sh` runs Luckfox's documented Lyra build in Docker and produces
`uboot.img`, `boot.img` and `rootfs.img`. It follows these pages:

- [SDK image compilation](https://wiki.luckfox.com/Luckfox-Lyra/SDK-Image-Compilation)
- [Buildroot configuration](https://wiki.luckfox.com/Luckfox-Lyra/Buildroot-Configuration)
- [Kernel configuration](https://wiki.luckfox.com/Luckfox-Lyra/Kernel-Configuration)

Use the Lyra pages, not the Pico ones. The Pico Pro Max page spells the
menuconfig target `buildrootconfig`; this SDK only accepts `buildroot-config`.
The Pico page also warns about uClibc. The Lyra toolchain is
`arm-none-linux-gnueabihf`, which is glibc.

## Requirements

- Docker with about 60 GB free in its disk image. The SDK source is about
  15 GB before building.
- The SDK tarball, `Luckfox_Lyra_SDK_<date>.tar.gz`, from Luckfox's Google
  Drive. `sdk.sh` won't download it:
  <https://drive.google.com/drive/folders/1l2ixhfw53J3eZunyvHMnw2DYViH9d8Cx>
- On an Apple Silicon Mac, turn on "Use Rosetta for x86_64/amd64 emulation" in
  Docker Desktop under General. Without it the build runs under QEMU and
  takes several times longer.

The container must be amd64. The SDK ships x86_64 host tools in
`prebuilts/gcc/linux-x86/`, and Buildroot needs a host compiler that emits
32-bit binaries.

## Build

1. Extract the SDK and select the board:

   ```sh
   ./sdk.sh init ~/Downloads/Luckfox_Lyra_SDK_<date>.tar.gz
   ```

   `init` runs `repo sync -l` on the tarball's mirror, then selects
   `luckfox_lyra_zero-w_buildroot_spinand_defconfig` (`lunch` options 4, 1,
   0). If your release lacks that defconfig, `init` lists the rk3506 ones it
   has. Set `DEFCONFIG` at the top of `sdk.sh` to one of them.
2. Apply this repo's changes from `config/`:

   ```sh
   ./sdk.sh apply
   ```

3. Build everything, in the documented order (uboot, kernel, rootfs,
   firmware):

   ```sh
   ./sdk.sh build
   ```

   The SDK's `build.sh` exits 0 even when a step fails. `sdk.sh` checks the
   target's image was rebuilt and fails if it wasn't.
4. Copy the images out to `../images/`:

   ```sh
   ./sdk.sh fetch
   ```

   `fetch` copies `uboot.img`, `boot.img` and `rootfs.img` and keeps the
   vendor `MiniLoaderAll.bin` and `parameter.txt`. `./sdk.sh fetch all`
   takes everything in `output/firmware/`, the SDK loader included.
5. Write `boot.img` to sector `0x4000` with the web tool's Flash tab.

An SDK-built `boot.img` boots. A full SDK-built `update.img` doesn't; the
fault is in `uboot.img` or `rootfs.img` and isn't found yet.

After the first build, rebuild only what you changed. `./sdk.sh build rootfs`
takes minutes.

## Changing something

| Goal | Commands |
| --- | --- |
| Add or remove a package | `./sdk.sh build buildroot-config`, then `./sdk.sh build rootfs` |
| Change a kernel option | `./sdk.sh build kernel-config`, then `./sdk.sh build kernel` |
| Edit the device tree | `./sdk.sh get kernel-6.1/arch/arm/boot/dts/rk3506b-luckfox-lyra-zero-w.dts`, edit, `./sdk.sh put rk3506b-luckfox-lyra-zero-w.dts kernel-6.1/arch/arm/boot/dts/`, then `./sdk.sh build kernel` |
| Add files to the root filesystem | put them in `config/overlay/`, then `./sdk.sh apply` and `./sdk.sh build rootfs` |
| Anything else | `./sdk.sh shell` |

`buildroot-config` and `kernel-config` open menuconfig and save the result to
the defconfig, so a clean build keeps it.

A change made only inside the volume is lost on `./sdk.sh clean`. Put it in
`config/` so `apply` can redo it.

Kernel modules install into `rootfs.img` under `/lib/modules`. A driver built
as a module needs `./sdk.sh build kernel`, then `./sdk.sh build rootfs`.

### A package doesn't appear after you enable it

Buildroot skips a package whose build directory already exists. Delete it and
rebuild:

```sh
./sdk.sh shell
rm -rf buildroot/output/rockchip_rk3506_luckfox/build/<package>-*
exit
./sdk.sh build rootfs
```

## Why the tree lives in a Docker volume

`check-sdk.sh` runs `findmnt -o FSTYPE` on the SDK directory and exits unless
it's `ext4`, `f2fs` or `btrfs`. On Docker Desktop for Mac:

| Location | Reported type | SDK check |
| --- | --- | --- |
| Named Docker volume | `ext4` | passes |
| Directory bind-mounted from macOS | `virtiofs` | fails |
| The container's own filesystem | `overlay` | fails |

`sdk.sh` keeps the tree in the volume `lyra-sdk` and moves files with `get`,
`put` and `fetch`. `./sdk.sh clean` deletes the volume.

The container runs as a non-root user that owns the tree. `check-sdk.sh`
requires the owner to match, and Buildroot's configure scripts refuse root.

## Dependencies

The `Dockerfile` installs the apt list from Luckfox's SDK compilation page,
plus what the SDK's preflight scripts check for. It leaves out
`python-is-python3`, which conflicts with the
`ln -sf /usr/bin/python2 /usr/bin/python` the same page tells you to run. The
`Dockerfile` creates that symlink instead.

## The network warning

Every build prints this and waits 5 s:

```
Your network is not able to access sources.buildroot.net!
```

Ignore it. `check-network.sh` sends `HEAD` to the bare hostname, which returns
403, then falls back to `ping`, which Docker's VM blocks. Downloads work. The
Zero W config leaves `RK_NETWORK_CHECK` unset, so the check only warns.
