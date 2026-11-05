#!/usr/bin/env python3
"""Diagnose an unresponsive 2.7inch e-Paper HAT on a Luckfox Lyra Zero W.

Timings and the command sequence follow Waveshare's own epd2in7_V2 driver:
reset is 200 ms / low 2 ms / 200 ms settle, BUSY is HIGH while the panel is
working, SPI is mode 0 at 4 MHz.

Stages, each isolating one thing:

  0. Is BUSY stable when nothing is happening?
  1. GPIO only, no SPI. Reset the panel and trace BUSY throughout. A live
     panel asserts BUSY during its power-on.
  2. One byte over spidev: SWRESET. Tests the data path alone.
  3. Full init plus an all-black frame, with the refresh timed.
  4. Sweep every free GPIO during a refresh, in case BUSY is not on gpio 12.

Run on the board:  python3 epaper-diag.py
"""

import fcntl
import glob
import os
import struct
import sys
import time

PIN = {"RST": 3, "PWR": 14, "BUSY": 12, "DC": 11}
SPI_CLAIMED = (6, 8, 10)          # spi1 owns these; they cannot be GPIOs
GPIO = "/sys/class/gpio"
SPIDEV = "/dev/spidev1.0"
SPI_IOC_WR_MODE = 0x40016B01
SPI_IOC_WR_BITS_PER_WORD = 0x40016B03
SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04
W, H = 176, 264
FB = (W // 8) * H

# --- sysfs GPIO numbering ------------------------------------------------
# /sys/class/gpio/gpioN numbers are a global allocation handed out in gpiochip
# probe order, not a stable property of a pin. Enabling an SPI controller can
# reorder probing and shift every number, which silently points RST, DC, PWR
# and BUSY at the wrong pins while CLK/MOSI/CS keep working because pinctrl
# muxes those by name. So the PIN table below holds bank-0 offsets, which are
# stable, and the sysfs number is resolved at run time.

def gpiochips():
    out = []
    for d in sorted(glob.glob(GPIO + "/gpiochip*")):
        try:
            with open(os.path.join(d, "label")) as f:
                label = f.read().strip()
            with open(os.path.join(d, "base")) as f:
                base = int(f.read().strip())
            with open(os.path.join(d, "ngpio")) as f:
                ngpio = int(f.read().strip())
        except (OSError, ValueError):
            continue
        out.append((label, base, ngpio, os.path.basename(d)))
    return out


def describe_gpiochips():
    rows = gpiochips()
    if not rows:
        return "  (no gpiochips found)"
    return "\n".join("  %-18s base=%-5d ngpio=%-3d %s" % r for r in rows)


def bank0_base():
    """sysfs base of Rockchip GPIO bank 0."""
    chips = gpiochips()
    for label, base, _, _ in chips:
        if label == "gpio0":
            return base
    # Older kernels label banks by DT node name; take the lowest-based 32-pin chip.
    banks = sorted(b for _, b, n, _ in chips if n == 32)
    return banks[0] if banks else 0


GPIO_BASE = None   # set in main()


def sysfs_num(offset):
    return (GPIO_BASE or 0) + offset



def export(offset, direction):
    num = sysfs_num(offset)
    path = "%s/gpio%d" % (GPIO, num)
    if not os.path.isdir(path):
        try:
            with open(GPIO + "/export", "w") as f:
                f.write(str(num))
        except OSError:
            return None
    for _ in range(50):
        try:
            with open(path + "/direction", "w") as f:
                f.write(direction)
            break
        except OSError:
            time.sleep(0.02)
    else:
        return None
    try:
        return open(path + "/value", "w+b", buffering=0)
    except OSError:
        return None


def wr(f, v):
    f.write(b"1" if v else b"0")


def rd(f):
    f.seek(0)
    return f.read(1) == b"1"


def trace(f, seconds, label):
    """Sample a pin as fast as possible, reporting every transition."""
    t0 = time.time()
    edges = []
    last = rd(f)
    first = last
    n = 1
    while time.time() - t0 < seconds:
        v = rd(f)
        n += 1
        if v != last:
            edges.append(((time.time() - t0) * 1000, v))
            last = v
    print("  %-20s start=%s  %d samples/%.2fs  %d transition(s)"
          % (label, "HIGH" if first else "LOW", n, seconds, len(edges)))
    for ms, v in edges[:8]:
        print("      %+8.1f ms -> %s" % (ms, "HIGH" if v else "LOW"))
    if len(edges) > 8:
        print("      ... %d more" % (len(edges) - 8))
    return edges


def main():
    if os.geteuid() != 0:
        print("run as root", file=sys.stderr)
        return 1

    global GPIO_BASE
    print("gpiochips on this kernel:")
    print(describe_gpiochips())
    GPIO_BASE = bank0_base()
    print("bank 0 base = %d, so RST=%d PWR=%d BUSY=%d DC=%d\n"
          % (GPIO_BASE, sysfs_num(PIN["RST"]), sysfs_num(PIN["PWR"]),
             sysfs_num(PIN["BUSY"]), sysfs_num(PIN["DC"])))

    rst = export(PIN["RST"], "out")
    pwr = export(PIN["PWR"], "out")
    dc = export(PIN["DC"], "out")
    busy = export(PIN["BUSY"], "in")
    for name, f in (("RST", rst), ("PWR", pwr), ("DC", dc), ("BUSY", busy)):
        if f is None:
            print("cannot claim %s on gpio %d (bank0 offset %d)"
                  % (name, sysfs_num(PIN[name]), PIN[name]))
            return 1

    print("Stage 0: BUSY at rest")
    wr(pwr, 1)
    time.sleep(0.3)
    trace(busy, 1.0, "idle")
    print()

    print("Stage 1: reset, GPIO only (Waveshare timing)")
    # Trace across the whole sequence: assert RST in a thread-free way by
    # driving it between short traces, so an assertion during the low pulse
    # or the settle is not missed.
    wr(rst, 1)
    trace(busy, 0.2, "RST high, settle")
    wr(rst, 0)
    trace(busy, 0.05, "RST low")
    wr(rst, 1)
    e1 = trace(busy, 1.0, "RST released")
    stage1 = bool(e1)
    print()

    if not os.path.exists(SPIDEV):
        print("no %s; stages 2-4 need it" % SPIDEV)
        return 1

    fd = os.open(SPIDEV, os.O_WRONLY)
    fcntl.ioctl(fd, SPI_IOC_WR_MODE, struct.pack("B", 0))
    fcntl.ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, struct.pack("B", 8))
    fcntl.ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("<I", 4000000))

    def cmd(c, *data):
        wr(dc, 0)
        os.write(fd, bytes((c,)))
        if data:
            wr(dc, 1)
            os.write(fd, bytes(data))

    def hw_reset():
        wr(rst, 1); time.sleep(0.2)
        wr(rst, 0); time.sleep(0.002)
        wr(rst, 1); time.sleep(0.2)

    print("Stage 2: SWRESET over spidev")
    hw_reset()
    cmd(0x12)
    e2 = trace(busy, 1.0, "after 0x12")
    stage2 = bool(e2)
    print()

    print("Stage 3: init and an all-black frame")
    hw_reset()
    cmd(0x12)
    time.sleep(0.05)
    cmd(0x45, 0x00, 0x00, 0x07, 0x01)
    cmd(0x4F, 0x00, 0x00)
    cmd(0x11, 0x03)
    cmd(0x4E, 0x00)
    cmd(0x4F, 0x00, 0x00)
    wr(dc, 0)
    os.write(fd, bytes((0x24,)))
    wr(dc, 1)
    buf = bytes(FB)                      # all zero bits == all black
    n = 0
    while n < len(buf):
        n += os.write(fd, buf[n:n + 4096])
    cmd(0x22, 0xF7)
    wr(dc, 0)
    os.write(fd, bytes((0x20,)))         # activate
    e3 = trace(busy, 6.0, "after 0x20")
    stage3 = bool(e3)
    print()

    print("Stage 4: sweep every free GPIO during a refresh")
    # If BUSY is not really on gpio 12, some other pin will move here.
    others = {}
    for num in range(0, 32):
        if num in SPI_CLAIMED or num in PIN.values():
            continue
        f = export(num, "in")
        if f is not None:
            others[num] = f
    print("  watching %d other gpios: %s"
          % (len(others), ",".join(str(n) for n in sorted(others))))
    before = {n: rd(f) for n, f in others.items()}
    hw_reset()
    cmd(0x12); time.sleep(0.05)
    cmd(0x45, 0x00, 0x00, 0x07, 0x01)
    cmd(0x4F, 0x00, 0x00)
    cmd(0x11, 0x03)
    cmd(0x4E, 0x00)
    cmd(0x4F, 0x00, 0x00)
    wr(dc, 0); os.write(fd, bytes((0x24,)))
    wr(dc, 1)
    n = 0
    while n < len(buf):
        n += os.write(fd, buf[n:n + 4096])
    cmd(0x22, 0xF7)
    wr(dc, 0); os.write(fd, bytes((0x20,)))
    moved = {}
    t0 = time.time()
    while time.time() - t0 < 5.0:
        for num, f in others.items():
            if rd(f) != before[num]:
                moved.setdefault(num, (time.time() - t0) * 1000)
    if moved:
        for num in sorted(moved):
            print("  gpio %-3d changed %.0f ms in  <-- candidate BUSY"
                  % (num, moved[num]))
    else:
        print("  no other gpio moved")
    print()

    print("Result")
    for n_, ok in (("0x12 SWRESET", stage2), ("RST pulse", stage1),
                   ("0x20 refresh", stage3)):
        print("  %-14s %s" % (n_, "BUSY responded" if ok else "no response"))
    print()
    print("  Did the screen turn black? That answers more than BUSY does:")
    print("    black  -> SPI works, only BUSY sensing is wrong")
    print("    blank  -> SPI is not reaching the panel")
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
