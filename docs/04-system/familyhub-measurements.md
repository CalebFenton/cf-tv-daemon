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
