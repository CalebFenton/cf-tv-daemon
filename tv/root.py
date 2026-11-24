#!/usr/bin/env python3
"""root - run commands on the TV as root, reboot it, and see what happened.

Paths, in order of preference (docs/01-root/access-contexts.md):

  channel  tv/chan.py's agent socket, ~10 ms/command; only while
           `tv/chan.py serve` runs on this host.
  daemon   cf_tv_daemon, started at every boot by the awk payload. Requests
           go through an sdb injection (uid 901) into /opt/cf_tv_daemon/state.
           ~0.75 s/command, System::Privileged.
  exploit  archive-root through sdb, ~7 s, label User. Recovery only (e.g.
           after a firmware update removed the boot payload): never chosen on
           its own, only with --via exploit.

No silent fallback: when the chosen path fails, the command is not retried
elsewhere (it may already have run), and when no path works, the error says
why and what to do. The host's sdb server is restarted automatically when it
holds a dead link (after every TV reboot).

Usage
-----
    tv/root.py exec 'id'                     # channel, else daemon
    tv/root.py exec 'a' 'b'                  # several commands, in order
    tv/root.py exec --timeout 60 'long cmd'  # per-command limit (daemon path)
    tv/root.py exec --via exploit 'id'       # force a path
    tv/root.py probe                         # which paths work right now
    tv/root.py doctor                        # health check, exit 1 on FAIL

    tv/root.py reboot [--report]  # reboot, wait until usable, time each stage
    tv/root.py report             # this boot on one timeline: daemon, hooks,
                                  # logging, speaker
    tv/root.py dlog arm           # Bluetooth dlog for the next boot only
    tv/root.py dlog fetch         # pull it to .local/dlog/ (or: disarm)

    tv/root.py boot install    # deploy payload/ (see payload/README.md), lock
                               # permissions down, restart cf_tv_daemon
    tv/root.py boot status     # device files vs repo: md5, modes, immutable flag
    tv/root.py boot restore    # vendor parseinfo.sh back (do this before a
                               # firmware update: it clears the immutable flag)
    tv/root.py hooks wake      # run the on-wake.d hooks now (or: boot)
    tv/root.py hooks list | enable NAME | disable NAME

Probe results are cached in /tmp/rootctl.probe for 30 s, tied to the sdb link
state.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import project_env

project_env.ensure()

ROOT = Path(__file__).resolve().parent.parent
# find_sdb() only searches the SDB env var; point it at the vendored binary
# before anything imports samsung_tv_root.
_LOCAL_SDB = ROOT / "vendor" / "sdb" / "data" / "tools" / "sdb"
if _LOCAL_SDB.is_file():
    os.environ["SDB"] = str(_LOCAL_SDB)  # authoritative; a stale
    # exported SDB must not win
SDB = ROOT / "vendor" / "sdb" / "data" / "tools" / "sdb"
TV = project_env.tv_ip()
SERIAL = f"{TV}:26101"
# Per TV: two TVs on one host must not share an answer, and the address can
# change under us (tv/devices.py re-finds it), so the key is the address.
PROBE_CACHE = Path(f"/tmp/rootctl.probe-{TV}")
# Must equal tv/chan.py's paths: that is the server this client talks to.
CHANNEL_SOCKET, CHANNEL_LOG = project_env.channel_paths(TV)
PROBE_TTL = 30.0

CMD_QUEUE = "/opt/cmdq"
CMD_OUT = "/opt/cmdout"
PAYLOAD = "/opt/etc/parseinfo.sh"
# The TV side of this project: launcher, daemon, hook runner, hooks, Chattr.
# The daemon's runtime state lives in RUN_DIR below it.
KIT = "/opt/cf_tv_daemon"
DAEMON_START = f"/usr/bin/awk -f {KIT}/cf_tv_daemon_start.awk"
PAYLOAD_BACKUP = f"{KIT}/parseinfo.ORIGINAL.sh"
HOOK_RUNNER = f"{KIT}/cf_tv_hooks.sh"
CHATTR = f"/usr/bin/dotnet {KIT}/chattr/Chattr.dll"
# /opt/cmdq is still read by parseinfo.sh at boot (each line runs as root):
# a manual recovery path that needs only uid 901 and a reboot. There is no
# "run it now" boot path any more: restarting AT-HUB as uid 901 is refused
# ("Failed to start AT-HUB.service: Access denied", 2026-10-25).


# ---------------------------------------------------------------- sdb helpers


def sdb_available() -> bool:
    """Is the TV's sdbd port answering at all (TV up, network up)?"""
    if not SDB.is_file():
        return False
    try:
        with socket.create_connection((TV, 26101), timeout=2):
            return True
    except OSError:
        return False


SDB_LOCK = Path("/tmp/tv-sdb.lock")


def sdb_ensure(attempts: int = 4) -> str | None:
    """Make the host sdb server hold a live link to the TV; None when it does.

    After a TV reboot the host's sdb server keeps the dead link: `sdb connect`
    then fails ("failed to connect") although port 26101 answers, until the
    server is restarted (seen after every reboot on 2026-10-25). So on failure:
    `sdb kill-server`, wait, try again. Returns the last error otherwise.

    One process at a time (flock on SDB_LOCK): two repairers restart the
    server under each other. tv/chan.py's keepalive and `tv/root.py reboot`
    did, and sdb took 59 s instead of ~24 s to come back (2026-10-25).
    """
    with SDB_LOCK.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        last = ""
        for attempt in range(attempts):
            result = subprocess.run((str(SDB), "connect", SERIAL), capture_output=True, text=True, timeout=20)
            out = result.stdout + result.stderr
            if "connected to" in out or "already connected" in out:
                return None
            last = out.strip().splitlines()[-1] if out.strip() else f"exit {result.returncode}"
            if not sdb_available():
                return f"TV not reachable on {TV}:26101 (off, rebooting, or network down)"
            subprocess.run((str(SDB), "kill-server"), capture_output=True, timeout=20)
            time.sleep(2 + attempt)
        return f"sdb connect failed after {attempts} tries with server restarts: {last}"


def inject(command: str, timeout: float = 25.0) -> tuple[bool, str]:
    """Run a shell command through the sdb appinstall injection.

    This is the primitive the exploit itself uses to launch its payload. It
    needs no root: it abuses package-name parsing in sdb's appinstall path. It
    runs as the sdb shell: uid 901 `sdk`, label `System`, no capabilities.
    That is enough to submit to the daemon queue, which is sdk-owned.

    Output comes back over a TCP callback because plain `sdb shell` does not
    return it. sdbd runs one injection at a time: a slow one delays the next.
    """
    error = sdb_ensure()
    if error:
        return False, error
    # Imported here: SdbClient pulls in the vendored package (~0.3 s), which
    # most callers of this module never need.
    from samsung_tv_root.sdb import SdbClient, find_sdb  # noqa: PLC0415

    client = SdbClient(find_sdb(), TV, timeout=timeout)
    try:
        client.connect()
        result = client.capture(command, timeout=timeout)
        return True, result.output
    except Exception as error:
        return False, f"{type(error).__name__}: {error}"


# ---------------------------------------------------------------- daemon path

# Kept out of the lockdown in install_commands: req/ stage/ out/ rc/ are sdk:sdk.
RUN_DIR = f"{KIT}/state"


DAEMON_FRESH = 15.0  # seconds; the loop beats every ~0.2 s, slower under boot load


def daemon_heartbeat_age() -> tuple[float | None, str]:
    """(seconds since the daemon's last heartbeat, detail); None if unknown.

    Liveness must come from the heartbeat, not from the existence of the pid
    file or the queue directories: both survive a reboot. Both numbers come
    from the TV's own boot clock, so host/TV clock skew cannot confuse this.
    """
    ok, output = inject(f'test -f {RUN_DIR}/heartbeat && cat {RUN_DIR}/heartbeat || echo 0; cut -d" " -f1 /proc/uptime')
    if not ok:
        return None, output
    try:
        body = output.split("[exit:0]")[0].strip().split("\n")
        beat = float(body[0].strip())
        uptime = float(body[1].strip().split()[0])
    except (ValueError, IndexError):
        return None, f"unreadable heartbeat: {output[:120]!r}"
    if beat <= 0 or beat > uptime:
        return None, "no heartbeat since this boot"
    return uptime - beat, ""


def daemon_available() -> bool:
    age, _ = daemon_heartbeat_age()
    return age is not None and age < DAEMON_FRESH


STAGE_DIR = f"{RUN_DIR}/stage"


def _daemon_submit_script(job: str, command: str) -> str:
    """Shell (run as uid 901) that places `command` in the queue as `job`.

    The daemon reads only the first line of a request file, so the command is
    written whole to stage/<job> (base64 in transit: no quoting problems) and
    the request is a one-line `eval` of it. The request is written under a
    dot-name and renamed, because the daemon's `ls` skips dotfiles: it never
    sees a partial request. Size is not a concern: SdbClient pushes oversized
    injections as a script, which works for uid 901.
    """
    encoded = base64.b64encode(command.encode()).decode()
    request = f'eval "$(cat {STAGE_DIR}/{job})"'
    return (
        f"cd {RUN_DIR} && mkdir -p stage && "
        f"printf %s {encoded} | base64 -d > stage/{job} && "
        f"printf %s {shell_quote(request)} > req/.{job} && mv req/.{job} req/{job} || "
        f"{{ echo '<<<SUBMIT FAILED>>>'; exit 0; }}; "
    )


# How long one injection waits for a result. sdbd runs one injection at a time,
# so this is also how long a long-running command can hold up other callers.
COLLECT_STEP = 3


def _daemon_collect_script(job: str) -> str:
    """Shell that waits up to COLLECT_STEP s for `job`, then prints its result or <<<PENDING>>>.

    rc is written last, so rc existing means out is complete.
    """
    return (
        f"cd {RUN_DIR}; i=0; while [ $i -lt {COLLECT_STEP * 20} ]; do "
        f"test -s rc/{job} && break; sleep 0.05; i=$((i+1)); done; "
        f'test -s rc/{job} || {{ echo "<<<PENDING>>>"; exit 0; }}; '
        f'cat out/{job}; printf "\\n<<<RC>>>\\n"; cat rc/{job}; '
        f"rm -f out/{job} rc/{job} stage/{job}"
    )


def daemon_exec(commands: list[str], timeout: float = 20.0) -> tuple[bool, str]:
    """Submit commands to the root daemon and collect their output.

    One request per command; each is collected before the next is submitted,
    so commands run in order. `timeout` bounds each command's runtime. The
    submit and the first wait share one injection (one sdb round trip for a
    quick command); a longer command is polled in further COLLECT_STEP waits.
    """
    token = os.urandom(4).hex()
    statuses: list[int] = []
    stream: list[str] = []

    for index, command in enumerate(commands):
        job = f"{token}{index}"
        script = _daemon_submit_script(job, command) + _daemon_collect_script(job)
        deadline = time.monotonic() + timeout
        while True:
            ok, output = inject(script, timeout=COLLECT_STEP + 20)
            if not ok:
                return False, output
            body = output.split("[exit:0]")[0]
            if "<<<SUBMIT FAILED>>>" in body:
                return False, f"could not queue {command!r}: {body.strip()[:200]}"
            if "<<<PENDING>>>" not in body or time.monotonic() > deadline:
                break
            script = _daemon_collect_script(job)

        if "<<<RC>>>" not in body:
            return False, f"no result for {command!r} after {timeout:.0f}s: {body[:200]}"
        stdout_part, rc_part = body.split("<<<RC>>>", 1)
        stream.append(stdout_part.rstrip("\n"))
        try:
            statuses.append(int(rc_part.split()[0]))
        except (IndexError, ValueError):
            statuses.append(-1)

    text = "\n".join(chunk for chunk in stream if chunk != "")
    return True, text + ("\n" if text else "") + status_marker(statuses)


def status_marker(codes: list[int]) -> str:
    """`[exit N]` for each failed command (-1 = unknown), on one last line.

    All three paths append it; cmd_exec turns it into a non-zero exit.
    """
    bad = [code for code in codes if code not in (0, -1)]
    return (" ".join(f"[exit {code}]" for code in bad) + "\n") if bad else ""


def any_failed(output: str) -> bool:
    lines = output.rstrip("\n").splitlines()
    return bool(lines) and re.fullmatch(r"(\[exit -?\d+\] ?)+", lines[-1].strip()) is not None


def shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def field(lines: list[str], key: str) -> str:
    """The rest of the first line that starts with `key `, or ""."""
    return next((line[len(key) + 1 :] for line in lines if line.startswith(key + " ")), "")


# ---------------------------------------------------------------- channel path


def channel_available() -> bool:
    if not CHANNEL_SOCKET.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(str(CHANNEL_SOCKET))
            sock.sendall(b'{"op":"status"}\n')
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        response = json.loads(data.decode())
        return bool(response.get("ok") and response.get("up"))
    except Exception:
        return False


def channel_exec(commands: list[str]) -> tuple[bool, str]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(60)
            sock.connect(str(CHANNEL_SOCKET))
            sock.sendall((json.dumps({"op": "exec", "commands": commands}) + "\n").encode())
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        response = json.loads(data.decode())
        if not response.get("ok"):
            return False, ": ".join(filter(None, [str(response.get("error")), response.get("detail")]))
        results = response["results"]
        return True, (
            "".join(r["stdout"] + r["stderr"] for r in results)
            + status_marker([r.get("exit_code", -1) for r in results])
        )
    except Exception as error:
        return False, f"{type(error).__name__}: {error}"


def channel_start() -> bool:
    """Bring the persistent channel up in the background (a no-op if a server runs).

    The log is appended to: a second `serve` exits at once, but truncating the
    file under a running server would garble its log.
    """
    env = dict(os.environ, SDB=str(SDB))
    log = open(CHANNEL_LOG, "a")
    subprocess.Popen(
        [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "tv" / "chan.py"), "serve"],
        cwd=str(ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.time() + 70
    while time.time() < deadline:
        if channel_available():
            return True
        time.sleep(2)
    return False


# ---------------------------------------------------------------- exploit path


def exploit_exec(commands: list[str]) -> tuple[bool, str]:
    env = dict(os.environ, SDB=str(SDB))
    args = [
        str(ROOT / ".venv" / "bin" / "python"),
        "-m",
        "samsung_tv_root",
        "--sdb",
        str(SDB),
        "--output",
        "json",
        "archive-root",
        "root",
        TV,
        "--payload-directory",
        str(ROOT / "build" / "payloads-netcoreapp3.1"),
    ]
    for command in commands:
        args.extend(("--command", command))
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=240, cwd=str(ROOT / "vendor" / "samsung-tv-root-source"), env=env
    )
    start = result.stdout.find("{")
    if start < 0:
        return False, (result.stderr or result.stdout).strip()[:400]
    try:
        evidence = json.loads(result.stdout[start:])
    except json.JSONDecodeError as error:
        return False, f"bad JSON: {error}"
    if not evidence.get("authenticated"):
        return False, "not authenticated"
    entries = evidence.get("commands", [])
    return True, (
        "".join(e.get("stdout", "") + e.get("stderr", "") for e in entries)
        + status_marker([e.get("exit_code", -1) for e in entries])
    )


# ---------------------------------------------------------------- probing


def probe(force: bool = False) -> dict:
    """Which root paths work now.

    Cached 30 s, tied to the sdb link state: an answer from before a reboot
    must not send commands down a dead path.
    """
    reachable = sdb_available()
    if not force and PROBE_CACHE.exists():
        try:
            cached = json.loads(PROBE_CACHE.read_text())
            fresh = time.time() - cached.get("at", 0) < PROBE_TTL
            if fresh and bool(cached.get("sdb")) == reachable:
                return cached
        except Exception:
            pass

    result: dict = {"at": time.time(), "sdb": reachable, "channel": channel_available()}
    if reachable:
        result["sdb_error"] = sdb_ensure()
        if not result["sdb_error"]:
            age, detail = daemon_heartbeat_age()
            result["daemon_heartbeat_age"] = age
            result["daemon_detail"] = detail
            result["daemon"] = age is not None and age < DAEMON_FRESH
    PROBE_CACHE.write_text(json.dumps(result, indent=1))
    return result


def pick(result: dict, wanted: str | None) -> str | None:
    """The forced path, else the channel (~10 ms/command), else the daemon (~1 s).

    Never the exploit on its own: it is slow, runs as `User`, and is only for
    recovery, so it has to be asked for (--via exploit).
    """
    if wanted:
        return wanted
    if result.get("channel"):
        return "channel"
    if result.get("daemon"):
        return "daemon"
    return None


def explain(result: dict) -> str:
    """Why no root path is usable, and what to do."""
    if not result.get("sdb"):
        return f"TV not reachable on {TV}:26101: off, still booting, or network down."
    if result.get("sdb_error"):
        return result["sdb_error"]
    age = result.get("daemon_heartbeat_age")
    state = f"heartbeat {age:.0f} s old" if age is not None else result.get("daemon_detail") or "no heartbeat"
    return (
        f"root daemon not responding ({state}); channel not running.\n"
        "  restart the daemon:  tv/root.py exec --via exploit "
        f"'{DAEMON_START}'\n"
        "  or reboot the TV; tv/root.py report shows what happened at boot."
    )


def run_on(path: str, commands: list[str], timeout: float) -> tuple[bool, str]:
    if path == "channel":
        return channel_exec(commands)
    if path == "daemon":
        return daemon_exec(commands, timeout=timeout)
    if path == "exploit":
        return exploit_exec(commands)
    return False, f"unknown path {path!r}"


# ---------------------------------------------------------------- commands


def cmd_exec(args) -> int:
    """Exit status like ssh.

    The first failing command's status, 0 if all succeeded, 255 if the command
    could not be run or its result is lost.
    """
    state = probe()
    via = pick(state, args.via)
    if via is None:
        print(explain(state), file=sys.stderr)
        return 255
    ok, output = run_on(via, args.commands, args.timeout)
    if ok:
        sys.stdout.write(output)
        if any_failed(output):
            code = int(re.search(r"-?\d+", output.rstrip("\n").splitlines()[-1]).group())
            return code if 0 < code < 255 else 1
        return 0
    # No retry on another path: the command may already have run.
    PROBE_CACHE.unlink(missing_ok=True)
    print(f"{via} path failed: {output.strip()}", file=sys.stderr)
    return 255


def cmd_probe(args) -> int:
    state = probe(force=True)
    print(json.dumps(state, indent=1))
    via = pick(state, None)
    print(f"\npreferred path: {via}" if via else f"\nno path: {explain(state)}")
    return 0


# ---------------------------------------------------------------- payload

PAYLOAD_DIR = ROOT / "payload"
CHATTR_BUILD = ROOT / "build" / "chattr"
CAPTURE_BUILD = ROOT / "build" / "capture"
# C# tools `boot install` deploys: build dir -> project in apps/. Built on first
# use (recipe: docs/03-video/app-build.md); build/ is not in git.
APP_BUILDS = {CHATTR_BUILD: "chattr.csproj", CAPTURE_BUILD: "capture-probe.csproj"}

# Expected ownership/mode after `boot install` (docs/06-security/threat-model.md).
# Numeric ids, not names: the exploit path runs inside the archive payload's
# mount namespace, whose synthetic /etc/passwd names uid 0 `owner` and has no
# `root` or `sdk` entry. `chown root:root` fails there; `chown 0:0` works
# everywhere. uid 0 is `root` in the device's real passwd.
LOCKDOWN = {
    PAYLOAD: "755 0:0",
    KIT: "755 0:0",
    RUN_DIR: "755 0:0",
    f"{RUN_DIR}/req": "700 901:901",
    f"{RUN_DIR}/stage": "700 901:901",
    f"{RUN_DIR}/out": "700 901:901",
    f"{RUN_DIR}/rc": "700 901:901",
    CMD_QUEUE: "600 901:901",
    CMD_OUT: "600 901:901",
    "/opt/masklist": "644 0:0",
    # The channel runs CaptureProbe.dll from here as root: a writable directory
    # would let any app swap it. Was 0777 until 2026-10-25.
    "/opt/cap": "755 0:0",
}


def payload_files() -> list[tuple[Path, str, str]]:
    """(repo file, device path, mode) for everything `boot install` deploys.

    With it, a wiped TV needs nothing from an earlier session.

    Chattr comes first: the parseinfo.sh step needs it on the device.
    """
    files = [
        (CHATTR_BUILD / "Chattr.dll", f"{KIT}/chattr/Chattr.dll", "644"),
        (CHATTR_BUILD / "Chattr.runtimeconfig.json", f"{KIT}/chattr/Chattr.runtimeconfig.json", "644"),
        (PAYLOAD_DIR / "parseinfo.awk", PAYLOAD, "755"),
        (PAYLOAD_DIR / "cf_tv_daemon_start.awk", f"{KIT}/cf_tv_daemon_start.awk", "644"),
        (PAYLOAD_DIR / "cf_tv_daemon.awk", f"{KIT}/cf_tv_daemon.awk", "644"),
        (PAYLOAD_DIR / "cf_tv_hooks.sh", HOOK_RUNNER, "644"),
    ]
    # The speaker 50-btconnect reconnects (tv/bt.py TARGET_FILE); not in git,
    # template payload/btconnect.addr.example.
    if (PAYLOAD_DIR / "btconnect.addr").is_file():
        files.append((PAYLOAD_DIR / "btconnect.addr", f"{KIT}/btconnect.addr", "644"))
    files += [
        # Screen capture for bin/snap, bin/stream (docs/02-capture/screen-capture.md).
        (CAPTURE_BUILD / "CaptureProbe.dll", "/opt/cap/CaptureProbe.dll", "755"),
        (CAPTURE_BUILD / "CaptureProbe.runtimeconfig.json", "/opt/cap/CaptureProbe.runtimeconfig.json", "644"),
    ]
    for event in ("boot", "wake"):
        for hook in sorted((PAYLOAD_DIR / f"on-{event}.d").glob("*")):
            files.append((hook, f"{KIT}/on-{event}.d/{hook.name}", "644"))
    return files


# Hooks switched off on the TV (tv/root.py hooks disable NAME) live here, in
# the same on-EVENT.d layout; the runner never looks there.
DISABLED = f"{KIT}/disabled"


def disabled_path(dest: str) -> str | None:
    """Where a hook lives while disabled; None for files that are not hooks."""
    for event in ("boot", "wake"):
        if dest.startswith(f"{KIT}/on-{event}.d/"):
            return f"{DISABLED}/on-{event}.d/" + dest.rsplit("/", 1)[1]
    return None


def build_apps() -> None:
    """Build each deployed C# tool whose output is missing or older than its project or source."""
    env = dict(os.environ, PATH=f"{ROOT / '.dotnet8'}:{os.environ['PATH']}", DOTNET_CLI_HOME="/tmp/dnethome8")
    for out, project in APP_BUILDS.items():
        sources = [ROOT / "apps" / project, (ROOT / "apps" / project).with_suffix(".cs")]
        dlls = list(out.glob("*.dll"))
        if dlls and min(d.stat().st_mtime for d in dlls) >= max(s.stat().st_mtime for s in sources):
            continue
        print(f"building apps/{project} -> {out.relative_to(ROOT)}", file=sys.stderr)
        subprocess.run(
            [
                "dotnet",
                "build",
                str(ROOT / "apps" / project),
                "-c",
                "Release",
                "-o",
                str(out),
                f"-p:BaseIntermediateOutputPath={ROOT / 'build' / 'obj' / out.name}/",
            ],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
        )


# One command may carry at most 65,536 bytes on the channel (root agent
# limit); the file payload is split well below that. The full install was
# 66,618 bytes once the capture probe joined it (2026-10-25).
INSTALL_CHUNK = 40_000


def install_commands(files: list[tuple[Path, str, str]]) -> list[str]:
    """Write every file atomically as uid 0, then apply LOCKDOWN.

    Several commands, run in order on one path: a setup, groups of files under
    INSTALL_CHUNK bytes each, and the lockdown, which prints INSTALLED. A
    failed group shows as `[exit N]` in the output (cmd_boot checks).

    parseinfo.sh lives in the world-writable /opt/etc, so a file mode alone
    does not protect it: anyone can rename a file over it. The immutable flag
    does (uid 901 mv/rm/write all fail, verified 2026-10-24); it has to be
    cleared to replace the file and set again afterwards.
    """
    commands = [
        f"mkdir -p /opt/cap {KIT}/chattr {KIT}/on-boot.d {KIT}/on-wake.d "
        f"{DISABLED}/on-boot.d {DISABLED}/on-wake.d && "
        f"{{ test -f {PAYLOAD_BACKUP} || cp {PAYLOAD} {PAYLOAD_BACKUP}; }}"
    ]
    group: list[str] = []
    for source, dest, mode in files:
        encoded = base64.b64encode(source.read_bytes()).decode()
        # A hook disabled on the TV (tv/root.py hooks disable) is updated
        # where it is and stays disabled.
        disabled = disabled_path(dest)
        lines = [
            f"if [ -e {disabled} ]; then d={disabled}; else d={dest}; fi" if disabled else f"d={dest}",
            f"printf %s {encoded} | base64 -d > $d.new",
            f"chown 0:0 $d.new && chmod {mode} $d.new",
        ]
        if dest == PAYLOAD:
            lines.append(f"{CHATTR} -i {dest} 2>/dev/null || true")
        lines.append("mv -f $d.new $d")
        if dest == PAYLOAD:
            lines.append(f"{CHATTR} +i {dest}")
        if group and sum(map(len, group)) + sum(map(len, lines)) > INSTALL_CHUNK:
            commands.append("\n".join(["set -e", *group]))
            group = []
        group += lines
    if group:
        commands.append("\n".join(["set -e", *group]))
    commands.append(
        "\n".join(
            [
                "set -e",
                f"find {KIT} -path {RUN_DIR} -prune -o -exec chown 0:0 {{}} +",
                f"find {KIT} -path {RUN_DIR} -prune -o -type d -exec chmod 755 {{}} +",
                f"find {KIT} -path {RUN_DIR} -prune -o -type f -exec chmod 644 {{}} +",
                f"touch {CMD_QUEUE} {CMD_OUT} /opt/masklist",
                f"chown 901:901 {CMD_QUEUE} {CMD_OUT} && chmod 600 {CMD_QUEUE} {CMD_OUT}",
                "chown 0:0 /opt/masklist && chmod 644 /opt/masklist",
                "chown 0:0 /opt/cap && chmod 755 /opt/cap",
                "echo INSTALLED",
            ]
        )
    )
    return commands


def device_status(files: list[tuple[Path, str, str]]) -> str:
    """md5 of each deployed file and the modes, read as uid 901; the immutable flag through root.

    Chattr cannot run as uid 901: dotnet execs as `User`, and `User` may not
    read files the daemon wrote (System::Privileged).
    """
    dests = " ".join(dest for _, dest, _ in files)
    ok, output = inject(
        f"for f in {dests}; do md5sum $f 2>/dev/null || "
        f"md5sum $(echo $f | sed 's#^{KIT}/on-#{DISABLED}/on-#') 2>/dev/null || echo \"MISSING $f\"; done; "
        f'for f in {KIT}/on-*.d/* {DISABLED}/on-*.d/*; do [ -f "$f" ] && echo "HOOKFILE $f"; done; '
        f"stat -c 'MODE %a %u:%g %n' {' '.join(LOCKDOWN)} 2>&1",
        timeout=40,
    )
    if not ok:
        return ""
    _, flag_ok, flags = run_root([f"{CHATTR} get {PAYLOAD} 2>&1"], via=None, timeout=30)
    return output + (flags if flag_ok else "")


def run_root(commands: list[str], via: str | None, timeout: float = 60.0) -> tuple[str, bool, str]:
    """Run side-effecting commands once, on one root path, with no fallback."""
    state = probe()
    path = pick(state, via)
    if path is None:
        return "none", False, explain(state)
    return (path, *run_on(path, commands, timeout))


def restart_daemon(via: str | None = None) -> bool:
    """Restart cf_tv_daemon from outside its cgroup; wait for the new pid.

    The launcher stops the old unit, which kills every process in its cgroup,
    including a daemon request that asked for the restart. So the restart runs
    in its own transient unit, 2 s later, and the request returns first.
    """

    def current() -> tuple[str, float, float]:
        ok, output = inject(f"cat {RUN_DIR}/daemon.pid; cat {RUN_DIR}/heartbeat; cut -d' ' -f1 /proc/uptime")
        fields = output.split("[exit:")[0].split() if ok else []
        try:
            return fields[0], float(fields[1]), float(fields[2])
        except (IndexError, ValueError):
            return "", 0.0, 0.0

    old_pid = current()[0]
    path, ok, output = run_root(
        [
            "systemctl reset-failed cf_tv_restart.service 2>/dev/null; "
            "systemd-run --unit=cf_tv_restart --no-block /bin/sh -c "
            f"'sleep 2; exec {DAEMON_START}'"
        ],
        via=via,
        timeout=30,
    )
    if not ok:
        print(f"restart request failed ({path}): {output}", file=sys.stderr)
        return False
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        time.sleep(2)
        pid, beat, uptime = current()
        if pid and pid != old_pid and uptime - beat < 5:
            print(f"cf_tv_daemon restarted: pid {old_pid or '?'} -> {pid}")
            PROBE_CACHE.unlink(missing_ok=True)
            return True
    print("cf_tv_daemon did not come back within 40 s; recover with tv/root.py exec --via exploit", file=sys.stderr)
    return False


def payload_check(files: list[tuple[Path, str, str]]) -> tuple[list[str], int] | None:
    """Device vs repo: one line per file and mode, plus extra hook files and the immutable flag.

    Returns (lines, number of problems); None if sdb failed. EXTRA hooks are
    reported, not counted: a dlog one-shot is one on purpose.
    """
    output = device_status(files)
    if not output:
        return None
    lines: list[str] = []
    sums = {
        line.split()[1]: line.split()[0]
        for line in output.splitlines()
        if len(line.split()) == 2 and len(line.split()[0]) == 32
    }
    drift = 0
    for source, dest, _ in files:
        want = hashlib.md5(source.read_bytes()).hexdigest()
        disabled = disabled_path(dest)
        have = sums.get(dest) or (sums.get(disabled) if disabled else None)
        state = "ok" if have == want else ("MISSING" if have is None else "DIFFERS")
        drift += state != "ok"
        note = "  (disabled)" if disabled and dest not in sums and disabled in sums else ""
        lines.append(f"{state:8} {dest}{note}")
    deployed = {disabled_path(dest) or dest for _, dest, _ in files} | {dest for _, dest, _ in files}
    for line in output.splitlines():
        if line.startswith("HOOKFILE ") and line[9:] not in deployed:
            lines.append(f"EXTRA    {line[9:]} (on the TV, not in payload/)")
    immutable = False
    for line in output.splitlines():
        if line.startswith("MODE "):
            _, mode, owner, path = line.split(maxsplit=3)
            want = LOCKDOWN.get(path)
            state = "ok" if want == f"{mode} {owner}" else f"WANT {want}"
            drift += state != "ok"
            lines.append(f"{state:8} {mode} {owner} {path}")
        elif line.startswith("0x") and line.endswith(PAYLOAD):
            immutable = line.split()[1] == "i"
    drift += not immutable
    lines.append(
        f"ok       immutable flag on {PAYLOAD}" if immutable else f"MUTABLE  {PAYLOAD} (or the flag could not be read)"
    )
    return lines, drift


def cmd_boot(args) -> int:
    build_apps()
    files = payload_files()

    if args.action == "status":
        checked = payload_check(files)
        if checked is None:
            print("sdb injection failed", file=sys.stderr)
            return 1
        print("\n".join(checked[0]))
        return 0 if checked[1] == 0 else 1

    if args.action == "install":
        path, ok, output = run_root(install_commands(files), via=args.via)
        print(output.strip())
        if not ok or "INSTALLED" not in output or any_failed(output):
            print(f"install failed on the {path} path", file=sys.stderr)
            return 1
        # Same path as the install: on a wiped TV (--via exploit) there is no
        # daemon yet, and this starts the first one.
        return 0 if restart_daemon(args.via) else 1

    if args.action == "restore":
        path, ok, output = run_root(
            [
                f"{CHATTR} -i {PAYLOAD} 2>/dev/null; "
                f"cp {PAYLOAD_BACKUP} {PAYLOAD} && chmod 755 {PAYLOAD} && echo RESTORED"
            ],
            via=args.via,
        )
        print(output.strip())
        return 0 if ok and "RESTORED" in output else 1

    return 2


def cmd_hooks(args) -> int:
    if args.action in ("boot", "wake"):
        _path, ok, output = run_root(
            [
                f'( CF_EVENT={args.action}; eval "$(< {HOOK_RUNNER})" ); '
                f"echo 'on-{args.action}.d hooks started; log: /opt/cf_tv_hooks.log'"
            ],
            via=args.via,
        )
        print(output.strip())
        return 0 if ok else 1

    if args.action == "list":
        script = (
            f"for d in on-boot.d on-wake.d; do "
            f'for f in {KIT}/$d/* {DISABLED}/$d/*; do [ -f "$f" ] && echo "$f"; done; done; true'
        )
    else:
        if not args.name or "/" in args.name:
            print(f"hooks {args.action} needs a hook file name, e.g. 50-btconnect", file=sys.stderr)
            return 2
        # Move between on-EVENT.d and disabled/on-EVENT.d, for both events.
        src, dst = (KIT, DISABLED) if args.action == "disable" else (DISABLED, KIT)
        script = (
            f"n=0; for d in on-boot.d on-wake.d; do "
            f"if [ -f {src}/$d/{args.name} ]; then mkdir -p {dst}/$d && "
            f"mv -f {src}/$d/{args.name} {dst}/$d/{args.name} && n=$((n+1)) && "
            f'echo "{args.action}d: $d/{args.name}"; fi; done; '
            f"[ $n -gt 0 ] || {{ echo 'no such {'enabled' if args.action == 'disable' else 'disabled'} hook: {args.name}'; exit 1; }}"
        )
    _path, ok, output = run_root([script], via=args.via)
    if args.action == "list" and ok:
        for line in output.splitlines():
            if line.startswith(KIT):
                state = "disabled" if line.startswith(DISABLED) else "enabled "
                print(f"{state}  {line.rsplit('/', 2)[-2]}/{line.rsplit('/', 1)[-1]}")
        return 0
    print(output.strip())
    return 0 if ok and not any_failed(output) else 1


# ---------------------------------------------------------------- reboot, report, dlog

HOOK_LOG = "/opt/cf_tv_hooks.log"
DAEMON_LOG = "/opt/cf_tv_daemon.log"
DLOG_CONF = "/opt/etc/dlog.conf"
DLOG_BACKUP = f"{KIT}/dlog.conf.orig"
DLOG_HOOK = f"{KIT}/on-boot.d/00-dlog-once"
DLOG_OUT = "/opt/cap/dlog-boot.txt"
# Bluetooth stack tags and the speaker (docs/04-system/bluetooth.md, Tools).
DLOG_FILTER = (
    "BLUETOOTH|BT_|bt-btm|bt-btif|bt-l2cap|bt-avp|bluedroid|a2dp|avrcp|authoriz|btapp|connectionmanager"
)


def wait_for(check, seconds: float, every: float):
    """Call check() until it returns something true; that value, or None."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(every)
    return None


def cmd_reboot(args) -> int:
    """Reboot the TV and wait until it is usable again, timing each stage."""
    started = time.monotonic()
    stamp = lambda: f"{time.monotonic() - started:4.0f} s"  # noqa: E731
    path, ok, output = run_root(
        [
            "systemctl reset-failed cf_tv_reboot.service 2>/dev/null; "
            "systemd-run --unit=cf_tv_reboot --no-block /bin/sh -c 'sleep 2; reboot'"
        ],
        via=args.via,
        timeout=20,
    )
    if not ok:
        print(f"reboot request failed ({path}): {output.strip()}", file=sys.stderr)
        return 1
    PROBE_CACHE.unlink(missing_ok=True)
    print(f"{stamp()}  reboot requested ({path})")
    if not wait_for(lambda: not sdb_available(), 60, 1):
        print("the TV did not go down within 60 s", file=sys.stderr)
        return 1
    print(f"{stamp()}  TV down")
    if not wait_for(sdb_available, 300, 2):
        print("the TV is not back on the network after 5 min", file=sys.stderr)
        return 1
    print(f"{stamp()}  sdb port open")
    if not wait_for(lambda: sdb_ensure() is None, 120, 3):
        print(f"sdb does not connect: {sdb_ensure()}", file=sys.stderr)
        return 1
    print(f"{stamp()}  sdb connected")
    if not wait_for(daemon_available, 120, 3):
        PROBE_CACHE.unlink(missing_ok=True)
        print(explain(probe(force=True)), file=sys.stderr)
        return 1
    print(f"{stamp()}  root daemon responding")
    PROBE_CACHE.unlink(missing_ok=True)
    if not args.report:
        return 0
    args.lines = 0  # a fresh boot: show everything
    return cmd_report(args)


def cmd_doctor(args) -> int:
    """Is everything healthy? One line per check: ok, WARN or FAIL; exit 1 on FAIL."""
    fails = 0

    def line(state: str, what: str, detail: str = "") -> None:
        nonlocal fails
        fails += state == "FAIL"
        print(f"{state:5} {what:16} {detail}".rstrip())

    if not sdb_available():
        line("FAIL", "TV", f"not reachable on {TV}:26101 (off, asleep, or still booting)")
        return 1
    error = sdb_ensure()
    line("FAIL" if error else "ok", "sdb", error or "connected")
    if error:
        return 1

    PROBE_CACHE.unlink(missing_ok=True)
    state = probe(force=True)
    age = state.get("daemon_heartbeat_age")
    line(
        "ok" if state.get("daemon") else "FAIL",
        "daemon heartbeat",
        f"{age:.1f} s old" if age is not None else state.get("daemon_detail", "none"),
    )

    serve = subprocess.run(("pgrep", "-f", "tv/chan.py serve"), capture_output=True, text=True)
    if not serve.stdout.strip():
        line("WARN", "channel", "not running on this host (tv/chan.py serve &): commands use the daemon")
    else:
        line(
            "ok" if state.get("channel") else "WARN",
            "channel",
            "up" if state.get("channel") else "serve running, agent not connected yet",
        )

    script = (
        "echo $(systemctl show cf_tv_daemon -p Restart -p NRestarts -p ActiveState); "
        f"echo \"STARTS $(grep -c 'start pid' {DAEMON_LOG})\"; "
        f"echo \"QUEUE $(ls {RUN_DIR}/stage {RUN_DIR}/out {RUN_DIR}/rc {RUN_DIR}/req 2>/dev/null | grep -vc -e : -e '^$')\"; "
        f'echo "PLOG $(grep ^plog= {DLOG_CONF})"; '
        "echo \"OPTFREE $(df -m /opt | awk 'NR==2{print $4}')\"; "
        "echo \"FAILED\" $(systemctl list-units --failed --no-legend 'cf_tv*' | awk '{print $1}')"
    )
    _path, ok, output = run_root([script], via=None, timeout=30)
    if not ok:
        line("FAIL", "root command", output.strip()[:200])
        return 1

    def get(key: str) -> str:
        return field(output.splitlines(), key).strip()

    unit = output.splitlines()[0] if output else ""
    restarts = re.search(r"NRestarts=(\d+)", unit)
    line(
        "ok" if "Restart=on-failure" in unit else "WARN",
        "daemon unit",
        unit.strip() + ("" if "Restart=on-failure" in unit else "  (old launcher: tv/root.py boot install)"),
    )
    if restarts and int(restarts.group(1)) > 0:
        line("WARN", "daemon crashes", f"restarted {restarts.group(1)}x since it was launched; see {DAEMON_LOG}")
    leftovers = int(get("QUEUE") or 0)
    line("ok" if leftovers < 10 else "WARN", "queue", f"{leftovers} leftover files")
    plog = get("PLOG")
    line(
        "ok" if plog.endswith("0") else "WARN",
        "dlog",
        plog + ("" if plog.endswith("0") else "  platform logging ON: tv/root.py dlog disarm"),
    )
    free = int(get("OPTFREE") or 0)
    line("ok" if free > 300 else ("WARN" if free > 100 else "FAIL"), "/opt free", f"{free} MB")
    failed_units = get("FAILED")
    line("WARN" if failed_units else "ok", "failed units", failed_units or "none")

    build_apps()
    checked = payload_check(payload_files())
    if checked is None:
        line("FAIL", "payload", "could not read the device files")
    else:
        problems = [row for row in checked[0] if not row.startswith("ok")]
        line(
            "ok" if checked[1] == 0 else "FAIL", "payload", "matches the repo" if not problems else problems[0].strip()
        )
        for extra in problems[1:]:
            print(f"{'':22}{extra.strip()}")
        disabled = [row.split()[1] for row in checked[0] if "(disabled)" in row]
        if disabled:
            line("WARN", "hooks disabled", " ".join(disabled))
    return 1 if fails else 0


def cmd_report(args) -> int:
    """The current boot on one uptime timeline: daemon, hooks, logging, speaker."""
    import bt  # noqa: PLC0415  (tv/bt.py)

    script = (
        'read up idle < /proc/uptime; echo "UPTIME $up"; '
        'echo "BOOTID $(cat /proc/sys/kernel/random/boot_id)"; '
        f'sed "s/^/DAEMON /" {DAEMON_LOG}; '
        f'tail -n 400 {HOOK_LOG} | sed "s/^/HOOK /"; '
        f'for d in on-boot.d on-wake.d; do echo "HOOKS $d" $(ls {KIT}/$d); done; '
        f'echo "PLOG $(grep ^plog= {DLOG_CONF})"; ls {DLOG_OUT} 2>/dev/null | sed "s/^/DLOG /"; '
        # The live 4180 query (tv/bt.py connected_query), for the address in the
        # target file, built on the TV.
        f'a=$(cat {bt.TARGET_FILE} 2>/dev/null); [ -n "$a" ] && echo "SPEAKER $a $('
        "gdbus call --system --dest org.projectx.bt_product "
        "--object-path /org/projectx/bt_service_product "
        "--method org.projectx.bt_product.service_request_product 1 4180 0 "
        '"[byte 0x$(echo $a | sed s/:/,0x/g)]" "@ay []" "@ay []" "@ay []" "@ay []")" || true'
    )
    path, ok, output = run_root([script], via=getattr(args, "via", None), timeout=30)
    if not ok:
        print(output.strip(), file=sys.stderr)
        return 1
    lines = output.splitlines()

    def get(key: str) -> str:
        return field(lines, key)

    uptime = float(get("UPTIME") or 0)
    # The timeline is in TV clock time, read from the log lines. Uptime would
    # be wrong: it stops while the TV is suspended ("off" on the remote), so
    # (now - uptime) is not the boot time once the TV has slept, and the
    # first version of this report misplaced every event before a suspend.
    # Hook lines carry their own `up=` (uptime) where it matters.
    daemon = [row[7:] for row in lines if row.startswith("DAEMON ") and len(row) > 26]
    boots = [row[:19] for row in daemon if row[20:] == "event boot"]
    since = boots[-1] if boots else ""
    print(
        f"booted {since or '(no boot event in the daemon log)'} (TV time), "
        f"awake {uptime / 3600:.1f} h since, boot_id {get('BOOTID')[:8]}  [via {path}]"
    )
    events = sorted(
        [(row[:19], f"daemon: {row[20:]}") for row in daemon]
        + [(row[5:24], row[25:]) for row in lines if row.startswith("HOOK ") and len(row) > 25],
        key=lambda e: e[0],
    )
    events = [e for e in events if e[0] >= since]
    shown = events[-args.lines :] if args.lines else events
    if len(shown) < len(events):
        print(f"  ({len(events) - len(shown)} earlier lines since boot not shown; --lines 0 for all)")
    for stamp, text in shown:
        print(f"  {stamp[5:]}  {text}")
    if not events:
        print("  (no daemon or hook lines since this boot)")
    for line in lines:
        if line.startswith("HOOKS "):
            print(f"hooks {line[6:]}")
    plog = get("PLOG")
    print(f"dlog: {plog or '?'}" + ("  ** platform logging is ON **" if plog.endswith("1") else ""))
    if get("DLOG"):
        print(f"dlog capture waiting on the TV: {get('DLOG')} (tv/root.py dlog fetch)")
    speaker = get("SPEAKER")
    if speaker:
        addr, _, reply = speaker.partition(" ")
        state = "audio connected" if bt.audio_connected(reply) else "no audio"
        print(f"speaker {addr}: {state}  {reply.strip()}")
    return 0


def dlog_hook(filter_regex: str, seconds: int) -> str:
    return (
        "# One-shot, written by tv/root.py dlog arm: keep the dlog lines matching\n"
        "# a filter for a while after this boot, then switch platform logging\n"
        "# off again. Deletes itself first, so it never runs twice.\n"
        '[ "$CF_EVENT" = boot ] || exit 0\n'
        f"rm -f {DLOG_HOOK}\n"
        f"dlogutil -b main -b system -b radio -v kerneltime 2>&1 |\n"
        f"  grep --line-buffered -iE '{filter_regex}' | head -c 20000000 > {DLOG_OUT} &\n"
        f"sleep {seconds}\n"
        "kill $(pidof dlogutil) 2>/dev/null\n"
        f"cp -p {DLOG_BACKUP} {DLOG_CONF} && rm -f {DLOG_BACKUP}\n"
        f'say "dlog: $(wc -l < {DLOG_OUT}) lines in {DLOG_OUT}; platform logging off again"\n'
    )


def cmd_dlog(args) -> int:
    """Platform logging (dlog) for the next boot only; see docs/06-security/techniques.md."""
    if args.action == "arm":
        if "'" in args.filter:
            print("the filter may not contain a single quote", file=sys.stderr)
            return 2
        encoded = base64.b64encode(dlog_hook(args.filter, args.seconds).encode()).decode()
        _path, ok, output = run_root(
            [
                f"test -f {DLOG_BACKUP} || cp -p {DLOG_CONF} {DLOG_BACKUP}; "
                f"sed -i 's/^plog=0/plog=1/' {DLOG_CONF}; "
                f"printf %s {encoded} | base64 -d > {DLOG_HOOK}; chmod 644 {DLOG_HOOK}; "
                f"rm -f {DLOG_OUT}; grep ^plog= {DLOG_CONF}"
            ],
            via=args.via,
        )
        print(output.strip())
        if ok:
            print(
                f"armed: the next boot logs for {args.seconds} s, then switches itself off. "
                "Reboot: tv/root.py reboot --report"
            )
        return 0 if ok and "plog=1" in output else 1
    if args.action == "disarm":
        _path, ok, output = run_root(
            [
                f"test -f {DLOG_BACKUP} && cp -p {DLOG_BACKUP} {DLOG_CONF} && rm -f {DLOG_BACKUP}; "
                f"rm -f {DLOG_HOOK}; grep ^plog= {DLOG_CONF}"
            ],
            via=args.via,
        )
        print(output.strip())
        return 0 if ok and "plog=0" in output else 1
    # fetch
    error = sdb_ensure()
    if error:
        print(error, file=sys.stderr)
        return 1
    target = ROOT / ".local" / "dlog" / time.strftime("dlog-%Y%m%d-%H%M%S.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        (str(SDB), "-s", SERIAL, "pull", DLOG_OUT, str(target)), capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0 or not target.exists():
        print(f"pull failed: {(result.stdout + result.stderr).strip()}", file=sys.stderr)
        return 1
    run_root([f"rm -f {DLOG_OUT}"], via=args.via)
    print(f"{target} ({sum(1 for _ in target.open(errors='replace'))} lines); deleted on the TV")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # --tv is read at import time (tv/project_env.py): the TV resolves before
    # argparse runs. Declared here too so --help lists it.
    common = argparse.ArgumentParser(add_help=False)
    project_env.add_tv_argument(common)
    sub = parser.add_subparsers(dest="command", required=True)
    project_env.add_tv_argument(parser)

    run = sub.add_parser("exec", parents=[common])
    run.add_argument("--via", choices=("daemon", "channel", "exploit"), default=None)
    run.add_argument("--timeout", type=float, default=20.0, help="seconds each command may run (daemon path)")
    run.add_argument("commands", nargs="+")
    run.set_defaults(handler=cmd_exec)

    probe_parser = sub.add_parser("probe", parents=[common])
    probe_parser.set_defaults(handler=cmd_probe)

    boot = sub.add_parser("boot", parents=[common])
    boot.add_argument("action", choices=("install", "status", "restore"))
    boot.add_argument("--via", choices=("daemon", "channel", "exploit"), default=None)
    boot.set_defaults(handler=cmd_boot)

    hooks = sub.add_parser("hooks", help="run, list, enable or disable boot/wake hooks", parents=[common])
    hooks.add_argument(
        "action", choices=("boot", "wake", "list", "enable", "disable"), help="boot/wake: run that event's hooks now"
    )
    hooks.add_argument("name", nargs="?", help="hook file name, for enable/disable (both events)")
    hooks.add_argument("--via", choices=("daemon", "channel", "exploit"), default=None)
    hooks.set_defaults(handler=cmd_hooks)

    reboot = sub.add_parser("reboot", help="reboot the TV and wait until it is usable", parents=[common])
    reboot.add_argument("--report", action="store_true", help="print the boot report afterwards")
    reboot.add_argument("--via", choices=("daemon", "channel", "exploit"), default=None)
    reboot.set_defaults(handler=cmd_reboot)

    report = sub.add_parser("report", help="this boot on one timeline", parents=[common])
    report.add_argument("--via", choices=("daemon", "channel", "exploit"), default=None)
    report.add_argument("--lines", type=int, default=40, help="show the last N timeline lines since boot (0 = all)")
    report.set_defaults(handler=cmd_report)

    doctor = sub.add_parser("doctor", help="health check: sdb, daemon, channel, payload, space", parents=[common])
    doctor.set_defaults(handler=cmd_doctor)

    dlog = sub.add_parser("dlog", help="platform logging for the next boot only", parents=[common])
    dlog.add_argument("action", choices=("arm", "fetch", "disarm"))
    dlog.add_argument("--filter", default=DLOG_FILTER, help="grep -E regex (default: Bluetooth)")
    dlog.add_argument("--seconds", type=int, default=300, help="how long to keep logging")
    dlog.add_argument("--via", choices=("daemon", "channel", "exploit"), default=None)
    dlog.set_defaults(handler=cmd_dlog)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
