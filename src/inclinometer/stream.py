"""Pi-side ADXL355 streamer.

Reads samples from the ADXL355 via the kernel IIO sysfs interface and emits
one JSON object per line to stdout. Designed to be invoked over SSH from a
host machine; the host parses the line stream as it arrives.

Output schema (one object per line, stdout, UTF-8):
    {"event": "ready", "requested_hz": <f>, "actual_hz": <f>}    # once, on start
    {"t": <unix_epoch_s>, "x_g": <f>, "y_g": <f>, "z_g": <f>, "T_c": <f>}  # per sample

Errors are written to stderr as one line:
    {"event": "error", "msg": "<text>"}

Run on the Pi:
    uv run python -m inclinometer.stream --odr 125
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time

from inclinometer.adxl355 import ADXL355


def _emit(obj: dict, stream=sys.stdout) -> None:
    stream.write(json.dumps(obj, separators=(",", ":")))
    stream.write("\n")
    stream.flush()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ADXL355 → JSON Lines streamer (IIO)")
    p.add_argument(
        "--odr",
        type=float,
        default=125.0,
        help="output data rate in Hz (default: 125). Must match one of the "
             "rates the IIO driver exposes; check in_accel_sampling_frequency_available.",
    )
    p.add_argument(
        "--iio-path",
        default="/sys/bus/iio/devices/iio:device0",
        help="IIO device sysfs path (default: /sys/bus/iio/devices/iio:device0)",
    )
    args = p.parse_args(argv)

    # Graceful shutdown: stop the loop; the `with` block then runs cleanup.
    stop = False

    def _on_signal(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        with ADXL355(iio_path=args.iio_path, sampling_hz=args.odr) as imu:
            _emit({
                "event": "ready",
                "requested_hz": args.odr,
                "actual_hz": imu.sampling_hz,
            })

            period = 1.0 / imu.sampling_hz
            next_t = time.perf_counter()
            while not stop:
                s = imu.read_sample()
                _emit({
                    "t": time.time(),
                    "x_g": s.x_g, "y_g": s.y_g, "z_g": s.z_g,
                    "T_c": s.temp_c,
                })
                next_t += period
                sleep_for = next_t - time.perf_counter()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    # Fell behind (e.g. SSH backpressure or sysfs latency). Reset
                    # so we don't try to "catch up" by hammering the bus.
                    next_t = time.perf_counter()
    except Exception as e:
        _emit({"event": "error", "msg": str(e)}, stream=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
