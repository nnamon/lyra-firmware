# Changing the Linux environment

The board runs Buildroot, so there's no package manager. You can change it in
two ways.

| Route | Reaches | Cost |
| --- | --- | --- |
| Edit on the running board | Config files, scripts, startup | Seconds |
| Rebuild from the SDK | Packages, kernel, device tree, U-Boot | Hours the first time, needs Docker and a 2 GB download |

`rootfs/ubifs.sh` can also repack `rootfs.img` offline, but its output doesn't
boot. Use it to inspect an image, not to build one.

## What the images hold

| Image | Partition | Contents |
| --- | --- | --- |
| `uboot.img` | 4 MiB at 4 MiB | U-Boot |
| `boot.img` | 12 MiB at 8 MiB | Kernel and device tree, as a compressed FIT image |
| `rootfs.img` | 32 MiB onward | UBI image holding a UBIFS root filesystem |

Kernel modules live in `rootfs.img` under `/lib/modules`, not in `boot.img`.
A built-in driver change needs only a new `boot.img`. A module change needs a
new `boot.img` and a new `rootfs.img`, built from the same kernel.

## Editing on the running board

UBIFS is read-write, so edits survive a reboot. Check `cat /proc/mounts`
shows `ubi0:rootfs` mounted `rw` before you rely on it.

1. Get a shell. Over USB the board is an RNDIS adapter at `192.168.123.100`.
   Over UART, use the console on the pin header.
2. Edit with `vi` or `nano`.
3. Add a startup script as `/etc/init.d/S99name` and `chmod +x` it. BusyBox
   init runs `/etc/init.d/S*` in name order.
4. Reboot and check the change is still there.

You can't install a program this way, but you can copy one over.
`rootfs/cross/` cross-compiles in Docker without the SDK:

```sh
cd rootfs/cross
./cross.sh cc -O2 hello.c -o hello     # armv7-a, hard float, VFPv4
./cross.sh check hello                 # will it load on the board
scp hello root@192.168.123.100:/root
```

The board has glibc 2.38. A binary that needs a newer symbol version loads and
then dies. `check` catches that, plus a wrong architecture or interpreter.
`rootfs/cross/README.md` has the target ABI.

The SDK's own toolchain also works, at
`prebuilts/gcc/linux-x86/arm/gcc-arm-10.3-2021.07-x86_64-arm-none-linux-gnueabihf/bin`.

## Rebuilding from the SDK

Use this to add packages, change the kernel config or change the device tree.
[sdk/README.md](../sdk/README.md) has the steps.

### Where the config lives

| What | Path in the SDK |
| --- | --- |
| Board config | `device/rockchip/.chips/rk3506/luckfox_lyra_zero-w_buildroot_spinand_defconfig` |
| Partition table | `device/rockchip/.chips/rk3506/parameter-lyra-spinand.txt` |
| Buildroot packages | `buildroot/configs/rockchip_rk3506_luckfox_defconfig` |
| Root filesystem type | `buildroot/configs/rockchip/fs/ubifs.config` |
| Kernel config | `kernel-6.1/arch/arm/configs/rk3506_luckfox_defconfig` |
| Device tree, board | `kernel-6.1/arch/arm/boot/dts/rk3506b-luckfox-lyra-zero-w.dts` |
| Device tree, shared | `kernel-6.1/arch/arm/boot/dts/rk3506-luckfox-lyra-ultra.dtsi` |

### Adding files

Put them in `sdk/config/overlay/` and run `./sdk.sh apply`. Buildroot copies
the tree onto the root filesystem on every build.

## Enabling SPI, I2C, PWM or a UART

The vendor kernel has these drivers built in. Enabling one needs a `status`
change in the device tree, which means a new `boot.img`:

```sh
cd sdk
./sdk.sh shell          # edit the DTS, then exit
./sdk.sh build kernel
./sdk.sh fetch
```

Build `boot.img` with the SDK. A FIT repacked by hand doesn't boot. To keep
the change across `./sdk.sh clean`, save it as a patch in `sdk/config/`.

The Zero W device tree is `rk3506b-luckfox-lyra-zero-w.dts`. It includes
`rk3506-luckfox-lyra-ultra.dtsi`, shared with the Ultra, Ultra W and Pi. The
`-sd` variant is for SD boot, not SPI NAND. The vendor `.dts` enables a DSI
panel and touch controllers, and nothing on the 40-pin header.

You may not need a firmware change at all. A pin that no enabled node claims
is already usable as GPIO. Check `/sys/kernel/debug/pinctrl/` on the running
board to see what's claimed.

## WiFi on the Zero W

`wlan0` stays `NO-CARRIER` until the antenna GPIO is driven high:

```sh
echo 55 > /sys/class/gpio/export
echo out > /sys/class/gpio/gpio55/direction
echo 1 > /sys/class/gpio/gpio55/value
wifi-connect.sh <SSID> <password>
udhcpc -i wlan0
```

`55` is a sysfs number on the vendor device tree. Sysfs numbers move when the
device tree changes, so check `/sys/kernel/debug/gpio` on other builds.

The setting is lost on reboot. To keep it, put the commands in
`/etc/init.d/S99wlan0` with a `sleep 5` first. Add the script on the running
board, or ship it in `sdk/config/overlay/etc/init.d/`.
