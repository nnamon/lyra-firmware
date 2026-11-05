# SDK changes

Everything this project changes inside the Luckfox SDK, kept outside the
Docker volume so `./sdk.sh clean` cannot lose it.

| File | Does |
| --- | --- |
| `0001-spi1-epaper.patch` | routes SPI1 onto the 40-pin header pins a Pi HAT drives |
| `defconfig.fragment` | working GNU mirror, backup site, root filesystem overlay |
| `overlay/` | files copied into the root filesystem |
| `apply.sh` | applies all of the above; idempotent |

Run after `./sdk.sh init`:

```sh
./sdk.sh apply
```
