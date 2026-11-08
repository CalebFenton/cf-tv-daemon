# Documentation index

Each fact lives in one file; others link to it. Files in `90-log/` are dated
records and may be out of date.

## Start here

| file | read it for |
|---|---|
| `01-root/access-contexts.md` | **first:** the execution contexts, the ways in (channel, daemon, exploit), `tv/root.py` and `tv/chan.py` |
| `03-video/cftv.md` | putting images, text, video, sound and speech on the TV |
| `04-system/bluetooth.md` | the speaker: connect, auto-reconnect at boot and on wake, audio routing |
| `01-root/recovery.md` | something broke: what to do |
| `04-system/capability-matrix.md` | everything proven, one table |

## 01-root: getting and keeping root

| file | contents |
|---|---|
| `access-contexts.md` | uid/label per context; channel, daemon, boot queue, exploit; daemon queue protocol, boot/wake events and hooks, `tv/root.py boot` and `hooks`; limits; suspend vs boot; traps |
| `persistent-root.md` | why the awk payload works, the boot chain, the boot queue, the immutable `parseinfo.sh`, update/restore before a firmware update |
| `native-code.md` | what native code can run: C#/P/Invoke, shellcode, `elfload.dll`; `execve` blocked |
| `recovery.md` | runbook: unreachable TV, no root path, daemon not back after `boot install`, `/opt` full, wedged Bluetooth, black screen, wrong audio output, misbehaving hook, firmware update |

## 02-capture: reading the screen

| file | contents |
|---|---|
| `screen-capture.md` | `bin/snap`, `bin/stream`, the capture probe, inspection commands |
| `reading-frames.md` | what can be read from 720x404 frames |

## 03-video: putting things on screen

| file | contents |
|---|---|
| `cftv.md` | `bin/cftv` usage, protocol, video input restore |
| `hud.md` | the HUD over the TV picture: decisions, layers, the look, the live loop, timings; recognition, names and the scene caption come from cf-vision, a separate recognition service (not part of this repo) |
| `transparent-overlay.md` | platform rules: alpha-0, geometry over video, focus, launch screen, never kill, re-raise |
| `app-build.md` | building NUI apps, the borrowed app slot, why we don't install our own app |

## 04-system: the device

| file | contents |
|---|---|
| `device.md` | identity, storage, network, ports, power, developer mode |
| `control.md` | keys, the key-to-app table, volume, TTS, audio output routing, Voice Guide |
| `bluetooth.md` | speaker requests and function numbers, the `50-btconnect` hook, routing, the power-loss root cause, bt-service's own log (dlog) |
| `capability-matrix.md` | proven capabilities, still blocked, dead ends |
| `debloat.md` | what is disabled, how, rollback, what not to touch |
| `masking-daemons.md` | `/opt/masklist` (re-applied every boot) |
| `acr-telemetry.md` | content-recognition and telemetry services, current state |
| `block-layer.md` | eMMC, VDFS read-only flag |
| `lxc-zones.md` | `appzone`, `rdpzone`, `zone-manager` (and calls that crash it) |
| `static-analysis.md` | sink census tooling, `libkUEPUser` notes |
| `key-names.txt`, `process-list.txt`, `tv-app-inventory.csv` | data: keymap names, pre-debloat `ps`, pre-debloat registry |

## 05-firmware

| file | contents |
|---|---|
| `firmware-format.md` | decrypting and unpacking the firmware package |
| `remotepc-dbus.md` | the remotepc → `ps_agent` → `mount.smb.sh` chain |

## 06-security

| file | contents |
|---|---|
| `techniques.md` | reusable methods: watch & replay D-Bus (kdbus), ptrace tracing, running commands, building, static RE |
| `uep-bypass.md` | the UEP model and the userspace fail-open |
| `threat-model.md` | what the current setup exposes, the lockdown and its attack test, the vendor `/opt/etc/parseinfo.sh` hole and the immutable-flag fix, what stays open |
| `ps-agent-policy.md` | `ps_agent` authorization rules |

## 07-prior-art

| file | contents |
|---|---|
| `tiz0wn.md` | what we took from prior work |

## 90-log: history (not current)

Dated session records. They hold evidence that's hard to redo, but may be
out of date.

| file | period |
|---|---|
| `2026-11-07-sound-and-video/` | the chime that cut the programme's sound (bus captures); video decoded on the host (Option A measurements); map of the TV's second-video path (Option B) |
