"""Read the ADXL355 via the kernel IIO sysfs interface on Linux.

On Analog Devices' Kuiper Linux for Raspberry Pi, the `dtoverlay=rpi-adxl355`
device-tree overlay binds the ADXL355 (on SPI0/CE0) to the in-kernel `adxl355`
IIO driver and exposes it at `/sys/bus/iio/devices/iio:device0`. We read
samples through sysfs files — no raw SPI from userspace.

IIO accelerometer ABI: `processed = raw * scale`, where `scale` is m/s² per LSB.
We divide by g to report values in g. Temperature ABI:
`millideg_C = (raw + offset) * scale`.

Setting `in_accel_sampling_frequency` requires write access to a root-owned
0644 file. If we can't write it (run as user `analog` with default perms), the
driver leaves the existing rate in place and emits a warning to stderr. To
allow non-root rate changes, install a udev rule — see the project README.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

GRAVITY_MS2 = 9.80665
DEFAULT_IIO_PATH = Path("/sys/bus/iio/devices/iio:device0")
EXPECTED_NAME = "adxl355"


@dataclass(frozen=True)
class Sample:
    """A single tri-axis acceleration sample in g, with die-temperature in °C."""
    x_g: float
    y_g: float
    z_g: float
    temp_c: float


class ADXL355:
    """ADXL355 reader backed by the Linux IIO sysfs interface.

    Usage:
        with ADXL355(sampling_hz=125) as imu:
            sample = imu.read_sample()
    """

    def __init__(
        self,
        iio_path: Path | str = DEFAULT_IIO_PATH,
        *,
        sampling_hz: float | None = None,
    ) -> None:
        self._dev = Path(iio_path)

        name_path = self._dev / "name"
        if not name_path.exists():
            raise FileNotFoundError(
                f"{name_path} not found. Is the IIO driver bound? On Kuiper Linux, "
                "verify `dtoverlay=rpi-adxl355` is in /boot/config.txt and reboot."
            )
        actual_name = name_path.read_text().strip()
        if actual_name != EXPECTED_NAME:
            raise RuntimeError(
                f"{self._dev} reports name={actual_name!r}, expected {EXPECTED_NAME!r}. "
                "Wrong IIO device index?"
            )

        self._x = self._dev / "in_accel_x_raw"
        self._y = self._dev / "in_accel_y_raw"
        self._z = self._dev / "in_accel_z_raw"
        self._t = self._dev / "in_temp_raw"

        # Cache scale and temperature-conversion constants; they only change if
        # the device is reconfigured to a different range.
        scale_mps2_per_lsb = float((self._dev / "in_accel_scale").read_text())
        self.accel_g_per_lsb = scale_mps2_per_lsb / GRAVITY_MS2
        self._temp_scale_milliC = float((self._dev / "in_temp_scale").read_text())
        self._temp_offset_lsb = float((self._dev / "in_temp_offset").read_text())

        if sampling_hz is not None:
            self._try_set_sampling(float(sampling_hz))

        self.sampling_hz = self._current_sampling_hz()

    # -- context manager: nothing to release (sysfs is read-on-demand) ----

    def __enter__(self) -> "ADXL355":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def close(self) -> None:
        pass

    # -- public API --------------------------------------------------------

    def read_sample(self) -> Sample:
        x_raw = int(self._x.read_text())
        y_raw = int(self._y.read_text())
        z_raw = int(self._z.read_text())
        t_raw = int(self._t.read_text())
        return Sample(
            x_g=x_raw * self.accel_g_per_lsb,
            y_g=y_raw * self.accel_g_per_lsb,
            z_g=z_raw * self.accel_g_per_lsb,
            temp_c=(t_raw + self._temp_offset_lsb) * self._temp_scale_milliC / 1000.0,
        )

    def available_sampling_hz(self) -> list[float]:
        return [
            float(x)
            for x in (self._dev / "in_accel_sampling_frequency_available").read_text().split()
        ]

    # -- internals ---------------------------------------------------------

    def _current_sampling_hz(self) -> float:
        return float((self._dev / "in_accel_sampling_frequency").read_text())

    def _try_set_sampling(self, hz: float) -> None:
        p = self._dev / "in_accel_sampling_frequency"
        try:
            p.write_text(f"{hz:f}\n")
        except PermissionError:
            print(
                f"[adxl355] WARNING: cannot set sampling_frequency to {hz} Hz "
                f"({p} is root-owned 0644). Leaving it at "
                f"{self._current_sampling_hz()} Hz. To allow non-root writes, "
                "install the udev rule from the project README.",
                file=sys.stderr,
            )
