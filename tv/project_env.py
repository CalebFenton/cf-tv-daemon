"""Run tv/ scripts in the repo's uv environment (.venv), whatever Python started them.

Scripts are started as `tv/root.py ...` (shebang `/usr/bin/env python3`, often
the system Python) or through the `bin/` wrappers. Their dependencies (Pillow,
the vendored samsung_tv_root) live only in `.venv`, created by `uv sync`.

Also loads the repo's `.env` (KEY=value lines, not in git; template
`.env.example`) into the environment; variables already set win.
"""

import argparse
import functools
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


def add_tv_argument(parser: argparse.ArgumentParser) -> None:
    """Declare --tv; add it to the top parser and, via a parent, to each subparser.

    SUPPRESS as the default keeps a subparser from overwriting a value given
    before the subcommand, so --tv works on either side of it.
    """
    parser.add_argument(
        "--tv",
        metavar="NAME",
        default=argparse.SUPPRESS,
        help="which TV (default: devices.toml `default`; list them with tv/devices.py list)",
    )


def select(args: argparse.Namespace) -> None:
    """Apply a parsed --tv before anything calls tv(); child processes inherit it as CF_TV_NAME."""
    if name := getattr(args, "tv", None):
        os.environ["CF_TV_NAME"] = name
        _selection.cache_clear()


def ensure() -> None:
    """Re-exec the running script under .venv/bin/python unless it already runs there."""
    if Path(sys.prefix).resolve() == VENV.resolve():
        return
    python = VENV / "bin" / "python"
    if not python.exists():
        sys.exit(f"{VENV} is missing: run `uv sync` in {VENV.parent} (README.md, Setup)")
    os.execv(python, [str(python), *sys.argv])


def _devices():
    sys.path.insert(0, str(REPO / "tv"))
    import devices  # noqa: PLC0415  (only callers that need a TV pay for urllib)

    return devices


@functools.cache
def _selection() -> tuple[str | None, str]:
    """(name, address) of the selected TV: tv/devices.py resolve (--tv, CF_TV_NAME, `default`, CF_TV_IP).

    Resolved on first use, once per process: it is an HTTP request, and a LAN
    discovery when the TV's address moved.
    """
    return _devices().resolve()


def tv() -> str:
    """The selected TV's LAN address."""
    return _selection()[1]


def tv_settings() -> dict:
    """The selected TV's devices.toml entry (`speaker`, `note`, ...); empty when selected by CF_TV_IP."""
    name = _selection()[0]
    return _devices().load()["tv"].get(name, {}) if name else {}


def runtime_dir() -> Path:
    """This user's runtime files: $XDG_RUNTIME_DIR/cf-tv (mode 0700, per user, tmpfs).

    Not /tmp: there another local user could create the channel socket first
    and receive the root commands meant for the TV.
    """
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if not Path(base).is_dir():
        sys.exit(f"no per-user runtime directory ({base}); set XDG_RUNTIME_DIR")
    directory = Path(base) / "cf-tv"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def tv_dir() -> Path:
    """The selected TV's runtime files: the channel's socket, log and state, the probe cache."""
    directory = runtime_dir() / tv()
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def channel_socket() -> Path:
    """The socket `tv/chan.py serve` listens on for the selected TV."""
    return tv_dir() / "chan.sock"


def channel_log() -> Path:
    return tv_dir() / "chan.log"
