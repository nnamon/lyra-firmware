#!/usr/bin/env python3
"""Graphical demo for a Waveshare 2.7inch e-Paper HAT on a Luckfox Lyra Zero W.

Self-contained: it has its own panel code instead of Waveshare's driver. It
uses hardware SPI through /dev/spidev1.0 when that exists, and bit-bangs sysfs
GPIO otherwise, so it also runs on the vendor boot.img. A frame takes about
12 ms over spidev and about 15 s bit-banged.

    python3 epaper-demo.py                 # cycle every scene, forever
    python3 epaper-demo.py --scene life    # one scene
    python3 epaper-demo.py --list          # list scenes
    python3 epaper-demo.py --frames 20     # frames per scene
    python3 epaper-demo.py --fast          # V2 only: 1.5 s refresh, not 3 s
    python3 epaper-demo.py --bitbang       # force GPIO, ignore spidev
    python3 epaper-demo.py --spidev PATH   # use another SPI device

Only one transport works per boot. spi1 takes GPIO0_A6, GPIO0_B0 and GPIO0_B2
from the GPIO controller, so with spidev present the bit-bang path can't claim
CLK, DIN or CS.

Scenes:
    cube     rotating wireframe cube
    torus    rotating wireframe torus
    life     Conway's Game of Life, seeded with a glider gun
    mandel   Mandelbrot zoom, Floyd-Steinberg dithered
    julia    animated Julia set, dithered
    plasma   plasma effect, dithered
    balls    metaballs, threshold shaded
    tunnel   rotating tunnel
    photo    a dithered still, wiped in a band at a time

Ctrl-C puts the panel to sleep and exits.
"""

import argparse
import fcntl
import glob
import math
import os
import random
import signal
import struct
import sys
import time

W, H = 176, 264
ROW = W // 8
FB = ROW * H

PIN = {"RST": 3, "PWR": 14, "BUSY": 12, "DIN": 6, "DC": 11, "CLK": 8, "CS": 10}
BUSY_TIMEOUT = 25.0
# How long to let the panel assert BUSY before deciding it never will.
BUSY_ASSERT_TIMEOUT = 0.5
GPIO = "/sys/class/gpio"

# spi1 drives the 40-pin header: CLK on GPIO0_B0 (pin 23), MOSI on GPIO0_A6
# (pin 19), CSn0 on GPIO0_B2 (pin 24). The aliases node makes it bus 1, so
# chip select 0 is /dev/spidev1.0.
SPIDEV = "/dev/spidev1.0"
SPI_HZ = 4000000

# From include/uapi/linux/spi/spidev.h: _IOW('k', nr, type).
SPI_IOC_WR_MODE = 0x40016B01
SPI_IOC_WR_BITS_PER_WORD = 0x40016B03
SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04

# The kernel assigns /sys/class/gpio numbers in probe order. Enabling spi1
# shifts them, which moves RST, DC, PWR and BUSY onto the wrong pins while the
# spi1 pins keep working. PIN holds bank-0 offsets, converted to sysfs numbers
# at run time.

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
    # Some kernels label banks by DT node name. Take the lowest 32-line chip.
    banks = sorted(b for _, b, n, _ in chips if n == 32)
    return banks[0] if banks else 0


GPIO_BASE = None   # set in main()


def sysfs_num(offset):
    return (GPIO_BASE or 0) + offset


stop = False


def on_sig(signum, frame):
    global stop
    stop = True


def die(msg):
    sys.stderr.write("error: %s\n" % msg)
    sys.exit(1)


class Pin:
    def __init__(self, num, direction):
        num = sysfs_num(num)
        path = "%s/gpio%d" % (GPIO, num)
        if not os.path.isdir(path):
            try:
                with open(GPIO + "/export", "w") as f:
                    f.write(str(num))
            except OSError as e:
                die("cannot export gpio %d: %s" % (num, e))
        for _ in range(50):
            try:
                with open(path + "/direction", "w") as f:
                    f.write(direction)
                break
            except OSError:
                time.sleep(0.02)
        else:
            die("cannot set direction of gpio %d" % num)
        self.f = open(path + "/value", "w+b", buffering=0)

    def set(self, v):
        self.f.write(b"1" if v else b"0")

    def get(self):
        self.f.seek(0)
        return self.f.read(1) == b"1"


class BitBangBus:
    """SPI clocked out of sysfs GPIO, one file write per edge.

    Works on the vendor image, where no device tree node claims CLK, DIN and
    CS. A 5808-byte frame takes about 15 s.
    """

    def __init__(self):
        self.name = "bit-banged GPIO"
        self.cs = Pin(PIN["CS"], "out")
        self.clk = Pin(PIN["CLK"], "out")
        self.din = Pin(PIN["DIN"], "out")
        self.cs.set(1)
        self.clk.set(0)

    def write(self, buf):
        """Clock out every byte with CS held low for the whole buffer."""
        cw, dw = self.clk.f.write, self.din.f.write
        one, zero = b"1", b"0"
        self.cs.set(0)
        for b in buf:
            for i in (7, 6, 5, 4, 3, 2, 1, 0):
                cw(zero)
                dw(one if (b >> i) & 1 else zero)
                cw(one)
            cw(zero)
        self.cs.set(1)

    def close(self):
        pass


class SpidevBus:
    """Hardware SPI through a spidev device.

    The controller drives the clock and chip select. A frame takes about 12 ms.
    """

    def __init__(self, path, hz=SPI_HZ):
        self.name = path
        try:
            self.fd = os.open(path, os.O_WRONLY)
        except OSError as e:
            die("cannot open %s: %s\n"
                "  this firmware has no spidev on that bus; run with --bitbang"
                % (path, e))
        try:
            fcntl.ioctl(self.fd, SPI_IOC_WR_MODE, struct.pack("B", 0))
            fcntl.ioctl(self.fd, SPI_IOC_WR_BITS_PER_WORD, struct.pack("B", 8))
            fcntl.ioctl(self.fd, SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("<I", hz))
        except OSError as e:
            die("%s: cannot configure the bus: %s" % (path, e))
        # spidev rejects writes longer than its bufsiz parameter (4096 bytes
        # by default), so send the frame in chunks. CS rises between chunks.
        # The panel latches each byte on its own 8 clocks, so that's fine.
        self.chunk = 4096
        try:
            with open("/sys/module/spidev/parameters/bufsiz") as f:
                self.chunk = max(1, int(f.read().strip()))
        except (OSError, ValueError):
            pass

    def write(self, buf):
        if not isinstance(buf, (bytes, bytearray)):
            buf = bytes(buf)
        mv = memoryview(buf)
        i, n, chunk = 0, len(mv), self.chunk
        while i < n:
            j = i + chunk if i + chunk < n else n
            # spidev doesn't return short writes. Advance by the count anyway.
            i += os.write(self.fd, mv[i:j])

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


def open_bus(bitbang=False, path=None):
    """Hardware SPI if the spidev device exists, bit-banged GPIO otherwise."""
    if bitbang:
        return BitBangBus()
    if path:
        return SpidevBus(path)
    if os.path.exists(SPIDEV):
        return SpidevBus(SPIDEV)
    return BitBangBus()


class Panel:
    def __init__(self, v2=True, fast=False, bus=None):
        self.v2 = v2
        self.fast = fast
        self.bus = bus if bus is not None else open_bus()
        self.rst = Pin(PIN["RST"], "out")
        self.dc = Pin(PIN["DC"], "out")
        self.pwr = Pin(PIN["PWR"], "out")
        self.busy = Pin(PIN["BUSY"], "in")
        self.never_busy = 0
        self.pwr.set(1)
        time.sleep(0.1)

    def cmd(self, c):
        self.dc.set(0)
        self.bus.write(bytes((c,)))

    def data(self, *vals):
        self.dc.set(1)
        self.bus.write(bytes(vals))

    def block(self, buf):
        self.dc.set(1)
        self.bus.write(buf)

    def busy_now(self):
        """True while the panel is working. V1 holds BUSY low, V2 holds it high."""
        if self.v2:
            return self.busy.get()
        self.cmd(0x71)
        return not self.busy.get()

    def wait(self, what="panel"):
        """Wait for BUSY to assert, then to clear.

        Over spidev the trigger command finishes in microseconds, before the
        panel asserts BUSY. Polling only for idle reads the stale idle level
        and returns early.
        """
        t0 = time.time()
        started = False
        while time.time() - t0 < BUSY_ASSERT_TIMEOUT:
            if self.busy_now():
                started = True
                break
            time.sleep(0.001)
        if not started:
            # The refresh finished inside one poll, or no panel is connected.
            # Report it instead of claiming success.
            self.never_busy += 1
        while True:
            if not self.busy_now():
                return True
            if time.time() - t0 > BUSY_TIMEOUT:
                sys.stderr.write("  %s did not go idle in %.0fs\n"
                                 % (what, BUSY_TIMEOUT))
                return False
            time.sleep(0.02)

    def reset(self):
        """Reset with Waveshare's epd2in7_V2 timing: 200 ms high, 2 ms low,
        200 ms high.

        The panel powers on internally during the last 200 ms and ignores
        commands sent before that finishes.
        """
        self.rst.set(1); time.sleep(0.2)
        self.rst.set(0); time.sleep(0.002)
        self.rst.set(1); time.sleep(0.2)

    def _init_v2(self):
        if not self.wait("after reset"):
            return False
        self.cmd(0x12)                       # software reset
        if not self.wait("after SWRESET"):
            return False
        self.cmd(0x44); self.data(0x00, ROW - 1)      # RAM X window, bytes
        self.cmd(0x45); self.data(0x00, 0x00, 0x07, 0x01)   # RAM Y 0..263
        self.cmd(0x11); self.data(0x03)              # X inc, Y inc
        return True

    def _init_v2_fast(self):
        """Select the 1.5 s fast waveform.

        The panel must load its temperature value and select the waveform
        first. Sending refresh byte 0xC7 alone doesn't give a fast update."""
        if not self.wait("after reset"):
            return False
        self.cmd(0x12)
        if not self.wait("after SWRESET"):
            return False
        self.cmd(0x18); self.data(0x80)       # use the built-in temp sensor
        self.cmd(0x22); self.data(0xB1)       # load temperature value
        self.cmd(0x20)
        if not self.wait("loading temperature"):
            return False
        self.cmd(0x1A); self.data(0x64, 0x00)  # write temperature register
        self.cmd(0x44); self.data(0x00, ROW - 1)
        self.cmd(0x45); self.data(0x00, 0x00, 0x07, 0x01)
        self.cmd(0x11); self.data(0x03)
        self.cmd(0x22); self.data(0x91)       # select the fast waveform
        self.cmd(0x20)
        if not self.wait("selecting fast waveform"):
            return False
        return True

    def init(self):
        self.reset()
        if self.v2:
            return self._init_v2_fast() if self.fast else self._init_v2()
        self.cmd(0x01); self.data(0x03, 0x00, 0x2B, 0x2B, 0x09)
        self.cmd(0x06); self.data(0x07, 0x07, 0x17)
        for a, b in ((0x60, 0xA5), (0x89, 0xA5), (0x90, 0x00), (0x93, 0x2A),
                     (0xA0, 0xA5), (0xA1, 0x00), (0x73, 0x41)):
            self.cmd(0xF8); self.data(a, b)
        self.cmd(0x16); self.data(0x00)
        self.cmd(0x04)
        if not self.wait("after POWER_ON"):
            return False
        self.cmd(0x00); self.data(0xAF)
        self.cmd(0x30); self.data(0x3A)
        self.cmd(0x82); self.data(0x12)
        for reg, lut in ((0x20, LUT_VCOM), (0x21, LUT_WW), (0x22, LUT_WW),
                         (0x23, LUT_BB), (0x24, LUT_BB)):
            self.cmd(reg)
            self.data(*lut)
        return True

    def full_pass(self, buf):
        """One refresh with the full waveform, to clear the ghosting the fast
        waveform leaves. init selects the waveform, so this re-runs init."""
        if not self.v2:
            return self.show(buf)
        was, self.fast = self.fast, False
        self.reset()
        ok = self._init_v2() and self.show(buf)
        self.fast = was
        if was:
            self.reset()
            ok = self._init_v2_fast() and ok
        return ok

    def scrub(self):
        """White, black, white with the full waveform. Slow. Clears built-up
        ghosting."""
        for fill in (0xFF, 0x00, 0xFF):
            self.full_pass(bytearray([fill]) * FB)

    def show(self, buf):
        if self.v2:
            # The address counters stay where the last write left them. Rewind
            # them with 0x4E and 0x4F before 0x24, or later frames land in the
            # wrong place.
            self.cmd(0x4E); self.data(0x00)
            self.cmd(0x4F); self.data(0x00, 0x00)
            self.cmd(0x24)
            self.block(buf)
            self.cmd(0x22); self.data(0xC7 if self.fast else 0xF7)
            self.cmd(0x20)
        else:
            self.cmd(0x13)
            self.block(buf)
            self.cmd(0x12)
        return self.wait("refresh")

    def sleep(self):
        if self.v2:
            self.cmd(0x10); self.data(0x01)
        else:
            self.cmd(0x50); self.data(0xF7)
            self.cmd(0x02)
            self.cmd(0x07); self.data(0xA5)


LUT_VCOM = [0x00, 0x00, 0x00, 0x08, 0x00, 0x00, 0x00, 0x02, 0x60, 0x28, 0x28,
            0x00, 0x00, 0x01, 0x00, 0x14, 0x00, 0x00, 0x00, 0x01, 0x00, 0x12,
            0x12, 0x00, 0x00, 0x01] + [0x00] * 18
LUT_WW = [0x40, 0x08, 0x00, 0x00, 0x00, 0x02, 0x90, 0x28, 0x28, 0x00, 0x00,
          0x01, 0x40, 0x14, 0x00, 0x00, 0x00, 0x01, 0xA0, 0x12, 0x12, 0x00,
          0x00, 0x01] + [0x00] * 18
LUT_BB = [0x80, 0x08, 0x00, 0x00, 0x00, 0x02, 0x90, 0x28, 0x28, 0x00, 0x00,
          0x01, 0x80, 0x14, 0x00, 0x00, 0x00, 0x01, 0x50, 0x12, 0x12, 0x00,
          0x00, 0x01] + [0x00] * 18


# ---------------------------------------------------------------- canvas

class Canvas:
    """1 bit per pixel, bit set means white. Bit clear is black ink."""

    def __init__(self):
        self.b = bytearray(b"\xff" * FB)

    def clear(self, white=True):
        self.b[:] = b"\xff" * FB if white else b"\x00" * FB

    def px(self, x, y, black=True):
        if 0 <= x < W and 0 <= y < H:
            i = y * ROW + (x >> 3)
            m = 0x80 >> (x & 7)
            if black:
                self.b[i] &= ~m & 0xFF
            else:
                self.b[i] |= m

    def rect(self, x, y, w, h, black=True):
        for j in range(max(0, y), min(H, y + h)):
            base = j * ROW
            for i in range(max(0, x), min(W, x + w)):
                m = 0x80 >> (i & 7)
                k = base + (i >> 3)
                if black:
                    self.b[k] &= ~m & 0xFF
                else:
                    self.b[k] |= m

    def line(self, x0, y0, x1, y1):
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        dx = abs(x1 - x0); dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.px(x0, y0)
            if x0 == x1 and y0 == y1:
                break
            e2 = err * 2
            if e2 >= dy:
                err += dy; x0 += sx
            if e2 <= dx:
                err += dx; y0 += sy

    def frame(self, t=3):
        self.rect(0, 0, W, t)
        self.rect(0, H - t, W, t)
        self.rect(0, 0, t, H)
        self.rect(W - t, 0, t, H)

    def text(self, x, y, s, scale=1):
        for ch in s.upper():
            g = FONT.get(ch, FONT[" "])
            for col in range(5):
                bits = g[col]
                for row in range(7):
                    if bits & (1 << row):
                        self.rect(x + col * scale, y + row * scale, scale, scale)
            x += 6 * scale

    def centre(self, y, s, scale=1):
        self.text((W - (len(s) * 6 * scale - scale)) // 2, y, s, scale)


FONT = {
    " ": (0, 0, 0, 0, 0), "0": (0x3E, 0x51, 0x49, 0x45, 0x3E),
    "1": (0, 0x42, 0x7F, 0x40, 0), "2": (0x42, 0x61, 0x51, 0x49, 0x46),
    "3": (0x21, 0x41, 0x45, 0x4B, 0x31), "4": (0x18, 0x14, 0x12, 0x7F, 0x10),
    "5": (0x27, 0x45, 0x45, 0x45, 0x39), "6": (0x3C, 0x4A, 0x49, 0x49, 0x30),
    "7": (0x01, 0x71, 0x09, 0x05, 0x03), "8": (0x36, 0x49, 0x49, 0x49, 0x36),
    "9": (0x06, 0x49, 0x49, 0x29, 0x1E), ".": (0, 0x60, 0x60, 0, 0),
    "-": (0x08, 0x08, 0x08, 0x08, 0x08), ":": (0, 0x36, 0x36, 0, 0),
    "A": (0x7E, 0x11, 0x11, 0x11, 0x7E), "B": (0x7F, 0x49, 0x49, 0x49, 0x36),
    "C": (0x3E, 0x41, 0x41, 0x41, 0x22), "D": (0x7F, 0x41, 0x41, 0x22, 0x1C),
    "E": (0x7F, 0x49, 0x49, 0x49, 0x41), "F": (0x7F, 0x09, 0x09, 0x09, 0x01),
    "G": (0x3E, 0x41, 0x49, 0x49, 0x7A), "H": (0x7F, 0x08, 0x08, 0x08, 0x7F),
    "I": (0, 0x41, 0x7F, 0x41, 0), "J": (0x20, 0x40, 0x41, 0x3F, 0x01),
    "K": (0x7F, 0x08, 0x14, 0x22, 0x41), "L": (0x7F, 0x40, 0x40, 0x40, 0x40),
    "M": (0x7F, 0x02, 0x0C, 0x02, 0x7F), "N": (0x7F, 0x04, 0x08, 0x10, 0x7F),
    "O": (0x3E, 0x41, 0x41, 0x41, 0x3E), "P": (0x7F, 0x09, 0x09, 0x09, 0x06),
    "Q": (0x3E, 0x41, 0x51, 0x21, 0x5E), "R": (0x7F, 0x09, 0x19, 0x29, 0x46),
    "S": (0x46, 0x49, 0x49, 0x49, 0x31), "T": (0x01, 0x01, 0x7F, 0x01, 0x01),
    "U": (0x3F, 0x40, 0x40, 0x40, 0x3F), "V": (0x1F, 0x20, 0x40, 0x20, 0x1F),
    "W": (0x3F, 0x40, 0x38, 0x40, 0x3F), "X": (0x63, 0x14, 0x08, 0x14, 0x63),
    "Y": (0x07, 0x08, 0x70, 0x08, 0x07), "Z": (0x61, 0x51, 0x49, 0x45, 0x43),
}


def dither(c, grey):
    """Floyd-Steinberg a W*H list of 0..1 floats onto the canvas."""
    g = list(grey)
    for y in range(H):
        base = y * W
        for x in range(W):
            i = base + x
            old = g[i]
            new = 1.0 if old > 0.5 else 0.0
            g[i] = new
            err = old - new
            if x + 1 < W:
                g[i + 1] += err * 0.4375
            if y + 1 < H:
                if x > 0:
                    g[i + W - 1] += err * 0.1875
                g[i + W] += err * 0.3125
                if x + 1 < W:
                    g[i + W + 1] += err * 0.0625
            if new < 0.5:
                c.px(x, y)


# ---------------------------------------------------------------- scenes

def project(pts, ax, ay, az, scale, zoff):
    ca, sa = math.cos(ax), math.sin(ax)
    cb, sb = math.cos(ay), math.sin(ay)
    cc, sc = math.cos(az), math.sin(az)
    out = []
    for (x, y, z) in pts:
        y, z = y * ca - z * sa, y * sa + z * ca
        x, z = x * cb + z * sb, -x * sb + z * cb
        x, y = x * cc - y * sc, x * sc + y * cc
        d = scale / (z + zoff)
        out.append((W / 2 + x * d, H / 2 + y * d))
    return out


CUBE_V = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
          (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
CUBE_E = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
          (0, 4), (1, 5), (2, 6), (3, 7)]


def scene_cube(c, n):
    t = n * 0.22
    c.clear(); c.frame()
    for s, zo in ((150, 4.2), (95, 5.6)):
        p = project(CUBE_V, t, t * 0.7, t * 0.33, s, zo)
        for a, b in CUBE_E:
            c.line(p[a][0], p[a][1], p[b][0], p[b][1])
    c.centre(H - 20, "cube %02d" % n)


def torus_pts():
    pts, edges = [], []
    nu, nv = 16, 8
    for i in range(nu):
        for j in range(nv):
            u = 2 * math.pi * i / nu
            v = 2 * math.pi * j / nv
            r = 1.0 + 0.42 * math.cos(v)
            pts.append((r * math.cos(u), r * math.sin(u), 0.42 * math.sin(v)))
            a = i * nv + j
            edges.append((a, i * nv + (j + 1) % nv))
            edges.append((a, ((i + 1) % nu) * nv + j))
    return pts, edges


TORUS_V, TORUS_E = torus_pts()


def scene_torus(c, n):
    t = n * 0.19
    c.clear(); c.frame()
    p = project(TORUS_V, t, t * 0.6, t * 0.25, 120, 3.4)
    for a, b in TORUS_E:
        c.line(p[a][0], p[a][1], p[b][0], p[b][1])
    c.centre(H - 20, "torus %02d" % n)


CELL = 4
LW, LH = W // CELL, H // CELL
_life = None


def scene_life(c, n):
    global _life
    if n == 0 or _life is None:
        g = [[0] * LW for _ in range(LH)]
        # Gosper glider gun, top left
        gun = [(0, 4), (0, 5), (1, 4), (1, 5), (10, 4), (10, 5), (10, 6),
               (11, 3), (11, 7), (12, 2), (12, 8), (13, 2), (13, 8), (14, 5),
               (15, 3), (15, 7), (16, 4), (16, 5), (16, 6), (17, 5),
               (20, 2), (20, 3), (20, 4), (21, 2), (21, 3), (21, 4),
               (22, 1), (22, 5), (24, 0), (24, 1), (24, 5), (24, 6),
               (34, 2), (34, 3), (35, 2), (35, 3)]
        for x, y in gun:
            if 0 <= x < LW and 0 <= y < LH:
                g[y][x] = 1
        for _ in range(int(LW * LH * 0.06)):      # a little noise lower down
            g[random.randrange(LH // 2, LH)][random.randrange(LW)] = 1
        _life = g
    else:
        g = _life
        new = [[0] * LW for _ in range(LH)]
        for y in range(LH):
            ym, yp = (y - 1) % LH, (y + 1) % LH
            gy, gm, gp = g[y], g[ym], g[yp]
            for x in range(LW):
                xm, xp = (x - 1) % LW, (x + 1) % LW
                k = (gm[xm] + gm[x] + gm[xp] + gy[xm] + gy[xp]
                     + gp[xm] + gp[x] + gp[xp])
                new[y][x] = 1 if (k == 3 or (k == 2 and gy[x])) else 0
        _life = new
        g = new
    c.clear()
    for y in range(LH):
        gy = g[y]
        for x in range(LW):
            if gy[x]:
                c.rect(x * CELL, y * CELL, CELL - 1, CELL - 1)
    c.frame(2)


def scene_mandel(c, n):
    zoom = 1.0 / (1.0 + n * 0.55)
    cx, cy = -0.743643887037151, 0.131825904205330
    grey = [0.0] * (W * H)
    for y in range(H):
        iy = cy + (y - H / 2) * 3.0 * zoom / H
        base = y * W
        for x in range(W):
            ix = cx + (x - W / 2) * 3.0 * zoom / H
            zr = zi = 0.0
            i = 0
            while zr * zr + zi * zi <= 4.0 and i < 60:
                zr, zi = zr * zr - zi * zi + ix, 2 * zr * zi + iy
                i += 1
            grey[base + x] = (i / 60.0) ** 0.6
    dither(c, grey)
    c.frame(2)


def scene_julia(c, n):
    a = n * 0.28
    jr, ji = 0.7885 * math.cos(a), 0.7885 * math.sin(a)
    grey = [0.0] * (W * H)
    for y in range(H):
        zi0 = (y - H / 2) * 2.6 / H
        base = y * W
        for x in range(W):
            zr = (x - W / 2) * 2.6 / H
            zi = zi0
            i = 0
            while zr * zr + zi * zi <= 4.0 and i < 48:
                zr, zi = zr * zr - zi * zi + jr, 2 * zr * zi + ji
                i += 1
            grey[base + x] = 1.0 - (i / 48.0) ** 0.7
    dither(c, grey)
    c.frame(2)


def scene_plasma(c, n):
    t = n * 0.6
    grey = [0.0] * (W * H)
    for y in range(H):
        base = y * W
        fy = y * 0.07
        for x in range(W):
            fx = x * 0.07
            v = (math.sin(fx + t) + math.sin(fy * 1.3 - t * 0.7)
                 + math.sin((fx + fy) * 0.6 + t * 0.4)
                 + math.sin(math.hypot(fx - 6, fy - 9) * 1.1 - t))
            grey[base + x] = (v + 4) / 8.0
    dither(c, grey)
    c.frame(2)


def scene_balls(c, n):
    t = n * 0.45
    cs = [(W / 2 + 52 * math.cos(t), H / 2 + 86 * math.sin(t * 0.9), 1500),
          (W / 2 + 60 * math.cos(t * 1.4 + 2), H / 2 + 70 * math.sin(t * 1.1 + 1), 1900),
          (W / 2 + 40 * math.cos(t * 0.7 + 4), H / 2 + 100 * math.sin(t * 1.6), 1200)]
    grey = [0.0] * (W * H)
    for y in range(H):
        base = y * W
        for x in range(W):
            s = 0.0
            for (bx, by, m) in cs:
                dx = x - bx; dy = y - by
                s += m / (dx * dx + dy * dy + 24)
            grey[base + x] = 1.0 - min(1.0, s * 0.55)
    dither(c, grey)
    c.frame(2)


def scene_tunnel(c, n):
    t = n * 0.5
    grey = [0.0] * (W * H)
    for y in range(H):
        base = y * W
        dy = y - H / 2
        for x in range(W):
            dx = x - W / 2
            d = math.hypot(dx, dy) + 0.001
            ang = math.atan2(dy, dx)
            v = math.sin(26.0 / d + t * 2) * 0.5 + math.sin(ang * 6 + t) * 0.5
            grey[base + x] = (v + 2) / 4.0
    dither(c, grey)
    c.frame(2)


# ---------------------------------------------------------------- photo
# photos/lenna.png, Floyd-Steinberg dithered to 1 bit, zlib + base64. Bit set
# is white, matching the canvas.
PHOTO_B64 = """\
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
SE9Q2u4R3TDuOngLHDG1yOYmkiryfw+DjZ8="""

_photo = None


def photo_bits():
    global _photo
    if _photo is None:
        import base64 as _b64, zlib as _z
        _photo = _z.decompress(_b64.b64decode(PHOTO_B64))
        if len(_photo) != FB:
            die("embedded photo is %d bytes, expected %d" % (len(_photo), FB))
    return _photo


def scene_photo(c, n):
    """Wipe the still in from the top, a band at a time, then hold it."""
    bits = photo_bits()
    steps = 8
    if n >= steps:
        c.b[:] = bits
        return
    rows = int(H * (n + 1) / steps)
    c.clear()
    c.b[:rows * ROW] = bits[:rows * ROW]
    if rows < H:                      # a rule on the leading edge
        c.rect(0, rows - 2, W, 2)


SCENES = [
    ("cube", scene_cube), ("torus", scene_torus), ("life", scene_life),
    ("mandel", scene_mandel), ("julia", scene_julia), ("plasma", scene_plasma),
    ("balls", scene_balls), ("tunnel", scene_tunnel),
    ("photo", scene_photo),
]


def splash(c):
    c.clear(); c.frame()
    c.centre(40, "LUCKFOX", 3)
    c.centre(76, "LYRA ZERO W", 2)
    c.rect(24, 104, W - 48, 2)
    c.centre(120, "E-PAPER", 2)
    c.centre(144, "DEMO", 2)
    c.rect(24, 176, W - 48, 2)
    c.centre(196, "BIT-BANGED SPI", 1)
    c.centre(210, "ON PLAIN GPIO", 1)
    c.centre(232, "CTRL-C TO STOP", 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--v1", action="store_true")
    ap.add_argument("--v2", action="store_true")
    ap.add_argument("--fast", action="store_true",
                    help="V2 only: 1.5s refresh instead of 3s")
    ap.add_argument("--scene", help="run only this scene")
    ap.add_argument("--frames", type=int, default=12,
                    help="frames per scene before moving on (default 12)")
    ap.add_argument("--refresh-every", type=int, default=8, metavar="N",
                    help="full-waveform pass every N frames, 0 to disable "
                         "(default 8; only matters with --fast)")
    ap.add_argument("--scrub", action="store_true",
                    help="white/black/white full cycles, then exit")
    ap.add_argument("--bitbang", action="store_true",
                    help="clock SPI from GPIO even if spidev is present")
    ap.add_argument("--spidev", metavar="PATH",
                    help="SPI device to use (default %s)" % SPIDEV)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        for name, _ in SCENES:
            print(name)
        return 0
    if os.geteuid() != 0:
        sys.stderr.write("warning: not root, GPIO export may fail\n")

    global GPIO_BASE
    GPIO_BASE = bank0_base()
    print("gpio bank 0 base = %d (RST=%d PWR=%d BUSY=%d DC=%d)"
          % (GPIO_BASE, sysfs_num(PIN["RST"]), sysfs_num(PIN["PWR"]),
             sysfs_num(PIN["BUSY"]), sysfs_num(PIN["DC"])), flush=True)

    signal.signal(signal.SIGINT, on_sig)
    signal.signal(signal.SIGTERM, on_sig)

    order = [True, False]
    if a.v1:
        order = [False]
    elif a.v2:
        order = [True]

    bus = open_bus(bitbang=a.bitbang, path=a.spidev)
    print("transport: %s" % bus.name, flush=True)

    panel = None
    for v2 in order:
        p = Panel(v2=v2, fast=a.fast, bus=bus)
        print("trying %s ..." % ("V2" if v2 else "V1"), flush=True)
        if p.init():
            panel = p
            print("panel is %s" % ("V2" if v2 else "V1"), flush=True)
            break
    if panel is None:
        sys.stderr.write("no panel responded; is the HAT seated?\n")
        return 1

    scenes = SCENES
    if a.scene:
        scenes = [s for s in SCENES if s[0] == a.scene]
        if not scenes:
            die("no such scene: %s (try --list)" % a.scene)

    if a.scrub:
        print("scrubbing ghosting ...", flush=True)
        panel.scrub()
        panel.sleep()
        bus.close()
        return 0

    c = Canvas()
    splash(c)
    panel.full_pass(c.b)
    if panel.never_busy:
        sys.stderr.write(
            "warning: the panel never raised BUSY during startup.\n"
            "  Frames will appear to complete instantly and nothing will be\n"
            "  drawn. Check the HAT is seated and BUSY is on gpio %d.\n"
            % PIN["BUSY"])

    shown = 0
    before_busy = panel.never_busy
    try:
        while not stop:
            for name, fn in scenes:
                if stop:
                    break
                print("scene: %s" % name, flush=True)
                for n in range(a.frames):
                    if stop:
                        break
                    t0 = time.time()
                    fn(c, n)
                    t1 = time.time()
                    due = (a.refresh_every and panel.fast
                           and shown % a.refresh_every == a.refresh_every - 1)
                    if due:
                        panel.full_pass(c.b)
                    else:
                        panel.show(c.b)
                    shown += 1
                    quiet = panel.never_busy - before_busy
                    before_busy = panel.never_busy
                    print("  %-7s frame %2d  draw %4.1fs  total %4.1fs%s%s"
                          % (name, n, t1 - t0, time.time() - t0,
                             "  [full]" if due else "",
                             "  [BUSY never asserted]" if quiet else ""),
                          flush=True)
    finally:
        print("\n%d frames shown, sleeping the panel" % shown)
        if panel.never_busy:
            print("BUSY never asserted on %d of them. Those frames probably "
                  "didn't reach the panel." % panel.never_busy)
        panel.sleep()
        bus.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
