# e-Paper

Drives a Waveshare 2.7inch e-Paper HAT (176x264) from a Luckfox Lyra Zero W.

The panel driver is Waveshare's own, unmodified. Only the platform glue is
ours.

| Path | Is |
| --- | --- |
| `waveshare_epd/epd2in7_V2.py` | upstream, verbatim, sha256 `171bdc77a035f749782c78d2a513b7c3c987c9157ae39e7c0123e74e933b8a44` |
| `waveshare_epd/epdconfig.py` | ours: GPIO and SPI for this board |
| `epaper-show.py` | nine animated scenes |
| `epaper-photo.py` | pictures in 4 grey levels or 1 bit, dithered on the board |
| `prep-photo.py` | fits a picture to 176x264 greyscale, on your machine |
| `photos/` | the gallery. Only the sample is committed |
| `epaper-levels.py` | the four grey levels as bands, for calibration |
| `epaper-diag.py` | staged hardware diagnosis when nothing appears |
| `epaper-demo.py` | older, self-contained, bit-bangs SPI from GPIO |
| `boot/` | init script to run the demo at boot |

Upstream is
[waveshareteam/e-Paper](https://github.com/waveshareteam/e-Paper),
`RaspberryPi_JetsonNano/python/lib/waveshare_epd/`.

## Needs

`/dev/spidev1.0`, which means a `boot.img` built with this repo's device tree
change:

```sh
cd ../sdk && ./sdk.sh apply && ./sdk.sh build
```

Flash the resulting `boot.img` to sector `0x4000`. The panel's `python3`,
`spidev` and `PIL` are already in the vendor root filesystem.

`epaper-demo.py` is the exception: it bit-bangs SPI from sysfs GPIO and runs
on the stock vendor image with no firmware change. The two are mutually
exclusive on one boot, because enabling spi1 takes the pins away from the
GPIO controller.

## Run

```sh
python3 epaper-show.py                  # cycle every scene
python3 epaper-photo.py                 # every picture in photos/, once
python3 epaper-photo.py --loop          # ... round and round
python3 epaper-photo.py --list          # what is in photos/
python3 epaper-photo.py sleeve          # one of them
python3 epaper-photo.py any.jpg         # any file, fitted to 176x264
python3 epaper-photo.py --dither floyd  # Floyd-Steinberg instead
python3 epaper-levels.py                # calibration bands
```

## The gallery

`photos/` holds the pictures. Only `lenna.png` is committed, the
`.gitignore` excludes the rest: this repository is public.

```sh
./prep-photo.py ~/pics/*.jpg          # -> photos/<name>.png, 176x264 grey
./prep-photo.py --fit cover tall.jpg  # crop to fill instead of padding
./prep-photo.py --plain scan.png      # no autocontrast
```

Optional: `epaper-photo.py` fits a full-size file itself. Preparing first
makes the copy on the board 10-20 KB instead of megabytes.

Prepared pictures are autocontrasted, so they want gamma 1.0. `GAMMA` in
`epaper-photo.py` holds the exceptions, `--gamma` overrides.

## Wiring

The HAT seats on the header directly; all twelve Raspberry Pi power and
ground pins line up.

| HAT | Pi pin | Lyra | gpiochip0 line | Driven by |
| --- | --- | --- | --- | --- |
| RST | 11 | GPIO0_A3 | 3 | GPIO |
| PWR | 12 | GPIO0_B6 | 14 | GPIO |
| BUSY | 18 | GPIO0_B4 | 12 | GPIO, input |
| DIN | 19 | GPIO0_A6 | - | spi1 MOSI |
| DC | 22 | GPIO0_B3 | 11 | GPIO |
| CLK | 23 | GPIO0_B0 | - | spi1 CLK |
| CS | 24 | GPIO0_B2 | - | spi1 CSn0 |

Addressed by gpiochip line offset, not by `/sys/class/gpio` number: those are
a global allocation handed out in probe order and move when the device tree
changes.

## Install at boot

```sh
mkdir -p ../sdk/config/overlay/usr/share/epaper \
         ../sdk/config/overlay/etc/init.d \
         ../sdk/config/overlay/etc/default
cp -r waveshare_epd photos epaper-show.py epaper-photo.py \
      ../sdk/config/overlay/usr/share/epaper/
rm -f ../sdk/config/overlay/usr/share/epaper/photos/.gitignore
cp boot/S99epaper      ../sdk/config/overlay/etc/init.d/
cp boot/epaper.default ../sdk/config/overlay/etc/default/epaper
cd ../sdk && ./sdk.sh apply && ./sdk.sh build rootfs
```

## Designing a carrier board

The HAT is not a passive breakout: it carries a boost converter and charge
pump for the panel's drive rails, a P-FET power gate, and a level shifter.
See `../docs/epaper-carrier.md`.
