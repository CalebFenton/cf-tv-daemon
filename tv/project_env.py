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


def ensure() -> None:
    """Re-exec the running script under .venv/bin/python unless it already runs there."""
    if Path(sys.prefix).resolve() == VENV.resolve():
        return
    python = VENV / "bin" / "python"
    if not python.exists():
        sys.exit(f"{VENV} is missing: run `uv sync` in {VENV.parent} (README.md, Setup)")
    os.execv(python, [str(python), *sys.argv])


def tv_ip() -> str:
    """The TV's LAN address: CF_TV_IP, from the environment or `.env`."""
    ip = os.environ.get("CF_TV_IP", "").strip()
    if not ip:
        sys.exit(f"CF_TV_IP is not set: copy {REPO / '.env.example'} to {ENV_FILE} and set the TV's address")
    return ip
