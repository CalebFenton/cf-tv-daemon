# Panel browser — what it actually is

Measured from the Family Hub panel itself, 2026-11-25, by loading
`tools/fridge_probe.py` in Developer Mode → Web View.

## 1. The `WebEngine Version` line is not the browser version

Developer Mode → `WebEngine Version` reads **`76.0.3809.146`**. That string is
**wrong for the browser** — do not use it to pick an exploit.

The probe's feature table contradicts it. Every one of these was present:

| feature | landed in | present |
|---|---|---|
| optional chaining | M80 | yes |
| `WeakRef` | M84 | yes |
| `Intl.Segmenter`, `Atomics.waitAsync` | M87 | yes |
| `Array.prototype.at` | M92 | yes |
| `Object.hasOwn` | M93 | yes |
| `Array.prototype.findLast` | M97 | yes |
| `structuredClone` | M98 | yes |
| `AbortSignal.timeout` | **M103** | yes |

So the engine is **at least Chromium 103** (May 2022) — 27 milestones newer than
the menu claims. Either `WebEngine Version` names a different component (a
widget/EFL layer), or it is a stale constant. It is not the V8 build.

Consequence: **every 2019–2021 public V8 chain is patched out here.**
CVE-2020-6383 (Synacktiv's Q60T bug, fixed M80) and CVE-2020-6418 are dead on
this device. My earlier note that the panel was older than the Q60T was wrong;
it is years newer.

The upper bound is unresolved. `AbortSignal.timeout` is the highest milestone
confirmed, so the engine is somewhere in **M103–M109** unless a later feature
tests true. That range matters: CVE-2022-4262 (V8 type confusion, exploited in
the wild, fixed in `108.0.5359.94`) falls inside it.

## 2. The User-Agent is a configurable lie

The panel sends:

```
Mozilla/5.0 (Linux; Tizen 3.0; FamilyHub) AppleWebKit/537.3 (KHTML, like Gecko)
  Version/2.3 Mobile Safari/537.3
```

Three things prove this is an app-level override, not the engine:

- It claims **Tizen 3.0** on a device reporting **Tizen 7.2.1**.
- `Version/2.3 … Mobile Safari` is a WebKit/Safari token; no Chromium emits it.
- The Web View settings offer **two UA choices** (this one, and an Android one),
  so the string is a setting, not a fact.

The real engine still shows through in other properties:
`vendor = "Google Inc."`, `product = "Gecko"`, `platform = "Linux armv7l"`,
plus the M103+ feature set.

**Practical rule: never version-fingerprint this device by User-Agent.** It also
means switching to the Android UA changes what *remote servers* serve the panel,
not what the panel runs. It cannot change which V8 is executing.

## 3. Reachability from the Web View (observed)

| target | result |
|---|---|
| `http://127.0.0.1:17654/zc/0` (its own Spotify eSDK) | `TypeError: Failed to fetch` |
| `file:///proc/version`, `file:///etc/os-release`, `file:///proc/self/status` | `TypeError: Failed to fetch` |
| `http://<host>:8088/echo` (the probe's own origin) | HTTP 200, 114 ms |

So the Web View runs under a strict CSP/origin policy: it cannot read its own
loopback service and cannot read local files. That closes the cheap
"read `/proc` from the browser" route to the driver question.

Other observed properties: `hardwareConcurrency = 4`, `screen 540x960 @24`,
`devicePixelRatio 2`, `maxTouchPoints 0`, `WebGL2 = false`,
`SharedArrayBuffer = undefined`, timezone `CST6CDT`.

`WebGL2` off and `SharedArrayBuffer` absent mean a reduced attack surface: the
usual GPU- and wasm-thread-based primitives are unavailable, so a V8 exploit
here would have to be pure JS-heap work.

## 4. What the probe does not answer

Whether the kernel exposes `/dev/sdp_mem` or `/dev/ntksys` with the world-writable
permissions the TVs have. The browser cannot read the filesystem (§3), so that
question needs either a root-level primitive first or the panel's own
`eMMC Information` screen.

## 5. Reproducing

```sh
tools/fridge_probe.py 8088        # supervised service, binds 0.0.0.0:8088
# panel: Developer Mode → Web View → http://<this-host-ip>:8088/
```

The page prints the feature table and `POST`s the same data to `/report`, which
the server logs, so the result survives the Web View closing. The UA and the
full report are logged on every load.
