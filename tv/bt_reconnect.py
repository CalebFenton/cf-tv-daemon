#!/usr/bin/env python3
"""bt-reconnect - connect, disconnect, or inspect the Bluetooth speaker.

    bin/bt-reconnect status       connected?, saved route, configured speaker, hook log
    bin/bt-reconnect connect      send the A2DP connect request now
    bin/bt-reconnect disconnect   send the A2DP disconnect request

The requests are the ones the Sound Output menu sends to bt-service; see
docs/04-system/bluetooth.md. Runs through the root daemon (tv/root.py).
Automatic reconnect is the 50-btconnect boot/wake hook; to rerun it now:
tv/root.py hooks wake.
"""

from __future__ import annotations

import argparse
import sys

import project_env

project_env.ensure()

import bt
import root

# The speaker `boot install` deploys for the 50-btconnect hook; not in git
# (template payload/btconnect.addr.example).
ADDR_FILE = root.PAYLOAD_DIR / "btconnect.addr"
ADDR = ADDR_FILE.read_text().split()[0] if ADDR_FILE.is_file() else None
ROUTE = "buxton2ctl get system db/btapp/lastSoundRoute | awk '{print \"saved route:\", $NF}'"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("status", "connect", "disconnect"))
    parser.add_argument("--addr", default=ADDR, required=ADDR is None, help=f"default: {ADDR_FILE}")
    args = parser.parse_args()

    connected = bt.connected_query(args.addr)
    commands = {
        "status": [connected, ROUTE, f"cat {bt.TARGET_FILE}; grep btconnect /opt/cf_tv_hooks.log | tail -5"],
        "connect": [bt.connect(args.addr) + "; sleep 1", connected],
        "disconnect": [bt.request(bt.DISCONNECT, args.addr) + "; sleep 1", connected],
    }[args.action]

    ok, output = root.daemon_exec(commands, timeout=40)
    if not ok:
        print(output, file=sys.stderr)
        return 1
    lines = output.splitlines()
    # The gdbus reply is the live bitmask; lastSoundRoute is persisted, not live.
    for index, line in enumerate(lines):
        if line.startswith("(0, <[byte"):
            state = "connected" if bt.audio_connected(line) else "not connected"
            lines[index] = f"{state} ({line.strip()})"
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
