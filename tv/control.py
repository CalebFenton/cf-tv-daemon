#!/usr/bin/env python3
"""control - remote-control the TV from the host (docs/04-system/control.md).

Sends key events through Enlightenment's own D-Bus test interface:

    org.enlightenment.wm  /org/enlightenment/wm
        org.enlightenment.wm.Test.EventKey(uint32 pressed, string name)

That is what the TV's own /usr/bin/input_keyevent does. Only root may send
there, so this goes through the root channel (tv/chan.py).

Key names are X keysym names from the TV's own keymap
(/usr/share/X11/xkb/tizen_key_layout.txt.tv); the full list is in
docs/04-system/key-names.txt, and `bin/control keys` prints the usual ones.
The REST API on :8001 needs on-screen pairing and only reaches the remote's
keys; D-Bus injection needs neither.

Usage
-----
    bin/control key XF86AudioRaiseVolume      # one key, down+up
    bin/control key XF86Back 3                # press it 3 times
    bin/control vol up | down | mute | get | set N
    bin/control ch  up | down | list | guide | prev | favorite
    bin/control keys                          # list known names
    bin/control raw '<shell command>'         # escape hatch on the TV

`vol` uses pactl on tizenaudio-sink (up/down are +-5%, `set N` is absolute):
that is the TV's internal audio path, not the Bluetooth speaker. `ch up/down`
send XF86Raise/LowerChannel, which on this firmware open Accessibility
Shortcuts instead of changing channel.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAN = ROOT / "tv" / "chan.py"
sys.path.insert(0, str(ROOT / "tv"))

import project_env

PYTHON = sys.executable

# Keys the TV's keymap actually defines. Verified against
# /usr/share/X11/xkb/tizen_key_layout.txt.tv on the device (364 entries).
CONTROL_KEYS = {
    # volume / audio
    "XF86AudioRaiseVolume": "volume up",
    "XF86AudioLowerVolume": "volume down",
    "XF86AudioMute": "mute toggle",
    "XF86VolumeDirect": "volume direct",
    "XF86KeySpkMicMute": "speaker/mic mute",
    "XF86AudioPlay": "media play",
    "XF86AudioPause": "media pause",
    "XF86AudioPlayPause": "media play/pause",
    "XF86AudioStop": "media stop",
    "XF86AudioNext": "next track",
    "XF86AudioPrev": "previous track",
    "XF86AudioForward": "fast forward",
    "XF86AudioRewind": "rewind",
    # channel - WARNING: these do NOT change channel on this firmware.
    # Verified 2026-10-23: XF86RaiseChannel opens Accessibility Shortcuts.
    # Kept in the list because the mapping is real; behaviour is documented.
    "XF86RaiseChannel": "channel up (NOT channel change - opens Accessibility Shortcuts)",
    "XF86LowerChannel": "channel down (NOT channel change)",
    "XF86ChannelList": "channel list",
    "XF86ChannelGuide": "channel guide",
    "XF86ChannelAutoTune": "auto tune",
    "XF86PreviousChannel": "previous channel",
    "XF86FavoriteChannel": "favourite channel",
    # navigation
    "Up": "d-pad up",
    "Down": "d-pad down",
    "Left": "d-pad left",
    "Right": "d-pad right",
    "Return": "ok / enter",
    "XF86Back": "back",
    "XF86Home": "home",
    "XF86Menu": "menu",
    "XF86QuickMenu": "quick menu",
    "XF86Info": "info",
    "XF86Guide": "guide",
    # power / panel
    "XF86PowerOff": "power off",
    "XF86Sleep": "sleep",
    "XF86ScreenMute": "screen off (audio continues)",
    "XF86PanelPower": "panel power",
    "XF86EnergySaving": "energy saving",
    # digits, for direct channel entry
    **{str(d): f"digit {d}" for d in range(10)},
    "minus": "dash (sub-channel)",
}


def chan(command: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run one command through the persistent root channel."""
    return subprocess.run(
        [PYTHON, str(CHAN), "run", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# Keys that commit an action in whatever has focus. Sending these blind is how
# Voice Guide got enabled by accident on 2026-10-23: Return flipped the first
# toggle in an Accessibility menu that XF86RaiseChannel had opened.
COMMITTING_KEYS = {"Return", "minus"}


def press(name: str, count: int = 1) -> int:
    """Send down+up for `name`, `count` times. Returns exit code."""
    if name in COMMITTING_KEYS:
        print(f"note: '{name}' commits whatever is focused. Capture the screen first if you have not.", file=sys.stderr)
    if name not in CONTROL_KEYS and not name.startswith(("XF86", "KEY_")):
        print(f"warning: '{name}' is not in the known key list; sending anyway", file=sys.stderr)

    # One round trip: loop on the device rather than one call per press.
    inner = f"for i in $(seq 1 {count}); do /usr/bin/input_keyevent {name}; done"
    result = chan(inner)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode
    return 0


# --- volume: PulseAudio, not key injection -------------------------------
# Verified on device 2026-10-23: XF86AudioMute / XF86AudioRaiseVolume are
# accepted by the compositor (dbus-send returns boolean true) but do NOT change
# the TV volume. pactl does, and reports state back. Use pactl.

SINK = "0"


def audio_state() -> str:
    """Current volume and mute for the main sink, as the TV reports them."""
    result = chan(
        "pactl list sinks 2>/dev/null | sed -n '/Name: tizenaudio-sink/,/Base Volume/p' "
        "| grep -iE 'mute:|volume:' | head -2"
    )
    return (result.stdout or "").strip()


def audio_cmd(command: str) -> int:
    result = chan(command)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode
    out = audio_state()
    if out:
        print(out)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    project_env.add_tv_argument(parser)
    sub = parser.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("key", help="send a key by X keysym name")
    k.add_argument("name")
    k.add_argument("count", nargs="?", type=int, default=1)

    v = sub.add_parser("vol", help="volume convenience wrapper")
    v.add_argument("action", choices=["up", "down", "mute", "get", "set"])
    v.add_argument("value", nargs="?", type=int)

    c = sub.add_parser("ch", help="channel convenience wrapper")
    c.add_argument("action", choices=["up", "down", "list", "guide", "prev", "favorite"])

    sub.add_parser("keys", help="list known key names")

    r = sub.add_parser("raw", help="run a shell command on the TV")
    r.add_argument("command")

    args = parser.parse_args()
    project_env.select(args)

    if args.cmd == "key":
        return press(args.name, args.count)

    if args.cmd == "keys":
        width = max(len(n) for n in CONTROL_KEYS)
        for name in sorted(CONTROL_KEYS):
            print(f"  {name.ljust(width)}  {CONTROL_KEYS[name]}")
        print(f"\n{len(CONTROL_KEYS)} keys. The device keymap has 364; any XF86 name from it works.")
        return 0

    if args.cmd == "raw":
        result = chan(args.command)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode

    if args.cmd == "vol":
        if args.action == "up":
            return audio_cmd(f"pactl set-sink-volume {SINK} +5%")
        if args.action == "down":
            return audio_cmd(f"pactl set-sink-volume {SINK} -5%")
        if args.action == "mute":
            return audio_cmd(f"pactl set-sink-mute {SINK} toggle")
        if args.action == "get":
            print(audio_state() or "(no sink reported)")
            return 0
        if args.action == "set":
            if args.value is None:
                print("vol set requires a value 0-100", file=sys.stderr)
                return 2
            return audio_cmd(f"pactl set-sink-volume {SINK} {args.value}%")

    if args.cmd == "ch":
        mapping = {
            "up": "XF86RaiseChannel",
            "down": "XF86LowerChannel",
            "list": "XF86ChannelList",
            "guide": "XF86ChannelGuide",
            "prev": "XF86PreviousChannel",
            "favorite": "XF86FavoriteChannel",
        }
        return press(mapping[args.action])

    return 2


if __name__ == "__main__":
    sys.exit(main())
