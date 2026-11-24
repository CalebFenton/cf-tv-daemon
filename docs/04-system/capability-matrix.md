# Capability matrix: what is proven

The canonical table of what we can and cannot do on the TV, with evidence.
QN75QN85DAFXZA (`21_NIKEM2_QTV`), firmware `T-NKM2AKUC-2301.1`. Updated
2026-10-24. A second TV, UN55CU7000DXZA on `T-KSU2ECAKUC-0090-2230.0`, joined
2026-11-24: A5, A8 and A9 were re-verified there and the boot payload was
installed the same day (`device-cu7000.md`). Rows below without a CU7000 note
are the QN85D's only. Each row gives the status, the evidence with the date it was last
checked, and the detail doc. The details live in the linked docs; this file
only summarizes them.

Status: **yes** = verified, **no** = tested and blocked, **open** = untested or
unresolved, **not current** = proven once but not set up now.

## 1. Root access and persistence

Detail: `docs/01-root/access-contexts.md`, `docs/01-root/persistent-root.md`.

| # | capability | status | evidence |
|---|---|---|---|
| A1 | uid 0 with all 38 capabilities | yes | `CapEff 0000003fffffffff`, `cap_last_cap` 37, via the channel (2026-10-24) |
| A2 | SMACK label `System::Privileged` | yes | boot payload, `cf_tv_daemon` and its boot/wake hooks run with it. Channel commands raise themselves to it (`printf System::Privileged > /proc/self/attr/current`, needs `CAP_MAC_ADMIN`). Anything that execs `dotnet` is `User` (`SMACK64EXEC="User"`) (2026-10-24) |
| A3 | Fast root channel | yes | `tv/chan.py serve`: 1.3 s bring-up, ~10 ms/command, 14.6 MiB/s up, 9.7 MiB/s down; no exploit (2026-10-24) |
| A4 | Root command daemon | yes | `cf_tv_daemon`, directory queue reached by sdb injection, ~1 s/command (2026-10-24) |
| A5 | Root at every boot | yes | `AT-HUB.service` (root) → `/opt/etc/parseinfo.sh` (awk) → `cf_tv_daemon_start.awk` → unit `cf_tv_daemon` → boot hooks. Five cold boots 2026-10-20; real power cycles 2026-10-23/24 (AT-HUB ~9–10 s, daemon 9.4–11.4 s after boot) after the stale-lock fix |
| A6 | One-shot command at next boot | yes | `/opt/cmdq`: each line run by `parseinfo.sh`, then the file is truncated (`payload/parseinfo.awk`) |
| A7 | Run code when the TV wakes from standby | yes | "TV off" is kernel suspend (`db/boot/instant_on = 1`), processes survive. A jump in (wall clock − uptime) marks the wake; the daemon raises `wake` and runs `/opt/cf_tv_daemon/on-wake.d/`. The same detection, then in a separate unit, logged `wake detected` and acted 21 s later (2026-10-24) |
| A8 | Recovery path without the boot payload | yes | `tv/root.py exec --via exploit` (archive-root): 7.2 s, label `User`; works since the 2026-10-24 PYTHONPATH fix |
| A9 | `sdb shell` / injection identity | not root | uid 901 `sdk`, label `System`, no capabilities. Injections over 510 bytes are staged as a script and work |
| A10 | Boot and wake hooks | yes | files in `/opt/cf_tv_daemon/on-boot.d/` and `on-wake.d/` run as root on each event (`payload/README.md`); `50-btconnect` ran on the boot event 2026-10-24 11:28 |
| A11 | Installed apps cannot change what runs as root | yes | owners and modes in `docs/06-security/threat-model.md` §2; as uid 5001 with `CapEff 0`, all 10 write attempts were blocked (2026-10-24). uid 901 (through the queue), root, and vendor daemons with `CAP_DAC_OVERRIDE` still can |
| A12 | Immutable flag (`FS_IMMUTABLE_FL`) on `/opt` (VDFS) | yes | `apps/chattr.cs`; set on `/opt/etc/parseinfo.sh`. Scratch-file test 2026-10-24: uid 901 `mv` over and `rm` → "Operation not permitted", write → "Permission denied" |

Caveats:
- `/opt` is ~3.9 GB, 87 % full after the 2026-10-24 cleanup (541 MB free). A
  10-minute `busctl monitor` capture (154 MB) filled it and broke channel
  writes.
- `adagent-service` → `launch_script.sh` is **not** a root hook: the unit runs
  as `owner`, and the vendor `pisa-service` stop-marker blocks it
  (`ConditionResult=no`; it did not start this boot, 2026-10-24).

## 2. Filesystems and storage

Detail: `docs/04-system/block-layer.md`, `docs/04-system/device.md`.

| # | fact | status | evidence |
|---|---|---|---|
| F1 | `/` is VDFS, read-only, zlib + per-block sha256, signed | yes | `/proc/mounts` `vdfs ro` (2026-10-24); `mkfs.vdfs` options in the image |
| F2 | `/opt` is VDFS `rw,dncs` and persists across reboots | yes | `/proc/mounts` (2026-10-24); the boot payload lives there |
| F3 | `/tmp`, `/run` are tmpfs | yes | `/proc/mounts` (2026-10-24) |
| F4 | Runtime unit overrides in `/run/systemd/system` | yes | writable tmpfs, wiped at boot. `UnitPath`: `/etc/systemd/system` (read-only root) first, then `/run/systemd/system`, …, `/usr/lib/systemd/system` (2026-10-24) |
| F5 | Remount `/` read-write | no | refused; `--read-only` is an `mkfs.vdfs` flag recorded in the superblock |
| F6 | `/` is not write-protected at the block layer | yes, **can brick** | `ro` = 0 on `mmcblk0` and `mmcblk0p19` (2026-10-24). Raw writes to p19 worked on 2026-10-20 (512 B rewritten identically; marker at the last sector) |
| F7 | eMMC | — | Samsung `8GTF4R`, 06/2021 (2026-10-24), ~7.3 GiB, `ffu_capable 1`, RPMB present |
| F8 | VDFS accepts only signed volumes | open | strings only: `Driver supports only signed volumes`, `vdfs4_check_and_sign_dirty_bnodes`, `vdfs4_sign_mapping_pages`; never tested |

## 3. Disabling vendor services

Detail: `docs/04-system/debloat.md`, `docs/04-system/masking-daemons.md`.

| # | capability | status | evidence |
|---|---|---|---|
| S1 | Vendor stop-markers (`ConditionPathExists=!/opt/usr/share/systemd/system/<marker>`) | yes | 9 markers set 2026-10-22; guarded units never start after reboot (2026-10-24) |
| S2 | App registry: `app_disable='true'` in `/opt/dbspace/.pkgmgr_parser.db` | yes | held across two reboots |
| S3 | Runtime masks from `/opt/masklist`, re-applied every boot | yes (mechanism) | `.service` units only. A mask does not stop a running unit (needs `systemctl stop`); fully reversible (2026-10-20). For units without a marker |
| S4 | What is masked now | nothing | `/opt/masklist` and `/run/systemd/system/` empty (2026-10-24) |
| S5 | ACR frame grabber `contents-recognition-service` | still running | no marker, no registry entry; `active` (2026-10-24) |

## 4. Running our own code

Detail: `docs/01-root/native-code.md`, `docs/06-security/uep-bypass.md`,
`docs/06-security/threat-model.md`.

| # | capability | status | evidence |
|---|---|---|---|
| X1 | Unsigned scripts via `#!/usr/bin/awk -f` | yes | gawk has no UEP hook; the whole boot payload is awk |
| X2 | Unsigned `sed -f` scripts (`e`, `s///e`) | yes | 2026-10-20 |
| X3 | Unsigned bash/sh/node scripts | no | refused by the UEP check in `libkUEPUser.so` (see X12) |
| X4 | C# under the signed `/usr/bin/dotnet` as uid 0 | yes | everyday use: channel agent, `apps/*.cs`; label `User` |
| X5 | NUI app on screen | yes | `cftv` in the borrowed slot `com.samsung.tv.social-chat-app`, uid 5001 (`docs/03-video/app-build.md`, `docs/03-video/cftv.md`) |
| X6 | P/Invoke into the TV's own libraries | yes | `apps/cftv.cs`, `apps/bt-trace.cs` |
| X7 | Raw ARM code from C# | yes | anonymous `mmap` RW, `mprotect` RX, call: returned `0x1337`; `svc #0` `getpid()` (2026-10-20) |
| X8 | Our own ELF via `elfload.dll` | yes, freestanding only | `/opt/el/elfload.dll` maps `PT_LOAD`, applies the PIE bias, calls the entry: returned 1337 (2026-10-21). No argv/auxv/TLS, so static glibc segfaults (2026-10-23). Its source is not in the repo |
| X9 | `execve` of our own ELF | no | EPERM from every writable filesystem, any SMACK label, even for byte-identical copies of `/usr/bin/dotnet` and `/bin/sh` (2026-10-22). Builtin `uep` kernel module |
| X10 | What `execve` accepts | — | 2026-10-21: runs = files on the signed read-only root (also when bind-mounted onto `/opt`), a Samsung squashfs image on a new loop device, an untouched copy of `chrome-sandbox` (the one binary with a UEP trailer `…:UEP`). EPERM = our ELF on squashfs, our ELF with `chrome-sandbox`'s valid trailer appended. Inferred rule: a signed volume, or a trailer bound to the content |
| X11 | Swap a UEP-signed `.so` through a writable `LD_LIBRARY_PATH` | no | `failed to map segment from shared object` |
| X12 | UEP userspace bypass: empty stub bind-mounted over `/usr/lib/libkUEPUser.so` | yes 2026-10-21, **not current** | `uep_verify` is compiled into bash and fails open; with the stub, bash/sh scripts and `node` run from `/opt`. Not bound now and not installed at boot (`/proc/1/mounts`, 2026-10-24). Suspected cause of the 2026-10-20 outage: never bind it and then reload or restart services (`docs/01-root/recovery.md`) |
| X13 | Write another process's memory and run code in it | yes | `/proc/<pid>/mem` read/write at any offset, ignores W^X, works on `System::Privileged` pid 449; a hijack ran our bytes (`PWNED`, 2026-10-20). No Yama (2026-10-24) |
| X14 | ptrace any process | yes | `apps/bt-trace.cs` (Thumb `0xDE01` breakpoints): 52 attach/detach cycles clean (2026-10-23) |

## 5. UEP and kernel

Detail: `docs/06-security/uep-bypass.md`, `docs/06-security/techniques.md`.

| # | fact | status | evidence |
|---|---|---|---|
| U1 | UEP checks the interpreter, never the script | yes | awk scripts run, bash scripts refused, same directory |
| U2 | Userspace check `uep_verify` is compiled into bash (`T uep_verify` at `0x1b304`) and fails open when the library or symbol is missing | yes | disassembly (2026-10-21); which binaries carry the check: `docs/06-security/uep-bypass.md` |
| U3 | The kernel side is the builtin `uep` module | yes | `modinfo` → `(builtin)`; no parameters; no sysfs/procfs/debugfs control |
| U4 | Kernel enforcement flag `0xc06b58b4` has one writer (`0x1df728`); gate `cmp r4,#8`; verifier returns `{0,1,3,615,2561}` | static | disassembly (2026-10-21) |
| U5 | `/usr/lib/key.pub` is absent, yet the library still rejects unsigned scripts | yes | 2026-10-24 |
| K1 | `modules_disabled=0`, `tainted=4097` | yes | `/proc/sys/kernel` (2026-10-24) |
| K2 | `insmod` of a shipped module | yes | `acmd.ko` loaded, clean `rmmod` (2026-10-20) |
| K3 | Load a modified or our own module | no | modules are UEP-signed (`…AAACAg==:UEP`); a 1-byte append or flip → EPERM |
| K4 | `rmmod uep` | no | builtin |
| K5 | `/dev/mem`, `/dev/kmem`, `/proc/kcore`, `/proc/kallsyms` | absent | 2026-10-24 |
| K6 | Kernel link base `0xC0008000` (uImage `0x80008000` is physical) | static | 36,627 of 39,416 movw/movt pairs agree |

## 6. Privileged D-Bus services and zones

Detail: `docs/06-security/ps-agent-policy.md`, `docs/05-firmware/remotepc-dbus.md`,
`docs/04-system/lxc-zones.md`. All root-escalation routes; moot now that we
have root.

| # | fact | status | evidence |
|---|---|---|---|
| P1 | `com.safe.ps_agent` (uid 0, kdbus), `/com/safe/ps_agent`, one member `CallCommand`; the `ps_*` names are argument values | static | binary symbols and strings; `CallCommand`'s wire signature is undocumented |
| P2 | `mount.smb.sh` runs credential-file contents through root `bash -c` | static | source read |
| P3 | Policy: compiled (label, exe, rules) triples; CIFS rules exist only for `User::Pkg::org.tizen.remotepc`; `System::Privileged` has none | static | never checked live (`RULE_NO_FOUND`) |
| P4 | Whether `PS_AuthInfo::Match` ANDs or ORs label and exe | open | |
| P5 | `org.tizen.remotepc` `PutMessage(ay, i)` at `/org/tizen/remotepc/Handler`; message types `SAMBA_MOUNT`, `SAMBA_UNMOUNT`, `USERNAME`; reaches `mount.smb.sh` | static | introspected; never exercised end to end |
| P6 | Enter `rdpzone`, read its `/proc`, read `/zone` secure dirs | no | `Operation not permitted` / `Permission denied` as root `System::Privileged` (2026-10-24); `nsenter` untested |
| P7 | Author our own zone template | no (inferred) | templates are signed (`zone_verify_uep_sign`) |

## 7. Device control and observation

Detail: per row.

| # | capability | status | evidence |
|---|---|---|---|
| C1 | See every D-Bus method call, reply and signal, with the sender's pid, binary, uid and SMACK label | yes | `busctl --system monitor` as root (kdbus; `dbus-monitor` gets signals only). Captured the Bluetooth connect and the input switch (`docs/06-security/techniques.md`) |
| C2 | Call any vendor D-Bus method as root | yes | Bluetooth `180`/`181`, `ISourceControl` select. `busctl` cannot print variant replies and exits 1 even on success; use `gdbus` |
| C3 | Inject keys | yes | `EventKey` via `input_keyevent` (`bin/control`): `Return`, d-pad, `XF86Back` act (2026-10-24). `XF86AudioMute`/`XF86AudioRaiseVolume` had no effect and `XF86RaiseChannel` opened Accessibility Shortcuts (2026-10-23) (`docs/04-system/control.md` §1) |
| C4 | Change channel | open | no mechanism (`docs/04-system/control.md` §1) |
| C5 | Switch the input source | yes | `com.samsung.ISourceControl`: function 4 query, function 0 select; 0 = tuner, 15 = HDMI (`docs/03-video/cftv.md`) |
| C6 | Volume | partial | `pactl` set and read back work; audible effect unverified (`docs/04-system/control.md` §2) |
| C7 | Text to speech | yes | `bin/cftv say`, heard by the user (2026-10-24; `docs/04-system/control.md` §3) |
| C8 | Play a sound | yes | `bin/cftv sound`, heard by the user (2026-10-24) |
| C9 | Bluetooth speaker connect / disconnect | yes | requests `180` / `181` as root (`docs/04-system/bluetooth.md`) |
| C10 | Bluetooth speaker reconnects on wake / with the TV running | yes | the `50-btconnect` hook, the Sound Output menu's connect sequence; audio ~3–10 s after the request (`docs/04-system/bluetooth.md`) |
| C11 | Bluetooth speaker reconnects after a power loss | **fix deployed, not verified** | TV-side race found in bt-service's log: the speaker's own reconnect waits on an authorization that the vendor answers ~10 s too late, and bt-service's own connect collides with it. The hook accepts the authorization itself (`4177`) before ~20 s. Without a prior connection: audio at 76–143 s once the speaker advertises (2026-10-24) |
| C12 | Live "which audio output is active" | open | `lastSoundRoute` is persisted, not live; AVOC's output-selection methods are not implemented (`docs/04-system/control.md` §4) |
| V1 | Screen capture | yes | `bin/snap` 0.32 s/frame, 720×404; `bin/stream` 12.7 fps end to end, 40/40 frames distinct (`docs/02-capture/screen-capture.md`) |
| V2 | Overlay on live TV | yes | `bin/cftv` image, GIF, text, rect render; focus stays on `tv-viewer` (`docs/03-video/cftv.md`) |
| V3 | Play video | yes | takes the hardware video path; cftv restores the HDMI picture with an input bounce and re-routes the Bluetooth speaker (`docs/03-video/cftv.md`, `docs/04-system/bluetooth.md`) |
| V4 | Fullscreen transparent window over live HDMI video | no | the picture turns black (`docs/03-video/transparent-overlay.md`) |
| V5 | Which app has focus | yes | the `O` in the `Foc` column of `enlightenment_info -topvwins` (`-keygrab_status` always shows `csfs`) |
| V6 | Screenshots show every overlay item twice, 960 px apart | open | probably a capture artifact; not checked on the panel |

## 8. Still blocked or open

| wall | enforcer | status |
|---|---|---|
| `execve` of our own ELF | builtin `uep`, content signature | closed without Samsung's key; not needed (C#, `elfload.dll`) |
| glibc or dynamic ELF under `elfload.dll` | the loader passes no argv/auxv/TLS | open: extend the loader, or use C# |
| Write `/` through the filesystem; remount rw | VDFS `--read-only` superblock flag, signed volume | no. Raw block writes work (F6); remount after a raw edit untested |
| Custom firmware | needs Samsung's signing key | no |
| Our own kernel module | module verifier | no; `insmod -f` and reuse of a signed module untested |
| `rmmod uep` | builtin | impossible |
| Enter `rdpzone`; read `/zone` secure dirs | separate SMACK domain; per-inode VDFS refusal | no; `nsenter` untested |
| Bluetooth reconnect after a power loss | unknown `bt-service` state | open (C11) |
| Channel change | — | open (C4) |
| Live current-audio-output query | AVOC methods unimplemented | open (C12) |
| Screen goes dark after ~20 min idle | unknown; `db/menu/auto_power_off = 6`, meaning unknown | open, user observation only |

**Boundary:** we run any code we like as root (awk scripts, C#, raw ARM,
freestanding ELF, injection into running processes). The firmware stays
signed and byte-identical: no `execve` of our own binaries, no filesystem
writes to `/`, no kernel modules of our own.

## 9. Dead ends: do not retry

`/dev/ntk*` (absent on NIKEM2) · `/dev/sdp_mem` (a DDR test harness: `mmap`
works, the first read hangs) · prelink hijack · unsigned `LD_PRELOAD` ·
`hybridAOT` → root · setcap replacement · `sdbd` env injection ·
`debug-shell` · `dotnet-memory-reclaimer` `system()` · HyperUART udev chain ·
gamepad udev script · custom firmware image · QN90B/QN90F exploits ·
`sdb shell 0` · `sdb root on` · `keymap_update.sh` eval (dead code) ·
`dlog_logger_conf` (unused) · boot-script dot-source sweep (none) ·
`duid-gadget` vs the kernel gate · netlink protocol 31 into the `uep` flag
(packet format recovered, the `==8` gate is unreachable).

Bluetooth and audio dead ends: `docs/04-system/bluetooth.md`.
