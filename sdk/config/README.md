# SDK changes

These files hold every change this repo makes inside the Luckfox SDK. They
live outside the Docker volume, so `./sdk.sh clean` can't lose them.

| File | Does |
| --- | --- |
| `0001-spi1-epaper.patch` | routes SPI1 onto the 40-pin header pins a Pi HAT uses |
| `defconfig.fragment` | working GNU mirror, backup site, root filesystem overlay |
| `overlay/` | files copied into the root filesystem |
| `apply.sh` | applies all of the above |

Run it after `./sdk.sh init`. It skips anything already applied:

```sh
./sdk.sh apply
```
