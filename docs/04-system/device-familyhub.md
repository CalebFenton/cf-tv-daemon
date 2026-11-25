# Device — Family Hub refrigerator (reconnaissance only)

Third Tizen device, a Samsung Family Hub refrigerator. **No access obtained.**
This is what the network and public sources establish, and what does not carry
over from the TVs. Reconnaissance 2026-11-25.

Read [access-contexts.md](../01-root/access-contexts.md) first: every route
there starts with SDB on 26101, and this device has no SDB.

## 1. What the device is

Observed from this workstation:

| Item | Value | How |
|---|---|---|
| Hostname | `Samsung-FamilyHub` | reverse DNS |
| Model family | Family Hub, `Family_HUB` | eSDK `modelDisplayName` |
| LAN surface | **one TCP port open**, `17654` | full 1–65535 TCP scan |
| Port 17654 | Spotify **eSDK** (`Server: eSDK`), `remoteName` `SAMSUNG FAMILY HUB (5BF8)` | `GET /zc/0?action=getInfo` |
| mDNS | `_spotify-connect._tcp` only, `CPath=/zc/0` | mDNS query |
| Not present | 26101 SDB, 26099, 8001/8002 REST, 8888, 9197, 9080, any CoAP/DTLS on 49152–49160 | scan + DTLS ClientHello probes |

`/zc/0?action=getInfo` returns a full Spotify Connect payload: `version`
`2.9.0`, `libraryVersion` `3.166.72`, `deviceType` `SPEAKER`, `clientID`, and
`scope` `streaming,client-authorization-universal`. Every other path answers
`404` from the same `eSDK` server; `OPTIONS`/`HEAD` get `400`; a POST body is
ignored. This is the same service the Home Assistant Family Hub integrations
describe as the legacy local API — it is Spotify's embedded SDK, not a Samsung
control API, and it exposes no filesystem or shell path.

Consequence: **this device has no network foothold at all.** The TVs have SDB
and a REST API; the fridge has neither.

## 2. Platform

Public evidence, from
[doitaljosh/familyhub-reverse-engineering](https://github.com/doitaljosh/familyhub-reverse-engineering)
(1st-generation panel, firmware dumped from the eMMC):

- Device tree: `model = "Samsung FAMHUB_7 SDP1406 SoC"`, `compatible =
  "samsung,dtv\0samsung,sdp1406fhd"`, bootargs
  `root=/dev/mmcblk0p12 rootfstype=ext4 ro … console=ttyS0,115200N8`.
- Kernel source: `linux-3.10.30-famhubv1`, "based on the SDP1415 SoC (1st
  generation)".
- Storage: GPT, 21 partitions; rootfs is plain **ext4** with `ro`, not VDFS.
- `/usr/bin/dotnet` is absent from that image's package list; apps live in
  `/usr/apps` (`org.tizen.browser`, `org.tizen.glazecamera`,
  `com.samsung.samsung-connect`, Spotify Hub, …) — a different layout from the
  TVs' `/opt/usr/apps`.

So the panel is a Samsung **DTV** SoC with a Tizen TV–derived userspace, but a
fridge-specific build (Tizen 3.0 at dump time; 4.0/6.0 reported since). Two
things follow: the SoC family is one this repo already knows from the TV side,
and the filesystem is friendlier (ext4, writable once you have root), but the
service layout and packaging are not the TVs'.

**Unverified:** which generation this particular unit is. It is not on the
1st-gen `SDP1406` unless it is a 2017–2020 model. `Settings > About Family Hub`
shows the software version; the Developer Mode screen (§3) has `eMMC
Information`. Both need someone at the panel.

## 3. Getting into the service menus

From [u/doitaljosh](https://www.reddit.com/r/appliancerepair/comments/rfvwdg/),
who found them by disassembling the Fridge Manager binary:

Both start in **Fridge Manager** by tapping the two top corners of the title
bar, with A = top-left and B = top-right.

**Engineer Mode** — `ABABAB` (six alternating taps). No password. Gives
Fridge/Function tests, System Information, Static IP, **File Transfer**.

**Developer Mode** — `ABAAAB` (A, B, then A three times, then B), then a
password. Gives: Fridge Function Test, OTN, LCD Always On,
Silent Reboot, Factory Mode, Micom, Touch/Audio Version, Voice Engineering
Mode, **eMMC Information**, **Dump History Tracer Log**, Fridge Manager
Setting, **Web View**, Voice Developer, KPI Logger (SSM) Server, White Balance,
Geocoding Enable Server, Cloud Service, FKP Server, SES Update Client,
**Tizen Store Server**, Video Test, CPU Temp, Reset Location, Reset, Virtual
Micom, Screen Power Manager.

**Rescue Mode** — hold Recent Apps / Home for 10 s (physical or on-screen), the
unit reboots into a menu with factory reset, filesystem repair, firmware
upgrade.

Password history, all *reported by commenters*, none verified here:

| Build | Password | Status |
|---|---|---|
| before the July 2023 update | `1122` | worked; numeric pad |
| July 2023 onward | alphanumeric keyboard instead of a pad | nobody published one for ~2 years |
| reported 2025-10 onward, many independent confirmations | `tizenreflcd1!` | stated to come from Samsung support; some late-2026 units reject it |

Note the menu contents: this Developer Mode is a **service/diagnostic menu**. It
does not have the TV's "Developer Mode = On, Host PC IP" dialog, and public
reports agree that SDB stays closed: *"a script exists that accomplishes
enabling SDB for development builds, but it must be run as root"* (doitaljosh).
No one has published a fridge dev-mode path to SDB.

## 4. Root routes that exist

| Route | Evidence | Applies here? |
|---|---|---|
| **Hardware: eMMC in-circuit + UART** | doitaljosh soldered CLK/CMD/DAT on the flash chip, modified the ext4 rootfs, changed the root password, then had a root shell on the `CN9` UART console. Full dump + GPT published. | Only proven route. Requires opening the door panel. |
| **UART console, `CN9`** | Pinout published: pin 12 `UART0_TXD`, 13 `UART0_RXD`, 1/14 `GND`, 15 `5V5`; bootargs `console=ttyS0,115200N8`. `CN10` is the Micom debug header. | Login only works with the root password, which is why the eMMC edit comes first. |
| **SamyGO `uepkiller.sh`** | Named by doitaljosh as the way to defeat UEP so unsigned binaries run, after root. | Technique transfers; no fridge-specific package. |
| Network exploit | none published. 17654 was a Mongoose 6.6 server on Tizen 3.0 (2018 report); on this unit it is Spotify eSDK, and the old `.krate/...` paths return 404. | No. |

**There is no public root kit for the Family Hub.** Searches for one return the
TV kits, the 2019 hardware dump, and the service-menu passwords. Nothing else.

## 5. Does the TV code work here?

No. Every tool in this repo starts at SDB, and the fridge has no SDB.

| Component | Requires | Status |
|---|---|---|
| `tv/root.py` (probe, exec, boot, hooks) | SDB 26101, or the daemon/channel | unusable |
| `tv/devices.py`, `.local/devices.toml` | SDB/SSDP identity via REST 8001 | unusable (and it stores MACs/uuids — the fridge would need `.local`) |
| `tv/cftv.py`, `apps/cftv.cs` | the vendor `.NET` runtime in `/opt/usr/apps`, borrowed app slot | unusable; no `dotnet`, different app paths |
| `apps/chattr.cs` | VDFS + `FS_IMMUTABLE_FL` | irrelevant; rootfs is ext4 |
| `payload/parseinfo.awk` + boot chain | `AT-HUB.service` → `/opt/etc/parseinfo.sh`, VDFS `/opt` | unusable; no such unit or layout |
| `payload/on-boot.d/*`, `40-debloat` | hook runner + `buxton2ctl` + `pkgcmd` | unusable; different service manager and package DB |
| `vendor/samsung-tv-root-source` (archive-root) | SDB package-name injection, `.NET` 3.1/6, `sdbd-tarlauncher` | unusable; foothold does not exist |
| `docs/reference/tiz0wn`, `quiet-tizen` | same TV Tizen stack | unusable |

What does carry over is the *method*, not the code: find an interpreter the
vendor's UEP policy does not hook, get it run as root by a boot-time unit, keep
the payload on a writable partition. That is `persistent-root.md`'s lesson and
it should transfer to a fridge build — but only after root, which is the wall.

## 6. Next steps, cheapest first

1. **Read the model and software version** at the panel: `Settings > About
   Family Hub`. That decides whether this is the dumped 1st-gen platform or a
   newer one, and therefore whether the published device tree and kernel source
   describe it.
2. **Try Developer Mode** (`ABAAAB`, then `tizenreflcd1!`, else `1122`).
   Read `eMMC Information` and `System Information`. Confirm whether anything
   there opens a network service — check for 26101 afterwards.
3. **Try the Engineer Mode File Transfer / Web View**, which are the only
   non-hardware surfaces that might move a file onto the panel.
4. **Hardware only if 1–3 fail**: open the door panel, find `CN9`, and follow
   the eMMC route. Read-only dump first; the rootfs edit is what makes the UART
   login useful.

Do not run the TV payload against this device. Nothing in it is written for a
fridge, and `boot install` targets paths that do not exist there.
