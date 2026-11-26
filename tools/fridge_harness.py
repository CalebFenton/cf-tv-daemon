#!/usr/bin/env python3
"""Task harness for the Family Hub panel's Web View.

The panel loads one URL and then keeps asking for work, so iterating on the
exploit costs no TV interaction. Designed around the failure that matters: a
wrong V8 payload segfaults the renderer and the page dies. State lives on the
server and in localStorage, so when the page is reloaded it resumes at the task
that killed it instead of starting over.

    tools/fridge_harness.py                     # serve on 0.0.0.0:8090
    tools/fridge_harness.py --port 8090

    # then, from another shell:
    tools/fridge_harness.py enqueue 'navigator.userAgent'
    tools/fridge_harness.py watch               # tail results as they land
    tools/fridge_harness.py tasklist

The panel URL is http://<this-host>:<port>/.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

STATE_PATH = Path(os.environ.get("FRIDGE_HARNESS_STATE", "/tmp/fridge-harness/state.json"))
MAX_ATTEMPTS = 3
POLL_PAUSE_MS = 400


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------- state

@dataclass
class Task:
    id: int
    js: str
    label: str = ""
    status: str = "pending"       # pending | running | ok | error | crashed | skipped
    attempts: int = 0
    session: str = ""
    value: str | None = None
    error: str | None = None
    ms: int | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None


class State:
    """Task queue plus a result log, persisted so a restart loses nothing."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.tasks: list[Task] = []
        self.next_id = 1
        self.log: list[dict] = []
        self.load()

    # -- persistence

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self.next_id = raw.get("next_id", 1)
        self.log = raw.get("log", [])[-500:]
        for t in raw.get("tasks", []):
            try:
                self.tasks.append(Task(**t))
            except TypeError:
                continue

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "next_id": self.next_id,
            "tasks": [asdict(t) for t in self.tasks[-300:]],
            "log": self.log[-500:],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        tmp.replace(self.path)

    # -- queue

    def enqueue(self, js: str, label: str = "") -> Task:
        with self.lock:
            task = Task(id=self.next_id, js=js, label=label)
            self.next_id += 1
            self.tasks.append(task)
            self.save()
            return task

    def claim(self, session: str) -> Task | None:
        """Hand out the next task. Re-serves one whose session vanished (crash)."""
        with self.lock:
            for task in self.tasks:
                if task.status == "pending":
                    task.status = "running"
                    task.session = session
                    task.attempts += 1
                    self.save()
                    return task
                if task.status == "running" and task.session != session:
                    # The session that took it is gone. Either the page was
                    # reloaded or the renderer died mid-task; both mean retry.
                    if task.attempts >= MAX_ATTEMPTS:
                        task.status = "crashed"
                        task.finished = time.time()
                        self._log(task, f"gave up after {task.attempts} attempts")
                        self.save()
                        continue
                    task.status = "running"
                    task.session = session
                    task.attempts += 1
                    self.save()
                    return task
            return None

    def finish(self, task_id: int, ok: bool, value: str | None,
               error: str | None, ms: int | None) -> None:
        with self.lock:
            task = next((t for t in self.tasks if t.id == task_id), None)
            if task is None:
                return
            task.status = "ok" if ok else "error"
            task.value, task.error, task.ms = value, error, ms
            task.finished = time.time()
            self._log(task, value if ok else error)
            self.save()

    def _log(self, task: Task, text: str | None) -> None:
        head = f"#{task.id}"
        if task.label:
            head += f" [{task.label}]"
        if task.attempts > 1:
            head += f" (attempt {task.attempts})"
        first, _, rest = (text or "").partition("\n")
        self.log.append({"t": now(), "task": task.id, "status": task.status,
                         "head": head, "line": first[:400],
                         "more": (first[len(first):] + rest)[:2000] if rest else ""})


STATE: State


# ---------------------------------------------------------------- page

PAGE = r"""<!doctype html>
<meta charset="utf-8">
<title>harness</title>
<style>
 body{font:13px/1.45 ui-monospace,Menlo,monospace;background:#0d1117;color:#c9d1d9;padding:1em}
 h1{font-size:1em;color:#58a6ff;margin:.2em 0}
 #log div{padding:.12em 0;border-bottom:1px solid #161b22;white-space:pre-wrap;word-break:break-word}
 .ok{color:#3fb950}.error{color:#f85149}.crashed{color:#d29922}
 .muted{color:#6e7681}
</style>
<h1>harness <span id="st" class="muted">connecting</span></h1>
<div id="log"></div>
<script>
"use strict";
const SESSION = (crypto.getRandomValues(new Uint32Array(2)).join('-'));
const CURSOR_KEY = 'harness.cursor';
let cursor = Number(localStorage.getItem(CURSOR_KEY) || 0);
const st = document.getElementById('st');

function render(entry) {
  const d = document.createElement('div');
  d.className = entry.status || '';
  d.textContent = entry.head + '  ' + (entry.line || '');
  const log = document.getElementById('log');
  log.prepend(d);
  while (log.childElementCount > 60) log.lastElementChild.remove();
}

async function post(path, body) {
  return fetch(path, {method: 'POST', cache: 'no-store',
                      headers: {'Content-Type': 'text/plain'},
                      body: JSON.stringify(body)});
}

// The task source is a string evaluated in page scope. It may return a value or
// a promise; anything returned is formatted and shipped back.
async function run(task) {
  const t0 = performance.now();
  let out, ok = true, err = null;
  try {
    let fn;
    try { fn = new Function('return (async () => (' + task.js + '))()'); }
    catch (e) { fn = new Function('return (async () => { ' + task.js + '\n })()'); }
    out = await fn();
    if (out !== undefined) {
      try { out = JSON.stringify(out); } catch (e) { out = String(out); }
    } else { out = 'undefined'; }
  } catch (e) {
    ok = false;
    err = (e && e.stack) ? String(e.stack) : String(e);
    out = null;
  }
  const ms = Math.round(performance.now() - t0);
  // Show it immediately: a later task may kill the page before this is read.
  render({status: ok ? 'ok' : 'error', head: '#' + task.id + ' ' + ms + 'ms',
          line: ok ? out : err.split('\n')[0]});
  try { await post('/report', {id: task.id, ok, value: out, error: err, ms}); }
  catch (e) { /* server down; task stays running and will be re-served */ }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async function loop() {
  let backoff = 400;
  for (;;) {
    try {
      const r = await fetch('/task?session=' + SESSION + '&after=' + cursor,
                            {cache: 'no-store'});
      const t = await r.json();
      st.textContent = 'session ' + SESSION.slice(0, 8);
      backoff = 400;
      if (t.id) {
        cursor = t.id;
        // Persist before running: if this task crashes the renderer, the next
        // load asks for the one after it, not the whole queue again.
        localStorage.setItem(CURSOR_KEY, String(cursor));
        await run(t);
      } else {
        await sleep(t.pause_ms || 400);
      }
    } catch (e) {
      st.textContent = 'server unreachable, retrying';
      await sleep(backoff);
      backoff = Math.min(backoff * 2, 8000);
    }
  }
})();
</script>
"""


# ---------------------------------------------------------------- server

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "fridge-harness/1"

    def log_message(self, fmt: str, *args: object) -> None:
        # Page polls every 400 ms; logging each would bury the real output.
        if os.environ.get("HARNESS_VERBOSE"):
            print(f"[{now()}] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode())

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            return
        if path == "/task":
            params = dict(
                part.split("=", 1) for part in query.split("&") if "=" in part)
            session = params.get("session", "")
            task = STATE.claim(session)
            if task is None:
                self._json({"pause_ms": POLL_PAUSE_MS})
            else:
                self._json({"id": task.id, "js": task.js})
            return
        if path == "/log":
            self._json(STATE.log[-200:])
            return
        if path == "/health":
            self._json({"ok": True, "tasks": len(STATE.tasks)})
            return
        self._send(404, b"{}")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        path = self.path.partition("?")[0]
        if path == "/enqueue":
            # The CLI talks to a running server this way; writing the state file
            # from a second process would be invisible to it.
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._json({"ok": False}, 400)
                return
            task = STATE.enqueue(body.get("js", ""), body.get("label", ""))
            print(f"[{now()}] queued #{task.id} {task.label}", flush=True)
            self._json({"id": task.id})
            return
        if path == "/report":
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._json({"ok": False}, 400)
                return
            STATE.finish(int(body.get("id", 0)), bool(body.get("ok")),
                         body.get("value"), body.get("error"), body.get("ms"))
            entry = STATE.log[-1] if STATE.log else {}
            print(f"[{entry.get('t', now())}] {entry.get('status', '?'):7} "
                  f"{entry.get('head', '')}  {entry.get('line', '')}", flush=True)
            self._json({"ok": True})
            return
        self._send(404, b"{}")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


# ---------------------------------------------------------------- cli

def server_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def enqueue_remote(port: int, js: str, label: str) -> int:
    body = json.dumps({"js": js, "label": label}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/enqueue", data=body,
                                 headers={"Content-Type": "text/plain"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return int(json.loads(r.read())["id"])


def cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    sub = parser.add_subparsers(dest="cmd")

    p_enq = sub.add_parser("enqueue", help="queue JS for the panel to run")
    p_enq.add_argument("js", nargs="?", help="expression or statements; the value is returned")
    p_enq.add_argument("--file", "-f", type=Path,
                       help="queue a .js payload file instead of inline JS")
    p_enq.add_argument("--label", default="")

    p_watch = sub.add_parser("watch", help="print results as they arrive")
    p_watch.add_argument("--from", type=int, default=0, dest="since")

    sub.add_parser("tasklist", help="show the queue")
    return serve(parser.parse_args(argv))


def serve(args: argparse.Namespace) -> int:
    global STATE
    STATE = State(args.state)
    STATE.save()

    if args.cmd == "enqueue":
        js = args.js
        label = args.label
        if args.file:
            try:
                js = args.file.read_text()
            except OSError as exc:
                print(f"cannot read {args.file}: {exc}", file=sys.stderr)
                return 2
            label = label or args.file.stem
        if not js:
            print("nothing to enqueue: give JS or --file", file=sys.stderr)
            return 2
        if server_alive(args.port):
            task_id = enqueue_remote(args.port, js, label)
        else:
            task_id = STATE.enqueue(js, label).id
        print(f"queued #{task_id}" + (f" [{label}]" if label else ""))
        return 0

    if args.cmd == "tasklist":
        if not STATE.tasks:
            print("queue empty")
        for t in STATE.tasks[-40:]:
            line = f"#{t.id:3} {t.status:8} {t.attempts}x  {(t.label or t.js)[:70]}"
            if t.value:
                line += f"\n        -> {t.value[:200]}"
            if t.error:
                line += f"\n        !! {t.error.splitlines()[0][:200]}"
            print(line)
        return 0

    if args.cmd == "watch":
        seen = args.since
        printed = False
        try:
            while True:
                for entry in STATE.log:
                    if entry["task"] > seen:
                        print(f"[{entry['t']}] {entry['status']:7} "
                              f"{entry['head']}  {entry['line']}")
                        seen = entry["task"]
                        printed = True
                if not printed:
                    print("waiting for a result (Ctrl-C to stop)")
                    printed = True
                time.sleep(0.4)
        except KeyboardInterrupt:
            return 0

    # default: serve
    print(f"harness on http://0.0.0.0:{args.port}/  state={args.state}", flush=True)
    print("panel: open that URL in Developer Mode > Web View", flush=True)
    with Server(("0.0.0.0", args.port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
