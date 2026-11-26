# Where the Family Hub work stands, and what to try next

Written 2026-11-25 after the first exploit attempts. This is the planning
document: what has been ruled out, what remains, and in what order.

## 1. What is established (measured, not inferred)

| fact | value | confidence |
|---|---|---|
| Panel OS | Tizen 7.2.1, One UI 7.0, firmware `AFH-US-KTM-24-T7XXMU` | read off the panel |
| Web engine | **Chromium 105** (M105 features true, M106 false) | measured twice |
| User-Agent | a configurable lie (`Tizen 3.0 … Mobile Safari`); switching to the Android UA changed nothing | measured, two runs |
| GPU | **Mali-450 MP**, Utgard class (9 extensions, 4096 max texture) | measured |
| Renderer heap limit | 347 MB | measured |
| `performance.memory` | **a stub**, useless for GC detection | measured |
| Major GC | **does happen** (`FinalizationRegistry` fires) | measured twice |
| Network surface | **one port**, 17654, Spotify eSDK only | full TCP scan |
| SDB | absent, even in Developer Mode | measured |
| Web View | cannot read loopback or `file://` | measured |
| Partitions | `p17` csa, `p20` /opt (219 MB), `p21` /opt/usr (2.9 GB) | read off panel |
| Service menus | Engineer Mode (`ABABAB`), Developer Mode (`ABAAAB` + `tizenreflcd1!`) | from panel, user-confirmed |

## 2. What is ruled out

**Mali.** Mali-450 is Utgard, which uses the legacy `mali` driver. Every public
Mali kernel CVE since 2022 targets `mali_kbase` (Midgard/Bifrost/Valhall):
CVE-2022-20186, CVE-2022-38181, CVE-2023-4211, CVE-2025-8045. None apply. The
`sdp_gpu.ko` r23p0 signature in the TV notes is kbase terminology, so the TVs and
this panel do not even share a GPU driver family.

**SD-B-based routes.** No SDB listener exists to attack, so the entire TV
toolchain (SVE-2025-50109 injection, archive-root) has no entry point.

**OCF/CoAP.** No answer on 5683/5684 or 49152–49160.

**The 2017 hardware recipe.** That unit had an ext4 rootfs and `/usr/apps`; this
one has the TV partition shape. Different platform generation.

## 3. What is now ruled out by measurement: CVE-2022-4262

This one deserves detail, because it was the main plan and the evidence against
it is direct.

The bug's root cause is a *semantic* disagreement: bytecode is flushed by a major
GC and rebuilt, and during the rebuild V8's parser can decide differently whether
a direct sloppy `eval` in a scope may extend that scope's variables
(`sloppy_eval_can_extend_vars_`). That disagreement is observable with no memory
corruption at all: if the decision flips, `typeof` of the eval'd variable changes
across the flush.

`tools/payloads/flush_detector2.js` tests exactly that, with a control:

| target | sloppy control (must leak) | class-body eval | across flush |
|---|---|---|---|
| Node 24 (V8 13.x) | `string:sloppy` ✓ | `ReferenceError` | no change |
| **the panel** | `string:sloppy` ✓ | `ReferenceError` | **no change** |

The panel behaves identically to a modern V8 where the bug has been fixed for
years. Three shapes were tried (computed class key, class method, and the
reference exploit's own expression arrangement), all stable.

**Conclusion: the scope decision is consistent here, so the root cause is not
present.** The earlier exploit attempt did not fail because of wrong slot
indices; it hung because it was driving a bug that is not there.

Caveat, stated honestly: no variation is weaker evidence than variation. It is
possible that the flush simply never discarded the specific bytecode, so the
decision was never recomputed. What makes the negative convincing is that the
same probe on Node — where the bug is definitively fixed — gives the same answer,
and the panel matches in every shape. A positive would have been proof; the
negative is strong evidence, not proof.

## 4. What that leaves

The browser route needs *some* code execution as `uid=5001`. With 4262 out and
CVE-2020/2021 bugs patched (engine is M105), the remaining candidates are bugs
fixed in M106–M109 where the abused feature predates M105:

| CVE | kind | fixed | note |
|---|---|---|---|
| CVE-2022-3723 | V8 type confusion, ITW | 107.0.5304.87 | same family as 4262; needs the same kind of tuning |
| CVE-2022-4262 | V8 type confusion, ITW | 108.0.5359.94 | **ruled out by measurement** |

CVE-2022-3723 is the only remaining V8 candidate in that window. Before
committing to it, the same root-cause probe should be run against it — the
lesson from 4262 is that *version range membership is not reachability*. A bug
can be "present" by version and unreachable because the trigger depends on
machinery this build does not have.

## 5. The honest alternative

There is a real chance the browser route is closed on this device: it is a
frozen engine with Samsung's own patches, the renderer is deliberately stripped
(`mediaDevices` absent, no `SharedArrayBuffer`, no isolation), and the one bug we
could confirm as in-range turns out not to reproduce.

If CVE-2022-3723 also fails its root-cause probe, the browser route should be
declared dead rather than pursued further, and the effort should go to the one
route with public evidence on this hardware family: **physical access**.

That is not a satisfying answer, but it is the accurate one, and the physical
route has concrete published precedent for a Samsung fridge panel (doitaljosh's
eMMC + UART work), plus a writable `/opt` confirmed on this device.

## 6. Recommended order

1. **Run the root-cause probe for CVE-2022-3723.** Cheap, safe, and it either
   revives the browser route or closes it. Do this before writing any exploit.
2. **If it reproduces**, tune the exploit against this engine.
3. **If it does not**, stop the browser work and plan the physical route:
   identify the board revision, find UART, and read the partition table before
   touching anything.
4. In parallel, keep the harness running for cheap measurement: it costs the
   panel nothing and answers questions we have not thought to ask yet.

## 7. Why no further V8 candidate is worth trying

After 4262 was ruled out, the obvious next step was CVE-2022-3723 (V8 Turbofan
FieldType-`None` logic bug, fixed 107.0.5304.87, also exploited in the wild).

**It cannot be tested safely or decisively from JavaScript.** Its precondition —
a field's FieldType decaying to `None` after its backing Map is collected — is
invisible to script: V8 does not expose Map identity, and the only proxy is
deoptimization timing. `tools/payloads/flush_detector_3723.js` tries it, and the
numbers are noise: warm 0.77 ms vs rebuild 0.86 ms on a 4-core ARMv7 panel. That
is not evidence either way, and reporting it as such would be dishonest.

The same logic applies to every remaining candidate in the window:

| candidate | testable without a bug? |
|---|---|
| CVE-2022-3723 (FieldType None) | **no** — needs Map identity, not exposed |
| anything WasmGC-based | no — WasmGC ships in M119, this is M105 |
| anything patched in M106+ with a JS-observable precondition | none found |

So the position is: **the browser route cannot be advanced by measurement.** The
next step would be writing a full exploit for a bug whose presence is unknown,
on a frozen engine with Samsung's own patches, against a renderer that is
deliberately stripped. The expected value is poor and the cost is not.

## 8. Decision

Stop the browser exploitation work. Record what was learned, keep the harness as
a measurement tool, and move to the route with published precedent on this
hardware family: physical access.

If that is ever revisited, the entry point is not another V8 bug but a *leak*:
anything that reveals the engine's internal state would turn every remaining
question from guesswork into measurement. No such leak was found, and the Web
View blocks the obvious ones (`file://`, loopback).
