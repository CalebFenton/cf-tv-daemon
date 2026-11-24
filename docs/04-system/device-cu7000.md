# Device — CU7000 (`UN55CU7000DXZA`)

Second TV, rooted 2026-11-24. Read-only exploration, root, persistence install
and one power cycle. Facts below are read from the TV, not inferred, unless
marked.

The QN85D's own facts are in `device.md`; differences that matter to this repo
are called out here.

## Identity

| Item | Value | Source |
|---|---|---|
| Model | `UN55CU7000DXZA` ("Samsung CU7000 55") | `/api/v2/` → `device.modelName` |
| Fixed id | in `.local/devices.toml` | `/api/v2/` → `id` (equals `duid`, `udn`) |
| Platform model | `T-KSU2ECAKUC-0090` | `/etc/prd_info.ini` `Model=` |
| Firmware | `T-KSU2ECAKUC-0090-2230.0` | `/etc/prd_info.ini` `Version=` |
| Build | `TIZEN-TRUNK2025-KantSU2e-OSU-2023-HOTFIX-RELEASE_20260514.4`, `release`, 2026-05-14 08:41:39 | `/etc/prd_info.ini` |
| Kernel | `Linux 5.4.77 #1 SMP PREEMPT Mon Jan 5 12:33:32 UTC 2026 armv7l` | `uname -a` |
| Tizen | `9.0.0 (Tizen9/TV)` | `/etc/os-release` |
| Panel | `3840x2160` | `/api/v2/` → `device.resolution` |
| Network | wired (`eth0`); `wifiMac` is reported even so | `/api/v2/`, `ip route` |
| Platform family | `23_KSUE_UB_T09` | `/api/v2/` → `device.model` |

`/proc/target_version` reads `__TIZEN-TRUNK2025-KantSU2e-OSU-2023-HOTFIX-RELEASE_20260514.4__T-KSU2ECAKUC-0090`.

The REST API reports `firmwareVersion: "Unknown"`, as on the QN85D; use
`prd_info.ini`.

## Root

Route: **archive-launcher** (`SVE-2025-50109` SDB package-name injection for
the uid 901 foothold, then the vendor `dotnet`/`sdbd-tarlauncher` capability
path). Not the FDET or Mali kernel routes; those are model-specific and were
not needed.

- The public kit lists no CU7000 entry. Closest reported `KSU2E` devices are a
  DU7100 at `T-KSU2E...-0090-2130.0` and an AU7000 at `T-KSU2EUABC`. This TV
  runs a newer build (`2230.0`) on the same `.NET` 6 archive path.
- Active .NET runtime: `/usr/share/dotnet/shared/Microsoft.NETCore.App/6.0.9`.
  `dotnet --list-runtimes` is not supported on this firmware; the kit's
  `readlink` fallback detects the version. No `--runtime` override was needed.
- Root identity: `uid=0(root) gid=0(root)`, `CapEff 0000003fffffffff`, SMACK
  `User` for the exploit agent, `System::Privileged` for the daemon.

## Persistence

The QN85D's design works unchanged (`docs/01-root/persistent-root.md`): the
primitives it depends on were each tested here before installing.

| prerequisite | result |
|---|---|
| `AT-HUB.service` runs `/opt/etc/parseinfo.sh` at boot | present, `WantedBy=starter.target` |
| `/usr/bin/awk` is gawk 3.1.5, UEP-hooked? | gawk 3.1.5; `grep -c libkUEPUser` = **0** |
| `#!/usr/bin/awk -f` script runs as root | yes |
| `#!/bin/bash` script is refused | yes (`[uep][bash] the file is NOT signed!!`) |
| vendor `parseinfo.sh` | md5 `2c4ccd56e3f89818e9bafd6bef809262`, identical to `archive/originals/parseinfo.ORIGINAL.sh` |
| `FS_IMMUTABLE_FL` on `/opt` (VDFS) | works: set `0x10 i`, write gave EPERM, cleared cleanly |
| `systemd-run`, `systemctl` | present |

First boot after `tv/root.py boot install --via exploit`: daemon start 9 s after
sdb, root via the daemon at ~76 s from the reboot request, `boot/50-btconnect`
hook ran. `tv/root.py doctor`: all ok. `boot status`: every file and mode ok,
immutable flag set.

## Differences from the QN85D that bite

1. **The exploit's mount namespace has its own `/etc/passwd`.** It contains a
   single line, `owner:x:0:0:owner:/home/owner:/bin/sh`, so `chown root:root`
   and `chown sdk:sdk` fail there with "invalid user" although the device's real
   `/etc/passwd` has `root:x:0:0:root:/root:/bin/bash` and `sdk:x:901:901`.
   `tv/root.py` now uses numeric ids (`0:0`, `901:901`) everywhere, which works
   in both namespaces. Symptom before the fix: `boot install --via exploit`
   wrote every file but aborted at the lockdown with 12 `chown: invalid user`
   lines, leaving `parseinfo.sh` at mode 0666 (not executable) and no daemon.
   Re-running the fixed install rewrote and locked everything down.
2. **No `com.samsung.tv.social-chat-app` slot.** `ls /opt/usr/apps` has 305
   entries and no `com.samsung.tv.social-chat-app`. The cftv agent uses
   `com.samsung.tv.samsungdex` here instead. That app ships a precompiled
   stock copy in `bin/.native_image/`, which the launcher prefers over
   `bin/`, so it has to be moved aside (`docs/03-video/app-build.md` §7). The
   stock DLL and the native image link are saved in
   `/opt/usr/share/tv-backups/cftv/com.samsung.tv.samsungdex/`.
3. **Developer mode does not block internet on this TV.** TCP 443 to `1.1.1.1`
   opens. The kit author's TVs lose internet in developer mode; this one keeps
   it, so nothing here relies on the TV being offline.
4. **`/opt` is far less full**: 2.5 G used of 3.9 G (1437 MB free) against the
   QN85D's ~541 MB. The "do not write large files to /opt" rule still applies.
5. **`hostname`, `timeout` are missing** from the toybox set in the uid 901
   shell. Do not assume coreutils in injected commands.

## Capabilities verified on this TV

- root through the daemon path and through `--via exploit`
- the channel (`tv/chan.py serve`, per-TV socket) and `bin/snap` capture
  (112 KB JPEG, inspected)
- boot/wake hooks: `50-btconnect` runs at boot (the speaker is not on this TV's
  Bluetooth, so it logs "speaker not seen"; harmless, and the hook is shared
  with the QN85D by design)
- `tv/root.py doctor`, `report`, `boot status`
- `bin/cftv image` (PNG with alpha) and `text` over a game on HDMI, read back
  with `bin/snap` (2026-11-24). The agent built against the QN85D's reference
  assemblies runs unchanged on this TV's .NET 6.0.9.

Not verified here: screen capture probes other than `CaptureProbe`, the other
`cftv` items (GIF, rect, video, sound, say, notify, hud), Bluetooth speaker
routing, dlog.
