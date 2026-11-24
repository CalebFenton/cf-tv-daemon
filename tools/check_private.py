#!/usr/bin/env python3
"""check_private - fail when a file to be committed holds something that identifies the owner.

This repository is public. Two kinds of rule:

- Built in: hardware addresses and device ids, which identify a TV or a speaker
  whoever owns it: a MAC address, a `uuid:` device id. Placeholders pass:
  `AA:BB:CC:DD:EE:xx` for a MAC, all zeros for a uuid.
- `.local/private-patterns`, one regex per line (case-insensitive, `#` starts a
  comment): the owner's LAN, names, hosts, paths. It stays untracked, because
  it lists what it guards; without it only the built-in rules run.

Run by prek as the `private-text` hook; by hand: tools/check_private.py FILE...
Values that belong to a device live in `.local/devices.toml` instead.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERNS = Path(__file__).resolve().parent.parent / ".local" / "private-patterns"

MAC = re.compile(r"(?<![0-9a-f:])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f:])", re.I)
MAC_PLACEHOLDER = re.compile(r"aa:bb:cc:dd:ee:[0-9a-f]{2}|00(?::00){5}|ff(?::ff){5}", re.I)
UUID = re.compile(r"uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
UUID_PLACEHOLDER = "uuid:00000000-0000-0000-0000-000000000000"


def private_patterns() -> list[re.Pattern]:
    if not PATTERNS.is_file():
        return []
    lines = (line.strip() for line in PATTERNS.read_text().splitlines())
    return [re.compile(line, re.I) for line in lines if line and not line.startswith("#")]


def findings(text: str, patterns: list[re.Pattern]) -> list[str]:
    found = [m for m in MAC.findall(text) if not MAC_PLACEHOLDER.fullmatch(m)]
    found += [m for m in UUID.findall(text) if m.lower() != UUID_PLACEHOLDER]
    found += [m.group(0) for p in patterns if (m := p.search(text))]
    return found


def main() -> int:
    patterns = private_patterns()
    hits = []
    for name in sys.argv[1:]:
        try:
            lines = Path(name).read_text(errors="replace").splitlines()
        except (IsADirectoryError, FileNotFoundError):
            continue
        for number, line in enumerate(lines, 1):
            if found := findings(line, patterns):
                hits.append(f"{name}:{number}: {', '.join(found)}")
    if hits:
        print("Identifying text (tools/check_private.py; move it to .local/):", *hits, sep="\n", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
