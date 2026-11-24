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
    """--tv NAME or --tv=NAME from anywhere in argv.

    Read before argparse runs, because modules resolve their TV at import
    time (constants like TV and SOCKET_PATH). Exported as CF_TV_NAME, so child
    processes select the same TV.
    """
    argv = sys.argv[1:]
    for index, value in enumerate(argv):
        if value == "--tv" and index + 1 < len(argv):
            return argv[index + 1].strip() or None
        if value.startswith("--tv="):
            return value.partition("=")[2].strip() or None
    return None


if selected := _tv_from_argv():
    os.environ["CF_TV_NAME"] = selected


def add_tv_argument(parser) -> None:
    """Declare --tv so argparse accepts and documents the flag _tv_from_argv already applied."""
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


def tv_ip() -> str:
    """The selected TV's LAN address (tv/devices.py resolve: --tv, CF_TV_NAME, `default`, CF_TV_IP)."""
    sys.path.insert(0, str(REPO / "tv"))
    import devices  # noqa: PLC0415  (urllib and a thread pool; only callers that need a TV pay)

    return devices.resolve()[1]


def channel_paths(address: str) -> tuple[Path, Path]:
    """(socket, log) of the root channel for one TV; `serve` and its clients both derive them from the address."""
    return Path(f"/tmp/tvchan-{address}.sock"), Path(f"/tmp/tvchan-{address}.log")
