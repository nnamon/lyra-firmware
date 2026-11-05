#!/usr/bin/env python3
"""A long-running graphical demo for a Waveshare 2.7inch e-Paper HAT on a
Luckfox Lyra Zero W.

Uses hardware SPI through /dev/spidev1.0 when it is there, and falls back to
bit-banging GPIO when it is not, so the same script runs on the stock vendor
image and on firmware built with spi1 enabled. Hardware SPI puts a frame on
the panel in about 12 ms instead of 15 s.

    python3 epaper-demo.py                 # cycle every scene, forever
    python3 epaper-demo.py --scene life    # just one
    python3 epaper-demo.py --list          # what is available
    python3 epaper-demo.py --frames 20     # frames per scene before moving on
    python3 epaper-demo.py --fast          # V2 only: 1.5s refresh, not 3s
    python3 epaper-demo.py --bitbang       # force GPIO, ignore spidev
    python3 epaper-demo.py --spidev PATH   # use another SPI bus

Note that the two transports are mutually exclusive on one boot: enabling spi1
muxes GPIO0_A6, GPIO0_B0 and GPIO0_B2 away from the GPIO controller, so on
firmware that has spidev the bit-bang path cannot claim CLK, DIN or CS.

Scenes:
    cube     rotating wireframe cube, hidden-line free, sharp 1-bit edges
    torus    rotating wireframe torus
    life     Conway's Game of Life, seeded with a glider gun
    mandel   Mandelbrot zoom, Floyd-Steinberg dithered
    julia    animated Julia set, dithered
    plasma   classic demoscene plasma, dithered
    balls    metaballs, threshold shaded
    tunnel   rotating tunnel
    photo    a dithered still, wiped in a band at a time

Ctrl-C puts the panel to sleep and exits cleanly.
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

# spi1 in the device tree is the controller on the 40-pin header: CLK on
# GPIO0_B0 (pin 23), MOSI on GPIO0_A6 (pin 19) and CSn0 on GPIO0_B2 (pin 24),
# which are the three pins the HAT wants. of_alias_get_id gives it bus 1 from
# the aliases node, so its chip select 0 appears as /dev/spidev1.0.
SPIDEV = "/dev/spidev1.0"
SPI_HZ = 4000000

# From include/uapi/linux/spi/spidev.h: _IOW('k', nr, type).
SPI_IOC_WR_MODE = 0x40016B01
SPI_IOC_WR_BITS_PER_WORD = 0x40016B03
SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04

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

    The only transport available on the stock vendor image, where nothing in
    the device tree claims the panel's CLK, DIN and CS pins. About 15 s for a
    5808-byte frame, so the bus, not the panel, sets the frame rate.
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
    """Hardware SPI through a spidev character device.

    The controller generates the clock and drives chip select, so CS is no
    longer a GPIO. A frame goes out in about 12 ms.
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
        # spidev rejects a write longer than its bufsiz module parameter,
        # 4096 bytes by default, so a frame leaves in chunks. CS rises between
        # them, which this panel accepts: each byte is latched on its own eight
        # clocks, and the bit-bang path proved both patterns work on hardware,
        # raising CS per byte for commands and holding it low for a whole frame.
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
            # A short write is possible in principle; spidev never does one,
            # but advancing by the returned count costs nothing.
            i += os.write(self.fd, mv[i:j])

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


def open_bus(bitbang=False, path=None):
    """Hardware SPI if this firmware offers it, GPIO otherwise."""
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
        """Wait for the panel to start, then to finish.

        Waiting only for idle is a race. The panel takes a moment to assert
        BUSY after a trigger command, and over hardware SPI that command
        leaves in microseconds -- fast enough to read the stale idle level and
        return before the panel has begun. Bit-banging hid this because
        clocking a byte through sysfs took milliseconds. So poll for BUSY to
        assert first, then for it to clear.
        """
        t0 = time.time()
        started = False
        while time.time() - t0 < BUSY_ASSERT_TIMEOUT:
            if self.busy_now():
                started = True
                break
            time.sleep(0.001)
        if not started:
            # Either the refresh was shorter than the poll, or nothing is
            # listening. Say so rather than report a silent success.
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
        """Waveshare's epd2in7_V2 timing: 200 ms, low 2 ms, then 200 ms settle.

        The settle after releasing RST is the part that matters -- the panel
        does its internal power-on there, and commands sent before it finishes
        are ignored. Shorter delays appeared to work only because bit-banging
        a frame took seconds.
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
        """Fast update is not just a different refresh byte. The panel has to
        load its temperature value and be told to use the 1.5s waveform, or
        0xC7 does nothing useful."""
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
        """One full-waveform refresh. The fast waveform leaves remnants of
        earlier frames; a full pass clears them. Switching waveform means
        re-running init, because the waveform is selected there."""
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
        """White, black, white at full waveform. Slow, but it resets the
        panel state when ghosting has built up."""
        for fill in (0xFF, 0x00, 0xFF):
            self.full_pass(bytearray([fill]) * FB)

    def show(self, buf):
        if self.v2:
            # The address counters sit wherever the last write left them, so
            # every frame after the first lands in the wrong place unless they
            # are rewound. The panel spec lists 0x4E and 0x4F before 0x24.
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
# A 176x264 Floyd-Steinberg dithered still, zlib + base64. Decoded once on
# first use; bit set is white, matching the canvas.
PHOTO_B64 = """\
eNptWH9sG9d9fzyTDiU3Ca2aNtCgM61JgCYjTQss+aNZ57OmtDJdN7KqIxFxEpNhGVIyFLEtCg
cJ0l08Z3ECeFUUu8UStJE9qZOPpmm4Jq8QQV7WoBCSLGvi+qiMDHlZ4mwxWfI2x9Slutx99947
KvaGHSAK/PB7n/d931/v+30A/98j0if9f9CKquNPUwPjdlSaGue4UDyfVaXJW2hDlQB02X6vUq
H/9UShZnJcXsMk9g+QUwsAihwEmMw3m5qxQeAmmHgFOWpyIQMwhcwD2EyaWa8EYSPNg2wBlGMG
T1BLzSdMTjbKhpHQm5hd5y2Cr+eDEKlCHCP5rjyn6mVNlWFVM2OgmxaoEzcXv4PFeMsEXcsE1d
B4HCzTGPLuDFNeQ8ayU7oOuRz8tpsdzNjLgTqdhtgEVlE66ulAD0MDZF2DTLqVBENNVM2lg+xI
9zrehCzrzU15LhnnLWPtl6gTnc7LxIR8Ll1fh4qhWl2n3ILoXAeVmmOzYI7pwF/1Cz4P6v0WNp
JpQjZdTwNfMYad/cjhRdgpOU3Ttc3CplIEOMggxHZx94FFTLSKZfGipUU3i9CgO1iOU32x7IZe
sASEH+b5PQST5Uy4rmLrf0JA5B70N4E6t2ZttDT9MkUd3S/y+SbWYVVO5+TZ6gcURZ5B4lvMW2
tVGyq8g1g3Oo7YF0MJQpEJy4nczAxdDPnczv1YL03TWuUK/mRRm+IFLUf0lZ/WZ2VTaKPHejDG
86qi5jVjkrxOFEbPj5N4yfDTWi6+2RZ1M0yQ6KApWg7MPyWQE1sC7bvPwsKrMNNMhW+iNgVy78
YxYKhcVYbWReRALF3x2N9ixSADiVaUfwK/7kQObE3E8TgmgavIoLxDlqfcDgeOYTUJeiMzw9KN
kV309OHgADOQn9FSj2JAIErgj/AGzCZNrVoQBeTr2rkLOTD+4gEcPtX0lL6hsFQWeQhzCnRdaq
njVSwrHB/exQr414G9OnabPAPTItaAeZbBari7dz6szkG6XF1vcPjNHmx25PMJzi/jhGw9rZuz
o5ju2PwxSr7zDtCMtLoqlUQGeToHOxBLlnsJ76LUUq3fDbNd7MAZ37zHzTi6mIrMy+Gny0URO9
075CLKudEZHML1lgrTK/jbwNljrNvDsm63rmv58BRXW0LMQa+vhx3E3N2+AljrdRUyR31u9+A5
xknsLjCJqpqfM2K1FQYde2n4zKO7cKw4T8iJVj0ZhsgV1NN3aHQnOuhyssgXM/MFaIWUIQ9ieo
8OHt2FPA6344BUU0TZKp5BHUe5czt9zIDD0+3jrGQBlKS45GM7ey6zC7vQcRa546FUXQQ+M486
hk8f7Rtyv+hxPuN4oMXlK8nG2Jl9Tpf//eM9Peigz91zZDVTlObk5KDTt9K/dGbYtc/jHED9y1
yssV5LsfOdA+JqX6f7xOAi8nBcKitNy5EFNH/Kf3Vh5Dhyd/QI94pieWxd4QTWefAPuKETzMpZ
Fjk5/DRW89nnu46OimJPZy/jWRhAHBdL7Q9GsIudi/7S8kNoxY888/0Kl8msFhX/yMhyvzjf8y
yzb1RAAVEMpLhoCCHXkFiLHfKs7EI9Qr+YEsViKuY81332dKi7++DAC0sCOp3Ey4UIr3+Ei5x/
EZ3yeTzoXi4ytLCUynQd6jjDjV0aeuPg7rMMy0VSnL8rsI6Q/6oSOd3Djh50s0y/mB085xdF50
Lv0bFcZLkHg27kTXIjfrFY8aAXliNKcni+b2ABoX5R9C9zoShC+x8rT6T83SQ1WC8X4PqzSgod
61oSM8roiVMdwwjtVjKHOG4yi9ALZyrlGYwIzuPo9Bg+V5JjZQ9y9GTTiuLvRPNYVkxls5VoBA
36HNGZObH7WRz0Xf1KtByKZLPYqPN5VW9xA4s4qHZwqVgjmCrjhR2HE+aE+I8szi1vQKxKq+Un
EepC+apqiL+YxzG8O8NlSsWshByHuMM4+Vp+LIA6lZjGzzRKrLPXl9XMfCo2jVEvJ+aUqhxDHv
FcSAbdkAqCwOzggo25em0DodOjIi43Ew0YwgzZjJwsyDnE+JXDEzBTl41FotmYYmn1CkLJiAhG
Mw+aiJyuiGIlV60N1O2d5dKgBmS4jtD2VChpaekJAZ29IAJMjaStmyzWLFWQVo1NwedNiCmonB
tTy5ghkNnTgGwVdaCfRFSICK7E5wLanuG+KQfgUzTcdbcoQ571FIB1/gmX2tsyy3+Jy96263FL
8b23f2ORcUWKD6hx/mXB53RkwvDUo6+GYRC5UqGvtoz6N1Dfj9wlCTKLv1eERcRkxD0jy8KC0O
dGkTCU2L28yaJtAekeKDm7hK9v75ziIXbxWzqWdURKTlyxn0F9/du+GYTNJ0DW3kEuMYlNZPxS
6Nmx/f4wVF6NAryLULb0FYGU02/0ipvYFxHQ+EcFRkx1TuDyIvTtCnI86Dl8cH/CMikcNozICJ
eTYzfxodRcB/h3lhHLXx0WkIP1cbEMrtAz+O+ij0ly3q+dQAzb25+sY1lykt5YZJRJ705Sgc5W
LImedwDXWQeXudPvRsxil8QROdJt/PejjljIe5TFvN8x8XEA9Cy+ushki1+6cAqhxT3amNxuam
685uBmdmcO4fLy8K0e6GOWkVKdL18Wut7ZWwlsyeLVxta/Egwk/2ElpN9qrARHIP9CKZXyFw8U
SluyNYHJ1e9Sq+VHchO3eIuCIzWxY1q98EZD0mKzbfQCZqifDE+VQzOqbG4xVJAjOrejrMbeqB
pGdoshgxz55MnwnPqIdEEzt7TI4wCFzmZZeRsb++kthghyFMQfhlX+kURKNuRbsgq45gDLGqCE
2yiPHPHAyeSYeiiumibfRv8IoZzZWVmP/JsRS4htBvOnyBEL/jDOlx/idbWWb8vik0uF5+Klyt
tlHodC+8EOq2VOQnz9u6BA5v5bsjI8dzgee9tIzEQ2qTvBwrJK6O9F8fShCVCV+7UWhXFd4a21
WHnsfF0xY+89+M9bssno36WPKEcKhlRc4mh3aEUQsmBtZjK1ogdh5q/epD2mySMkSSfTmeLblc
1y832O7u5zXkAVcy3WTH5XD+K3bUN8/hfIkYg/F49z5yubyWz5mo1W8d6MtUSu+S+QsuzuFaNY
iU31Vxe03LK1mawU7Ab4Mws5wuYaaM3fSpUNnYYfRv8LIT2+FpWj5+Gp8bzdzMJn+GgehzWp3r
wGtXo0Z9sBiyIov16o5FKN6QfON1PBLVTi1+KgXZOV0A/GuDES1bh9Y0xYywyL583I/UuPu2Lh
IEVRWH/9Mrr7Gmz8dMVzafncDsDJjZChfrgutQZbYucVV8dY9mOA1zBDnP8nGS40c+d3ryxEJB
7nHOlmZPP1jQvGUrNxOHBAreBQI02dcxY+knTxZ9Jl7w9+VZNxz01cjNLa2kjv44OV0oA4NaGl
bQchHj4S11OBRGQnJyafxpsg7RtzAc5K3OPfDq0+I77HjdtmQOgAkc1+PDs6zMX6k9jAF0knJa
lnpYzy7eiKjwssEzvQfjVsXleK2f984KrQbxBak2wNZeFMVlKWH3xieHe6jFXQXyPoXrj+SiW7
fi/Xfc8UdRvZmrMMy0W9vlz4ZNEVJr4waRd7H1yTpIL+yrDgoq4oU/RVWC6r9eJPameZCvHmBu
UNwk11Q0+8NbzoSJAoS1Ed9vBLG2rlN9+rDSA7L9i2bFnXE5V3h1EQ62BlbFn4mVqeKv76z4+h
GZwyMEk72N+Hm3XQPzpyaREdwaABpAlF90A2u3Dq3n+9tiAoSUw7RRk64IMQb9SOrCyizKQM6x
bVrBMuL19F2PcPoVJSg7k6lXXx+MRDX377z4afiQVyDep4zMBfGkkmPvz15X3bsmIqDBpdjUn/
h8KJP4/cOHGsnOF0TaYMjHwpmi2+dOEXAw/FJiE8ZzO40zdK5b++8hbX95yUweeedZG26MFLUS
778/h5tr9UVXFmvEOHj/uujBeV779Z6/8xP5WRDZOi6Ps36vXEpXRNGFPo0ER1cO6/NL565Tdv
IW9cmxb1OA1f5Nl1JVpPbm8gx3SOyBorlMF7I5Yr7hPQtnyzIcnt1VDnpWjzKu4Z7ozneDx+Wr
bstlIsR3Tca4FCvGzLMp/Gah34/7hUo/m9aE9Ll4LDpMdOW5DFw4Jly6LSkx0eFjFBUpZxXgzb
6KePsYP4FWzyMgYNmwGt7uoi1g/yGasKX8i+y5IphkmCihMazNdsdMkpkPM0adFxXG/Lom7S5b
vwsBtP4COjPVl5TpABghHbNbk9hbkRi3mPiVmaF402AUMnJhdXyZM83tya2OggxEBCI8cI18ZY
JGDpbeMwM8tXG598IUqGIwabHM/O8ii6/WFIUvCztXO3ILIL2YRZTedXvgCJEq443ppJjsfbRV
0kgWS1cvl/8XatAzVv64uFiL47cFKoOGVL7q0dU1erth1u+m6thcNCt/uE686tpchzV55MrwBP
rbRt4Bwispp9vF2dp0JD9pB4DwYT9OCm4dCm2G2QxASTe8J2hY3u1XhyhQEtujiDB1KC3y/Tyx
Wr3La67eq99r2D3hiiXx+yf3yAegIsOkwiR5+NHiioQHinT9Gvyz66u/12V2G2NRv5Ov3xj1W6
Cy1CY8fJeSiasBsFc/UEle23bXYgrpLOSG7LJm00Ro8xy1TsXYzZKC/Z+j55wua1DRrbakpsfS
ecdPwFs0L81hi15/SkbclJ20O8Mki+bQ/YqGHvohUhEzLqOOygqGr7Ql06QVb3ihR1gN2VmCP7
yNc7Q7YzGsQ0FugctUpvjKIO2T52K6NLROG7Muh2BmOEhDj6URWRRbclbB2sc17KK1HUUVGpwj
X/MInrQInSbwObonXUT2TvjtsVJZGw72ROciQzlnO2bDskG9wIKQV3TFLjbDdz1EGtaGQIa9or
UwaXZOurjItEdkeZhvUdmr3YzIZCwsTB27KztJ3VquUoGdY6RfQlokqaHMVgGOYqCWDHj8m1BX
LN0NsiSGuNN4k3y3YC2JoZc8b0CrHWESrrrdGgtkDSRKJ/lJaH3TLkyEVLDswSkf0aRb3rVF+8
Ih8j5e20LVuwHYR/w7I9QicijvPG21FiWLj6cX/IUHQ/ThW8uyh2tCE6M1zflqyOST/QNV1mhf
LLz1B9+/M5HE/Wa1je3OyNBZxoG7nTsXfxIY8T/GpnFecX1cx28WPEdDfPGCk76fwbGrHOu3h/
/GZ/teW0dVCh3UCb2Euw2pa1qw4eF/Gap6x2Bdxvd2ybKja+9Uruqo0G2+1d++rTLgdoj4xVIn
pomOh77QMG3Xb1qvI6tMvBhLkF8mCkZtvHzoNbjT0pKDq06589xxB1Sf2ZbTPE+duIYaNdKklM
G3rL3pxm3xMih7qlrqbpZr598cZYltXeM6F6H7VD0r6dJe/jovI7G73Dfh23KyrwhvGZjXZWcv
ZCts5tk91tf9XbkfI3NnpPwt4C7W1Axx3h/wCBiu07"""

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
            print("BUSY never asserted on %d of them -- those frames did not "
                  "reach the panel" % panel.never_busy)
        panel.sleep()
        bus.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
