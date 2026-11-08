"""Bluetooth speaker requests to bt-service, as shell commands for the TV.

Transport-agnostic: callers run these through the daemon (tv/root.py) or the
channel (tv/chan.py). See docs/04-system/bluetooth.md for how each request was
found.
"""

from __future__ import annotations

import re

TARGET_FILE = "/opt/cf_tv_daemon/btconnect.addr"  # speaker the 50-btconnect hook reconnects
CONNECT, DISCONNECT = 180, 181  # bt-service A2DP connect / disconnect


def _octets(addr: str) -> list[int]:
    return [int(part, 16) for part in addr.split(":")]


def _call(product: bool, service_type: int, function: int, request_type: int, *params: list[int]) -> str:
    """Busctl call of bt-service's service_request (product: service_request_product).

    `params` are the leading byte arrays; the rest of the five are empty.
    busctl exits 1 even when the call succeeds, because it cannot parse the
    variant in the reply ("Bad message"); it does print "iv <code>" first.
    """
    if product:
        target = (
            "org.projectx.bt_product /org/projectx/bt_service_product org.projectx.bt_product service_request_product"
        )
    else:
        target = "org.projectx.bt /org/projectx/bt_service org.projectx.bt service_request"
    arrays = [*params] + [[]] * (5 - len(params))
    encoded = " ".join(" ".join(str(v) for v in [len(a), *a]) for a in arrays)
    return (
        f"busctl --system --timeout=20 call {target} iiiayayayayay {service_type} {function} {request_type} {encoded}"
    )


# The Sound Output menu (org.tizen.btaudiolist) connects like this (busctl
# monitor captures 2026-10-23 and 2026-10-24, the second after a power loss):
#   1. discovery: service_request 14 ([0,0], [30,0], [0,0,0,0]), ~1 s, 16 (stop)
#   2. PRELUDE, then 180(addr)
#   3. if 180 returned -9: PRELUDE + 180 once more. After a power loss the
#      first 180 returned -9 but brought the link up (DeviceConnected); the
#      second returned 0 and audio started (StereoHeadsetConnected).
# Meanings of the PRELUDE functions are unknown.
DISCOVERY_START = _call(False, 0, 14, 0, [0, 0], [30, 0], [0, 0, 0, 0])
DISCOVERY_STOP = _call(False, 0, 16, 0)
PRELUDE = (
    _call(True, 1, 4248, 0),
    _call(True, 1, 4247, 0, [2, 0, 0, 0]),
    _call(True, 1, 4247, 0, [16, 0, 0, 0]),
    _call(False, 4, 180, 0),
    _call(False, 0, 482, 0),
    _call(False, 0, 484, 0),
    _call(False, 0, 493, 0),
)


def request(function: int, addr: str) -> str:
    """One bt-service request for `addr` (needs root); prints `<function> rc <code>`.

    Codes (Tizen bluetooth-api.h): 0 started, -9 internal, -21 not connected,
    -29 in progress. The result is not the connection state: check it with
    connected_query().
    """
    return (
        _call(False, 0, function, 1, _octets(addr))
        + f" 2>&1 | grep -o 'iv -*[0-9]*' | sed 's/^iv/{function} rc/'; true"
    )


def _quiet(call: str) -> str:
    return f"{call} >/dev/null 2>&1"


def connect(addr: str) -> str:
    """The menu's full connect (see above). Prints each 180's rc; ~3-6 s."""
    attempt = "; ".join(_quiet(call) for call in PRELUDE) + "; " + request(CONNECT, addr)
    return (
        f"{_quiet(DISCOVERY_START)}; sleep 1; {_quiet(DISCOVERY_STOP)}; "
        f'r=$({attempt}); echo "$r"; '
        f"if [ \"$r\" = '{CONNECT} rc -9' ]; then {attempt}; fi"
    )


def connected_query(addr: str) -> str:
    """Live connected-profile bitmask (bt_product 4180). Any uid may ask."""
    octets = ",".join(f"0x{o:02x}" for o in _octets(addr))
    return (
        "gdbus call --system --dest org.projectx.bt_product "
        "--object-path /org/projectx/bt_service_product "
        "--method org.projectx.bt_product.service_request_product 1 4180 0 "
        f"'[byte {octets}]' '@ay []' '@ay []' '@ay []' '@ay []'"
    )


def audio_connected(reply: str) -> bool:
    """True when the speaker's audio link is up: bit 0x04 of the third byte.

    Replies: (0, <[byte 0x00, 0x00, 0x0c, 0x00]>) connected with audio (menu
    connect, 2026-10-23/24); 0x08 alone = a partial link without audio (after
    a power loss, 2026-10-24: it read 0x08 for ~2 min, sound stayed on the TV
    speakers, then 0x00); 0x00 = not connected.
    """
    if not reply.strip().startswith("(0, <[byte"):
        return False
    values = re.findall(r"0x([0-9a-f]{2})", reply.split("[", 1)[1])
    return len(values) > 2 and bool(int(values[2], 16) & 0x04)
