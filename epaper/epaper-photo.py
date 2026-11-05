#!/usr/bin/env python3
"""Show pictures on the 2.7inch e-Paper HAT, dithered on the board.

Waveshare's driver does the panel work. This reads a picture, dithers it on
the board (about a second each) and hands it over. Atkinson by default.

The gallery is photos/. Only the sample is committed; prep-photo.py fills
the rest.

    python3 epaper-photo.py                     # the whole gallery, once
    python3 epaper-photo.py --loop              # ... round and round
    python3 epaper-photo.py --list              # what is in photos/
    python3 epaper-photo.py lenna               # one of them
    python3 epaper-photo.py lenna sleeve        # those two in turn
    python3 epaper-photo.py pic.jpg             # any file, fitted to 176x264
    python3 epaper-photo.py --gamma 1.3         # darker
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
import os
import sys
import time

from PIL import Image

from waveshare_epd import epd2in7_V2

W, H = 176, 264
CODES = [0x00, 0x80, 0xC0, 0xFF]

HERE = os.path.dirname(os.path.abspath(__file__))
PHOTOS = os.path.join(HERE, "photos")
EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")

# prep-photo.py autocontrasts, so prepared pictures want 1.0. Exceptions go
# here; --gamma overrides.
GAMMA = {"photo": 0.8, "loveless": 1.3}


def gallery():
    """What is in photos/, in a stable order."""
    if not os.path.isdir(PHOTOS):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(PHOTOS)
                  if f.lower().endswith(EXTS))


def resolve(name):
    """A gallery name or a file path, to something PIL can open."""
    if os.path.isfile(name):
        return name
    for ext in EXTS:
        path = os.path.join(PHOTOS, name + ext)
        if os.path.isfile(path):
            return path
    sys.exit("no image %r; --list shows what there is" % name)


def refit(img, how):
    """Scale to 176x264: contain pads with white, cover crops to fill."""
    sw, sh = img.size
    scale = max(W / sw, H / sh) if how == "cover" else min(W / sw, H / sh)
    img = img.resize((max(1, int(sw * scale + 0.5)), max(1, int(sh * scale + 0.5))),
                     Image.LANCZOS)
    sw, sh = img.size
    if how == "cover":
        x, y = (sw - W) // 2, (sh - H) // 2
        return img.crop((x, y, x + W, y + H))
    canvas = Image.new("L", (W, H), 255)
    canvas.paste(img, ((W - sw) // 2, (H - sh) // 2))
    return canvas


def source(name, gamma, how):
    img = Image.open(resolve(name))
    if img.mode in ("RGBA", "LA", "P"):
        # Composite onto white first: convert("L") on RGBA drops alpha and
        # leaves transparent areas as whatever colour hid under them.
        img = img.convert("RGBA")
        img = Image.alpha_composite(
            Image.new("RGBA", img.size, (255, 255, 255, 255)), img)
    img = img.convert("L")
    if img.size != (W, H):
        img = refit(img, how)
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


def show(epd, name, a, looks):
    gamma = a.gamma if a.gamma is not None else GAMMA.get(name, 1.0)
    img = source(name, gamma, a.fit)
    t0 = time.time()

    if a.bw:
        idx = dither(img, [looks[0], looks[3]], a.dither)
        out = Image.new("L", (W, H))
        out.putdata([0 if v == 0 else 255 for v in idx])
        print("%s: 1-bit, %s, gamma=%.2f, dithered in %.1fs"
              % (name, a.dither, gamma, time.time() - t0), flush=True)
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
        print("%s: 4 grey levels, %s, gamma=%.2f, looks=%s, dithered in %.1fs"
              % (name, a.dither, gamma, looks, time.time() - t0), flush=True)
        print("  level counts: %s" % hist, flush=True)
        # 4Gray needs its own init: a different waveform and LUT.
        if epd.Init_4Gray() != 0:
            sys.exit("Init_4Gray failed -- is /dev/spidev1.0 there?")
        epd.display_4Gray(epd.getbuffer_4Gray(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("image", nargs="*",
                    help="gallery name or file path (default: all of photos/)")
    ap.add_argument("--list", action="store_true", help="print the gallery and exit")
    ap.add_argument("--loop", action="store_true", help="keep going round")
    ap.add_argument("--pause", type=float, default=6.0,
                    help="seconds a picture stays up when there is more than one")
    ap.add_argument("--fit", default="contain", choices=("contain", "cover"),
                    help="fit a source that is not already 176x264 (default contain)")
    ap.add_argument("--gamma", type=float, default=None,
                    help="below 1 brightens, above 1 darkens (default: per image)")
    ap.add_argument("--looks", default="0,85,170,255",
                    help="how the four levels actually look (default even)")
    ap.add_argument("--dither", default="atkinson",
                    help="atkinson (default), floyd, sierra or none")
    ap.add_argument("--bw", action="store_true", help="1-bit instead of 4 levels")
    ap.add_argument("--clear", action="store_true", help="blank the panel")
    a = ap.parse_args()

    if a.list:
        for name in gallery():
            print(name)
        return 0

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

    names = list(a.image) or gallery()
    if not names:
        sys.exit("photos/ is empty; ./prep-photo.py <image> fills it")

    try:
        while True:
            for i, name in enumerate(names):
                show(epd, name, a, looks)
                if a.loop or i < len(names) - 1:
                    # The panel must not sit biased. show() re-inits.
                    epd.sleep()
                    time.sleep(a.pause)
            if not a.loop:
                break
    except KeyboardInterrupt:
        print("")

    epd.sleep()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
