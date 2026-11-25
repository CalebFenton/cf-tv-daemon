#!/usr/bin/env python3
"""Serve a fingerprint page for the Family Hub panel's Web View.

Logs every request (method, path, UA, source IP) to stdout, and serves a page
that reports what the panel's browser is and what it can reach.

    tools/fridge_probe.py            # listens on 0.0.0.0:8088
"""

from __future__ import annotations

import http.server
import json
import socketserver
import sys
from datetime import datetime

PORT = 8088

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>probe</title>
<style>
 body { font: 16px/1.5 monospace; background:#111; color:#eee; padding:1.5em; }
 h1 { font-size:1.2em; color:#7cf; }
 td { padding:.15em .8em .15em 0; vertical-align:top; }
 .k { color:#8a8; }
 .ok { color:#6f6; } .no { color:#f66; }
</style>
<h1>panel probe</h1>
<table id="t"></table>
<h1>reachability</h1>
<table id="r"></table>
<h1>raw</h1>
<pre id="j"></pre>
<script>
const facts = {};
const rows = [];
function add(t, k, v) {
  const tr = t.insertRow();
  tr.insertCell().className = 'k';
  tr.cells[0].textContent = k;
  tr.insertCell().textContent = (v === '' || v == null) ? '(empty)' : String(v);
}
function collect() {
  const n = navigator, s = screen;
  add(document.getElementById('t'), 'userAgent', n.userAgent);
  add(document.getElementById('t'), 'appVersion', n.appVersion);
  add(document.getElementById('t'), 'appName', n.appName);
  add(document.getElementById('t'), 'platform', n.platform);
  add(document.getElementById('t'), 'vendor', n.vendor);
  add(document.getElementById('t'), 'product', n.product);
  add(document.getElementById('t'), 'language', n.language);
  add(document.getElementById('t'), 'hardwareConcurrency', n.hardwareConcurrency);
  add(document.getElementById('t'), 'maxTouchPoints', n.maxTouchPoints);
  add(document.getElementById('t'), 'deviceMemory', n.deviceMemory);
  add(document.getElementById('t'), 'onLine', n.onLine);
  add(document.getElementById('t'), 'screen', s.width + 'x' + s.height + ' @' + s.colorDepth);
  add(document.getElementById('t'), 'availScreen', s.availWidth + 'x' + s.availHeight);
  add(document.getElementById('t'), 'devicePixelRatio', window.devicePixelRatio);
  add(document.getElementById('t'), 'innerWindow', innerWidth + 'x' + innerHeight);
  add(document.getElementById('t'), 'timezone', Intl.DateTimeFormat().resolvedOptions().timeZone);
  add(document.getElementById('t'), 'tzOffsetMin', new Date().getTimezoneOffset());
  add(document.getElementById('t'), 'localTime', new Date().toString());
  // Engine features that pin a Chromium milestone.
  const feats = {
    'WebAssembly': typeof WebAssembly,
    'Wasm.threads': WebAssembly && typeof WebAssembly.validate === 'function'
      ? WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,10,10,1,8,0,65,0,254,16,26,11])) : 'n/a',
    'BigInt': typeof BigInt,
    'globalThis': typeof globalThis,
    'Optional.chaining': (() => { try { return eval('({})?.a') === undefined } catch(e) { return false } })(),
    'Array.flat': typeof [].flat,
    'WeakRef': typeof WeakRef,
    'Atomics.waitAsync': typeof Atomics !== 'undefined' && typeof Atomics.waitAsync,
    'structuredClone': typeof structuredClone,
    'reportError': typeof reportError,
    'AbortSignal.timeout': typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout,
    'requestIdleCallback': typeof requestIdleCallback,
    'ResizeObserver': typeof ResizeObserver,
    'CSS.supports(display:grid)': CSS.supports('display','grid'),
    'OffscreenCanvas': typeof OffscreenCanvas,
    'WebGL2': (() => { try { return !!document.createElement('canvas').getContext('webgl2') } catch(e) { return false } })(),
    'SharedArrayBuffer': typeof SharedArrayBuffer,
    'Intl.Segmenter': typeof Intl !== 'undefined' && typeof Intl.Segmenter,
    'Array.at': typeof [].at,
    'Object.hasOwn': typeof Object.hasOwn,
    'findLast': typeof [].findLast,
  };
  for (const [k, v] of Object.entries(feats)) add(document.getElementById('t'), k, v);
  for (const k of Object.keys(facts)) delete facts[k];
  for (const row of document.querySelectorAll('#t tr')) {
    facts[row.cells[0].textContent] = row.cells[1].textContent;
  }
}
async function probeUrl(t, label, url, opts) {
  const tr = t.insertRow();
  tr.insertCell().className = 'k';
  tr.cells[0].textContent = label;
  const cell = tr.insertCell();
  const t0 = performance.now();
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 4000);
    const r = await fetch(url, Object.assign({ signal: ctl.signal, cache: 'no-store' }, opts || {}));
    clearTimeout(timer);
    const body = await r.text();
    const ms = Math.round(performance.now() - t0);
    cell.className = r.ok ? 'ok' : 'no';
    cell.textContent = 'HTTP ' + r.status + ' ' + ms + 'ms len=' + body.length +
      ' :: ' + body.slice(0, 160).replace(/\\s+/g, ' ');
    return body;
  } catch (e) {
    cell.className = 'no';
    cell.textContent = String(e) + ' after ' + Math.round(performance.now() - t0) + 'ms';
    return null;
  }
}
(async () => {
  collect();
  const r = document.getElementById('r');
  // The panel's own HTTP surface, from the panel itself.
  await probeUrl(r, 'self:17654/zc/0', 'http://127.0.0.1:17654/zc/0?action=getInfo');
  await probeUrl(r, 'self:17654 root', 'http://127.0.0.1:17654/');
  // Local files, if file:// egress is allowed from this origin.
  await probeUrl(r, 'file /proc/version', 'file:///proc/version');
  await probeUrl(r, 'file /etc/os-release', 'file:///etc/os-release');
  await probeUrl(r, 'file /proc/self/status', 'file:///proc/self/status');
  // Confirm our own origin works.
  await probeUrl(r, 'host echo', '/echo?' + encodeURIComponent(navigator.userAgent));
  document.getElementById('j').textContent = JSON.stringify(facts, null, 1).slice(0, 6000);
  // Ship the result home so it survives a Web View that closes on us.
  try {
    await fetch('/report', { method: 'POST', body: JSON.stringify(facts) });
  } catch (e) {}
})();
</script>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "probe/1"

    def log_message(self, format: str, *args: object) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{stamp}] {self.client_address[0]} {format % args}", flush=True)

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/echo"):
            body = json.dumps({"ua": self.headers.get("User-Agent"), "path": self.path}).encode()
            self._send(200, body, "application/json")
            return
        if self.path in ("/", "/index.html", "/probe"):
            self._send(200, PAGE.encode())
            return
        self._send(404, b"no")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path == "/report":
            print("=== REPORT ===", flush=True)
            print(f"UA: {self.headers.get('User-Agent')}", flush=True)
            try:
                data = json.loads(raw)
                for k, v in data.items():
                    print(f"  {k} = {v}", flush=True)
            except Exception:
                print(raw[:4000].decode("utf-8", "replace"), flush=True)
            print("=== END ===", flush=True)
        self._send(204, b"")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    with Server(("0.0.0.0", port), Handler) as httpd:
        print(f"probe on http://0.0.0.0:{port}/", flush=True)
        httpd.serve_forever()
