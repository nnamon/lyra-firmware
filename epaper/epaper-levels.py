#!/usr/bin/env python3
"""Show the panel's four grey levels as bands, to measure their reflectance.

Dithering onto a palette needs the levels' real appearance, not the code
values the encoder happens to use. getbuffer_4Gray wants 0x00, 0x80, 0xC0 and
0xFF, but those are register codes -- what they look like on glass is a
property of the waveform and the ink, and the spacing is not the same.

This writes four labelled bands so you can see the actual spacing. Feed what
you see back into LOOKS in mkassets.py and the error diffusion will target the
levels the panel really produces.

    python3 epaper-levels.py            # four horizontal bands
    python3 epaper-levels.py --ramp     # bands plus a dithered 0-100% ramp
"""

import argparse
import sys

from PIL import Image, ImageDraw

from waveshare_epd import epd2in7_V2

W, H = 176, 264
CODES = [0x00, 0x80, 0xC0, 0xFF]        # what getbuffer_4Gray keeps distinct
NAMES = ["00 black", "80 dark", "C0 light", "FF white"]


def bands():
    img = Image.new("L", (W, H), 0xFF)
    d = ImageDraw.Draw(img)
    bh = H // len(CODES)
    for i, code in enumerate(CODES):
        d.rectangle([0, i * bh, W, (i + 1) * bh - 1], fill=code)
        # label in the opposite extreme so it stays readable
        d.text((6, i * bh + 6), NAMES[i], fill=0xFF if code < 0x90 else 0x00)
    return img


def ramp():
    """Bands on top, a dithered sweep below, for judging tonal response."""
    img = Image.new("L", (W, H), 0xFF)
    d = ImageDraw.Draw(img)
    top = H // 2
    bw = top // len(CODES)
    for i, code in enumerate(CODES):
        d.rectangle([0, i * bw, W, (i + 1) * bw - 1], fill=code)
        d.text((6, i * bw + 4), NAMES[i], fill=0xFF if code < 0x90 else 0x00)

    # Floyd-Steinberg a linear sweep onto the four codes, treating the codes
    # as if evenly spaced. If the panel's levels are not evenly spaced, the
    # sweep will visibly bunch up -- which is the thing worth seeing.
    sw, sh = W, H - top
    px = [255.0 * x / (sw - 1) for y in range(sh) for x in range(sw)]
    out = [0] * (sw * sh)
    for y in range(sh):
        for x in range(sw):
            i = y * sw + x
            old = px[i]
            qi = min(range(4), key=lambda k: abs(old - k * 85))
            out[i] = CODES[qi]
            err = old - qi * 85
            for dx, dy, f in ((1, 0, 7/16), (-1, 1, 3/16), (0, 1, 5/16), (1, 1, 1/16)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < sw and 0 <= ny < sh:
                    px[ny * sw + nx] += err * f
    sweep = Image.new("L", (sw, sh))
    sweep.putdata(out)
    img.paste(sweep, (0, top))
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ramp", action="store_true",
                    help="also show a dithered linear sweep")
    a = ap.parse_args()

    img = ramp() if a.ramp else bands()
    epd = epd2in7_V2.EPD()
    if epd.Init_4Gray() != 0:
        sys.exit("Init_4Gray failed -- is /dev/spidev1.0 there?")
    epd.display_4Gray(epd.getbuffer_4Gray(img))
    epd.sleep()
    print("shown. Judge the spacing of the four bands:")
    print("  evenly spaced      -> LOOKS = [0, 85, 170, 255]")
    print("  greys near white   -> raise the two middle numbers")
    print("  greys near black   -> lower them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
