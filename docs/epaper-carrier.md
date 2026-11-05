# Driving a bare 2.7inch e-Paper panel from a carrier board

A bare panel needs a boost supply, a power gate and an LDO on the carrier.
These notes come from two Waveshare schematics:

- 2.7inch e-Paper HAT:
  `files.waveshare.com/upload/2/2a/2.7inch_e-Paper_HAT_Schematic.pdf`
- E-Paper ESP32 Driver Board V3:
  `files.waveshare.com/wiki/E-Paper-ESP32-Driver-Board/E-Paper_ESP32_Driver_Board_V3.pdf`

The part values below are complete. The netlist isn't: the PDF text doesn't
give full node connectivity, so lay out from the PDFs themselves.

## Panel FPC pinout

J3, 24 pins. Both schematics agree on every pin.

| Pin | Signal | | Pin | Signal |
| --- | --- | --- | --- | --- |
| 1 | HLT_CTL | | 13 | SCLK |
| 2 | GDR | | 14 | SDA (MOSI) |
| 3 | RESE | | 15 | VDDIO |
| 4 | VGL | | 16 | VCI |
| 5 | VGH | | 17 | VSS |
| 6 | TSCL | | 18 | VDD |
| 7 | TSDA | | 19 | VPP |
| 8 | BS | | 20 | VSH |
| 9 | BUSY | | 21 | PREVGH |
| 10 | RST | | 22 | VSL |
| 11 | D/C | | 23 | PREVGL |
| 12 | CS | | 24 | VCOM |

Six pins go to the host: SCLK, SDA, CS, D/C, RST and BUSY. The rest are power
and the panel's supply rails.

## Carrier circuits

### Boost converter and charge pump

The panel controller has the boost controller on-die. The carrier supplies
the power parts:

| Part | 2.7inch HAT | ESP32 board |
| --- | --- | --- |
| `L1` inductor | 68 µH | 10 µH |
| `Q1` N-MOSFET, gate on `GDR` | yes | yes |
| `R2` current sense into `RESE` | 0.47 Ω | 0.47 Ω |
| Schottky diodes | 3× `MBR0530` (0.5 A, 30 V) | 3× `MBR0530` |

Use 68 µH for a 2.7inch-only carrier. The ESP32 board drives panels up to
7.5 inch and uses 10 µH for the higher current.

Put reservoir capacitors on PREVGH, PREVGL, VGH, VGL, VSH, VSL and VCOM. The
BOM text shows 4.7 µF, 1 µF and 22 nF. Keep the L1, Q1 and diode loop tight,
keep the RESE sense trace short, and put the VCOM capacitor next to the
connector.

### Panel power gate

A P-FET passes the panel rail. A small transistor drives its gate from a GPIO,
with 100 kΩ and 10 kΩ bias. Both boards use designators `Q31` and `Q32`.

| | P-FET | Enable |
| --- | --- | --- |
| 2.7inch HAT | `AO3401` | `VCC_EN` |
| ESP32 board | `SI2301CDS` | IO4 |

The driver calls this GPIO `PWR`.

### Level shifter

The HAT has a `TXS0108` (`U1`) on CLK, MOSI, CS, D/C, RST and BUSY. The ESP32
board has none. Both sides run at 3.3 V, so it doesn't translate voltage.

Fit one if you gate panel power. Driving 3.3 V logic into an unpowered panel
forward-biases its ESD diodes and back-powers it through the signal pins. If
the panel is always powered, you can leave it out.

### 3.3 V LDO

`U2` on the HAT, `RT9193-33` on the ESP32 board.

### Other pins

- `BS` selects 3-wire or 4-wire SPI. Tie it for 4-wire.
- `TSCL` and `TSDA` take an optional external I2C temperature sensor. The
  controller has an internal one (command `0x18` with `0x80`, which
  `init_Fast` uses). Fit the external sensor only if you need accuracy over a
  wide temperature range.

## Host interface

3.3 V logic, SPI mode 0 at 4 MHz, hardware CS, no MISO. On the Lyra this is
spi1 with `rm_io8_spi1_clk`, `rm_io6_spi1_mosi` and `rm_io10_spi1_csn0`, plus
RST, D/C, BUSY and PWR as GPIOs. This setup drives the HAT on real hardware.

## Before you commit copper

Check the charge-pump values against the reference circuit in the 2.7inch V2
panel spec:
`files.waveshare.com/upload/b/ba/2.7inch_e-Paper_V2_Specification.pdf`. A
wrong value often doesn't fail outright. The panel half-works or degrades over
time.
