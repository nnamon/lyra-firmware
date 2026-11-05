# Inspecting the root filesystem

`ubifs.sh` unpacks `rootfs.img` into a Docker volume so you can read it. It
can also repack an image, but **repacked images don't boot**. To change the
root filesystem, build it with the SDK (see [../sdk/README.md](../sdk/README.md)).

## Image format

`rootfs.img` is a UBI image holding one UBIFS volume. There's no partition
table and no loop mount.

| Property | Value |
| --- | --- |
| Volume | id 0, name `rootfs`, dynamic, `autoresize` |
| Logical eraseblock | 0x1f000 (126976 bytes) |
| Physical eraseblock | 0x20000 (131072 bytes) |
| Minimum I/O unit | 0x800 (2048 bytes) |
| Sub-page | 2048 bytes |
| Maximum LEB count | 8456, which caps the volume at 1 GiB |
| Compression | lzo |
| Free space fixup | on (`mkfs.ubifs -F`) |

These match the board's Winbond W25N02KV: 2048-byte pages, 128 KiB blocks.
`autoresize` makes the volume fill the 224 MiB after `uboot` and `boot`, which
is why `parameter.txt` marks the partition `rootfs:grow`.

If a new SDK release changes them, these are the files to check:

| Value | Source in the SDK |
| --- | --- |
| LEB size, sub-page | `buildroot/configs/rockchip/fs/ubifs.config` |
| Minimum I/O, PEB size | `buildroot/fs/ubifs/Config.in`, `buildroot/fs/ubi/Config.in` |
| Maximum LEB count | `BR2_TARGET_ROOTFS_UBIFS_MAX_SIZE=1024`, so 1024 MiB / 126976 |
| Compression | `BR2_TARGET_ROOTFS_UBIFS_RT_LZO` |
| Volume layout | `buildroot/fs/ubi/ubinize.cfg` |

To read the geometry of your own image:

```sh
./ubifs.sh info rootfs.img
```

## Reading an image

1. Unpack it: `./ubifs.sh unpack /path/to/rootfs.img`
2. Open a root shell in the tree: `./ubifs.sh shell`. You start in
   `/work/tree`.

The tree lives in a Docker volume because a macOS bind mount silently drops
device nodes, setuid bits and ownership.

## Repacking

These commands work, but the image they produce doesn't boot. Don't flash it.

- `./ubifs.sh pack new-rootfs.img` builds an image from the unpacked tree.
- `./ubifs.sh apply` copies `files/` onto the tree and makes those files
  root-owned.
- `./ubifs.sh roundtrip /path/to/rootfs.img` unpacks, repacks and compares
  the two file trees.

`roundtrip` reports all 7263 entries of the vendor image identical, and the
repacked image still doesn't boot. Matching file trees don't show the kernel
can mount the result. The cause isn't found yet.

## New programs

Buildroot has no package manager. To add a program, cross-compile it with
[cross/](cross/README.md) and copy it to the board, or enable the package in
the SDK.
