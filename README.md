# inclinometer_ADXL355

Raspberry Pi inclinometer using the Analog Devices **ADXL355** 3-axis MEMS accelerometer.

## Hardware

- Raspberry Pi running **Analog Devices Kuiper Linux** (Debian 11)
- ADXL355 breakout / EVAL board

### Wiring (SPI0, CE0)

Kuiper's `dtoverlay=rpi-adxl355` binds the chip to SPI0/CE0 and exposes it
through the kernel IIO driver at `/sys/bus/iio/devices/iio:device0`. That's
why `/dev/spidev0.0` doesn't show up as a userspace device — the kernel
driver owns it.

| ADXL355 pin | Pi header | BCM |
|-------------|-----------|-----|
| VDD (3.3V)  | pin 1     | —   |
| GND         | pin 6     | —   |
| /CS         | pin 24    | CE0 (GPIO8)  |
| SCLK        | pin 23    | SCLK (GPIO11) |
| MOSI        | pin 19    | MOSI (GPIO10) |
| MISO        | pin 21    | MISO (GPIO9)  |

Verify the binding worked:

```bash
cat /sys/bus/iio/devices/iio:device0/name      # → adxl355
cat /sys/bus/iio/devices/iio:device0/in_accel_x_raw
```

## Architecture

The Pi is a **thin sensor node**. It runs only a small streamer that reads
the IIO sysfs files (`in_accel_{x,y,z}_raw`, `in_temp_raw`) and emits one
JSON object per sample to stdout. The Mac is the host: it spawns a persistent
SSH connection (`ssh analog@analog.local 'uv run python -m inclinometer.stream'`),
parses the line stream, and does tilt math, plotting, etc.

The Pi side has **zero pip dependencies** — `inclinometer.adxl355` reads
sysfs files directly. Heavier dependencies (numpy, FastAPI for the dashboard)
live in the `host` dependency group and only install on the Mac.

## Allowing non-root sampling-rate changes (optional)

`in_accel_sampling_frequency` is root-owned (mode 0644 by default), so
running the streamer as user `analog` cannot change the device's sample
rate — only read whatever's already set. To allow non-root writes, install
this udev rule on the Pi (one-time):

```bash
sudo tee /etc/udev/rules.d/99-iio-adxl355.rules <<'EOF'
SUBSYSTEM=="iio", ATTRS{name}=="adxl355", GROUP="plugdev", MODE="0664"
EOF
sudo udevadm control --reload && sudo udevadm trigger
```

User `analog` is in `plugdev`, so writes to `in_accel_sampling_frequency`
will then succeed. Without this rule the streamer still works — it just
prints a warning and uses the current rate.

To set a rate without the udev rule:

```bash
sudo sh -c 'echo 125.000000 > /sys/bus/iio/devices/iio:device0/in_accel_sampling_frequency'
```

Available rates (Hz): `4000 2000 1000 500 250 125 62.5 31.25 15.625 7.813 3.906`.

## Development

Edit on the Mac, deploy/run on the Pi.

```bash
# Mac (host): full deps including numpy + FastAPI + dev tools
uv sync --group host --group dev

# Pi (sensor node): zero runtime deps; uv only sets up the venv
uv sync

# One-shot sanity read on the Pi
uv run python scripts/read_once.py

# Pi-side streamer (emits JSON lines on stdout, one per sample)
uv run python -m inclinometer.stream --odr 125

# Mac-side: spawn ssh, parse the stream, print samples
uv run python scripts/host_stream.py
```

## Layout

```
src/inclinometer/
  adxl355.py     IIO sysfs reader → Sample(x_g, y_g, z_g, temp_c)
  stream.py     reads sensor in a loop, emits JSON lines on stdout
scripts/
  read_once.py   sanity-check single sample (Pi)
  host_stream.py spawn ssh + parse the stream (Mac)
```
