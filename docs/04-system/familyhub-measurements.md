# Panel measurements from the harness

Run 2026-11-25 through `tools/fridge_harness.py`, tasks #1–#6. Every value here
came from the panel's own Web View. Several correct earlier claims in this
directory, and those corrections are marked.

## Environment

| item | value | how |
|---|---|---|
| Renderer heap limit | **347 MB** | `performance.memory.jsHeapSizeLimit` |
| `hardwareConcurrency` | 4 | navigator |
| GPU | **Mali-450 MP** | `WEBGL_debug_renderer_info` |
| WebGL1 | yes | context creation |
| `crossOriginIsolated` | **false** | COOP/COEP verified sent; the Web View refuses |
| `mediaDevices` | **undefined** | no camera API at all |
| H.264 / MSE | `probably`, `2/2` codecs | `canPlayType`, `MediaSource` |
| VP8 / HEVC | no | `canPlayType` |
| `localStorage` | **writable** | 1 KB round-trip |
| `indexedDB` | present | typeof |

## Corrections

**`performance.memory` is a stub.** It reported `usedJSHeapSize` pinned at
9.5 MB through six failed 2 GB allocations *and* one successful 1 GiB
`ArrayBuffer`. A real metric cannot stay flat across that. Any probe that
concludes from it is wrong, including an earlier version of the GC probe in this
repo.

**Mali-450 MP is a route we had discounted.** `docs/07-prior-art/tiz0wn.md`
records that "both public Mali CVEs are closed on our driver", but that was
measured on the QN85D. On this panel there is a live `Mali-450 MP` GPU context
reachable from the renderer. Utgard-era driver CVEs are a separate escalation
family from the physmap devices, and are not yet checked here.

## The GC question, answered

CVE-2022-4262 needs bytecode flushing, which happens only on a major
(mark-compact) GC. Two earlier probes of this were wrong for two different
reasons, both worth keeping:

1. The tracked object was built inside an IIFE, so V8 scalar-replaced it and it
   was never a heap allocation. Nothing to collect.
2. Even with a real allocation, the check ran in the same job. By spec (WeakRef
   `KeepDuringJob`), a `WeakRef` target is kept alive until the job ends, so a
   synchronous check always reports "alive". Reproduced in Node: alive when
   checked synchronously, dead after one macrotask plus `gc()`.

The corrected probe drops the strong reference, yields a macrotask, then
pressures and observes. On the panel:

| trial | result |
|---|---|
| failed oversized allocations | target still alive |
| one 1 GiB allocation then free | target still alive |
| pure JS churn | target still alive |
| `FinalizationRegistry` callbacks | **2 fired** |

`finalizedCount = 2` is the answer: **collections do happen**, so bytecode
flushing is achievable and the bug is reachable. The `WeakRef` trials simply
never got a collection at the moment they looked; the registry, which reports
after the fact, proves one ran.

So the earlier plan holds: pressure with oversized allocations, and do not trust
`performance.memory` to tell you when it worked. Use a `FinalizationRegistry`
sentinel.

## What this means for the port

Unchanged on the main line: the primitive layer is still the next step, and the
escalation still depends on a reachable physmap device, which is still unmeasured.

New information that affects it:

- The renderer **does** have media support (H.264 + MSE), so it is not a stripped
  sandbox; the group memberships that come with media are plausible.
- The renderer has **no camera API**, which is a deliberate strip — do not read
  `mediaDevices` absence as general restriction.
- `localStorage` works, which is what the harness's crash cursor relies on.
- Mali-450 MP opens a second escalation family worth a survey, distinct from
  `/dev/sdp_mem`, `/dev/ntksys` and `/dev/video40`.

## The Mali finding, resolved

`WEBGL_debug_renderer_info` reports `ARM` / `Mali-450 MP`, and the capability
envelope agrees rather than contradicting it:

| limit | value |
|---|---|
| `MAX_TEXTURE_SIZE` | 4096 |
| `MAX_VERTEX_UNIFORM_VECTORS` | 256 |
| `MAX_FRAGMENT_UNIFORM_VECTORS` | 256 |
| `MAX_VARYING_VECTORS` | 12 |
| extensions | 9 (`OES_standard_derivatives`, `WEBGL_depth_texture`, …) |
| `WEBGL_draw_buffers`, `OES_texture_float` | absent |
| `VERSION` | `WebGL 1.0 (OpenGL ES 2.0 Chromium)` |

Nine extensions and a 4096 cap is genuine Utgard silicon, not a rewritten
string: the User-Agent is spoofable but this envelope is not.

**Consequence: the Mali escalation family is weak here.** Mali-450 is
**Utgard**, which ships the legacy `mali` driver, while every public Mali kernel
CVE of the last five years targets `mali_kbase` (Midgard/Bifrost/Valhall):

| CVE | targets | applies to Utgard |
|---|---|---|
| CVE-2022-20186 (alias overflow) | kbase | no |
| CVE-2022-38181 (shrinker mmap) | kbase | no |
| CVE-2023-4211 (tracking-page UAF) | kbase r<43 | no |
| CVE-2025-8045 (CSF queue UAF) | Valhall r53–r54 | no |

So the earlier hope that Mali offered a second escalation family does not hold
on this panel. `docs/05-firmware/firmware-format.md` reached a compatible
conclusion for the QN85D by disassembly (`sdp_gpu.ko` `r23p0`, UK 11.18, two
CVEs backported); this panel is a different GPU generation again.

Two facts worth keeping:

- The `sdp_gpu.ko` `r23p0` / UK 11.18 signature in the TV docs is **kbase**
  terminology, so the TVs and this panel do not share a GPU driver family.
  Do not carry TV Mali conclusions across.
- Utgard's legacy driver has very few public CVEs at all. If the browser route
  needs an escalation primitive, the physmap devices remain the better bet.

Unverified: whether `/dev/mali0` exists on this panel and with what permissions.
That still needs a shell.

## First exploit attempt: result and what it tells us

`tools/payloads/cve_2022_4262.js` ran the reference exploit's feedback-slot loop
on the panel (task #9, 2026-11-25). It did not crash the renderer and it did not
produce a map change: it **hung** the renderer in a blocking loop, which killed
the Web View.

Control: the same payload on Node 24 (V8 13.x, where this bug has been fixed for
years) completes cleanly and reports `no-map-change`. So the payload itself is
well-formed; the difference is the engine.

Interpretation, stated as inference rather than fact:

- A hang rather than an exception means the loop ran but the slot alignment did
  not match this engine's bytecode, so it walked off into something that does not
  terminate.
- The reference exploit documents slot indices measured on V8 10.8 (Chromium
  108). This panel measures as Chromium 105 — close, but the whole bug is a slot
  index, so "close" is not close enough. The `SetNamedStrict` slot that must land
  at index 8 after the flush is the thing to measure, and there is no way to read
  it without a primitive.

Practical consequence: this is now a tuning problem, not a research problem. The
loop structure is right; the slot indices and the `makeBig`/`makeSmall` shapes
need to match this build. The reference's own guidance is to vary the ballast
expression until the vector and metadata lengths agree, and that is a search we
can run cheaply now that a hang costs one deadline instead of the session.
