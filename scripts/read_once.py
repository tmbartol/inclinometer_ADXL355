"""One-shot sanity check: open the ADXL355, print a single sample.

Run on the Pi:
    uv run python scripts/read_once.py
"""

from __future__ import annotations

import sys

from inclinometer.adxl355 import ADXL355, ODR, Range


def main() -> int:
    try:
        with ADXL355(bus=0, device=0, range_=Range.G2, odr=ODR.R_125) as imu:
            s = imu.read_sample()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"X = {s.x_g:+.5f} g")
    print(f"Y = {s.y_g:+.5f} g")
    print(f"Z = {s.z_g:+.5f} g")
    print(f"|a| = {(s.x_g**2 + s.y_g**2 + s.z_g**2) ** 0.5:.5f} g  (should be ~1.0 at rest)")
    print(f"T = {s.temp_c:+.1f} °C")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
