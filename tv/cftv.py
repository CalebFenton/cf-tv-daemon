#!/usr/bin/env python3
"""cftv - put things on the TV: images, text, shapes, video, streams, sound, speech.

Installs and starts the on-TV agent `apps/cftv.cs` through the root channel
(tv/chan.py), then talks to it directly over TCP (port 7741, a per-start token
from the agent's directory, one JSON line per command and reply). The agent
runs in a borrowed app slot (SLOTS) as transparent, non-focusable,
click-through windows, so whatever is on screen keeps playing and keeps the
remote.

    bin/cftv image logo.png --at 100,80               natural size
    bin/cftv image cat.gif --at 1400,600 --size 400x400 --id cat
    bin/cftv text "Dinner's ready" --at 560,60 --size 800x120 --color '#FFD000'
    bin/cftv rect --at 0,1040 --size 1920x40 --color '#00000080' --id bar
    bin/cftv video clip.mp4 --at 1280,40 --size 600x340 --loop
    bin/cftv video http://host/live.m3u8 --at 0,0 --size 960x540
    bin/cftv sound chime.mp3 --volume 0.5
    bin/cftv say "the laundry is done"
    bin/cftv notify "Ready in 5 minutes" --title Dinner --icon pot.png --sound
    bin/cftv move cat --at 200,200
    bin/cftv remove cat | clear | ping | log

The agent stays running between commands and never exits by itself: any exit
hands focus to Smart Hub, whose bar slides up over the picture. `clear`
removes everything.

Local files are uploaded once (named by content hash) into the agent's media
directory; URLs (http, https, rtsp, HLS) are passed straight to the player.
Sounds are converted to WAV on this computer first (`sound_media`): the agent
plays them with WavPlayer, which mixes them into the programme's audio.
"""

from __future__ import annotations

import argparse
import base64
import functools
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tv"))
import project_env

project_env.ensure()

import bt
from chan import client_request
from PIL import Image


@dataclass(frozen=True)
class Slot:
    """A shipped NUI app whose entry DLL the agent replaces (docs/03-video/app-build.md §7)."""

    app: str
    dll: str  # entry DLL file name in bin/

    @property
    def entry(self) -> str:
        return f"/opt/usr/apps/{self.app}/bin/{self.dll}"

    @property
    def native_image(self) -> str:
        return f"/opt/usr/apps/{self.app}/bin/.native_image/{self.dll}"

    @property
    def q(self) -> str:
        """The agent's directory: log, token, ready, media, fonts."""
        return f"/opt/usr/home/owner/apps_rw/{self.app}/cftv/"

    def own(self, path: str) -> str:
        """Files the app (uid 5001) reads or writes need its owner and SMACK label."""
        return f"chown 5001:5001 {path} && chsmack -a 'User::Pkg::{self.app}' {path}"


# The first one installed on the TV is used. Each is a `dotnet-inhouse`
# ui-application with nodisplay="false" (a hidden one never reaches the
# screen, app-build.md §9) that nothing launches on its own.
SLOTS = (
    Slot("com.samsung.tv.social-chat-app", "Com.Samsung.TV.SocialChatApp.dll"),  # QN85D
    Slot("com.samsung.tv.samsungdex", "com.samsung.tv.samsungdex.dll"),  # CU7000, which lacks the one above
)
BACKUPS = "/opt/usr/share/tv-backups/cftv"  # <app>/: the stock entry DLL and native image link
PORT = 7741  # the agent's command port (apps/cftv.cs Port)
CHUNK = 4 * 1024 * 1024  # agent WRITEFILE ceiling per item
# The TV's own notification chime; `notify --sound` with no file plays it.
SYSTEM_SOUND = "/usr/share/feedback/sound/operation/normal_noti.wav"

BUILD_DIR = ROOT / "build" / "cftv"
AGENT_DLL = BUILD_DIR / "Cftv.dll"
SOURCES = tuple(
    ROOT / "apps" / f for f in ("cftv.cs", "cftv-hud.cs", "cftv-frames.cs", "cftv-esplayer.cs", "cftv.csproj")
)
# Fonts the agent registers (FontClient.AddCustomFontDirectory) for the HUD.
FONTS = sorted((ROOT / "apps" / "assets" / "fonts").glob("*.ttf"))


@functools.cache
def slot() -> Slot:
    """The slot this TV has; one channel round trip, the first time it is needed."""
    apps = " ".join(s.app for s in SLOTS)
    found = run(f"for a in {apps}; do [ -d /opt/usr/apps/$a ] && {{ echo $a; break; }}; done; true").strip()
    for candidate in SLOTS:
        if candidate.app == found:
            return candidate
    raise CftvError(f"none of the agent's app slots is installed on {project_env.tv()}: {apps}")


class CftvError(RuntimeError):
    pass


# ------------------------------------------------------------ channel helpers


def request(payload: dict) -> dict:
    """One channel request.

    Starts `tv/chan.py serve` first if it is not running (bring-up through the
    daemon, ~1.5 s).
    """
    response = client_request(payload)
    if response.get("error") in ("no_daemon", "FileNotFoundError", "ConnectionRefusedError"):
        import root  # noqa: PLC0415  (tv/root.py)

        if not root.channel_start():
            raise CftvError(f"could not start the root channel (see {project_env.channel_log()})")
        response = client_request(payload)
    return response


def run(command: str) -> str:
    response = request({"op": "exec", "commands": [command]})
    if not response.get("ok"):
        raise CftvError(f"channel: {response.get('error')} {response.get('detail', '')}".strip())
    result = response["results"][0]
    if result["exit_code"] != 0:
        raise CftvError(
            f"TV command failed ({result['exit_code']}): {result['stderr'].strip() or result['stdout'].strip()}"
        )
    return result["stdout"]


def upload(data: bytes, remote: str) -> None:
    """Write `data` to `remote` (absolute path on the TV) via staged chunks."""
    parts = []
    token = os.urandom(4).hex()
    for index in range(0, max(len(data), 1), CHUNK):
        name = f"cftv-{token}-{index // CHUNK:04d}"
        parts.append(name)
        response = request(
            {
                "op": "put",
                "items": [
                    {
                        "data": base64.b64encode(data[index : index + CHUNK]).decode("ascii"),
                        "path": name,
                    }
                ],
            }
        )
        if not response.get("ok"):
            raise CftvError(f"upload failed: {response.get('error')} {response.get('detail', '')}")
        staged = response["written"][0]["path"]
    staging = staged.rsplit("/", 1)[0] + "/"
    files = " ".join(shlex.quote(staging + p) for p in parts)
    run(f"cat {files} > {remote}.part && mv {remote}.part {remote} && rm -f {files}")


# ------------------------------------------------------------ agent lifecycle


def build() -> Path:
    if AGENT_DLL.is_file() and all(AGENT_DLL.stat().st_mtime >= s.stat().st_mtime for s in SOURCES):
        return AGENT_DLL
    env = dict(
        os.environ,
        PATH=f"{ROOT / '.dotnet8'}:{os.environ['PATH']}",
        DOTNET_CLI_HOME="/tmp/dnethome8",
        DOTNET_CLI_TELEMETRY_OPTOUT="1",
    )
    result = subprocess.run(
        [
            "dotnet",
            "build",
            str(ROOT / "apps" / "cftv.csproj"),
            "-c",
            "Release",
            "--nologo",
            "-o",
            str(BUILD_DIR),
            f"-p:TV_FRAMEWORK_DIR={ROOT / 'build' / 'tv-framework'}",
            f"-p:BaseIntermediateOutputPath={ROOT / 'build' / 'obj' / 'cftv'}/",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise CftvError("agent build failed:\n" + result.stdout[-2000:])
    return AGENT_DLL


# The app that has focus: the process of the window marked `O` (Foc column)
# in the compositor's list, mapped to its app id by its SMACK label
# `User::Pkg::<appid>`. Empty if unknown. (`(pattern)` with both parentheses:
# this text runs inside `$( )`, where a bare `pattern)` ends the substitution.)
FOCUSED_APP = (
    "p=$(enlightenment_info -topvwins 2>/dev/null | awk '$9 == \"O\" {print $4; exit}'); "
    "l=$(cat /proc/$p/attr/current 2>/dev/null); "
    "case $l in (User::Pkg::*) l=${l#User::Pkg::}; echo ${l%%::*};; esac"
)


def stop_agent() -> None:
    """Stop the agent (to replace its DLL), and give focus back to the app that had it.

    Any exit of the agent hands focus to Smart Hub, whose bar slides up over
    the picture ~1 s later (screenshots 2026-11-05). So: note the focused app
    first; for 3 s after the agent is gone, if focus is anywhere else, resume
    that app.
    """
    app = slot().app
    before = run(f"{FOCUSED_APP}; true").strip()
    moved = run(
        f"app_launcher -t {app} >/dev/null 2>&1; "
        f"i=0; while app_launcher -r {app} >/dev/null && [ $i -lt 30 ]; do sleep 0.1; i=$((i+1)); done; "
        f"case '{before}' in ''|{app}) exit 0;; esac; "
        f"i=0; while [ $i -lt 30 ]; do now=$({FOCUSED_APP}); "
        f'if [ -n "$now" ] && [ "$now" != \'{before}\' ]; then '
        f"app_launcher -s '{before}' >/dev/null 2>&1; echo \"$now\"; break; fi; "
        "sleep 0.1; i=$((i+1)); done; true"
    ).strip()
    if moved:
        print(f"focus had moved to {moved}; gave it back to {before}", file=sys.stderr)


def ensure() -> None:
    """Install the agent and its fonts if the TV's copies differ, start it if needed."""
    dll = build()
    local = hashlib.md5(dll.read_bytes()).hexdigest()
    s = slot()
    q, fonts_dir = s.q, s.q + "fonts/"
    state = run(
        # md5sum prints nothing for a missing file; `none` keeps the fields aligned.
        f"h=$(md5sum {s.entry} 2>/dev/null | cut -d' ' -f1); echo ${{h:-none}}; "
        f"app_launcher -r {s.app} >/dev/null && echo running || echo stopped; "
        f"r=$(cat {q}ready 2>/dev/null); echo ${{r:-none}}; "
        f"t=$(cat {q}token 2>/dev/null); echo ${{t:-none}}; "
        f"cd {fonts_dir} 2>/dev/null && md5sum *.ttf 2>/dev/null; true"
    ).split()
    installed, running, ready_pid, token = state[0], state[1] == "running", state[2], state[3]
    fonts_on_tv = dict(zip(state[5::2], state[4::2], strict=True))  # name -> md5

    if installed != local:
        if running:
            # Another app (or an old agent) holds the slot; let it go first.
            stop_agent()
            running = False
        upload(dll.read_bytes(), "/tmp/Cftv.dll")
        backup = f"{BACKUPS}/{s.app}"
        run(
            f"mkdir -p {backup} && "
            # The stock DLL, once; a slot that already holds an agent has nothing to save.
            f"{{ [ -e {backup}/{s.dll} ] || grep -q Cftv {s.entry} || cp -a {s.entry} {backup}/; }} && "
            # The launcher loads the stock precompiled copy in bin/.native_image
            # in preference to bin/, so it would run instead of the agent.
            f"{{ ! [ -e {s.native_image} -o -L {s.native_image} ] || mv {s.native_image} {backup}/native_image; }} && "
            f"cp /tmp/Cftv.dll {s.entry} && rm /tmp/Cftv.dll && "
            f"chown 201:0 {s.entry} && chmod 755 {s.entry} && "
            f"chsmack -a 'User::Pkg::{s.app}::RO' {s.entry}"
        )

    stale = [f for f in FONTS if fonts_on_tv.get(f.name) != hashlib.md5(f.read_bytes()).hexdigest()]
    if stale:
        run(f"mkdir -p {fonts_dir} && " + s.own(fonts_dir))
        for font in stale:
            upload(font.read_bytes(), fonts_dir + font.name)
            run(s.own(fonts_dir + font.name))

    alive = (
        running and ready_pid != "none" and run(f"test -d /proc/{ready_pid} && echo yes || echo no").strip() == "yes"
    )
    if not alive:
        run(
            f"mkdir -p {q}media && rm -f {q}ready {q}token && "
            f"chown -R 5001:5001 {q} && chsmack -a 'User::Pkg::{s.app}' {q} {q}media"
        )
        token = run(
            f"app_launcher -u 5001 -s {s.app} >/dev/null 2>&1; "
            f"i=0; while [ ! -s {q}ready ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done; "
            f"test -s {q}ready || {{ echo 'agent did not start; log:'; tail -5 {q}cftv.log; exit 1; }}; "
            f"cat {q}token"
        ).strip()
    global _token
    _token = token


_token: str | None = None  # this agent run's token, read by ensure()
_agent: tuple[socket.socket, object] | None = None  # connection and its line reader


def _connect() -> tuple[socket.socket, object]:
    """Connect and authenticate; right after a start the agent may still be binding."""
    if _token is None:
        ensure()
    for attempt in range(20):
        try:
            sock = socket.create_connection((project_env.tv(), PORT), timeout=5)
            break
        except ConnectionRefusedError:
            if attempt == 19:
                raise
            time.sleep(0.1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.sendall(_token.encode() + b"\n")
    return sock, sock.makefile("rb")


def send(command: dict, timeout: float = 5.0) -> dict:
    """Send one command to the agent and return its JSON reply.

    One connection per process, reused. If it broke (the agent restarted),
    `ensure()` brings the agent back and the command is sent once more.

    Videos take the TV's hardware video path away from the HDMI/tuner picture,
    which does not take it back on its own (the screen stays black). So the
    active input is remembered before the first video, and re-selected after
    the agent reports that a video was released.
    """
    global _agent
    if command.get("op") == "video":
        saved = slot().q + "source"
        run(f"test -s {saved} || echo '{source_get()} {speaker_in_use() or '-'}' > {saved}")
    line = json.dumps(command).encode() + b"\n"
    for attempt in (1, 2):
        try:
            if _agent is None:
                _agent = _connect()
            sock, reader = _agent
            sock.settimeout(timeout)
            sock.sendall(line)
            raw = reader.readline()
            if not raw:
                raise ConnectionResetError("agent closed the connection")
            break
        except TimeoutError as error:
            _agent = None
            raise CftvError(f"agent: no reply within {timeout}s") from error
        except OSError:
            _agent = None
            if attempt == 2:
                raise
            ensure()
    reply = json.loads(raw)
    # A video replacing a video also releases one; keep going until no video is left.
    if reply.get("video_released") and command.get("op") != "video":
        reply["source_restored"] = restore_source()
    return reply


# ------------------------------------------------------------ TV input (source)
#
# com.samsung.ISourceControl.MethodCall(function, size, buffer): Samsung's TV
# service. Buffers were captured with busctl monitor while the user switched
# inputs (2026-10-24): function 4 queries the current source (last int32),
# function 0 selects one (int32 at byte 32), sent by tv-viewer with its own
# app id at the end. Sources seen: 0 = TV tuner, 15 = HDMI (AVR S750H).
# Re-selecting the source that is already active is a no-op, so restoring
# means selecting another source and then the original.

SOURCE_CALL = (
    "gdbus call --system --dest com.samsung.ISourceControl "
    "--object-path /com/samsung/Object --method com.samsung.ISourceControl.MethodCall"
)
SOURCE_QUERY = bytes.fromhex("0408140000000004040000000100000000040400000000007800")
SOURCE_SELECT = bytes.fromhex(
    "000847000000000404000000010000000004040000000000780000040400000000000000"
    "000823000000000404000000130000000104130000006f72672e74697a656e2e74762d766965776572"
)
SOURCE_OFFSET = 32
TUNER = 0


def _gvariant_bytes(data: bytes) -> str:
    return "'[byte " + ",".join(f"0x{b:02x}" for b in data) + "]'"


def source_get() -> int:
    out = run(f"{SOURCE_CALL} 4 {len(SOURCE_QUERY)} {_gvariant_bytes(SOURCE_QUERY)}")
    # Reply: (byte 0x01, 26, [byte 0x04, ...]); only the array is the buffer.
    values = [int(v, 16) for v in re.findall(r"0x([0-9a-f]{2})", out.split("[", 1)[-1])]
    if len(values) < 26:
        raise CftvError(f"unexpected source reply: {out.strip()[:120]}")
    return int.from_bytes(bytes(values[22:26]), "little")


def source_set(source: int) -> None:
    buf = bytearray(SOURCE_SELECT)
    buf[SOURCE_OFFSET : SOURCE_OFFSET + 4] = source.to_bytes(4, "little")
    run(f"{SOURCE_CALL} 0 {len(buf)} {_gvariant_bytes(bytes(buf))} >/dev/null")


def speaker_in_use() -> str | None:
    """Address of the auto-reconnect speaker if it is connected right now."""
    addr = run(f"cat {bt.TARGET_FILE} 2>/dev/null || true").strip()
    if not addr:
        return None
    return addr if bt.audio_connected(run(bt.connected_query(addr))) else None


def restore_source() -> bool:
    """Re-select the input that was active before the first video.

    The bounce through the tuner also moves audio: coming back to HDMI, the
    TV routed sound to the AVR receiver over HDMI instead of the Bluetooth
    speaker that was playing (2026-10-24). The speaker stays connected, so
    nothing reconnects it on its own; a disconnect + connect makes the TV
    route audio to it again. Only done if it was in use before the video.
    """
    saved = run(f"f={slot().q}source; cat $f 2>/dev/null; rm -f $f").split()
    source = int(saved[0]) if saved and saved[0].isdigit() else source_get()
    speaker = saved[1] if len(saved) > 1 and saved[1] != "-" else None
    if source == TUNER:
        return False  # the bounce goes through the tuner; nothing to bounce through
    source_set(TUNER)
    time.sleep(2.5)
    source_set(source)
    # The pipeline is back when tv-viewer's video sink windows reappear.
    restored = (
        run(
            "i=0; while [ $i -lt 50 ]; do "
            "enlightenment_info -topvwins 2>/dev/null | grep -q tvvideosink && { echo yes; exit 0; }; "
            "sleep 0.2; i=$((i+1)); done; echo no"
        ).strip()
        == "yes"
    )
    if speaker:
        run(bt.request(bt.DISCONNECT, speaker) + "; sleep 2; " + bt.connect(speaker))
    return restored


def media(src: str) -> str:
    """URLs pass through; local files are uploaded once, named by content hash."""
    if "://" in src:
        return src
    path = Path(src)
    data = path.read_bytes()
    remote = slot().q + "media/" + hashlib.sha256(data).hexdigest()[:16] + path.suffix.lower()
    if run(f"test -f {remote} && echo have || echo need").strip() == "need":
        upload(data, remote)
        run(slot().own(remote) + f" && chmod 644 {remote}")
    return remote


def sound_media(src: str, volume: float | None = None) -> str:
    """A sound file or URL as a WAV on the TV, scaled by `volume`; WAV and OGG files without one are uploaded as they are.

    The agent plays sounds with WavPlayer, which takes local WAV or OGG files
    only, and has no volume control.
    """
    if volume is not None and volume <= 0:
        raise CftvError("volume must be above 0")
    if "://" not in src and Path(src).suffix.lower() in (".wav", ".ogg") and volume is None:
        return media(src)
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                src,
                "-filter:a",
                f"volume={1.0 if volume is None else volume}",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                "-",
            ],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        raise CftvError("ffmpeg is needed to convert sounds to WAV") from None
    if result.returncode != 0:
        raise CftvError(f"ffmpeg could not read {src}: {result.stderr.decode(errors='replace').strip()[-300:]}")
    remote = slot().q + "media/" + hashlib.sha256(result.stdout).hexdigest()[:16] + ".wav"
    if run(f"test -f {remote} && echo have || echo need").strip() == "need":
        upload(result.stdout, remote)
        run(slot().own(remote) + f" && chmod 644 {remote}")
    return remote


# ------------------------------------------------------------ image sizes


def image_size(path: Path) -> tuple[int, int] | None:
    """Natural size from the file header (Pillow reads only the header)."""
    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, Image.UnidentifiedImageError):
        return None


# ------------------------------------------------------------ notifications


def notify_command(
    text: str = "",
    *,
    title: str = "",
    icon: str | None = None,
    sound: str | None = None,
    volume: float | None = None,
    duration: float = 5.0,
    width: int = 560,
    id: str | None = None,  # noqa: A002  (the agent's field name)
) -> dict:
    """Agent command for a notification card; uploads the icon and sound if needed.

    `sound=SYSTEM_SOUND` plays the TV's own chime, at its own level. `duration=0` keeps the card
    until `send({"op": "remove", "id": ...})`. The default id is new per card,
    so cards stack; reusing an id replaces that card.
    """
    command = {
        "op": "notify",
        "id": id or f"notify-{os.urandom(3).hex()}",
        "title": title,
        "text": text,
        "duration": duration,
        "w": width,
    }
    if icon:
        command["icon"] = media(icon)
    if sound == SYSTEM_SOUND and volume is not None:
        raise CftvError("the TV's chime plays at its own level; --volume needs a sound file")
    if sound:
        command["sound"] = sound if sound == SYSTEM_SOUND else sound_media(sound, volume)
    return command


def notify(text: str = "", **options) -> dict:
    """Show a notification card (options: `notify_command`); returns the agent's reply."""
    ensure()
    return send(notify_command(text, **options))


# ------------------------------------------------------------ CLI


def pair(text: str, sep: str) -> tuple[int, int]:
    a, b = text.lower().split(sep)
    return int(a), int(b)


def geometry(args, command: dict, natural: tuple[int, int] | None = None) -> None:
    if args.at:
        command["x"], command["y"] = pair(args.at, ",")
    if args.size:
        command["w"], command["h"] = pair(args.size, "x")
    elif natural:
        command["w"], command["h"] = natural
    if args.opacity is not None:
        command["opacity"] = args.opacity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    project_env.add_tv_argument(parser)
    sub = parser.add_subparsers(dest="op", required=True)

    def visual(name: str, *positional: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name)
        for arg in positional:
            p.add_argument(arg)
        p.add_argument("--id")
        p.add_argument("--at", help="X,Y in screen pixels (1920x1080)")
        p.add_argument("--size", help="WxH")
        p.add_argument("--opacity", type=float)
        return p

    visual("image", "src")
    p = visual("text", "text")
    p.add_argument("--color", default="#FFFFFFFF")
    p.add_argument("--bg", default="#00000000")
    p.add_argument("--font", type=float, default=48, help="pixel size")
    p = visual("rect")
    p.add_argument("--color", default="#FF0000FF")
    p.add_argument("--radius", type=float, default=0)
    p = visual("video", "src")
    p.add_argument("--loop", action="store_true")
    p = sub.add_parser("sound")
    p.add_argument("src")
    p.add_argument("--id")
    p.add_argument("--loop", action="store_true")
    p.add_argument("--volume", type=float, help="scale the sound; 0.5 = half")
    p = sub.add_parser("say")
    p.add_argument("text")
    p = sub.add_parser("notify", help="card in the top-right corner; slides in, stays, slides out")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--title", default="")
    p.add_argument("--icon", help="image file (shown 72x72)")
    p.add_argument("--sound", nargs="?", const=SYSTEM_SOUND, help="sound file; no file = the TV's chime")
    p.add_argument("--volume", type=float, help="scale the sound; 0.5 = half")
    p.add_argument("--duration", type=float, default=5, help="seconds on screen; 0 = until `remove ID`")
    p.add_argument("--width", type=int, default=560)
    p.add_argument("--id", help="reuse to replace a card; default: a new id per card")
    sub.add_parser("remove").add_argument("id")
    for name in ("clear", "ping", "log"):
        sub.add_parser(name)

    args = parser.parse_args()
    project_env.select(args)
    try:
        if args.op == "log":
            print(run(f"tail -20 {slot().q}cftv.log"), end="")
            return 0

        ensure()
        command: dict = {"op": args.op}
        if args.op == "image":
            natural = None if "://" in args.src else image_size(Path(args.src))
            command.update(id=args.id or Path(args.src).stem, src=media(args.src))
            geometry(args, command, natural)
        elif args.op == "text":
            command.update(id=args.id or "text", text=args.text, color=args.color, bg=args.bg, size=args.font)
            geometry(args, command)
        elif args.op == "rect":
            command.update(id=args.id or "rect", color=args.color, radius=args.radius)
            geometry(args, command)
        elif args.op == "video":
            command.update(id=args.id or "video", src=media(args.src), loop=args.loop)
            geometry(args, command)
        elif args.op == "sound":
            command.update(id=args.id or "sound", src=sound_media(args.src, args.volume), loop=args.loop)
        elif args.op == "say":
            command.update(id="say", text=args.text)
        elif args.op == "notify":
            command = notify_command(
                args.text,
                title=args.title,
                icon=args.icon,
                sound=args.sound,
                volume=args.volume,
                duration=args.duration,
                width=args.width,
                id=args.id,
            )
        elif args.op == "move":
            command["id"] = args.id
            geometry(args, command)
        elif args.op == "remove":
            command["id"] = args.id
        reply = send(command)
    except CftvError as error:
        print(f"cftv: {error}", file=sys.stderr)
        return 1
    print(json.dumps(reply))
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
