# Family Hub: who has done this, and what transfers

Survey of public work, 2026-11-25. The point is to avoid reinventing the chain.
Written against this panel: Tizen 7.2.1, Chromium **105**, ARM32, Web View.

## 1. Short answer

**Nobody has published a Family Hub root.** Several people have published the
two halves of the chain on closely related Samsung hardware, and one of them —
`commit-regrets/samsung_tizen_tv_root` — is a browser-to-root chain on Tizen 9
ARM32 that needs no SDB, no dev-mode and no hardware. That is the closest
existing work by a wide margin.

## 2. The closest published chain

### `commit-regrets/samsung_tizen_tv_root` (Tizen 9, 2024 Neo QLED)

Target: 2024 Neo QLED, Tizen 9, QNX1D, kernel 5.4.249 ARM32, Chromium
**120.0.6099.5**. ★30, pushed 2026-11-13.

Flow (`docs/EXPLOIT.md`):

```
browser page --CVE-2024-2887--> renderer R/W + ARM shellcode (uid 5001)
                                  |
                    /dev/video40 mmap(vm_pgoff = phys page)
                                  |
                arbitrary physical read/write (remap_pfn_range)
                                  |
        find kernel Image -> init_task -> our task -> overwrite cred
                                  |
                        uid=0 + CapEff=0x3fffffffff
```

Their own `docs/ROOT-METHODS.md` verdict table:

| # | method | verdict |
|---|---|---|
| 1 | browser RCE + V4L2 `/dev/video40` physmap → cred patch | **WORKS** |
| 2 | on-boot dotnet-inhouse app with replaced IL | **WORKS** |
| 3 | boot payload + TCP 31337 root channel | **WORKS** |
| 4 | `sdb` shell / `appinstall` injection (SVE-2025-50109) | DEAD |
| 5 | `sdbd root:on` | DEAD |
| 8 | `/dev/tzmem` | DESIGNED SAFELY (no physmap) |

Note entry 4: the SVE-2025-50109 injection that this repo's TV kit relies on is
**dead** on their target. Their physmap devices (`/dev/video40`) are also a
**different family** from the TV kit's `/dev/ntksys` and `/dev/sdp_mem`.

### `J-Delsinne/samsung-tizen9-root`

Packaging of the above: five scripts, persistence (MCS DLL hijack, `uep31.ko`,
dropbear on :2222), one-command install. Published 2026-11-18 (one week ago).
Built on `commit-regrets`.

### `hdbreaker/samsung-smarttv-tizen-9-chromium-120-v8-rce`

The CVE-2024-2887 exploit itself, ARM32, with a full Spanish-language blog:
WasmGC type confusion → `addrOf`/`read32`/`write32` → anchor → `elf_base` →
dump 82.6 MB of `libchrome.so` → fake vtable → ROP `mprotect` → RWX → ARM
shellcode → reverse shell as `owner`. No ADB, no dev mode.

## 3. The blocker for this panel: WasmGC

CVE-2024-2887 is a **WebAssembly GC** type confusion. WasmGC reached Chrome
**119** (origin trial M112–M118, shipped by default M119). This panel is
**Chromium 105**.

So the single most relevant public chain **cannot run here** — not because the
bug is patched, but because the feature it abuses did not exist yet. Gate 0 of
their portability guide ("Browser still runs Chromium ~120 with WasmGC") fails.

This is the same class of error as reading a fix version backwards: "the bug is
present in M105" is true, and still useless if the *reachable feature* was not
shipped until M119.

## 4. What is actually reachable at M105

Bugs fixed in M106–M109, where the abused feature predates M105:

| CVE | kind | fixed | usable here |
|---|---|---|---|
| CVE-2022-3723 | V8 type confusion, ITW | 107.0.5304.87 | yes, feature-wise |
| **CVE-2022-4262** | V8 type confusion, ITW | 108.0.5359.94 | yes, feature-wise |

Public material for CVE-2022-4262:
`mistymntncop/CVE-2022-4262` (★58) contains `exploit.js`, plus Project Zero's
RCA and `bjrjk`'s full-chain analysis (★105).

**Caveat measured just now:** that `exploit.js` is **x64-only**. It uses V8
debug builtins (`%DebugPrint`, `%GlobalPrint`) and 64-bit tagged-pointer
assumptions (`smi()`, `itod()`, `& 0xFFFFFFFFn`). It demonstrates the primitives;
it is not an ARM32 payload. Porting means re-deriving the object layout for
ARM32 V8 — which is exactly what `hdbreaker` had to do for their TV, and they
document that process in `blog/01-primitivas.md` … `blog/06-rop-y-shellcode.md`.

## 5. The physmap half is separate and more portable

The escalation half does not care which browser bug got you code execution. It
needs *some* renderer R/W as `uid=5001` with `gid=44 (video)`, then a device
whose `mmap` honours an arbitrary `vm_pgoff`.

Devices seen in public work, by platform generation:

| device | permission | where seen |
|---|---|---|
| `/dev/sdp_mem` | 0666, SMACK `*` | Synacktiv, Q60T (Tizen 5.5 / 2020-era) |
| `/dev/ntksys` + `/dev/ntkhdma` | 0666, SMACK `*` | Calif, UN43T5300 (KantS2) |
| `/dev/video40` (V4L2) | 0660 `root:video` | commit-regrets, 2024 Neo QLED |
| `/dev/tzmem` | — | designed safely, no physmap |

Which of these, if any, exists on a 2024 **fridge** panel is unknown. That is
gate 1 in their guide, and it needs code execution or a shell to test.

## 6. What this means for the next step

The order that avoids wasted work:

1. **Settle gate 1 before porting any browser exploit.** If no physmap device is
   reachable from `uid=5001`, a browser RCE buys a shell as `owner` and nothing
   more. Every route in §5 is a *local privilege escalation* that assumes a
   physical-memory window exists.
2. **Cheapest way to see the device nodes**: the panel's own `eMMC Information`
   and any shell the panel offers. The Web View cannot read `file://`, so it
   cannot enumerate `/dev` — confirmed by probe.
3. Only then choose a browser bug. At M105 the realistic choices are
   CVE-2022-3723 and CVE-2022-4262, both needing an ARM32 port.

## 7. Honest gaps

- No public work targets a Family Hub panel at any Tizen version.
- No public ARM32 PoC for CVE-2022-4262 exists; the x64 one is a research
  artifact, not a payload.
- Whether this panel exposes any physmap device is unmeasured, and it is the
  gate that decides whether the browser route is worth pursuing at all.

## 8. What the eMMC screen establishes

Read from Developer Mode → `eMMC Information`, 2026-11-25:

| partition | mount | total | used |
|---|---|---|---|
| `mmcblk0p17` | `csa` | 10 MB | — |
| `mmcblk0p20` | `opt` | 219 MB | 34 MB |
| `mmcblk0p21` | `usr` | 2938 MB | 1856 MB |

This is the **Tizen TV partition shape**, not the 2017 fridge's. Compare on the
QN85D: `p17` = `/mnt/drm`, `p20` = `/mnt/systemrw`, `p21` = `/opt` (`vdfs rw`).
The 2017 fridge dump instead had rootfs on `p12` (ext4), apps in `/usr/apps`, and
no `/opt` split at all.

Three consequences:

1. **The 2017 recipes do not describe this device.** The eMMC/UART route that
   worked on `SDP1406` targeted an ext4 rootfs and `/usr/apps`; this unit has the
   TV's `vdfs` layout with `/opt` and `/opt/usr` split.
2. **A writable, persistent store exists** (`p20` `/opt`, 219 MB, 34 MB used;
   `p21` `/opt/usr`, 2.9 GB, 1.8 GB used). That is the same shape
   `payload/parseinfo.awk` writes to, and the same shape commit-regrets uses for
   gate 3 (a stock on-boot app on writable `/opt`).
3. **`csa` (10 MB) is the TV's DRM/keystore partition name.** Its presence means
   this panel is on the TV platform, so `vdfs`, UEP and the mask/stop-file
   mechanisms are all plausible — and so are the physmap drivers in §5.

It does not show the filesystem type. `vdfs` vs `ext4` changes the hardware
recipe completely, and only a shell or a mount listing answers it.

## 9. If it has to be built from scratch

The order that avoids wasted work, cheapest first. Each step is a gate; stop as
soon as one fails.

**Step 1 — get any code execution as the panel's `owner` (uid 5001).**
Two candidates, both needing an ARM32 port:

- Port `mistymntncop/CVE-2022-4262` to ARM32. The x64 PoC's 64-bit machinery is
  not needed: ARM32 V8 has **no pointer-compression cage**, so a type confusion
  yields full-address-space R/W directly. `hdbreaker`'s
  `blog/01-primitivas.md` documents exactly this and is the model to follow —
  `addrOf` = write the object to a confused slot, read it back as an integer.
- Or `CVE-2022-3723`, one milestone earlier, same class.

**Step 2 — identify `libchrome` and gadgets.** ARM32 needs `elf_base` and
`mprotect`; `hdbreaker`'s `anchor.js` → `soscan.js` → `sodump.js` sequence does
this from JS alone, dumping ~82 MB of `libchrome.so` over HTTP with no shell.

**Step 3 — find the physmap device.** From the renderer:
`ls -l /dev/video*` and `id` (needs `gid=44 video`). Try `mmap` with a known
`vm_pgoff`. If a V4L2 node honours it, the rest is mechanical; if none does, stop
— the browser route dead-ends at a `owner` shell with no escalation.

**Step 4 — kernel constants.** Kernel base, `init_task`, `task_struct` and `cred`
offsets. commit-regrets measured all of these for 5.4.249 ARM32; a fridge build
will need the same work, and their `docs/EXPLOIT.md` documents the method
(`__ksymtab` string scan for `init_task`, walk `tasks.prev`, patch `cred+4..32`).

**Step 5 — persistence, only if asked.** Their gate 3: a stock on-boot app on
writable `/opt`. §8 says that partition exists here.

The honest estimate: steps 1 and 2 are a port of published ARM32 work; step 3 is
a coin flip that decides everything; step 4 is measurement, not research.

## 10. Port status

The ARM32 port is scaffolded, not working. Current state:

| piece | where | state |
|---|---|---|
| test harness | `tools/fridge_harness.py` | **works** — crash-safe queue, verified against a simulated client including the crash path |
| V8 fingerprint | `tools/payloads/fingerprint.js` | written, runs under Node, not yet run on the panel |
| primitive layer | `tools/payloads/arm32_chain.js` | scaffold: layout probes and GC-flush probe only; the confusion itself is not wired |
| escalation | — | not started; blocked on a working primitive |

`tools/payloads/arm32_chain.js` records the three differences that make the
ARM32 port easier than the x64 original: no pointer-compression cage, 32-bit
tagged values instead of 64-bit slots, and object offsets that must be measured
per build rather than assumed.

The order from §9 still holds. The immediate unknown is the panel's exact V8
build: `WebEngine Version` gives a Chromium number that we already know is a
stale constant, so the V8 version has to come from behaviour. That is what
`fingerprint.js` asks for.
