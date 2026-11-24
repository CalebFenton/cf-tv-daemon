#!/usr/bin/env python3
"""devices - name the TVs, find them again when their addresses move.

A TV's LAN address is not stable: DHCP hands out a new one after a lease
expires, a reboot, or a router restart. Its identity is stable: every Samsung
TV answers `GET http://<ip>:8001/api/v2/` without a login, and that JSON
carries a fixed `id` (`uuid:...`, the same value as `duid`/`udn`).

So the registry stores, per TV, a name and that fixed id, never the address as
the key. When a name is used, the stored address is tried first, then the /24
is scanned for the id. The scanned address is written back, so the scan pays
once per move.

`devices.toml` (not in git: `.env` holds no secrets but the layout is a local
choice) lives at `$CF_TV_DEVICES`, else `<repo>/devices.toml`. Create it with
`tv/devices.py scan`; `tv/devices.py list` shows it.

    [tv.qn85d]                 # the name every tool takes
    id = "uuid:..."            # the fixed identity; the key that survives DHCP
    address = "192.0.2.175" # last known, refreshed by a scan
    model = "QN75QN85DAFXZA"   # informational
    note = "living room"       # informational

Selection, in order: `--tv NAME`, `$CF_TV_NAME`, `default` in the file, then
`CF_TV_IP` from `.env` as a name-less fallback (a bare address still works and
matches the one-TV setup this repo had before).

Usage
-----
    tv/devices.py list                # the registry, with reachability
    tv/devices.py scan                # find TVs on the LAN, add/update the file
    tv/devices.py address qn85d       # the address to use now (scans if it moved)
    tv/devices.py current             # which name this shell selects
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO / "devices.toml"
API_PORT = 8001
SDB_PORT = 26101
PROBE_TIMEOUT = 1.2
SCAN_WORKERS = 128
# After a reboot sdbd answers ~10 s before the REST API that proves identity.
BOOT_API_WAIT = 30.0


def path() -> Path:
    """The registry file: $CF_TV_DEVICES, else <repo>/devices.toml."""
    configured = os.environ.get("CF_TV_DEVICES", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_PATH


def load() -> dict:
    """The parsed file: {"default": NAME|None, "tv": {name: {id, address, ...}}}."""
    file = path()
    if not file.is_file():
        return {"default": None, "tv": {}}
    with file.open("rb") as handle:
        data = tomllib.load(handle)
    televisions = data.get("tv") or {}
    return {"default": data.get("default"), "tv": televisions}


def _toml_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else json.dumps(name)


def save(registry: dict) -> None:
    """Write the registry back, stable order, one TV per table, keeping keys added by hand.

    Values go through json.dumps: a JSON string, number or boolean is also a
    valid TOML value, so a quote in a note cannot break the file.
    """
    lines = [
        "# TVs this repo drives. Written by `tv/devices.py scan`; edit names and",
        "# notes by hand. Keyed by the TV's fixed id, never by address: DHCP moves",
        "# the address, the id does not. Not in git (a local layout choice).",
        "",
    ]
    if registry.get("default"):
        lines += [f"default = {json.dumps(registry['default'])}", ""]
    for name in sorted(registry["tv"]):
        entry = registry["tv"][name]
        lines.append(f"[tv.{_toml_key(name)}]")
        first = [k for k in ("id", "address", "model", "note") if k in entry]
        for key in first + sorted(k for k in entry if k not in first):
            if entry[key] not in ("", None):
                lines.append(f"{_toml_key(key)} = {json.dumps(entry[key])}")
        lines.append("")
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines))


def api(address: str, timeout: float = 3.0) -> dict | None:
    """The TV's REST identity, or None when nothing answers there."""
    url = f"http://{address}:{API_PORT}/api/v2/"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except (OSError, ValueError):
        return None


def identity(address: str, timeout: float = 3.0) -> dict | None:
    """The fields the registry keys on: id, address, model, name."""
    payload = api(address, timeout)
    if not payload:
        return None
    device = payload.get("device") or {}
    tv_id = payload.get("id") or device.get("id") or device.get("duid")
    if not tv_id:
        return None
    return {
        "id": tv_id,
        "address": device.get("ip") or address,
        "model": device.get("modelName") or "",
        "name": device.get("name") or "",
    }


def local_address() -> str:
    """This computer's address on the TVs' LAN (routing table answer, no traffic)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 9))
        return str(sock.getsockname()[0])


def subnet() -> str:
    """The /24 this computer is on."""
    return local_address().rsplit(".", 1)[0]


def scan(hosts: str | None = None, workers: int = SCAN_WORKERS) -> list[dict]:
    """Every Samsung TV answering on API_PORT in the /24 (or a prefix like 192.168.19)."""
    prefix = hosts or subnet()
    addresses = [f"{prefix}.{n}" for n in range(1, 255)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        found = [f for f in pool.map(lambda a: identity(a, PROBE_TIMEOUT), addresses) if f]
    return sorted(found, key=lambda f: f["address"])


def resolve(name: str | None = None) -> tuple[str | None, str]:
    """(name, address) for the TV this command should use.

    The name comes from the argument, $CF_TV_NAME, the file's `default`, then
    - with no name at all - CF_TV_IP from `.env`. A name resolves through the
    registry: stored address first, a scan if the id is not there.
    """
    registry = load()
    chosen = name or os.environ.get("CF_TV_NAME", "").strip() or registry.get("default")

    if not chosen:
        address = os.environ.get("CF_TV_IP", "").strip()
        if not address:
            raise SystemExit(
                "no TV selected: run `tv/devices.py scan` to create devices.toml, "
                "pass --tv NAME, set CF_TV_NAME, or set CF_TV_IP in .env"
            )
        return None, address

    if chosen not in registry["tv"]:
        known = ", ".join(sorted(registry["tv"])) or "none"
        raise SystemExit(f"unknown TV {chosen!r}; devices.toml has: {known}. Run `tv/devices.py scan` to find it.")

    entry = registry["tv"][chosen]
    stored = entry.get("address")
    found = scan_for(entry.get("id"), stored)
    if found:
        if found != stored:
            entry["address"] = found
            save(registry)
        return chosen, found
    raise SystemExit(
        f"TV {chosen!r} ({entry.get('id')}) is not answering on {subnet()}.0/24; "
        "is it off? Check with `tv/devices.py list`."
    )


def _usable(address: str, tv_id: str | None) -> bool:
    """Does the TV with this id answer at this address?

    A booting TV answers on sdb before its REST API. An open sdb port does not
    say which TV it is (DHCP may have given the address to another one), so
    while sdb is open the API is polled for up to BOOT_API_WAIT instead.
    """
    deadline = time.monotonic() + BOOT_API_WAIT
    while True:
        found = identity(address, PROBE_TIMEOUT)
        if found:
            return not tv_id or found["id"] == tv_id
        if time.monotonic() >= deadline or not _sdb_open(address):
            return False
        time.sleep(1)


def _sdb_open(address: str, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((address, SDB_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def scan_for(tv_id: str | None, preferred: str | None = None) -> str | None:
    """The address of `tv_id` on this LAN, scanning only if the last known one fails."""
    if not tv_id:
        return preferred if preferred and identity(preferred, PROBE_TIMEOUT) else None
    if preferred and _usable(preferred, tv_id):
        return preferred
    for found in scan():
        if found["id"] == tv_id:
            return found["address"]
    return None


def reachable(address: str) -> dict:
    """What answers at this address: the REST API, sdb."""
    detail = identity(address, 1.5)
    sdb = _sdb_open(address)
    return {
        "api": bool(detail),
        "sdb": sdb,
        "model": (detail or {}).get("model", ""),
        "name": (detail or {}).get("name", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list")
    scan_cmd = sub.add_parser("scan")
    scan_cmd.add_argument("--hosts", help="address prefix, default this computer's /24")
    scan_cmd.add_argument("--prune", action="store_true", help="drop TVs in the file that did not answer")
    address_cmd = sub.add_parser("address")
    address_cmd.add_argument("name", nargs="?")
    sub.add_parser("current")
    set_default = sub.add_parser("default")
    set_default.add_argument("name")
    args = parser.parse_args()

    if args.command == "scan":
        found = scan(args.hosts)
        registry = load()
        by_id = {entry["id"]: name for name, entry in registry["tv"].items() if entry.get("id")}
        for tv in found:
            name = by_id.get(tv["id"])
            if name is None:
                base = (tv["model"] or "tv").lower().replace("-", "")
                name, n = base, 2
                while name in registry["tv"]:
                    name, n = f"{base}-{n}", n + 1
                registry["tv"][name] = {"id": tv["id"], "model": tv["model"]}
                print(f"added    {name:12} {tv['address']:15} {tv['model']}")
            registry["tv"][name]["address"] = tv["address"]
            if registry["tv"][name].get("model") != tv["model"] and tv["model"]:
                registry["tv"][name]["model"] = tv["model"]
        seen = {tv["id"] for tv in found}
        if args.prune:
            for name, entry in list(registry["tv"].items()):
                if entry.get("id") not in seen:
                    print(f"pruned   {name:12} (not on this LAN)")
                    del registry["tv"][name]
        if not registry.get("default") and len(registry["tv"]) == 1:
            registry["default"] = next(iter(registry["tv"]))
            print(f"default  {registry['default']}")
        save(registry)
        if not found:
            print(f"no Samsung TV answered on {args.hosts or subnet()}.0/24 port {API_PORT}")
        return 0 if found else 1

    if args.command == "list":
        registry = load()
        if not registry["tv"]:
            print(f"no TVs in {path()}: run `tv/devices.py scan`")
            return 1
        for name in sorted(registry["tv"]):
            entry = registry["tv"][name]
            address = entry.get("address", "?")
            state = reachable(address)
            live = "up  " if state["api"] else "down"
            sdb = "sdb" if state["sdb"] else "   "
            mark = "*" if name == registry.get("default") else " "
            model = entry.get("model") or state["model"]
            note = f"  {entry['note']}" if entry.get("note") else ""
            print(f"{mark}{name:12} {address:15} {live} {sdb} {model}{note}")
        return 0

    if args.command == "address":
        _name, address = resolve(args.name)
        print(address)
        return 0

    if args.command == "current":
        name, address = resolve(None)
        print(f"{name or '(CF_TV_IP)'} {address}")
        return 0

    if args.command == "default":
        registry = load()
        if args.name not in registry["tv"]:
            print(f"unknown TV {args.name!r}", file=sys.stderr)
            return 1
        registry["default"] = args.name
        save(registry)
        print(f"default = {args.name}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
