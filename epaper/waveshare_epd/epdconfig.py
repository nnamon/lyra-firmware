"""Luckfox Lyra Zero W platform glue for Waveshare's waveshare_epd drivers.

Drop-in replacement for upstream epdconfig.py, which only supports Raspberry
Pi and Jetson (RPi.GPIO, gpiozero, lgpio, Jetson.GPIO -- none of which exist
on this board). The panel drivers in this package are upstream and unmodified;
everything board-specific lives here.

Interface the drivers rely on, and nothing more:
    RST_PIN DC_PIN CS_PIN BUSY_PIN
    module_init() module_exit()
    digital_write(pin, value) digital_read(pin) delay_ms(ms)
    spi_writebyte(list_of_bytes)

Two deliberate differences from a Raspberry Pi:

  * GPIO goes through /dev/gpiochip0 line offsets, not /sys/class/gpio
    numbers. sysfs numbers are a global allocation handed out in gpiochip
    probe order, so they are not a property of a pin -- enabling an SPI
    controller can shift them all and silently move RST, DC, PWR and BUSY to
    the wrong pins. A character-device line offset is stable by construction.

  * Chip select is driven by the SPI controller, so digital_write(CS_PIN, ...)
    is a no-op. Upstream does the same on the gpiozero path, where
    GPIO_CS_PIN is commented out.

Pin numbers below are gpiochip0 line offsets, which for Rockchip means
bank 0: GPIO0_A<n> is n, GPIO0_B<n> is 8+n, GPIO0_C<n> is 16+n.

    HAT    Pi pin  Lyra            offset
    RST    11      GPIO0_A3        3
    PWR    12      GPIO0_B6        14
    BUSY   18      GPIO0_B4        12
    DC     22      GPIO0_B3        11
    CS     24      GPIO0_B2        24 -> spi1 csn0, not a GPIO
    DIN    19      GPIO0_A6        -> spi1 mosi
    CLK    23      GPIO0_B0        -> spi1 clk

Requires spi1 enabled in the device tree so /dev/spidev1.0 exists.
"""

import errno
import fcntl
import glob
import os
import struct
import time

import spidev

# Pin identifiers. The values are gpiochip0 line offsets; CS is a token only.
RST_PIN = 3
PWR_PIN = 14
BUSY_PIN = 12
DC_PIN = 11
CS_PIN = -1          # driven by the SPI controller

GPIOCHIP = "/dev/gpiochip0"
SPI_BUS, SPI_DEV = 1, 0          # /dev/spidev1.0
SPI_HZ = 4000000
SPI_MODE = 0b00

# --- /dev/gpiochip ABI ---------------------------------------------------
# Verified against kernel 6.1 include/uapi/linux/gpio.h and sized with the
# compiler: line_request is 592 bytes with config at 288, num_lines at 560 and
# fd at 588; line_values is 16. Every field is fixed-width and __aligned_u64,
# so the layout is identical on 32-bit ARM and 64-bit x86.
GPIO_V2_GET_LINE_IOCTL = 0xC250B407
GPIO_V2_LINE_GET_VALUES_IOCTL = 0xC010B40E
GPIO_V2_LINE_SET_VALUES_IOCTL = 0xC010B40F

GPIO_V2_LINE_FLAG_INPUT = 1 << 2
GPIO_V2_LINE_FLAG_OUTPUT = 1 << 3

_REQUEST_SIZE = 592
_OFF_CONSUMER = 256
_OFF_CONFIG_FLAGS = 288
_OFF_CONFIG_NUM_ATTRS = 296
_OFF_NUM_LINES = 560
_OFF_FD = 588

_OUTPUTS = (RST_PIN, DC_PIN, PWR_PIN)
_INPUTS = (BUSY_PIN,)

_chip = None
_lines = {}          # pin -> (request fd, bit index)
_fds = []
_spi = None


def _bank0_base():
    """sysfs numbering base for GPIO bank 0, or None if sysfs is absent.

    Only needed to clean up stale exports; the lines themselves are addressed
    by offset through the character device.
    """
    chips = []
    for d in sorted(glob.glob("/sys/class/gpio/gpiochip*")):
        try:
            with open(os.path.join(d, "label")) as f:
                label = f.read().strip()
            with open(os.path.join(d, "base")) as f:
                base = int(f.read().strip())
            with open(os.path.join(d, "ngpio")) as f:
                ngpio = int(f.read().strip())
        except (OSError, ValueError):
            continue
        if label == "gpio0":
            return base
        chips.append((base, ngpio))
    banks = sorted(b for b, n in chips if n == 32)
    return banks[0] if banks else None


def _release_sysfs(offsets):
    """Drop any leftover /sys/class/gpio export of the lines we want.

    An exported sysfs GPIO holds its line, so GPIO_V2_GET_LINE returns EBUSY.
    Scripts that export through sysfs and are killed with Ctrl-C leave the
    export behind, which makes the next run fail for a reason that has nothing
    to do with the panel.
    """
    base = _bank0_base()
    if base is None:
        return []
    freed = []
    for off in offsets:
        num = base + off
        if not os.path.isdir("/sys/class/gpio/gpio%d" % num):
            continue
        try:
            with open("/sys/class/gpio/unexport", "w") as f:
                f.write(str(num))
            freed.append((off, num))
        except OSError:
            pass
    return freed


def _holder(offset):
    """Who holds a line, from debugfs, for a useful error message."""
    try:
        with open("/sys/kernel/debug/gpio") as f:
            text = f.read()
    except OSError:
        return None
    for line in text.splitlines():
        if ("gpio-%d " % offset) in line or ("gpio0-%d " % offset) in line:
            return line.strip()
    return None


def _request_lines(offsets, flags, consumer):
    """GPIO_V2_GET_LINE: claim a set of lines, returning the request fd."""
    req = bytearray(_REQUEST_SIZE)
    struct.pack_into("<%dI" % len(offsets), req, 0, *offsets)
    name = consumer.encode()[:31]
    req[_OFF_CONSUMER:_OFF_CONSUMER + len(name)] = name
    struct.pack_into("<Q", req, _OFF_CONFIG_FLAGS, flags)
    struct.pack_into("<I", req, _OFF_CONFIG_NUM_ATTRS, 0)
    struct.pack_into("<I", req, _OFF_NUM_LINES, len(offsets))
    try:
        fcntl.ioctl(_chip, GPIO_V2_GET_LINE_IOCTL, req)
    except OSError as e:
        if e.errno != errno.EBUSY:
            raise
        # Name the line that is actually taken, and who has it, instead of
        # reporting EBUSY for the whole set.
        busy = []
        for off in offsets:
            one = bytearray(_REQUEST_SIZE)
            struct.pack_into("<I", one, 0, off)
            one[_OFF_CONSUMER:_OFF_CONSUMER + 5] = b"probe"
            struct.pack_into("<Q", one, _OFF_CONFIG_FLAGS, flags)
            struct.pack_into("<I", one, _OFF_NUM_LINES, 1)
            try:
                fcntl.ioctl(_chip, GPIO_V2_GET_LINE_IOCTL, one)
                os.close(struct.unpack_from("<i", one, _OFF_FD)[0])
            except OSError:
                busy.append(off)
        detail = []
        for off in busy:
            who = _holder(off)
            detail.append("  line %d%s" % (off, ": " + who if who else ""))
        raise OSError(
            errno.EBUSY,
            "gpiochip0 lines already claimed:\n%s\n"
            "  If a sysfs-based script was interrupted, its export still "
            "holds the line:\n"
            "    ls -d /sys/class/gpio/gpio[0-9]* | sed 's#.*/gpio##' | \\\n"
            "      while read n; do echo $n > /sys/class/gpio/unexport; done"
            % "\n".join(detail))
    fd = struct.unpack_from("<i", req, _OFF_FD)[0]
    if fd < 0:
        raise OSError("GPIO_V2_GET_LINE returned fd %d for %r" % (fd, offsets))
    return fd


def module_init():
    global _chip, _spi
    if _chip is not None:
        return 0
    try:
        _chip = os.open(GPIOCHIP, os.O_RDWR)
    except OSError as e:
        raise OSError("cannot open %s: %s" % (GPIOCHIP, e))

    freed = _release_sysfs(_OUTPUTS + _INPUTS)
    if freed:
        print("epdconfig: released stale sysfs exports: %s"
              % ", ".join("line %d (gpio%d)" % f for f in freed))

    # No bias flag: leave each line's existing bias as the board configured
    # it, matching how these pins already behave.
    out_fd = _request_lines(_OUTPUTS, GPIO_V2_LINE_FLAG_OUTPUT, "epd-out")
    in_fd = _request_lines(_INPUTS, GPIO_V2_LINE_FLAG_INPUT, "epd-busy")
    _fds.extend((out_fd, in_fd))
    for i, pin in enumerate(_OUTPUTS):
        _lines[pin] = (out_fd, i)
    for i, pin in enumerate(_INPUTS):
        _lines[pin] = (in_fd, i)

    _spi = spidev.SpiDev()
    _spi.open(SPI_BUS, SPI_DEV)
    _spi.max_speed_hz = SPI_HZ
    _spi.mode = SPI_MODE

    digital_write(PWR_PIN, 1)        # upstream module_init powers the panel
    delay_ms(100)
    return 0


def module_exit():
    global _chip, _spi
    try:
        digital_write(PWR_PIN, 0)
        digital_write(RST_PIN, 0)
        digital_write(DC_PIN, 0)
    except Exception:
        pass
    if _spi is not None:
        _spi.close()
        _spi = None
    for fd in _fds:
        try:
            os.close(fd)
        except OSError:
            pass
    del _fds[:]
    _lines.clear()
    if _chip is not None:
        os.close(_chip)
        _chip = None


def digital_write(pin, value):
    if pin == CS_PIN:
        return                      # the SPI controller owns chip select
    fd, bit = _lines[pin]
    mask = 1 << bit
    bits = mask if value else 0
    fcntl.ioctl(fd, GPIO_V2_LINE_SET_VALUES_IOCTL,
                struct.pack("<QQ", bits, mask))


def digital_read(pin):
    fd, bit = _lines[pin]
    mask = 1 << bit
    buf = bytearray(struct.pack("<QQ", 0, mask))
    fcntl.ioctl(fd, GPIO_V2_LINE_GET_VALUES_IOCTL, buf)
    bits = struct.unpack_from("<Q", buf, 0)[0]
    return 1 if bits & mask else 0


def delay_ms(ms):
    time.sleep(ms / 1000.0)


def spi_writebyte(data):
    _spi.writebytes(data)


def spi_writebyte2(data):
    # Upstream exposes this for drivers that send a frame in one go. The 2.7"
    # V2 driver does not use it, but other panels in the package do.
    _spi.writebytes2(data)
