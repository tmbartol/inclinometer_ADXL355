"""Async client: spawn `ssh pi 'python -m inclinometer.stream …'`, parse stdout.

Yields one event per line: a `Ready` (once), then a `Sample` per sensor read,
or an `Error` if the remote streamer crashes.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass


REMOTE_CMD_TEMPLATE = (
    "/home/analog/.local/bin/uv run --directory /home/analog/inclinometer_ADXL355 "
    "python -m inclinometer.stream --odr {odr}"
)


@dataclass
class Sample:
    t: float
    x_g: float
    y_g: float
    z_g: float
    temp_c: float


@dataclass
class Ready:
    requested_hz: float
    actual_hz: float


@dataclass
class Error:
    msg: str


Event = Sample | Ready | Error


async def open_stream(host: str, odr: float) -> AsyncIterator[Event]:
    """Spawn the remote streamer over SSH; yield parsed events forever.

    The subprocess is terminated when the generator is closed (the consumer
    breaks out of `async for` or the surrounding task is cancelled).
    """
    remote = REMOTE_CMD_TEMPLATE.format(odr=odr)
    proc = await asyncio.create_subprocess_exec(
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ServerAliveInterval=10",
        host,
        remote,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                # Remote exited / pipe closed. Surface any stderr the remote left.
                if proc.stderr is not None:
                    tail = await proc.stderr.read()
                    if tail:
                        yield Error(msg=tail.decode("utf-8", errors="replace").strip())
                break
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(f"[client] non-JSON line: {line!r}", file=sys.stderr)
                continue

            ev = obj.get("event")
            if ev == "ready":
                yield Ready(requested_hz=obj["requested_hz"], actual_hz=obj["actual_hz"])
            elif ev == "error":
                yield Error(msg=obj.get("msg", ""))
            else:
                yield Sample(
                    t=obj["t"],
                    x_g=obj["x_g"],
                    y_g=obj["y_g"],
                    z_g=obj["z_g"],
                    temp_c=obj["T_c"],
                )
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
