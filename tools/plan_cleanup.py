#!/usr/bin/env python3
"""Generate guard-checked apply/restore SQL for the TV app registry.

Adapted from jss8649/quiet-tizen (MIT). Reads a snapshot, refuses to plan from a
corrupt database, requires exactly one row per app, and embeds a drift guard so
the transaction aborts rather than overwriting a value someone else changed.
"""
import argparse, json, os, re, sqlite3
from pathlib import Path

FIELDS = ("app_disable", "app_onboot", "app_autorestart")
DISABLED = ("true", "false", "false")
DB = "/opt/dbspace/.pkgmgr_parser.db"


def literal(v):
    if v is None:
        return "NULL"
    if v not in ("true", "false"):
        raise ValueError(f"Unexpected registry boolean: {v!r}")
    return "'" + v + "'"


def validate_id(app_id):
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", app_id):
        raise ValueError(f"Invalid application ID: {app_id!r}")
    return "'" + app_id + "'"


def read_rows(database, ids):
    uri = database.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("Snapshot integrity check failed.")
        rows = []
        for app_id in ids:
            validate_id(app_id)
            found = conn.execute(
                "SELECT app_disable,app_onboot,app_autorestart FROM package_app_info WHERE app_id=?",
                (app_id,)).fetchall()
            if len(found) != 1:
                raise ValueError(f"{app_id}: expected one row, found {len(found)}")
            for v in found[0]:
                literal(v)
            rows.append((app_id, tuple(found[0])))
        return rows


def make_sql(rows, restore=False):
    lines = ["BEGIN IMMEDIATE;", "CREATE TEMP TABLE guard(ok INTEGER CHECK(ok=1));"]
    for app_id, original in rows:
        expected = DISABLED if restore else original
        cond = " AND ".join(f"{k} IS {literal(v)}" for k, v in zip(FIELDS, expected))
        lines.append(f"INSERT INTO guard SELECT count(*)=1 FROM package_app_info "
                     f"WHERE app_id={validate_id(app_id)} AND {cond};")
    for app_id, original in rows:
        values = original if restore else DISABLED
        sets = ",".join(f"{k}={literal(v)}" for k, v in zip(FIELDS, values))
        lines.append(f"UPDATE package_app_info SET {sets} WHERE app_id={validate_id(app_id)};")
    lines += ["DROP TABLE guard;", "COMMIT;"]
    return "\n".join(lines) + "\n"


def make_shell(sql):
    return f"""#!/bin/sh
set -eu
test "$(id -u)" = 0
test "$(sqlite3 -readonly {DB} 'PRAGMA quick_check;')" = ok
sqlite3 -bail {DB} <<'GEN_SQL'
{sql}GEN_SQL
test "$(sqlite3 -readonly {DB} 'PRAGMA quick_check;')" = ok
echo 'Registry transaction complete. This does not terminate running processes.'
"""


def main():
    os.umask(0o077)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path, required=True)
    ap.add_argument("--apps", type=Path, required=True, help="file with one app_id per line")
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    ids = [l.strip() for l in a.apps.read_text().splitlines() if l.strip() and not l.startswith("#")]
    if len(ids) != len(set(ids)):
        ap.error("duplicate app IDs")
    rows = read_rows(a.snapshot, ids)
    out = a.output.resolve()
    if out.exists():
        ap.error("output dir exists; preserve previous rollback records")
    out.mkdir(parents=True, mode=0o700)
    for action in ("apply", "restore"):
        sql = make_sql(rows, restore=(action == "restore"))
        (out / f"{action}.sql").write_text(sql)
        (out / f"{action}.sh").write_text(make_shell(sql))
    (out / "before.json").write_text(json.dumps(
        [{"app_id": i, **dict(zip(FIELDS, v))} for i, v in rows], indent=2) + "\n")
    print(f"Generated {len(rows)} targeted changes in {out}")
    print("Review apply.sql and restore.sql before use.")


if __name__ == "__main__":
    main()
