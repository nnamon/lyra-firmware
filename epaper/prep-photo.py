#!/usr/bin/env python3
"""Fit pictures to the panel: 176x264 8-bit greyscale PNGs in photos/.

    ./prep-photo.py ~/pics/sleeve.jpg               # -> photos/sleeve.png
    ./prep-photo.py --name cover ~/pics/sleeve.jpg
    ./prep-photo.py --fit cover ~/pics/*.jpg        # crop to fill, not pad
    ./prep-photo.py --plain ~/pics/scan.png         # no autocontrast

epaper-photo.py fits a full-size file itself, so this is optional. Doing it
here makes what the board stores 10-20 KB instead of megabytes.

Autocontrast unless --plain: four grey levels cannot spare any of the range.
Baked in here, so --gamma stays free as a display-time tweak.

photos/ is gitignored bar the sample. This repository is public.
"""

import argparse
import os
import sys

from PIL import Image, ImageOps

W, H = 176, 264
HERE = os.path.dirname(os.path.abspath(__file__))
PHOTOS = os.path.join(HERE, "photos")


def prepare(path, how, autocontrast):
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "P"):
        # Composite onto white first: convert("L") on RGBA drops alpha and
        # leaves transparent areas as whatever colour hid under them.
        img = img.convert("RGBA")
        img = Image.alpha_composite(
            Image.new("RGBA", img.size, (255, 255, 255, 255)), img)
    img = img.convert("L")
    if autocontrast:
        img = ImageOps.autocontrast(img, cutoff=1)
    if how == "cover":
        return ImageOps.fit(img, (W, H), Image.LANCZOS)
    img = ImageOps.contain(img, (W, H), Image.LANCZOS)
    canvas = Image.new("L", (W, H), 255)
    canvas.paste(img, ((W - img.size[0]) // 2, (H - img.size[1]) // 2))
    return canvas


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", nargs="+", help="image files to prepare")
    ap.add_argument("--name", help="output name, only with a single source")
    ap.add_argument("--fit", default="contain", choices=("contain", "cover"),
                    help="contain pads with white (default), cover crops to fill")
    ap.add_argument("--plain", action="store_true", help="skip autocontrast")
    ap.add_argument("--out", default=PHOTOS, help="output directory")
    a = ap.parse_args()

    if a.name and len(a.source) > 1:
        sys.exit("--name takes one source")
    os.makedirs(a.out, exist_ok=True)

    for src in a.source:
        name = a.name or os.path.splitext(os.path.basename(src))[0]
        img = prepare(src, a.fit, not a.plain)
        dest = os.path.join(a.out, name + ".png")
        img.save(dest, optimize=True)
        print("%-14s %s %6d bytes" % (name, a.fit, os.path.getsize(dest)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
