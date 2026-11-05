#!/usr/bin/env python3
"""Show a photo on the 2.7inch e-Paper HAT, dithered on the board.

Waveshare's driver does all the panel work. This file carries the source
image, dithers it on the board (about a second), and hands the result over.
Dithering defaults to Atkinson.

    python3 epaper-photo.py                     # 4 grey levels
    python3 epaper-photo.py --gamma 1.0         # darker (no lift)
    python3 epaper-photo.py --gamma 0.7         # brighter still
    python3 epaper-photo.py --looks 0,70,150,255
    python3 epaper-photo.py --dither floyd      # Floyd-Steinberg instead
    python3 epaper-photo.py --dither none       # no diffusion, for contrast
    python3 epaper-photo.py --bw                # 1-bit, for comparison
    python3 epaper-photo.py --clear

Two numbers that are easy to confuse, and must not be:

  CODES  what getbuffer_4Gray keeps distinct: 0x00, 0x80, 0xC0, 0xFF. These
         are register values. It reads the top two bits and remaps 0xC0->0x80
         and 0x80->0x40, so an even 0/85/170/255 ramp collapses two levels.

  LOOKS  what those four levels actually look like on glass, 0..255. Error
         diffusion must use THESE, because the error pushed into neighbouring
         pixels is a perceptual error, not a register one.

LOOKS defaults to an even ramp, which is an assumption rather than a
measurement -- run epaper-levels.py to see the real spacing. If the picture
comes out too dark, the panel's greys are darker than the default claims:
lower the middle two numbers, or just raise --gamma's effect by lowering it.
"""

import argparse
import base64
import io
import sys
import time

from PIL import Image

from waveshare_epd import epd2in7_V2

W, H = 176, 264
CODES = [0x00, 0x80, 0xC0, 0xFF]

PHOTO_GREY_B64 = """\
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"""


def source(gamma):
    img = Image.open(io.BytesIO(base64.b64decode(PHOTO_GREY_B64))).convert("L")
    if img.size != (W, H):
        sys.exit("embedded photo is %dx%d, expected %dx%d"
                 % (img.size[0], img.size[1], W, H))
    if gamma != 1.0:
        lut = [min(255, int(255.0 * (i / 255.0) ** gamma + 0.5)) for i in range(256)]
        img = img.point(lut)
    return img


# Error-diffusion kernels: (dx, dy, weight). Floyd-Steinberg spreads all of
# the error over 4 neighbours. Atkinson spreads only 6/8 of it over 6, and
# throws the remaining quarter away -- that loss is the point, it stops error
# smearing across flat areas, so highlights and shadows stay clean and the
# result reads as higher contrast with less crawling texture. It was written
# for 1-bit Macs and suits e-paper for the same reason.
KERNELS = {
    "atkinson": (((1, 0), 0.125), ((2, 0), 0.125),
                 ((-1, 1), 0.125), ((0, 1), 0.125), ((1, 1), 0.125),
                 ((0, 2), 0.125)),
    "floyd": (((1, 0), 0.4375),
              ((-1, 1), 0.1875), ((0, 1), 0.3125), ((1, 1), 0.0625)),
    "sierra": (((1, 0), 0.5), ((-1, 1), 0.25), ((0, 1), 0.25)),
    "none": (),
}


def dither(img, looks, kernel="atkinson"):
    """Error diffusion onto len(looks) levels, returning level indices."""
    try:
        kern = KERNELS[kernel]
    except KeyError:
        sys.exit("unknown dither %r, try: %s"
                 % (kernel, ", ".join(sorted(KERNELS))))
    px = [float(v) for v in img.getdata()]
    out = bytearray(W * H)
    n = len(looks)
    # Midpoints between adjacent levels, so the choice honours real spacing.
    cuts = [(looks[i] + looks[i + 1]) / 2.0 for i in range(n - 1)]
    last = n - 1
    for y in range(H):
        base = y * W
        for x in range(W):
            i = base + x
            old = px[i]
            qi = 0
            while qi < last and old >= cuts[qi]:
                qi += 1
            out[i] = qi
            err = old - looks[qi]
            if err:
                for (dx, dy), w in kern:
                    nx = x + dx
                    if nx < 0 or nx >= W:
                        continue
                    ny = y + dy
                    if ny >= H:
                        continue
                    px[ny * W + nx] += err * w
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gamma", type=float, default=0.8,
                    help="below 1 brightens, above 1 darkens (default 0.8)")
    ap.add_argument("--looks", default="0,85,170,255",
                    help="how the four levels actually look (default even)")
    ap.add_argument("--dither", default="atkinson",
                    help="atkinson (default), floyd, sierra or none")
    ap.add_argument("--bw", action="store_true", help="1-bit instead of 4 levels")
    ap.add_argument("--clear", action="store_true", help="blank the panel")
    a = ap.parse_args()

    epd = epd2in7_V2.EPD()

    if a.clear:
        if epd.init() != 0:
            sys.exit("init failed")
        epd.Clear()
        epd.sleep()
        print("cleared")
        return 0

    try:
        looks = [int(v) for v in a.looks.split(",")]
    except ValueError:
        sys.exit("--looks wants comma-separated numbers, e.g. 0,70,150,255")
    if len(looks) != 4 or sorted(looks) != looks:
        sys.exit("--looks wants four ascending numbers")

    img = source(a.gamma)
    t0 = time.time()

    if a.bw:
        idx = dither(img, [looks[0], looks[3]], a.dither)
        out = Image.new("L", (W, H))
        out.putdata([0 if v == 0 else 255 for v in idx])
        print("1-bit, %s, gamma=%.2f, dithered in %.1fs"
              % (a.dither, a.gamma, time.time() - t0), flush=True)
        if epd.init() != 0:
            sys.exit("init failed -- is /dev/spidev1.0 there?")
        epd.display(epd.getbuffer(out.convert("1")))
    else:
        idx = dither(img, looks, a.dither)
        out = Image.new("L", (W, H))
        out.putdata([CODES[v] for v in idx])
        hist = [0] * 4
        for v in idx:
            hist[v] += 1
        print("4 grey levels, %s, gamma=%.2f, looks=%s, dithered in %.1fs"
              % (a.dither, a.gamma, looks, time.time() - t0), flush=True)
        print("  level counts: %s" % hist, flush=True)
        # 4Gray needs its own init: a different waveform and LUT.
        if epd.Init_4Gray() != 0:
            sys.exit("Init_4Gray failed -- is /dev/spidev1.0 there?")
        epd.display_4Gray(epd.getbuffer_4Gray(out))

    epd.sleep()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
