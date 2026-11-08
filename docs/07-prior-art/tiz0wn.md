# tiz0wn — prior art

Repo: https://github.com/sebastianspicker/tiz0wn (MIT).
Their target: GQ55Q60TGUXZG, `T-NKLDEUC-2743.0` (README, checked 2026-10-24).
Ours: QN75QN85DAFXZA, `T-NKM2AKUC-2301.1` (build 2025-11-12). Same platform
family and protection stack; different SoC generation and year.

It is an evidence-first debloating and security research project: static
firmware analysis, bounded live probes, and explicit statements of what was
*not* achieved. Its one live root result is the Remote PC/CIFS injection,
proven 2026-09-27 with a `$(/usr/bin/id>/tmp/<marker>)` **password** that
produced a uid-0 file. That was a root-command proof, not persistence or a
shell. Mali CVE-2022-46395 work there is marked inconclusive.

## What we used

Their findings are as stated in their repo, not re-verified here unless a
column says so.

| their finding | what it means for us |
|---|---|
| Firmware AES key not rotated since 2021 (NikeL) | Reading firmware is easy; signing is not. Our NikeM2 key came from `unixtract` (`docs/05-firmware/firmware-format.md`). |
| Every section carries `OURSAValidationDesc` (their key id `0x19fc36c9`) | Ours carries one too (key id `3349761644`). A modified image will not boot. |
| VDFS rootfs is zlib + signed, not encrypted, `mkfs.vdfs … --read-only` | Same on ours (`docs/04-system/block-layer.md`). |
| VDFS layout `2007` vs `2006`, `COMPR_LAYOUT_VER_06` | Ours is `2007`; why upstream extractors failed (fixed in `tools/vdfs-unpack`). |
| `sdp_hwmem` hardened (whitelist + kernel-overlap reject) | We skipped the old Q60T physmap route. |
| **`sdb uninstall` returns 0 but does nothing** | Never trust exit codes for app removal; check the live app list (`docs/04-system/debloat.md`). |
| `/dev/mali0` 0666 + Smack `*` | Same on ours (verified 2026-10-24). But both public Mali CVEs are closed on our driver (`docs/05-firmware/firmware-format.md`), so their Mali route does not carry over. |
| Remote PC/CIFS credential injection | Same sink on our build, byte-identical `mount.smb.sh`; not exercised here (`docs/05-firmware/remotepc-dbus.md`). |
| Code-execution attempts died at `failed to map segment` | Same kernel UEP wall. We avoid it by running managed code under the signed `dotnet` (`docs/01-root/native-code.md`) and awk scripts at boot. |

## The CIFS bug in one paragraph

`mount.smb.sh` reads the credential file, joins its lines into the mount
option string, and runs `/bin/bash -c "$UTIL $UTIL_OPT"` as root. The
`ps_agent` policy validates the credential *filename*, not its contents, so a
`$(command)` in a credential value runs as root. Their proof needed one manual
Shared Folder click in the Remote PC UI. Whether `PutMessage` can be driven
without the UI is open (`docs/05-firmware/remotepc-dbus.md`).

## Their live-mode safety rules worth copying

Exact target and firmware gates, one change at a time, one non-retried
classification per run, and evidence kept private. Do not expose RDP, SMB, SDB,
or TV-control ports to the Internet.
