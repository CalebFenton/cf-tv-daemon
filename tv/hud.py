#!/usr/bin/env python3
"""hud - a heads-up display over the TV picture: faces, hand gestures, objects and people.

cf-vision, a separate recognition service (not part of this repo) running on
this computer, recognises what the TV shows; the cftv agent on the TV draws
the HUD.

    bin/hud live                          HUD on until Ctrl-C
    bin/hud live --seconds 60             ... for 60 s
    bin/hud off                           remove the HUD (if a live run was killed)
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import socket
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import project_env

project_env.ensure()

import cftv
from PIL import Image
from websockets.sync.client import connect

VISION = "ws://127.0.0.1:7750/"
# The cf-vision session: every kind, the scene caption, faces named from the
# "family" photo gallery and from the subjects learned on the TV, new ones learned there.
SESSION = {
    "kinds": ["face", "person", "object", "hand"],
    "scene": True,
    "match": ["family", "subjects:tv"],
    "learn": "subjects:tv",
}
FRAME_TIME = struct.Struct("<d")  # cf-vision's frame message: the frame time, then the JPEG
# The capture probe's frame server, kept running and asked for frames over
# TCP to avoid a process start and a shell round trip per frame. It exits
# after 30 s without a request.
PROBE = "/opt/cap/CaptureProbe.dll"
CAPTURE_PORT = 7742
# Stop a previous server (it holds the port and expects another token) and
# wait until it is gone, then start one with this token. The pid is checked
# against the command line because a stale pid file can name a reused pid.
CAPTURE_START = (
    "p=$(cat /tmp/cf-capture.pid 2>/dev/null); "
    "if grep -q CaptureProbe /proc/$p/cmdline 2>/dev/null; then kill $p; while [ -d /proc/$p ]; do sleep 0.02; done; fi; "
    "CF_CAPTURE_TOKEN={token} setsid /usr/bin/dotnet " + PROBE + f" --serve {CAPTURE_PORT} 30 "
    ">/dev/null 2>&1 </dev/null &"
)
# COCO keypoint pairs for the skeleton: face, arms, torso, legs.
SKELETON = (
    (0, 1), (0, 2), (1, 3), (2, 4),
    (5, 7), (7, 9), (6, 8), (8, 10), (5, 6),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
)  # fmt: skip

# HUD look per kind: bracket colour and label. Faces are the red "lock".
HUD_COLORS = {"face": "#FF3B30", "person": "#00E5FF", "hand": "#FFC400", "object": "#7CFFB2"}
HUD_NAMES = {"face": "FACE", "person": "HUMAN"}
GESTURES = {
    "Thumb_Up": "THUMBS UP",
    "Thumb_Down": "THUMBS DOWN",
    "Victory": "PEACE SIGN",
    "Open_Palm": "OPEN PALM",
    "Closed_Fist": "FIST",
    "Pointing_Up": "POINTING",
    "ILoveYou": "I LOVE YOU",
}
SCREEN = (1920, 1080)  # the HUD's coordinates; frames are scaled up to it
# Boxes are sent where the target should be when the TV's glide ends, so they
# keep moving between updates instead of stopping at each one.
SMOOTHING = 0.5  # weight of the newest measurement in the update-period average
MAX_AHEAD = 1.0  # seconds; never extrapolate further
INSET_SIZE = 284  # px; must equal InsetImage in apps/cftv-hud.cs
INSET_INTERVAL = 0.25  # seconds; each inset is a JPEG upload and a file write on the TV
STATUS_CHARS = 50  # what fits on a status panel line


class CaptureFailed(cftv.CftvError):
    pass


class VisionError(cftv.CftvError):
    pass


class Camera:
    """The TV picture from the capture probe's frame server.

    `request()` asks for a frame and `receive()` returns it, so the TV can
    capture the next frame while this one is recognised. A frame's time is
    when it was requested: the probe captures as soon as the request arrives.
    """

    def __init__(self) -> None:
        self.sock: socket.socket | None = None
        self.requested: float | None = None  # time of the request not yet received

    def _start(self) -> None:
        token = os.urandom(16).hex()
        cftv.run(CAPTURE_START.format(token=token))
        for attempt in range(50):  # dotnet takes a fraction of a second to start
            try:
                self.sock = socket.create_connection((project_env.tv(), CAPTURE_PORT), timeout=5)
                break
            except ConnectionRefusedError:
                if attempt == 49:
                    raise
                time.sleep(0.1)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.sendall(token.encode() + b"\n")

    def _read(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionResetError("capture server closed the connection")
            data += chunk
        return bytes(data)

    def _ask(self) -> None:
        if self.sock is None:
            self._start()
        self.requested = time.monotonic()
        self.sock.sendall(b"f")

    def request(self) -> None:
        """Ask for the next frame; if the server is gone, `receive()` starts another."""
        try:
            self._ask()
        except OSError:
            self.sock, self.requested = None, None

    def receive(self) -> tuple[bytes, float]:
        """The requested frame (JPEG) and the time it was requested (monotonic seconds)."""
        for attempt in (1, 2):
            try:
                if self.requested is None:
                    self._ask()
                jpeg = self._read(struct.unpack(">I", self._read(4))[0])
                break
            except OSError:
                # The server exited (idle) or was replaced: start another, ask again.
                self.sock, self.requested = None, None
                if attempt == 2:
                    raise
        requested, self.requested = self.requested, None
        if not jpeg:
            raise CaptureFailed("the TV refused or failed the capture (protected apps refuse it)")
        return jpeg, requested


class Vision:
    """A cf-vision session: frames in, tracks out."""

    def __init__(self, config: dict) -> None:
        try:
            self.ws = connect(VISION, compression=None, max_size=None)
        except OSError as error:
            raise VisionError(f"cf-vision is not running at {VISION} ({error})") from None
        self.ws.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.ws.send(json.dumps(config))
        self._reply()

    def recognise(self, jpeg: bytes, at: float) -> dict:
        """The result for the frame `jpeg` taken at `at` (monotonic seconds)."""
        self.ws.send(FRAME_TIME.pack(at) + jpeg)
        return self._reply()

    def _reply(self) -> dict:
        reply = json.loads(self.ws.recv())
        if "error" in reply:
            raise VisionError(f"cf-vision: {reply['error']}")
        return reply

    def close(self) -> None:
        self.ws.close()


def hud_target(track: dict, frame: tuple[int, int], ahead: float, primary: bool) -> dict:
    """One target of the agent's `hud` command, in screen pixels.

    The box is extrapolated `ahead` seconds from its frame at its velocity,
    capped at MAX_AHEAD; a target missed this update stays put. Confidence is
    shown in 10 % steps because every changed label costs the TV a text
    layout. Bones and mesh are relative to the box so they move with it.
    """
    kind, box, person = track["kind"], track["box"], track.get("person")
    sx, sy = SCREEN[0] / frame[0], SCREEN[1] / frame[1]
    lead = 0.0 if track["missed"] else min(ahead, MAX_AHEAD)
    x0, y0, x1, y1 = (edge + v * lead for edge, v in zip(box, track["velocity"], strict=True))
    subject = person is not None and person["subject"]
    if subject:
        title = person["label"]
    elif person:
        title = "IDENTIFIED: " + person["label"].upper()
    elif kind == "hand":
        title = "GESTURE: " + GESTURES[track["label"]] if track["label"] in GESTURES else "HAND"
    else:
        title = HUD_NAMES.get(kind, track["label"].upper())
    if person is None:
        evidence = f"  CONF {int(track['score'] * 10) * 10}%"
    elif subject:
        evidence = f"  SEEN {person['sessions']}×"  # noqa: RUF001  (Share Tech Mono has U+00D7)
    else:
        evidence = f"  MATCH {int(person['similarity'] * 10) * 10}%"
    traits = f"  {'M' if track['male'] >= 0.5 else 'F'} {round(track['age'])}" if kind == "face" else ""
    target = {
        "id": f"t{track['id']}",
        "x": round(x0 * sx),
        "y": round(y0 * sy),
        "w": round(max(x1 - x0, 1) * sx),
        "h": round(max(y1 - y0, 1) * sy),
        "title": title,
        "detail": f"TGT-{track['id']:03d}" + traits + evidence + ("  SIGNAL LOST" if track["missed"] else ""),
        "color": HUD_COLORS[kind],
    }

    def rel(x: float, y: float) -> tuple[int, int]:
        return round((x - box[0]) * sx), round((y - box[1]) * sy)

    if points := track.get("keypoints"):
        target["bones"] = [[*rel(*points[a]), *rel(*points[b])] for a, b in SKELETON if points[a] and points[b]]
    if mesh := track.get("mesh"):
        target["mesh"] = [v for x, y in mesh for v in rel(x, y)]
    if primary:
        target["primary"] = True
    return target


def primary_target(tracks: list[dict]) -> dict | None:
    """The face the HUD focuses on: a named person first, then a subject, then the largest seen in this update."""
    seen = [t for t in tracks if t["kind"] == "face" and not t["missed"]]
    return max(
        seen,
        key=lambda t: (
            t["person"] is not None and not t["person"]["subject"],
            t["person"] is not None,
            (t["box"][2] - t["box"][0]) * (t["box"][3] - t["box"][1]),
        ),
        default=None,
    )


def inset(jpeg: bytes, track: dict) -> dict:
    """The agent's ENHANCE inset: the target's face, square, with a margin, magnified."""
    x0, y0, x1, y1 = track["box"]
    cx, cy, half = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0) * 0.7
    with Image.open(io.BytesIO(jpeg)) as image:
        crop = image.convert("RGB").crop((round(cx - half), round(cy - half), round(cx + half), round(cy + half)))
    buffer = io.BytesIO()
    crop.resize((INSET_SIZE, INSET_SIZE), Image.Resampling.LANCZOS).save(buffer, "JPEG", quality=85)
    who = track["person"]["label"].upper() if track["person"] else f"TGT-{track['id']:03d}"
    return {"jpeg": base64.b64encode(buffer.getvalue()).decode(), "title": f"ENHANCE // {who}"}


def scene_lines(scene: dict | None) -> list[str]:
    """Status panel lines for the scene caption and the text on screen."""
    if scene is None:
        return ["SCENE: ANALYSING ..."]
    lines = ["SCENE: " + scene["caption"].upper()[: STATUS_CHARS - 7]]
    if scene["text"]:
        lines.append("TEXT: " + " / ".join(scene["text"]).upper()[: STATUS_CHARS - 6])
    return lines


def hud(command: dict) -> None:
    reply = cftv.send({"op": "hud", **command})
    if not reply.get("ok"):
        raise cftv.CftvError(f"hud: {reply.get('error')}")


def live(interval: float, seconds: float | None) -> int:
    """Grab, recognise and draw until Ctrl-C (or `seconds`); the HUD is removed on exit."""
    cftv.ensure()
    hud({"targets": [], "status": ["INITIALISING RECOGNITION CORE", "CONNECTING TO CF-VISION ..."]})
    vision = sent = None
    # Sends run in this thread while the loop recognises the next frame; one
    # send at a time, as the agent connection is not shared between threads.
    sender = ThreadPoolExecutor(1, thread_name_prefix="hud-send")
    try:
        vision = Vision(SESSION)
        camera = Camera()
        inset_at, inset_shown = 0.0, False
        started = time.monotonic()
        period = max(interval, 0.1)  # smoothed time between updates: the TV glides for this long
        last = None
        camera.request()
        while seconds is None or time.monotonic() - started < seconds:
            begin = time.monotonic()
            if last is not None:
                period = SMOOTHING * (begin - last) + (1 - SMOOTHING) * period
            last = begin
            try:
                jpeg, shot = camera.receive()
            except CaptureFailed:
                # Protected apps (such as the TV's own Netflix app) refuse
                # capture; retry once a second.
                if sent:
                    sent.result()
                sent = None
                hud({"targets": [], "status": ["SIGNAL BLOCKED", "PROTECTED CONTENT: CAPTURE REFUSED"], "scan": False})
                time.sleep(1.0)
                camera.request()
                last = None
                continue
            camera.request()  # the TV captures the next frame while this one is recognised
            result = vision.recognise(jpeg, shot)
            now = time.monotonic()
            tracks, size = result["tracks"], tuple(result["size"])
            focus = primary_target(tracks)
            count = {kind: sum(t["kind"] == kind for t in tracks) for kind in HUD_COLORS}
            command = {
                "targets": [hud_target(t, size, now - shot + period, t is focus) for t in tracks],
                "glide": round(period * 1000),
                "status": [
                    f"TARGETS {len(tracks):02d}  HUMANS {count['person']}  FACES {count['face']}"
                    f"  HANDS {count['hand']}  OBJECTS {count['object']}",
                    f"LATENCY {now - shot:.1f}s  RATE {1 / period:2.0f} HZ  {time.strftime('%H:%M:%S')}",
                    *scene_lines(result["scene"]),
                ],
            }
            if focus and now - inset_at >= INSET_INTERVAL:
                command["inset"], inset_at, inset_shown = inset(jpeg, focus), now, True
            elif not focus and inset_shown:
                command["inset"], inset_shown = None, False
            if sent:
                sent.result()  # raises its error; waits only if the TV is slower than a recognition
            sent = sender.submit(hud, command)
            time.sleep(max(0.0, interval - (time.monotonic() - begin)))
    except KeyboardInterrupt:
        pass
    finally:
        sender.shutdown()
        hud({"off": True})
        if vision:
            vision.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    project_env.add_tv_argument(parser)
    sub = parser.add_subparsers(dest="op", required=True)
    p = sub.add_parser("live", help="HUD on the TV until Ctrl-C")
    p.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="minimum seconds between updates (default 0: as fast as frames come, the capture limit is ~22/s)",
    )
    p.add_argument("--seconds", type=float, help="stop after this long")
    sub.add_parser("off", help="remove the HUD from the TV")
    args = parser.parse_args()
    project_env.select(args)
    if args.op == "live":
        return live(args.interval, args.seconds)
    hud({"off": True})
    return 0


if __name__ == "__main__":
    sys.exit(main())
