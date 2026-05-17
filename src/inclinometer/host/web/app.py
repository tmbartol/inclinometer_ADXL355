"""FastAPI dashboard: spawns the SSH stream once, multiplexes samples over WebSocket.

Architecture:
    SSH subprocess (Pi-side `inclinometer.stream`)
        → stdout JSON lines
        → async client (inclinometer.host.client.open_stream)
        → server adds pitch/roll/inclination
        → broadcasts to all connected WebSocket clients
        → browser maintains its own ring buffer for plots + histograms

Browsers do their own buffering / binning so the server is essentially a
single async pipe with a fan-out.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from inclinometer.host.client import (
    DEFAULT_REMOTE_DIR,
    DEFAULT_REMOTE_HOST,
    DEFAULT_REMOTE_UV,
    Error,
    Ready,
    Sample,
    open_stream,
)
from inclinometer.host.tilt import tilt_from_accel

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("inclinometer.web")


class Broadcaster:
    """Tracks connected WebSockets; broadcasts each message to all of them."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        text = json.dumps(payload, separators=(",", ":"))
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


def make_app(
    pi_host: str,
    odr: float,
    *,
    remote_uv: str = DEFAULT_REMOTE_UV,
    remote_dir: str = DEFAULT_REMOTE_DIR,
) -> FastAPI:
    broadcaster = Broadcaster()
    state: dict = {"ready": None, "samples_seen": 0, "last_error": None}
    stream_task: asyncio.Task | None = None

    async def stream_loop() -> None:
        # Reconnect-on-loss: when the SSH stream ends (Pi reboot, network
        # blip, sensor crash), wait and retry. Backoff escalates only when
        # we fail BEFORE receiving a Ready event — once we know the link
        # is good, drop back to the initial delay for transient drops.
        BACKOFF_INITIAL_S = 2.0
        BACKOFF_MAX_S = 30.0
        backoff = BACKOFF_INITIAL_S

        while True:
            log.info("opening SSH stream: host=%s odr=%s", pi_host, odr)
            await broadcaster.broadcast({"event": "connecting", "host": pi_host})
            state["ready"] = None
            connected = False

            try:
                async for ev in open_stream(
                    host=pi_host, odr=odr,
                    remote_uv=remote_uv, remote_dir=remote_dir,
                ):
                    if isinstance(ev, Ready):
                        connected = True
                        backoff = BACKOFF_INITIAL_S
                        state["ready"] = {
                            "requested_hz": ev.requested_hz,
                            "actual_hz": ev.actual_hz,
                        }
                        log.info("sensor ready: actual_hz=%s", ev.actual_hz)
                        await broadcaster.broadcast({"event": "ready", **state["ready"]})
                    elif isinstance(ev, Error):
                        state["last_error"] = ev.msg
                        log.warning("stream error: %s", ev.msg)
                        await broadcaster.broadcast({"event": "error", "msg": ev.msg})
                    elif isinstance(ev, Sample):
                        state["samples_seen"] += 1
                        tilt = tilt_from_accel(ev.x_g, ev.y_g, ev.z_g)
                        await broadcaster.broadcast({
                            "t": ev.t,
                            "x_g": ev.x_g, "y_g": ev.y_g, "z_g": ev.z_g,
                            "T_c": ev.temp_c,
                            "pitch": tilt.pitch_deg,
                            "roll": tilt.roll_deg,
                            "incl": tilt.inclination_deg,
                        })
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("stream iteration crashed")
                state["last_error"] = str(e)

            state["ready"] = None
            await broadcaster.broadcast({
                "event": "disconnected",
                "retry_in_s": backoff,
            })
            log.info("stream ended; retrying in %.1f s", backoff)
            await asyncio.sleep(backoff)
            if not connected:
                backoff = min(backoff * 2, BACKOFF_MAX_S)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal stream_task
        stream_task = asyncio.create_task(stream_loop(), name="inclinometer-stream")
        try:
            yield
        finally:
            if stream_task and not stream_task.done():
                stream_task.cancel()
                try:
                    await stream_task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(lifespan=lifespan)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    async def status() -> dict:
        return {
            "ready": state["ready"],
            "samples_seen": state["samples_seen"],
            "last_error": state["last_error"],
        }

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await broadcaster.add(websocket)
        # Send current ready state immediately so a new tab knows the actual rate.
        if state["ready"]:
            await websocket.send_text(json.dumps({"event": "ready", **state["ready"]}))
        try:
            while True:
                # We don't need anything from the client; this just blocks until
                # the socket closes (then WebSocketDisconnect fires).
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await broadcaster.remove(websocket)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def main() -> int:
    p = argparse.ArgumentParser(description="Inclinometer dashboard")
    p.add_argument("--pi", default=DEFAULT_REMOTE_HOST,
                   help=f"ssh target for the Pi (default: {DEFAULT_REMOTE_HOST}; "
                        "env: INCLINOMETER_REMOTE_HOST)")
    p.add_argument("--remote-uv", default=DEFAULT_REMOTE_UV,
                   help=f"full path to uv on the Pi (default: {DEFAULT_REMOTE_UV}; "
                        "env: INCLINOMETER_REMOTE_UV)")
    p.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR,
                   help=f"project directory on the Pi (default: {DEFAULT_REMOTE_DIR}; "
                        "env: INCLINOMETER_REMOTE_DIR)")
    p.add_argument("--odr", type=float, default=125.0, help="requested ODR in Hz")
    p.add_argument("--host", default="127.0.0.1", help="bind address for the web server")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = make_app(
        pi_host=args.pi, odr=args.odr,
        remote_uv=args.remote_uv, remote_dir=args.remote_dir,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
