# Device — Family Hub refrigerator (reconnaissance only)

Third Tizen device, a Samsung Family Hub refrigerator. **No access obtained.**
Reconnaissance 2026-11-25. Facts are marked observed, read off the panel, or
public.

Read [access-contexts.md](../01-root/access-contexts.md) first: every route
there starts with SDB on 26101, and this device has no SDB and no network
service to reach.

## 1. Identity

Read from the panel's `Software information` screen, 2026-11-25:

| Item | Value |
|---|---|
| One UI | `7.0 Refrigerator` |
| Tizen | **`7.2.1`** |
| Firmware | `AFH-US-KTM-24-T7XXMU 20251031.075639` |
| Build tail | `AW3AW4R` / `2006250C, FFFFFFFF` |
| Wi-Fi | `TMA-1`, address in `.local/devices.toml` (observed in the panel's own network dialog) |
| Hostname | `Samsung-FamilyHub` (reverse DNS) |

`AFH-US-KTM-24` and the `20251031` build stamp make this a **2024-model panel**
that took the One UI 7 update. It is **not** the 2017 `SDP1406`/Tizen 3.0
platform that the published firmware dumps describe, and not the Tizen 3.0
"Mobile profile" the old Family Hub developer page mentions. Tizen 7.2.1 puts
it on the same generation as 2024 TVs. Its web engine measures as **at least
Chromium 103** — see `familyhub-browser.md`, which explains why the menu's
`WebEngine Version` of `76.0.3809.146` must not be trusted. That version is the
important fact for §4.

## 2. Network surface

Observed from this workstation, full 1–65535 TCP scan:

| Item | Value |
|---|---|
| Open TCP ports | **17654 only** |
| Port 17654 | Spotify **eSDK** (`Server: eSDK`) |
| mDNS | `_spotify-connect._tcp`, `CPath=/zc/0` |
| Absent | 26101 SDB, 26099, 8001/8002 REST, 8888, 9197, 9080; no CoAP/DTLS answer on 49152–49160 or 5683 |

`GET /zc/0?action=getInfo` returns a Spotify Connect payload (`version 2.9.0`,
`libraryVersion 3.166.72`, `deviceType SPEAKER`, `remoteName SAMSUNG FAMILY HUB
(5BF8)`). Every other path answers `404` from the same server, `OPTIONS`/`HEAD`
`400`. Path traversal is rejected. This is Spotify's embedded SDK, not a Samsung
control API; it exposes no filesystem or shell.

**No network foothold exists.** Unlike the TVs, there is no SDB listener to
attack and no REST API to read state from. Anything that gets in has to arrive
through the panel itself: the browser, the service menus, or the USB port.

## 3. Service menus

Found by [u/doitaljosh](https://www.reddit.com/r/appliancerepair/comments/rfvwdg/)
disassembling the Fridge Manager binary, on the 2021-era build. Both start in
**Fridge Manager** by tapping the two top corners of the title bar, A =
top-left, B = top-right:

- **Engineer Mode** — `ABABAB`. No password. Fridge/Function tests, System
  Information, Static IP, **File Transfer**.
- **Developer Mode** — `ABAAAB`, then a password. Fridge Function Test, OTN,
  LCD Always On, Silent Reboot, Factory Mode, Micom, Touch/Audio Version, Voice
  Engineering Mode, **eMMC Information**, **Dump History Tracer Log**, Fridge
  Manager Setting, **Web View**, Voice Developer, KPI Logger (SSM) Server,
  White Balance, Geocoding Enable Server, Cloud Service, FKP Server, SES Update
  Client, **Tizen Store Server**, Video Test, CPU Temp, Reset Location, Reset,
  Virtual Micom, Screen Power Manager.
- **Rescue Mode** — hold Recent Apps/Home 10 s: factory reset, filesystem
  repair, firmware upgrade.

Password history (**reported by commenters, not verified here**): `1122` until
the July 2023 update; the pad became an alphanumeric keyboard and nothing
public surfaced for ~2 years; `tizenreflcd1!` reported working from 2025-10
with many independent confirmations, apparently given out by Samsung support.
Some late-2026 units reject both. One UI 7 may have changed this again.

This is a **service/diagnostic menu, not the TV's developer-mode dialog**: no
"Host PC IP" field, and public reports agree SDB stays closed. doitaljosh:
*"a script exists that accomplishes enabling SDB for development builds, but it
must be run as root."*

## 4. Root routes, and the one that is not hardware

**There is a published non-hardware route class. It has not been demonstrated
on a fridge.** The chain is: get code execution inside the panel's browser, then
escalate with a world-writable vendor driver. Both halves are public.

**4.1 Browser foothold.** The panel ships a Web View that runs a Chromium-based
engine. Measured from the panel itself (`familyhub-browser.md`), that engine is
**Chromium 105** (exact: M105 features true, M106 false). The Developer Mode
menu's `76.0.3809.146` is a stale constant and the User-Agent is a configurable
override, so neither can be used to pick an exploit.

**The direction of the version comparison matters.** A bug fixed in a later
milestone is **present** in M105. So the panel is exposed to every V8/Wasm bug
fixed from M106 onward — a window of roughly three and a half years — not a
narrow 2022 slice. Only bugs fixed at or before M104 are patched out, which does
kill the 2019–2021 corpus (CVE-2020-6383 was fixed in M80).

Live candidates whose affected range includes M105, several exploited in the
wild: CVE-2022-3723, CVE-2022-4262, CVE-2023-2033, CVE-2023-3079,
CVE-2024-0519, and **CVE-2024-2887** (Wasm type confusion, Pwn2Own 2024, fixed in
Chrome 123). `CVE-2024-2887` is the most promising for this panel: reached from
ordinary JS, WebAssembly is enabled here, and ZDI's write-up calls the primitive
a universal type confusion in the Wasm type section.

Demonstrated on TVs, for shape:

- Synacktiv, Q60T (2021): CVE-2020-6383 → code execution in `efl_webprocess` as
  `uid=5001`, label `User::Pkg::org.tizen.browser`. **Patched here** (fixed M80).
  https://github.com/synacktiv/samsung-q60t-exploit
- Alejandro Parodi (2026): ARM32 V8 RCE on a Samsung TV browser, no ADB and no
  hardware access, ROP → reverse shell. The technique shape is current; the bug
  he used is not necessarily.
  https://hdbreaker.github.io/blog/samsung-tv-v8-rce-rop-reverse-shell/

The delivery is the ordinary browser: the panel has an Internet app and a
`Web View` entry in Developer Mode, so a URL is enough. Confirmed working — the
probe page loaded and ran.

Measured constraints on this panel: `WebGL2` off, `WebGL1` on, and the Web View
refuses cross-origin isolation, so `SharedArrayBuffer` stays unavailable. That
rules out the usual shared-memory primitives but not Wasm-based ones. The Web
View also cannot read its own loopback or `file://` URLs, so it cannot inspect
`/proc` or `/dev` directly.

**4.2 Escalation.** Two public primitives, both from the browser's `uid=5001`
context:

- `/dev/sdp_mem` — world-writable, `SECLABEL "*"`, and `sdp_mem_mmap()` passes
  the user's `vm_pgoff` to `remap_pfn_range` with no bounds check: arbitrary
  physical R/W. Synacktiv used it to clear `s_uepStatus` (so unsigned binaries
  run), rewrite `poweroff_cmd`, and point a `/proc` read handler at
  `__orderly_poweroff()` to get command execution as root.
- `/dev/ntksys` — `MODE="0666", SECLABEL{smack}="*"`. `SET_MEM_INFO` stores a
  user-supplied physical base and size, then `mmap` remaps that PFN verbatim.
  Calif's 2026 write-up uses it to scan RAM for the browser's `struct cred` and
  zero the UID/GID fields — data-only root, no ROP, no shellcode, and it
  survives UEP because nothing new is executed from disk.
  https://github.com/califio/publications/blob/main/MADBugs/samsung-tv/writeup.md

Both are Novatek/Samsung DTV-platform drivers and both were found on TVs, not on
a fridge. **Whether this panel exposes `sdp_mem`/`ntksys` with the same
permissions is unverified**; it is the single question that decides whether the
non-hardware route works here.

**4.3 Hardware route** (the only one actually proven on a fridge, and only on
the 2017 generation): eMMC in-circuit read/write plus UART on `CN9` (pins 12
`UART0_TXD`, 13 `UART0_RXD`, 115200). doitaljosh soldered CLK/CMD/DAT, edited
the ext4 rootfs, changed the root password, and got a root shell. That unit's
rootfs was ext4; this 2024 unit has the TV partition shape instead
(`familyhub-prior-art.md` §8), which changes the recipe. `CN9` on a 2024 board is
also not the 2017 pinout until someone checks.

Note what the `eMMC Information` screen did establish: `p20` is `/opt` (219 MB,
34 MB used) and `p21` is `/opt/usr` (2.9 GB, 1.8 GB used), with `p17` as `csa`.
That is the TV layout, so a writable persistent store exists in the same places
the TV payload uses — but the filesystem type is still unknown, and `vdfs` vs
`ext4` decides whether the 2017 hardware recipe transfers at all.

## 5. Does the TV code work here?

No, as written. All of it starts at SDB, and the fridge has no SDB.

| Component | Requires | Status |
|---|---|---|
| `tv/root.py`, `tv/chan.py`, `tv/cftv.py` | SDB 26101 / channel / daemon | unusable |
| `payload/parseinfo.awk`, `payload/on-boot.d/*` | `AT-HUB.service`, VDFS `/opt`, `buxton2ctl`, `pkgcmd` | unusable |
| `vendor/samsung-tv-root-source` | SDB package-name injection, `sdbd-tarlauncher` | unusable; foothold absent |
| `apps/cftv.cs`, `apps/chattr.cs` | vendor `.NET`, VDFS immutable flag | unusable |

What transfers is the **escalation half**, not the entry half. `sdp_mem` and
`ntksys` are the same driver family as on the TVs, so a payload written for
either should work unchanged *if* the nodes are present with the same
permissions — a static ARMv7 binary through `memfd` to beat UEP. The entry half
(browser exploit) and the persistence half (nothing in this repo applies) are
both new work.

## 6. Next steps, cheapest first

1. **Developer Mode at the panel**: `ABAAAB`, try `tizenreflcd1!`, then `1122`.
   Report what the password prompt looks like (pad vs keyboard) and whether
   `Web View`, `eMMC Information` and `Tizen Store Server` are present. That
   tells us whether the One UI 7 build kept the 2021 menu.
2. **From this workstation**: watch for 26101/8001 while that menu is open
   (`nc -z -w2 $FRIDGE_IP 26101`). If SDB appears, the whole TV toolchain
   becomes usable and this doc gets much shorter.
3. **Enumerate devices from the browser** if the panel's browser can be pointed
   at an HTTP server: a probe page that fetches `file://`/`/proc` paths tells us
   the engine version (from the User-Agent) and whether the driver nodes are
   visible. No code execution needed to learn the browser build.
4. **Decide route**: if `sdp_mem`/`ntksys` are present and world-writable, the
   public escalation is the non-hardware path and is worth porting. If they are
   not, the honest answer is hardware again.

Do not run the TV payload against this device. `boot install` targets paths that
do not exist on it.
