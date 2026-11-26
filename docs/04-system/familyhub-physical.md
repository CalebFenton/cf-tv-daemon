# The physical route, and the USB route that may not be physical at all

Researched 2026-11-25. This supersedes the earlier assumption that "physical"
means opening the door panel.

## 1. The finding that changes the order

**The Family Hub has a USB port inside the top-right door**, behind a small
cover. It is a documented user-facing feature: you plug in a FAT32/exFAT stick to
import photos. It is not buried behind the screen.

That matters because there are now **two published exploit families that start
at a USB port on Samsung Tizen hardware**, and neither needs UART, Developer
Mode, or disassembly:

| project | platform | kernel | trigger |
|---|---|---|---|
| `Luminger/samsung-tv-usb-root` | Muse.M (2019 TVs, `T-MSMDEUC` 1500.9) | 4.1.10 ARMv7 | crafted exFAT `num_ext` |
| `GiuseppeFiorillo/samsung-tv-usb-root-kant-m2` | Kant-M2 (2018 TVs, `T-KTM2DEUC` 1400.2) | 4.1.10 ARMv7 | same exFAT bug |

The Kant-M2 port is the interesting one by name, because this panel's firmware
string is `AFH-US-KTM-24-T7XXMU` and "KTM" is the Kant-M family code.

## 2. Why the name match is not enough

This is exactly the trap that produced the earlier Chromium claims, so state it
plainly. **`KTM` in this firmware string does not establish the same kernel.**

The Kant-M2 port was built for **Tizen 4.0 on kernel 4.1.10** (2018 silicon).
This panel runs **Tizen 7.2.1** with the TV-style partition layout
(`p20 /opt`, `p21 /opt/usr`), which corresponds to a much newer kernel — the TVs
in this repo run 5.4.x and this panel is plausibly in that range.

The exploit's own compatibility document is explicit that this matters:

> The kernel addresses and frame offsets are tied to the kernel image. A
> different Kant-M2 firmware release must be checked against its own
> `kernel.bin`; if the kernel is identical the constants apply, otherwise they
> must be re-derived.
>
> Static compatibility is not permission to use an image on another set. A wrong
> address can hang or reboot the TV.

So the honest status is: **two unknowns, both decisive** — whether this
panel's exFAT driver still carries the `num_ext` overflow, and what its kernel
looks like if it does.

## 3. What the exploit actually does

Worth understanding before deciding, because it is unusually clean:

1. `storaged` mounts the USB and enumerates the exFAT partition.
2. A crafted `num_ext` entry overflows `ffsReadDir`'s saved registers on the
   kernel stack.
3. A fixed kernel chain copies a stage into an unreferenced core-text cave,
   flushes the cache, and jumps to it.
4. The stage runs the *signed* `/bin/bash` through `call_usermodehelper` — this
   is why it does not need to defeat UEP, the binary is already trusted.
5. Bash connects back to a listener on the operator's machine.

Nothing is written to the TV. No boot hook, no modified firmware, no module
patching. Root is re-established from the stick on each cold boot.

Operational caveats from the authors: the stick must be connected at every cold
boot, leaving it in across several boots caused a **reboot loop** on the
validated unit, and the shell is a reverse connection to the operator's host (not
a listener on the fridge).

## 4. The other non-physical surfaces, honestly assessed

| surface | status |
|---|---|
| USB port (media import) | **the promising one.** Public exploit family, physical access without disassembly |
| Developer Mode → File Transfer / Web View | explored; Web View cannot read `file://` or loopback |
| Engineer Mode → File Transfer | untested; unknown what it accepts |
| Rescue mode → firmware upgrade | would need a Samsung service image, which is not public |
| UART / JTAG (`CN9`, `CN10`) | known on the **2017** board; pinout for a 2024 board is not public |
| eMMC in-circuit | proven on the 2017 board; 2024 board not documented |

## 5. What to check, in order, and what would falsify each step

The discipline that was missing earlier: name the falsifier before the test.

**Step 1 — read the kernel version.**
Cheapest possible. Does the panel expose its kernel anywhere without code
execution? Candidates: `Settings > About`, an error/diagnostics screen, the
`Dump History Tracer Log` in Developer Mode, or a malformed-USB error dialog.
*Falsified if:* nothing exposes it — then we are guessing and should say so.

**Step 2 — establish whether the exFAT driver is the vulnerable lineage.**
If the kernel is 4.1.x, the Kant-M2/`sdfat` code path is plausible and the project
becomes a candidate after re-deriving constants for this kernel.
If the kernel is 5.4+, the driver lineage is almost certainly the rewritten
upstream one, and the `num_ext` overflow is not the same bug — the route is
different work, not a port.
*Falsified if:* the kernel is modern, which is the likely outcome.

**Step 3 — decide between USB and physical.**
Only after 1–2. If the kernel is modern, USB-route work means finding a *new*
bug in a modern exFAT driver, which is real research rather than a port, and the
panel-access calculations change.

## 6. Risk, stated plainly

- A wrong kernel address **hangs or reboots** the TV per the authors' own warning.
- This is a refrigerator. The screen and the cooling system are separate, so a
  bricked screen does not endanger food — but it does mean a service call.
- The exploit is AGPL-3.0 and explicitly for hardware you own. That is satisfied
  here.

## 7. What I would do next

Read the kernel version before anything else. It is one question, it decides
whether the USB route is a port or a research project, and it costs nothing.

Everything past that is conditional, and I would rather write that down now than
discover it after another confident wrong claim.

> **Correction (2026-11-26).** Section 2 above says the kernel is "plausibly
> 5.4.x" and treats that as fatal to the USB route. That reasoning was wrong.
> `Luminger/samsung-tv-usb-root`'s compatibility document classifies
> `T-PTMAKUC` (2022, **kernel 5.4.77**) as *vulnerable, chain incomplete*: the
> exFAT defect survives into 5.4.x because Samsung kept its out-of-tree driver.
> See `familyhub-exfat-crosscheck.md`. The route is not blocked by the kernel
> version; it is blocked by not having the firmware archive.

## Appendix: the device's full network map, re-measured

Re-measured 2026-11-26 after the user pointed out I kept tunnel-visioning on one
CVE family instead of mapping the device.

| probe | result |
|---|---|
| full TCP 1–65535 | **only 17654** |
| TCP 9495, 8766, 8080, 443, 80, 8443, 49152–4, 1883, 6668, 6669, 7777, 9999, 10000 | all closed |
| UDP 1900 (SSDP) | no answer |
| UDP 5683 (CoAP) | no answer |
| UDP 5355 (LLMNR) | no answer |
| ICMP | replies, ~26 ms |

Two conclusions:

1. **`SmartThings` hub ports are not exposed.** The Family Hub is documented to
   contain a SmartThings hub with Zigbee/Z-Wave radios (`/hub/data/hubcore`,
   hubCore over ports 443/8080/8766/9495), and that stack has public CVEs
   (CVE-2018-3926 and the Talos 20-vuln set). None of those ports answer here.
   Whether the radio exists but is firewalled, or the appliance uses a different
   integration, is unknown — but it is not a network-accessible surface today.
2. The **only** remote surface remains 17654, the Spotify eSDK, which exposes
   nothing beyond Spotify Connect.

So the search should stop looking for a network entry point. There is one, and
it is a music player.

## The `Tizen Store Server` menu item, and why it is probably the entry point

Developer Mode on this panel lists an item the TV equivalent does not have by
that name: **`Tizen Store Server`**, alongside `SES Update Client`, `Cloud
Service` and `FKP Server`. Every one of those is a URL or endpoint setting.

That matters because the panel is a Tizen device with an app installer. On TVs,
the chain that makes third-party apps installable is: developer mode + a host PC
IP + a distributor certificate containing the device's DUID + `tizen install`.
The Web View here has no such dialog — but a menu that lets an operator repoint
the **store server** is the same capability expressed differently. If the panel
will fetch and install a package from a server you name, the signing gate is the
only thing left, and signing is a local operation given the DUID.

This is a lead, not a finding. What is unknown:

- whether `Tizen Store Server` accepts a URL and actually installs from it;
- whether it checks a Samsung signature we cannot produce;
- whether the panel's installer accepts a `.wgt`, `.tpk`, or only a vendor format.

It is cheap to test and needs no exploit: change the value, watch whether the
panel makes an outbound request we can see. Our harness already runs an HTTP
server on this host, so a request to it would be visible in its log.

## What the device is not

Recorded so the search does not repeat itself:

- No SmartThings hub surface (9495/8766/8080/443 all closed; no SSDP, no CoAP).
- No SDB, in or out of Developer Mode.
- No loopback or `file://` access from the Web View.
- No OCF.
- The only open port, 17654, is Spotify's eSDK.

The network is not the way in. The menus and the USB port are.
