"""Driver for the Analog Devices ADXL355 3-axis accelerometer over SPI.

Datasheet: ADXL355 Rev. B (Analog Devices). All register numbers, scale factors,
and the temperature-conversion constants below come from there.

SPI framing: first byte is (addr << 1) | R/W, with R/W = 1 for read.
Multi-byte transfers auto-increment the register address.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

try:
    import spidev  # Linux-only
except ImportError:
    spidev = None  # type: ignore[assignment]


# --- Register map ---------------------------------------------------------

DEVID_AD = 0x00     # expect 0xAD
DEVID_MST = 0x01    # expect 0x1D
PARTID = 0x02       # expect 0xED
REVID = 0x03
STATUS = 0x04
FIFO_ENTRIES = 0x05
TEMP2 = 0x06        # 4 MSB of 12-bit temperature
TEMP1 = 0x07        # 8 LSB of 12-bit temperature
XDATA3 = 0x08       # start of 9 consecutive bytes of XYZ data (20-bit signed each)
FILTER = 0x28
RANGE = 0x2C
POWER_CTL = 0x2D
RESET = 0x2F


class Range(IntEnum):
    """Accelerometer full-scale range. Values are the RANGE register bits [1:0]."""
    G2 = 0b01   # ±2.048 g, 256000 LSB/g
    G4 = 0b10   # ±4.096 g, 128000 LSB/g
    G8 = 0b11   # ±8.192 g,  64000 LSB/g


# LSB-per-g for each range (datasheet).
_LSB_PER_G = {Range.G2: 256_000.0, Range.G4: 128_000.0, Range.G8: 64_000.0}


class ODR(IntEnum):
    """Output data rate / low-pass corner. Written to FILTER[3:0]. LPF = ODR/4."""
    R_4000 = 0x0
    R_2000 = 0x1
    R_1000 = 0x2
    R_500 = 0x3
    R_250 = 0x4
    R_125 = 0x5
    R_62_5 = 0x6
    R_31_25 = 0x7
    R_15_625 = 0x8
    R_7_8125 = 0x9
    R_3_90625 = 0xA


@dataclass(frozen=True)
class Sample:
    """A single tri-axis acceleration sample in g, with die-temperature in °C."""
    x_g: float
    y_g: float
    z_g: float
    temp_c: float


class ADXL355:
    """ADXL355 over Linux SPI via spidev.

    Typical use:
        with ADXL355(bus=0, device=0) as imu:
            print(imu.read_sample())
    """

    # ADXL355 spec: SPI mode 0, MSB first, up to 10 MHz.
    SPI_MODE = 0b00
    DEFAULT_HZ = 5_000_000

    def __init__(
        self,
        bus: int = 0,
        device: int = 0,
        *,
        spi_hz: int = DEFAULT_HZ,
        range_: Range = Range.G2,
        odr: ODR = ODR.R_125,
    ) -> None:
        if spidev is None:
            raise RuntimeError(
                "spidev is not installed. The ADXL355 driver only runs on Linux "
                "(e.g. Raspberry Pi). On macOS, develop here but run on the Pi."
            )
        self._spi = spidev.SpiDev()
        self._spi.open(bus, device)
        self._spi.max_speed_hz = spi_hz
        self._spi.mode = self.SPI_MODE
        self._range = range_
        self._lsb_per_g = _LSB_PER_G[range_]
        self._configure(range_, odr)

    # --- context manager -------------------------------------------------

    def __enter__(self) -> "ADXL355":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._spi.close()

    # --- low-level register I/O ------------------------------------------

    def _read(self, reg: int, n: int = 1) -> list[int]:
        # First byte: (addr << 1) | R/W bit (1 = read).
        tx = [(reg << 1) | 0x01] + [0x00] * n
        rx = self._spi.xfer2(tx)
        return rx[1:]

    def _write(self, reg: int, value: int) -> None:
        self._spi.xfer2([(reg << 1) & 0xFE, value & 0xFF])

    # --- configuration ---------------------------------------------------

    def _configure(self, range_: Range, odr: ODR) -> None:
        # Verify part ID before touching anything else — catches wiring errors early.
        dev_ad, dev_mst, part = self._read(DEVID_AD, 3)
        if (dev_ad, dev_mst, part) != (0xAD, 0x1D, 0xED):
            raise RuntimeError(
                f"ADXL355 not detected: got DEVID_AD={dev_ad:#04x} "
                f"DEVID_MST={dev_mst:#04x} PARTID={part:#04x} "
                "(expected 0xAD, 0x1D, 0xED). Check wiring, SPI mode, and CS line."
            )

        # Enter standby to change config (datasheet requires STANDBY=1 for register writes
        # to RANGE/FILTER to take effect cleanly).
        self._write(POWER_CTL, 0x01)
        self._write(RANGE, int(range_) & 0x03)
        self._write(FILTER, int(odr) & 0x0F)
        # Leave standby → measurement mode.
        self._write(POWER_CTL, 0x00)

    # --- sample read -----------------------------------------------------

    def read_sample(self) -> Sample:
        """Read one acceleration sample plus die temperature."""
        # Burst-read 9 bytes starting at XDATA3: X3 X2 X1 Y3 Y2 Y1 Z3 Z2 Z1.
        raw = self._read(XDATA3, 9)
        x = self._g_from_bytes(raw[0], raw[1], raw[2])
        y = self._g_from_bytes(raw[3], raw[4], raw[5])
        z = self._g_from_bytes(raw[6], raw[7], raw[8])
        return Sample(x_g=x, y_g=y, z_g=z, temp_c=self.read_temp_c())

    def read_temp_c(self) -> float:
        """Read on-die temperature in degrees Celsius.

        Datasheet: 12-bit; intercept 1852 LSB at 25 °C, slope -9.05 LSB/°C.
        """
        hi, lo = self._read(TEMP2, 2)
        raw = ((hi & 0x0F) << 8) | lo
        return (raw - 1852) / -9.05 + 25.0

    def _g_from_bytes(self, b2: int, b1: int, b0: int) -> float:
        # 20-bit signed, packed MSB-first into 3 bytes; lower nibble of b0 unused.
        raw = (b2 << 12) | (b1 << 4) | (b0 >> 4)
        if raw & 0x80000:
            raw -= 0x100000  # sign-extend 20-bit two's complement
        return raw / self._lsb_per_g
