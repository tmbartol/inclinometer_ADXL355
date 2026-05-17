# inclinometer_ADXL355

Raspberry Pi inclinometer using the Analog Devices **ADXL355** 3-axis MEMS accelerometer over SPI.

## Hardware

- Raspberry Pi (any model with SPI0)
- ADXL355 breakout / EVAL board (PMOD or bare module)

### Wiring (SPI0, CE0)

| ADXL355 pin | Pi header | BCM |
|-------------|-----------|-----|
| VDD (3.3V)  | pin 1     | —   |
| GND         | pin 6     | —   |
| /CS         | pin 24    | CE0 (GPIO8)  |
| SCLK        | pin 23    | SCLK (GPIO11) |
| MOSI        | pin 19    | MOSI (GPIO10) |
| MISO        | pin 21    | MISO (GPIO9)  |

Enable SPI on the Pi: `sudo raspi-config` → Interface Options → SPI.

## Development

Edit on the Mac, deploy/run on the Pi. `spidev` is Linux-only and is auto-skipped on macOS.

```bash
uv sync                              # macOS: skips spidev. Pi: installs everything.
uv run python scripts/read_once.py   # one-shot sensor read (Pi only)
```

## Layout

```
src/inclinometer/
  adxl355.py     SPI driver, register map, raw + g-units reads
  tilt.py        accel → pitch / roll / inclination
  calibration.py bias/scale, 6-point routine, persistence
  logger.py      sample logger (CSV)
  web/app.py     FastAPI app + static UI
scripts/
  read_once.py   sanity-check single sample
```
