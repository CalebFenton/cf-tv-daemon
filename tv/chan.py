#!/usr/bin/env python3
"""chan - fast root channel to the TV.

The TV-side root agent (`SamsungTvRootAgent.cs`, vendored) keeps one
authenticated TCP connection to the host and runs commands on it. This server
starts that agent once, then serves local clients over the UNIX socket
/tmp/tvchan.sock: ~10 ms per command, raw file transfer both ways.

Bring-up (1.3 s): the boot-installed root daemon (cf_tv_daemon) launches the
agent as the transient unit cf_tv_agent with a fresh single-use token; the
agent calls back to our listener. The daemon is the only way up: if it is
unavailable the channel stays down and says why. The exploit is a recovery
tool, run only on request (`tv/root.py exec --via exploit`); an automatic
exploit bring-up once left a hung archive-root process in sdbd that blocked
every later sdb injection, the daemon's queue included (2026-11-05).
Commands are raised from the agent's `User` label to System::Privileged (see
PRIVILEGED).

Usage
-----
    tv/chan.py serve             # run in the background; brings the agent up
    tv/chan.py run 'id' 'b'      # commands in one round trip
    tv/chan.py put f1 f2         # WRITEFILE into the agent's staging dir (4 MiB each)
    tv/chan.py pull /path        # READFILE (staging dir or agent runtime dir only)
    tv/chan.py status            # up?, label, staging dir

Protocol between client and server: one JSON line request, one JSON line
response.

Failure model
-------------
The agent dies on suspend/reboot without closing its socket, and its token is
single-use. Before a request on a connection idle for over 2 s, the server
pings the agent and reconnects first if it is gone, so the request is not
lost. Every 15 s a keepalive does the same while the TV answers: after a TV
reboot the channel is back on its own. `channel_down` means the bring-up
through the daemon failed, or the agent died during the request (which may
then have run).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import project_env

project_env.ensure()

if TYPE_CHECKING:  # asyncio costs ~1 s to import; only `serve` needs it
    import asyncio

ROOT = Path(__file__).resolve().parent.parent
SOCKET_PATH = Path("/tmp/tvchan.sock")
STATE_PATH = Path("/tmp/tvchan.state")

# The root-agent/crypto stack is heavy (~1.6 s of CPU just to import). CLI
# callers only need a UNIX socket, so these are imported lazily inside
# serve_forever(). Importing them eagerly made every `tv/chan.py run` cost ~2 s.
STAGING_ROOT = None  # resolved in serve_forever()
RootAgentServer = None
generate_secret = None
SdbClient = None
find_sdb = None
route_callback_host = None
write_secret = None


def _load_heavy_deps() -> None:
    """Import the root-channel machinery. Only `serve` needs this."""
    global asyncio
    global STAGING_ROOT, RootAgentServer, generate_secret, SdbClient
    global find_sdb, route_callback_host, write_secret
    if STAGING_ROOT is not None:
        return
    import asyncio as _asyncio  # noqa: PLC0415

    asyncio = _asyncio
    # The agent is staged where archive-root stages it (sdb may push there).
    from samsung_tv_root.archive_root import STAGING_ROOT as _staging  # noqa: PLC0415
    from samsung_tv_root.root_agent import (  # noqa: PLC0415
        RootAgentServer as _ras,
        generate_secret as _gs,
        write_secret as _ws,
    )
    from samsung_tv_root.sdb import (  # noqa: PLC0415
        SdbClient as _sc,
        find_sdb as _fs,
        route_callback_host as _rch,
    )

    STAGING_ROOT = _staging
    RootAgentServer, generate_secret, write_secret = _ras, _gs, _ws
    SdbClient, find_sdb, route_callback_host = _sc, _fs, _rch


TV = project_env.tv_ip()
PAYLOADS = ROOT / "build" / "payloads-netcoreapp3.1"

# Resolve sdb from the repo before falling back to discovery. Without this a
# fresh shell (no SDB in the environment) fails bring-up and the channel lies
# about being up.
_LOCAL_SDB = ROOT / "vendor" / "sdb" / "data" / "tools" / "sdb"
if _LOCAL_SDB.is_file():
    os.environ["SDB"] = str(_LOCAL_SDB)  # authoritative; a stale
    # exported SDB must not win


def log(message: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


# Each command's shell raises itself from `User` (the agent's label, forced by
# dotnet's SMACK64EXEC) to System::Privileged, which it may do as root with
# CAP_MAC_ADMIN. Children inherit it, except anything that execs dotnet again.
PRIVILEGED = "printf System::Privileged > /proc/self/attr/current 2>/dev/null; "


AGENT_UNIT = "cf_tv_agent"
AGENT_FILES = ("SamsungTvRootAgent.dll", "SamsungTvRootAgent.runtimeconfig.json")


async def bring_up_daemon() -> tuple[object, object, dict]:
    """Start the root agent through the boot-installed daemon. No exploit.

    The agent only needs `dotnet SamsungTvRootAgent.dll HOST PORT TOKEN DIR`
    run as root; the exploit was just one way to get that. The daemon is
    already root (System::Privileged), so it launches the agent as the
    transient unit cf_tv_agent and the agent calls back to our listener,
    authenticated with a fresh single-use token.
    """
    sys.path.insert(0, str(ROOT / "tv"))
    import root as rootctl  # noqa: PLC0415  (tv/root.py)

    started = time.monotonic()
    callback = route_callback_host(TV)
    error = await asyncio.to_thread(rootctl.sdb_ensure)
    if error:
        raise RuntimeError(error)
    client = SdbClient(find_sdb(), TV, timeout=15.0)
    await asyncio.to_thread(client.connect)

    package = "cf-agent-" + secrets.token_hex(8)
    staging = STAGING_ROOT / package
    secret = generate_secret()
    server = RootAgentServer(callback, 0, TV, secret, require_root=True)
    await server.start()
    port = server.listening_port
    log(f"listener {callback}:{port}")

    temp = tempfile.TemporaryDirectory(prefix="tvchan-")
    try:
        token = Path(temp.name) / "root-agent.token"
        write_secret(token, secret)

        def stage() -> None:
            client.capture(f"/bin/mkdir -p {staging}", timeout=15)
            for name in AGENT_FILES:
                client.push(PAYLOADS / name, staging / name)
            client.push(token, staging / "root-agent.token")

        await asyncio.to_thread(stage)
        log("staged")

        launch = (
            f"systemctl stop {AGENT_UNIT} 2>/dev/null; "
            f"systemctl reset-failed {AGENT_UNIT} 2>/dev/null; "
            # The agent runs as `User` no matter what: /usr/bin/dotnet has
            # SMACK64EXEC="User", so exec switches the label. Commands are
            # raised to System::Privileged per call instead (PRIVILEGED below).
            f"systemd-run --unit={AGENT_UNIT} --no-block /usr/bin/dotnet "
            f"{staging}/SamsungTvRootAgent.dll {callback} {port} "
            f"{staging}/root-agent.token {staging}/"
        )
        ok, output = await asyncio.to_thread(rootctl.daemon_exec, [launch], 20.0)
        if not ok:
            log(f"agent launch through the daemon failed: {output.strip()[:300]}")
            raise RuntimeError(
                "the root daemon did not launch the agent (daemon down or stuck?); run tv/root.py doctor"
            )

        connection = await server.accept(30.0)
        identity = await connection.ping()
        log(f"authenticated uid={identity.uid} smack={identity.smack_label.strip()}")
        closing = server.detach(connection)
        return (
            connection,
            closing,
            {
                "tv": TV,
                "uid": identity.uid,
                "smack": identity.smack_label.strip(),
                "package": package,
                "staging": str(staging) + "/",
                "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "bringup_seconds": round(time.monotonic() - started, 1),
            },
        )
    except BaseException:
        await server.close()
        raise
    finally:
        temp.cleanup()


def cleanup_package(package: str) -> None:
    """Remove this channel's staging after the agent has gone.

    Only called when the channel is deliberately closed or has died, never
    while the agent is live: deleting the directory out from under a running
    agent leaves it with no token file and no status path.
    """
    if not package:
        return
    client = SdbClient(find_sdb(), TV, timeout=15.0)
    try:
        client.connect()
        client.capture(f"/bin/rm -rf {STAGING_ROOT / package}", timeout=20)
    except Exception:
        pass


async def serve_forever() -> int:
    _load_heavy_deps()
    if SOCKET_PATH.exists():
        # One server per socket: unlinking a live server's socket orphans it
        # and leaves two agents fighting (seen 2026-11-05). Only a socket
        # nobody answers on is stale.
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.connect(str(SOCKET_PATH))
        except OSError:
            SOCKET_PATH.unlink()
        else:
            log("another chan.py serve already answers on this socket; exiting")
            return 1

    sys.path.insert(0, str(ROOT / "tv"))
    import root as rootctl  # noqa: PLC0415  (tv/root.py)

    connection = None
    closing = None
    state: dict = {}
    lock = asyncio.Lock()
    last_ok = 0.0

    async def drop(reason: str) -> None:
        nonlocal connection
        old, connection = connection, None
        log(f"agent gone ({reason})")
        try:
            await old.close()
        except Exception:
            pass

    async def alive() -> bool:
        """Is the agent still there?

        A TV reboot or suspend kills it without closing the socket, so only a
        ping tells. Skipped right after a successful command (bulk transfers
        stay fast).
        """
        nonlocal last_ok
        if connection is None:
            return False
        if time.monotonic() - last_ok < 2:
            return True
        try:
            await asyncio.wait_for(connection.ping(), 5)
        except Exception as error:
            await drop(f"ping failed: {type(error).__name__}")
            return False
        last_ok = time.monotonic()
        return True

    async def ensure() -> str:
        """A live agent, reconnecting through the daemon first if needed.

        Called before every request, so a dead agent costs a reconnect, never
        the request. Returns "" when the agent is live, else why it is not.
        """
        nonlocal connection, closing, state, last_ok
        if await alive():
            return ""
        log("bringing up root channel")
        try:
            connection, closing, state = await bring_up_daemon()
        except Exception as error:
            log(f"bring-up failed: {type(error).__name__}: {error}")
            connection = None
            return str(error)
        last_ok = time.monotonic()
        STATE_PATH.write_text(json.dumps(state, indent=1))
        log(f"channel ready in {state['bringup_seconds']}s")
        return ""

    async def keepalive() -> None:
        """Every 15 s: if the agent is gone and the TV answers, reconnect.

        So the channel is back after a TV reboot or wake before anyone needs it.
        """
        while True:
            await asyncio.sleep(15)
            if lock.locked():
                continue
            async with lock:
                if await alive() or not await asyncio.to_thread(rootctl.sdb_available):
                    continue
                await ensure()

    def oversized(commands: list[str]) -> str:
        """Why a command cannot be sent, or "".

        The root agent refuses commands over 65,536 bytes, and the client
        library raises before sending.
        """
        for command in commands:
            size = len((PRIVILEGED + command).encode())
            if size > 65536:
                return f"{size} bytes; the agent limit is 65536 (split the command, or use put for files)"
        return ""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal last_ok
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            request = json.loads(line or b"{}")
            commands = request.get("commands") or []

            if request.get("op") == "status":
                payload = {"ok": True, "state": state, "up": connection is not None}
            elif request.get("op") == "put":
                # Raw WRITEFILE: 4 MiB ceiling, no base64 inflation, and NOT
                # subject to the 64 KB MaximumCommandBytes limit. This is the
                # right way to move a file; piping base64 through a command was
                # the mistake.
                #
                # The agent only accepts paths under its staging dir or runtime
                # dir, so write there and let the caller `cp` into place.
                items = request.get("items") or []
                async with lock:
                    if reason := await ensure():
                        payload = {"ok": False, "error": "channel_down", "detail": reason}
                    else:
                        started = time.monotonic()
                        written = []
                        try:
                            staging = state.get("staging") or ""
                            for item in items:
                                raw = base64.b64decode(item["data"])
                                target = item["path"]
                                if not target.startswith("/"):
                                    target = staging + target
                                # The agent writes with FileMode.CreateNew and
                                # throws if the path exists, which kills the
                                # connection. Remove first.
                                try:
                                    await connection.execute("rm -f " + target, 15.0)
                                except Exception:
                                    pass
                                blob = await connection.write_file(target, raw, 30.0)
                                written.append({"path": target, "bytes": blob.length})
                            last_ok = time.monotonic()
                            payload = {"ok": True, "written": written, "seconds": round(time.monotonic() - started, 3)}
                        except Exception as error:
                            await drop(f"write_file failed: {type(error).__name__}: {error}")
                            payload = {"ok": False, "error": "channel_down", "detail": str(error)}
            elif request.get("op") == "file":
                # Raw READFILE: up to 4 MiB per frame, no base64 inflation, and
                # not subject to the 1 MiB command-output cap. This is how
                # frames leave the TV at video rate.
                paths = request.get("paths") or []
                async with lock:
                    if reason := await ensure():
                        payload = {"ok": False, "error": "channel_down", "detail": reason}
                    else:
                        started = time.monotonic()
                        files = []
                        try:
                            for path in paths:
                                blob = await connection.read_file(path, 30.0)
                                files.append(
                                    {
                                        "path": path,
                                        "sha256": blob.sha256,
                                        "data": base64.b64encode(blob.data).decode("ascii"),
                                    }
                                )
                            last_ok = time.monotonic()
                            payload = {
                                "ok": True,
                                "files": files,
                                "seconds": round(time.monotonic() - started, 3),
                            }
                        except Exception as error:
                            await drop(f"read_file failed: {type(error).__name__}: {error}")
                            payload = {"ok": False, "error": "channel_down", "detail": str(error)}
            elif too_big := oversized(commands):
                # The caller's error, not a dead agent: answer it and keep the
                # connection (it was dropped and rebuilt for this once,
                # 2026-10-25).
                payload = {"ok": False, "error": "command_too_large", "detail": too_big}
            else:
                async with lock:
                    if reason := await ensure():
                        payload = {"ok": False, "error": "channel_down", "detail": reason}
                    else:
                        results = []
                        try:
                            for command in commands:
                                started = time.monotonic()
                                result = await connection.execute(PRIVILEGED + command, 30.0)
                                results.append(
                                    {
                                        "command": command,
                                        "exit_code": result.exit_code,
                                        "timed_out": result.timed_out,
                                        "stdout": result.stdout,
                                        "stderr": result.stderr,
                                        "seconds": round(time.monotonic() - started, 3),
                                    }
                                )
                            last_ok = time.monotonic()
                            payload = {"ok": True, "results": results}
                        except Exception as error:
                            await drop(f"execute failed: {type(error).__name__}: {error}")
                            payload = {"ok": False, "error": "channel_down", "detail": str(error)}
        except Exception as error:
            payload = {"ok": False, "error": "bad_request", "detail": str(error)}
        finally:
            try:
                writer.write((json.dumps(payload) + "\n").encode())
                await writer.drain()
            except Exception:
                pass
            writer.close()

    await ensure()
    keeper = asyncio.create_task(keepalive())

    server = await asyncio.start_unix_server(handle, str(SOCKET_PATH), limit=64 * 1024 * 1024)
    os.chmod(SOCKET_PATH, 0o600)
    log(f"daemon listening on {SOCKET_PATH}")

    try:
        async with server:
            await server.serve_forever()
    finally:
        keeper.cancel()
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                pass
        cleanup_package(state.get("package", ""))


def client_request(request: dict) -> dict:
    if not SOCKET_PATH.exists():
        return {"ok": False, "error": "no_daemon"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(60)
            sock.connect(str(SOCKET_PATH))
            sock.sendall((json.dumps(request) + "\n").encode())
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        return json.loads(b"".join(chunks).decode())
    except Exception as error:
        return {"ok": False, "error": type(error).__name__, "detail": str(error)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve")
    run = sub.add_parser("run")
    run.add_argument("commands", nargs="+")
    sub.add_parser("status")
    put = sub.add_parser("put")
    put.add_argument("local", nargs="+")
    pull = sub.add_parser("pull")
    pull.add_argument("paths", nargs="+")
    pull.add_argument("--outdir", default=".")

    args = parser.parse_args()

    if args.command == "serve":
        # asyncio is imported lazily (see _load_heavy_deps); `serve` is the only
        # path that needs it, but `run` here must import it before asyncio.run.
        _load_heavy_deps()
        try:
            return asyncio.run(serve_forever())
        except KeyboardInterrupt:
            return 0

    if args.command == "status":
        response = client_request({"op": "status"})
        print(json.dumps(response, indent=1))
        return 0 if response.get("ok") else 1

    if args.command == "put":
        items = []
        for local in args.local:
            data = Path(local).read_bytes()
            items.append({"data": base64.b64encode(data).decode("ascii"), "path": Path(local).name})
        response = client_request({"op": "put", "items": items})
        if not response.get("ok"):
            print(f"tvchan: {response.get('error')}: {response.get('detail', '')}".strip(), file=sys.stderr)
            return 1
        for entry in response.get("written", []):
            print(f"{entry['path']} {entry['bytes']} bytes")
        return 0

    if args.command == "pull":
        # tv/chan.py pull <remote> [<remote> ...] -- into --outdir
        response = client_request({"op": "file", "paths": args.paths})
        if not response.get("ok"):
            print(f"tvchan: {response.get('error')}: {response.get('detail', '')}".strip(), file=sys.stderr)
            return 1
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        for entry in response["files"]:
            name = Path(entry["path"]).name
            (outdir / name).write_bytes(base64.b64decode(entry["data"]))
            print(str(outdir / name))
        return 0

    response = client_request({"op": "exec", "commands": args.commands})
    if not response.get("ok"):
        print(f"tvchan: {response.get('error')}: {response.get('detail', '')}".strip(), file=sys.stderr)
        return 1
    for result in response["results"]:
        sys.stdout.write(result["stdout"])
        if result["stderr"]:
            sys.stderr.write(result["stderr"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
