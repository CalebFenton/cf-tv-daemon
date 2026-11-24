#!/usr/bin/env python3
"""stream - capture the TV screen as a burst of JPEG frames and pull them to the host.

Capture runs on the TV (~22 fps, 720x404), then the frames come back over the
root channel's raw file read (READFILE): no base64 inside the channel, up to
4 MiB per file. READFILE only accepts paths inside the agent's staging
directory, so frames are captured straight into <staging>/frames/.

Usage
-----
    bin/stream --frames 30                  # capture + pull 30 frames
    bin/stream --seconds 10                 # repeat capture/pull cycles for 10 s
    bin/stream --frames 50 --outdir /tmp/s

Requires: tv/chan.py serve.
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import time
from pathlib import Path

import project_env

project_env.ensure()

SOCKET_PATH, _LOG = project_env.channel_paths(project_env.tv_ip())
PROBE = "/opt/cap/CaptureProbe.dll"


def request(payload: dict, timeout: float = 180.0) -> dict:
    if not SOCKET_PATH.exists():
        return {"ok": False, "error": "no_daemon"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(SOCKET_PATH))
            sock.sendall((json.dumps(payload) + "\n").encode())
            chunks = []
            while True:
                chunk = sock.recv(1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
        return json.loads(b"".join(chunks).decode())
    except Exception as error:
        return {"ok": False, "error": type(error).__name__, "detail": str(error)}


def run(command: str, timeout: float = 180.0) -> str | None:
    result = request({"op": "exec", "commands": [command]}, timeout)
    if not result.get("ok"):
        print(f"stream: {result.get('error')}: {result.get('detail', '')}", file=sys.stderr)
        return None
    return result["results"][0]["stdout"]


def frame_dir() -> str | None:
    status = request({"op": "status"}, 30)
    staging = (status.get("state") or {}).get("staging")
    if not status.get("up") or not staging:
        print("stream: channel not up (start tv/chan.py serve)", file=sys.stderr)
        return None
    return staging + "frames"


def capture(frames: str, count: int) -> int | None:
    """Capture `count` frames on the TV; return how many exist."""
    # The first ~10-15 captures after a pause come back black: the scaler
    # pipeline is not producing yet. Discard a warmup burst (~0.5 s).
    out = run(
        f"rm -f {frames}/*.jpg; "
        f"/usr/bin/dotnet {PROBE} {frames}/w.jpg --loop 16 0 >/dev/null 2>&1; "
        f"rm -f {frames}/w_*.jpg; "
        f"/usr/bin/dotnet {PROBE} {frames}/f.jpg --loop {count} 0 >/dev/null 2>&1; "
        f"ls {frames}/f_*.jpg 2>/dev/null | wc -l"
    )
    if out is None:
        return None
    produced = int(out.strip() or "0")
    if produced == 0:
        print(f"stream: no frames produced; is {PROBE} on the TV?", file=sys.stderr)
        return None
    return produced


def pull(frames: str, count: int, outdir: Path, offset: int, batch: int) -> int:
    """Fetch frames 0..count-1 with READFILE, `batch` files per round trip."""
    written = 0
    for start in range(0, count, batch):
        paths = [f"{frames}/f_{i:04d}.jpg" for i in range(start, min(start + batch, count))]
        result = request({"op": "file", "paths": paths})
        if not result.get("ok"):
            print(f"stream: pull failed: {result.get('error')}", file=sys.stderr)
            break
        for entry in result["files"]:
            (outdir / f"frame_{offset + written:05d}.jpg").write_bytes(base64.b64decode(entry["data"]))
            written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--frames", type=int, default=30, help="frames per capture burst (default 30)")
    parser.add_argument("--seconds", type=float, default=0, help="repeat capture/pull bursts for this long")
    parser.add_argument("--batch", type=int, default=8, help="frames per READFILE round trip (default 8)")
    parser.add_argument("--outdir", default="/tmp/stream")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    frames = frame_dir()
    if frames is None or run(f"mkdir -p {frames}") is None:
        return 1

    total = 0
    started = time.monotonic()
    deadline = started + args.seconds
    while True:
        produced = capture(frames, args.frames)
        if produced is None:
            return 1
        total += pull(frames, produced, outdir, total, args.batch)
        if not args.seconds or time.monotonic() >= deadline:
            break
    run(f"rm -f {frames}/*.jpg")

    elapsed = max(time.monotonic() - started, 1e-9)
    print(f"{total} frames in {elapsed:.2f}s = {total / elapsed:.1f} fps -> {outdir}")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
