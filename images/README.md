# images

Put the partition images here. Luckfox's zip,
`Luckfox_Lyra_Zero_W_Flash_261020`, ships all of them loose.

| File | What it is | Replace it |
| --- | --- | --- |
| `MiniLoaderAll.bin` | Loader, also written as the IDBlock | No, keep the vendor's |
| `uboot.img` | U-Boot, 4 MiB partition | Not yet: an SDK build is a suspect in the boot failure |
| `boot.img` | Kernel and device tree, 12 MiB partition | Yes, an SDK build boots |
| `rootfs.img` | UBI image holding a UBIFS root filesystem | Not yet: an SDK build is a suspect in the boot failure |

`build.sh` copies `package-file` and `parameter.txt` in from the directory
above, so don't edit the copies here.

Git ignores these files.

`../sdk/sdk.sh fetch` writes `uboot.img`, `boot.img` and `rootfs.img` here
and leaves the vendor `MiniLoaderAll.bin` alone.
