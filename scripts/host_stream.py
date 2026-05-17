"""Mac-side pipe-test client.

Spawns an SSH connection to the Pi, runs `python -m inclinometer.stream`,
parses one JSON object per stdout line, and prints a one-line summary per
sample. The point is to prove the SSH → stdout → JSON path works end-to-end
before any UI is built on top.

Run on the Mac:
    uv run python scripts/host_stream.py
    uv run python scripts/host_stream.py --host analog@analog.local --odr 62.5

Requires passwordless SSH to the Pi (set up SSH key auth first).
"""

from __future__ import annotations

import argparse
import json
import shlex
import signal
import subprocess
import sys
import time

from inclinometer.host.client import (
    DEFAULT_REMOTE_DIR,
    DEFAULT_REMOTE_HOST,
    DEFAULT_REMOTE_UV,
    build_remote_cmd,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Read ADXL355 stream from a remote Pi over SSH")
    p.add_argument("--host", default=DEFAULT_REMOTE_HOST,
                   help=f"ssh target (default: {DEFAULT_REMOTE_HOST}; "
                        "env: INCLINOMETER_REMOTE_HOST)")
    p.add_argument("--remote-uv", default=DEFAULT_REMOTE_UV,
                   help=f"full path to uv on the Pi (default: {DEFAULT_REMOTE_UV}; "
                        "env: INCLINOMETER_REMOTE_UV)")
    p.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR,
                   help=f"project directory on the Pi (default: {DEFAULT_REMOTE_DIR}; "
                        "env: INCLINOMETER_REMOTE_DIR)")
    p.add_argument("--odr", type=float, default=125.0, help="ODR in Hz to request from the Pi")
    p.add_argument("--max-samples", type=int, default=0,
                   help="stop after N samples (0 = run until interrupted)")
    args = p.parse_args(argv)

    remote = build_remote_cmd(args.remote_uv, args.remote_dir, args.odr)
    ssh_argv = [
        "ssh",
        "-o", "BatchMode=yes",            # fail if password would be required
        "-o", "ServerAliveInterval=10",
        args.host,
        remote,
    ]

    print(f"[host] $ {' '.join(shlex.quote(a) for a in ssh_argv)}", file=sys.stderr)

    proc = subprocess.Popen(
        ssh_argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,                        # line-buffered
    )

    # Ctrl-C: terminate the ssh subprocess; broken pipe on the remote side
    # then triggers the streamer's signal handler and it shuts down cleanly.
    def _on_sigint(_signum, _frame):
        if proc.poll() is None:
            proc.terminate()
    signal.signal(signal.SIGINT, _on_sigint)

    n = 0
    t0 = time.time()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(f"[host] non-JSON line: {line!r}", file=sys.stderr)
                continue

            if obj.get("event") == "ready":
                print(
                    f"[host] sensor ready: requested={obj['requested_hz']} Hz, "
                    f"actual={obj['actual_hz']} Hz",
                    file=sys.stderr,
                )
                continue
            if obj.get("event") == "error":
                print(f"[host] remote error: {obj.get('msg')}", file=sys.stderr)
                continue

            n += 1
            mag = (obj["x_g"] ** 2 + obj["y_g"] ** 2 + obj["z_g"] ** 2) ** 0.5
            print(
                f"#{n:6d}  x={obj['x_g']:+.4f}  y={obj['y_g']:+.4f}  "
                f"z={obj['z_g']:+.4f}  |a|={mag:.4f}g  T={obj['T_c']:+.1f}C"
            )
            if args.max_samples and n >= args.max_samples:
                break
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        rc = proc.returncode
        elapsed = time.time() - t0
        rate = n / elapsed if elapsed > 0 else 0.0
        print(f"[host] received {n} samples in {elapsed:.1f}s ({rate:.1f}/s); ssh exit {rc}",
              file=sys.stderr)
        # Drain any stderr from ssh / the remote process.
        if proc.stderr is not None:
            tail = proc.stderr.read()
            if tail:
                print(f"[host] remote stderr:\n{tail}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
