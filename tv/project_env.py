"""Run tv/ scripts in the repo's uv environment (.venv), whatever Python started them.

Scripts are started as `tv/root.py ...` (shebang `/usr/bin/env python3`, often
the system Python) or through the `bin/` wrappers. Their dependencies (Pillow,
the vendored samsung_tv_root) live only in `.venv`, created by `uv sync`.

Also loads the repo's `.env` (KEY=value lines, not in git; template
`.env.example`) into the environment; variables already set win.
"""

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENV = REPO / ".venv"
ENV_FILE = REPO / ".env"

if ENV_FILE.is_file():
    for line in ENV_FILE.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def _tv_from_argv() -> str | None:
    """--tv NAME from the command line, if present.

    The TV is resolved at import time (module constants like TV, SERIAL,
    SOCKET_PATH), before argparse has seen anything, so the flag is read
    here. `--tv NAME` and `--tv=NAME` both work; the flag is left in argv and
    every parser declares it, so --help lists it. A subcommand's own argv is
    not touched: an unrelated "--tv" value is not a thing in this repo.
    """
    argv = sys.argv[1:]
    for index, value in enumerate(argv):
        if value == "--tv" and index + 1 < len(argv):
            return argv[index + 1].strip() or None
        if value.startswith("--tv="):
            return value.partition("=")[2].strip() or None
    return None


SELECTED_TV = _tv_from_argv()
if SELECTED_TV:
    os.environ["CF_TV_NAME"] = SELECTED_TV


def add_tv_argument(parser) -> None:
    """Declare --tv on a parser, so --help lists it and argparse accepts it.

    The value is already applied at import time (SELECTED_TV); this only keeps
    the flag legal. Every entry point that resolves a TV calls it, or argparse
    rejects the flag the module already honoured.
    """
    parser.add_argument(
        "--tv",
        metavar="NAME",
        help="which TV (default: devices.toml `default`; list them with tv/devices.py list)",
    )


def ensure() -> None:
    """Re-exec the running script under .venv/bin/python unless it already runs there."""
    if Path(sys.prefix).resolve() == VENV.resolve():
        return
    python = VENV / "bin" / "python"
    if not python.exists():
        sys.exit(f"{VENV} is missing: run `uv sync` in {VENV.parent} (README.md, Setup)")
    os.execv(python, [str(python), *sys.argv])


def tv_name(explicit: str | None = None) -> str | None:
    """The selected TV's name: --tv, $CF_TV_NAME, else devices.toml `default`.

    None means no name is selected and the caller should fall back to
    CF_TV_IP (the single-TV setup this repo had before devices.toml).
    """
    chosen = explicit or os.environ.get("CF_TV_NAME", "").strip()
    if chosen:
        return chosen
    registry = REPO / "devices.toml"
    if registry.is_file():
        import tomllib  # noqa: PLC0415  (stdlib, cheap, but only needed here)

        with registry.open("rb") as handle:
            return tomllib.load(handle).get("default")
    return None


def tv_ip(explicit: str | None = None) -> str:
    """The selected TV's LAN address.

    A name (--tv NAME, $CF_TV_NAME, devices.toml `default`) resolves through
    the registry in tv/devices.py, which re-finds a TV whose DHCP address
    moved. With no name selected, CF_TV_IP is used as before.
    """
    name = tv_name(explicit)
    if name:
        sys.path.insert(0, str(REPO / "tv"))
        import devices  # noqa: PLC0415  (needs the re-exec above for tomllib-free imports)

        return devices.resolve(name)[1]
    ip = os.environ.get("CF_TV_IP", "").strip()
    if not ip:
        sys.exit(
            "no TV selected: run `tv/devices.py scan` (creates devices.toml), "
            f"or set CF_TV_IP in {ENV_FILE if ENV_FILE.is_file() else '.env'}"
        )
    return ip


def channel_paths(address: str) -> tuple[Path, Path]:
    """(socket, log) of the root channel for one TV.

    One channel per TV: a `serve` process binds the socket for the TV it
    serves, and the clients (tv/root.py, tv/stream.py, bin/snap) must agree on
    the name. Keyed by address, the only thing both sides know when a `serve`
    starts, and the address a name resolves to is stable while that process
    runs.
    """
    return Path(f"/tmp/tvchan-{address}.sock"), Path(f"/tmp/tvchan-{address}.log")
