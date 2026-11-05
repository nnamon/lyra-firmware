# e-Paper

Drives a Waveshare 2.7inch e-Paper HAT (176×264) from a Luckfox Lyra Zero W.
The panel driver is Waveshare's, unmodified. Only `epdconfig.py` is ours.

For a bare panel on your own carrier board, see
[../docs/epaper-carrier.md](../docs/epaper-carrier.md).

| Path | Is |
| --- | --- |
| `waveshare_epd/epd2in7_V2.py` | upstream, verbatim, sha256 `171bdc77a035f749782c78d2a513b7c3c987c9157ae39e7c0123e74e933b8a44` |
| `waveshare_epd/epdconfig.py` | GPIO and SPI for this board |
| `epaper-show.py` | nine animated scenes |
| `epaper-photo.py` | pictures in 4 grey levels or 1 bit, dithered on the board |
| `prep-photo.py` | fits a picture to 176×264 greyscale, run on your machine |
| `photos/` | the gallery |
| `epaper-levels.py` | the four grey levels as bands, for calibration |
| `epaper-diag.py` | step-by-step hardware checks for a blank panel |
| `epaper-demo.py` | self-contained demo, runs without spi1 by bit-banging GPIO |
| `boot/` | init script that runs a demo at boot |

Upstream is
[waveshareteam/e-Paper](https://github.com/waveshareteam/e-Paper),
`RaspberryPi_JetsonNano/python/lib/waveshare_epd/`.

## Requirements

Everything except `epaper-demo.py` needs `/dev/spidev1.0`. That means a
`boot.img` built with this repo's device tree patch:

```sh
cd ../sdk && ./sdk.sh apply && ./sdk.sh build kernel && ./sdk.sh fetch
```

Write `../images/boot.img` to sector `0x4000`. The vendor root filesystem
already has `python3`, `spidev` and `PIL`.

`epaper-demo.py` has its own panel code. It uses `/dev/spidev1.0` when it
exists and bit-bangs sysfs GPIO otherwise, so it also runs on the vendor
`boot.img`.

## Running

Copy this directory to the board, then run from inside it:

```sh
python3 epaper-show.py                  # cycle every scene
python3 epaper-photo.py                 # every picture in photos/, once
python3 epaper-photo.py --loop          # repeat forever
python3 epaper-photo.py --list          # what is in photos/
python3 epaper-photo.py lenna           # one picture
python3 epaper-photo.py any.jpg         # any file, fitted to 176×264
python3 epaper-photo.py --dither floyd  # Floyd-Steinberg instead of Atkinson
python3 epaper-levels.py                # calibration bands
```

## Gallery

Git tracks only `photos/lenna.png`. The `.gitignore` keeps everything else
in `photos/` out of this public repo.

Prepare pictures with `prep-photo.py`:

```sh
./prep-photo.py ~/pics/*.jpg          # writes photos/<name>.png, 176×264 grey
./prep-photo.py --fit cover tall.jpg  # crop to fill instead of padding
./prep-photo.py --plain scan.png      # skip autocontrast
```

`epaper-photo.py` can fit a full-size file itself. Preparing first keeps each
picture on the board to 10–20 KB.

Prepared pictures are autocontrasted and look right at gamma 1.0. Add
per-picture exceptions to `GAMMA` in `epaper-photo.py`. `--gamma` overrides
both.

## Wiring

The HAT plugs straight onto the header. All twelve Raspberry Pi power and
ground pins line up.

| HAT | Pi pin | Lyra | gpiochip0 line | Driven by |
| --- | --- | --- | --- | --- |
| RST | 11 | GPIO0_A3 | 3 | GPIO |
| PWR | 12 | GPIO0_B6 | 14 | GPIO |
| BUSY | 18 | GPIO0_B4 | 12 | GPIO, input |
| DIN | 19 | GPIO0_A6 | | spi1 MOSI |
| DC | 22 | GPIO0_B3 | 11 | GPIO |
| CLK | 23 | GPIO0_B0 | | spi1 CLK |
| CS | 24 | GPIO0_B2 | | spi1 CSn0 |

`epdconfig.py` uses gpiochip line offsets. Don't use `/sys/class/gpio`
numbers: the kernel assigns them in probe order, and they change when the
device tree changes.

## Running at boot

On a running board:

1. Copy this directory to `/usr/share/epaper/`.
2. Copy `boot/S99epaper` to `/etc/init.d/` and `chmod +x` it.
3. Copy `boot/epaper.default` to `/etc/default/epaper`. Set `SCRIPT` to the
   demo you want and `ARGS` to its options.
4. Reboot.

`S99epaper` exits quietly if the script is missing, so a board without the
files still boots.

To build the files into an image, copy them into `../sdk/config/overlay/` at
the same paths and run `./sdk.sh apply && ./sdk.sh build rootfs`. An
SDK-built `rootfs.img` hasn't booted yet, so prefer the steps above.
