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
the menu claims. §3b pins it exactly to **105**. Either `WebEngine Version` names
a different component (a widget/EFL layer), or it is a stale constant. It is not
the V8 build.

Consequence: **every 2019–2021 public V8 chain is patched out here.**
CVE-2020-6383 (Synacktiv's Q60T bug, fixed M80) and CVE-2020-6418 are dead on
this device. My earlier note that the panel was older than the Q60T was wrong;
it is years newer.

The upper bound is pinned in §3b: **Chromium 105**. Read §3b before drawing any
conclusion about which bugs are live — the direction of the comparison matters,
and an earlier version of this file got it backwards.

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
`devicePixelRatio 2`, `maxTouchPoints 0`, `WebGL2 = false`, timezone
`CST6CDT`.

**Correction:** an earlier run reported `SharedArrayBuffer = undefined` and I
read that as a capability limit on the panel. That was my own bug — every
Chromium hides `SharedArrayBuffer` unless the page is cross-origin isolated with
`Cross-Origin-Opener-Policy: same-origin` and
`Cross-Origin-Embedder-Policy: require-corp`. The probe now sends both, so the
next run measures the engine rather than the page's headers.

## 2b. The Android UA changes nothing

The Web View offers a second User-Agent (Android). Loading the probe under it
produced an **identical feature table** — same milestones true, same ones false —
and only the UA string and `appVersion` changed:

```
Mozilla/5.0 (Linux; U; Android 2.2.1; en-us; ADR6400L 4G Build/FRG83D)
  AppleWebKit/533.1 (KHTML, like Gecko) Version/4.0 Mobile Safari/533.1
```

`vendor` stayed `Google Inc.`, `product` stayed `Gecko`, `platform` stayed
`Linux armv7l`. This is the control experiment that settles it: the UA is an
app-level setting with no effect on the engine. It changes only what remote
sites serve.

`WebGL2` is off, so the GPU-backed primitives are unavailable; any V8 work here
has to be pure JS-heap. `WebGL1` availability is now measured separately.

## 3b. The version window: Chromium 105

Three runs, each narrowing the bracket:

| milestone | present | run |
|---|---|---|
| M98 `structuredClone` | yes | 1 |
| M103 `AbortSignal.timeout` | **yes** | 1 |
| M105 `HTMLScriptElement.supports`, CSS `:has()` | **yes** | 3 |
| M106 `Intl.NumberFormat.formatRange` | **no** | 3 |
| M110 `Array.toSorted` / `.with` / `.toReversed` | no | 1 |
| M112 RegExp `v` flag | no | 1 |
| M117+ | no | 1 |
| M90 `navigator.userAgentData` | absent | all |

**The engine is Chromium 105** (released 2022-08). M105 true and M106 false is an
exact bracket, not an approximation.

The consequence for exploitation — **this is the opposite of what an earlier
version of this file said**:

- A fix shipped in a *later* milestone means the bug is **present** in M105.
  Comparing an affected engine to a fix version only tells you which side of the
  fix you are on, and M105 is on the vulnerable side of everything released
  afterwards.
- So Chromium 105 is exposed to **every V8 bug fixed from M106 through the
  present day** — roughly a three-and-a-half-year window, not a 2022-only one.
- Only bugs fixed at or before M104 are truly patched here (CVE-2020-6383,
  CVE-2020-6418 and the 2019–2021 corpus).

Candidate bugs, all of which list an affected range that includes M105:

| CVE | kind | fixed in |
|---|---|---|
| CVE-2022-3723 | V8 type confusion, exploited in the wild | 107.0.5304.87 |
| CVE-2022-4262 | V8 type confusion, exploited in the wild | 108.0.5359.94 |
| CVE-2023-2033 | V8 type confusion, exploited in the wild | 112.0.5615.121 |
| CVE-2023-3079 | V8 type confusion, exploited in the wild | 114.0.5735.110 |
| CVE-2024-0519 | V8 out-of-bounds, exploited in the wild | 120.0.6099.224 |
| CVE-2024-2887 | **Wasm** type confusion, Pwn2Own 2024 | 123.0.6312.86 |

`CVE-2024-2887` is the interesting one for this panel: it is a WebAssembly type
confusion reached from ordinary JS, WebAssembly is enabled here, and its affected
range is "all Chrome before 123". The public ZDI write-up describes the primitive
as a universal type confusion in the Wasm type section.

## 3c. Cross-origin isolation is refused

The server sends both `Cross-Origin-Opener-Policy: same-origin` and
`Cross-Origin-Embedder-Policy: require-corp` (verified with `curl -D-`), and the
panel still reports:

```
crossOriginIsolated = false
SharedArrayBuffer    = undefined
Atomics              = object
```

So this is the Web View refusing isolation, not a missing header. Without
`SharedArrayBuffer`, the common V8 exploit building blocks — racy JIT-adjacent
primitives, worker-shared backing stores — are unavailable. Any exploit here
must be pure single-page JS-heap work.

`WebGL1` is available (`true`), `WebGL2` is not, so GPU-backed primitives are
out as well.

`navigator.userAgentData` being absent is still unexplained: Chromium shipped it
in M90, so an M105 engine should have it. Either Samsung compiled it out, or this
is not a stock Chromium build. Open question, not a contradiction.

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
