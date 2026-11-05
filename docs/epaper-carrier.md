# Driving a bare 2.7" e-Paper panel from a carrier board

Read out of two independent Waveshare schematics, not from memory:

- 2.7inch e-Paper HAT —
  `files.waveshare.com/upload/2/2a/2.7inch_e-Paper_HAT_Schematic.pdf`
- E-Paper ESP32 Driver Board V3 —
  `files.waveshare.com/wiki/E-Paper-ESP32-Driver-Board/E-Paper_ESP32_Driver_Board_V3.pdf`

Values are what the schematics show; connectivity per node was not fully
recoverable from the PDF text, so **work from the PDFs for the netlist** —
this is a parts-and-blocks summary, not a substitute.

## Panel FPC — J3, 24 pins

| Pin | Signal | | Pin | Signal |
|----|----------|--|----|----------|
| 1  | HLT_CTL  | | 13 | SCLK |
| 2  | GDR      | | 14 | SDA (MOSI) |
| 3  | RESE     | | 15 | VDDIO |
| 4  | VGL      | | 16 | VCI |
| 5  | VGH      | | 17 | VSS |
| 6  | TSCL     | | 18 | VDD |
| 7  | TSDA     | | 19 | VPP |
| 8  | BS       | | 20 | VSH |
| 9  | BUSY *   | | 21 | PREVGH |
| 10 | RST      | | 22 | VSL |
| 11 | D/C      | | 23 | PREVGL |
| 12 | CS       | | 24 | VCOM |

\* pin 9 was inferred from the HAT and then **confirmed** against the ESP32
board, which labels J1 pin 9 `BUSY` directly. Both boards agree on the whole
pinout.

Only **6** of those 24 go to the host: SCLK, SDA, CS, D/C, RST, BUSY. The rest
is power and the panel's own supply generation.

## What the HAT adds beyond wiring

**1. Boost converter + charge pump.** The controller has the switching
*controller* on-die but none of the power parts:
- `L1` inductor: **68 uH** on the 2.7" HAT, **10 uH** on the ESP32 board.
  The ESP32 board is universal and drives panels up to 7.5", so it needs more
  current and less inductance. For a 2.7"-only carrier, 68 uH is the value
  matched to this panel.
- `Q1` N-MOSFET, gate driven from the panel's **GDR** pin
- `R2` 0.47 ohm current sense into **RESE** — identical on both boards
- 3x `MBR0530` Schottky (0.5 A / 30 V) — identical on both boards
- reservoir caps on PREVGH, PREVGL, VGH, VGL, VSH, VSL, VCOM
  (4.7 uF, 1 uF / "105", 22 nF seen in the BOM text)

This is a real switching supply sitting next to the panel. Layout matters:
keep the L1/Q1/diode loop tight and the RESE sense path short, and put the
VCOM cap close to the connector.

**2. Panel power gating.** A P-FET passing the panel rail, driven by a small
transistor from a GPIO, with 100K/10K bias. Both boards use the same
designators `Q31`/`Q32` and the same topology — it is a stock Waveshare
sub-circuit. HAT: `AO3401`, switched by **VCC_EN**. ESP32: `SI2301CDS`,
switched by IO4. That GPIO is "PWR" in the driver.

**3. A level shifter — `U1` TXS0108.** 8-bit auto-direction translator
carrying CLK, MOSI, CS, D/C, RST, BUSY between host and panel.

This one is easy to omit and important. Both sides are 3.3 V, so it is not
there for voltage translation — it is there so that **when VCC_EN gates the
panel off, the signal lines go high-Z**. Drive 3.3 V logic into an unpowered
panel and you forward-bias its ESD diodes and back-power it through the pins.
If your carrier board gates panel power, it needs this isolation; if you hard-
wire the panel always-on, you can likely drop it, at the cost of standby draw.

**4. A 3.3 V LDO.** HAT: `U2`. ESP32 board: `RT9193-33`.

**5. `BS` pin** selects 3-wire vs 4-wire SPI. We use 4-wire.

**6. `TSCL` / `TSDA`** are an optional external I2C temperature sensor.
E-paper waveforms are strongly temperature dependent; the controller has an
internal sensor (command 0x18 with 0x80 selects it, which `init_Fast` uses).
Only populate the external one if you need accuracy across a wide range.

## The digital side, proven on hardware

3.3 V logic, SPI mode 0 at 4 MHz, hardware CS. On the Lyra: spi1 with
`rm_io8_spi1_clk` / `rm_io6_spi1_mosi` / `rm_io10_spi1_csn0`, plus RST, D/C,
BUSY and PWR as plain GPIOs. No MISO needed.

## Before committing a layout

The two schematics above already agree on the sense resistor, the diodes, the
power-gate topology and the full FPC pinout, which is the cross-check that
matters. Still worth a look at the 2.7" V2 panel spec
(`files.waveshare.com/upload/b/ba/2.7inch_e-Paper_V2_Specification.pdf`) for
the controller's own reference application circuit before committing copper,
because a wrong charge-pump value does not fail cleanly — it half-works, or
degrades the panel over time.
