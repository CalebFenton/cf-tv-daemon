# cf-tv-daemon

A root daemon and host tooling for Caleb Fenton's Samsung TV ("CF" = Caleb
Fenton). On the TV, `cf_tv_daemon` runs as root from every boot: it takes
commands from this computer and runs boot and wake hooks (Bluetooth speaker
reconnect). On this computer, `tv/` and `bin/` drive it: root commands, screen
capture, overlays and notifications, sound, remote keys, and the recognition
HUD. The project began as research into developing on the TV; the research
record is in `docs/`.

Everything here targets one television:

| field | value |
|---|---|
| model | QN75QN85DAFXZA (21_NIKEM2_QTV, 2024 Neo QLED) |
| firmware | `T-NKM2AKUC-2301.1` |
| SoC | ARMv7, .NET Core 3.1 |
| root | persistent, uid 0, survives reboot |

**Start here:** this file, then `docs/INDEX.md` → `docs/01-root/access-contexts.md`.
Most wrong conclusions in this repo's history came from confusing the execution
contexts with each other.

---

## What actually works

| capability | entry point | status |
|---|---|---|
| root, persistent across power cycles | `tv/root.py exec 'cmd'` | daemon starts at boot; no exploit needed |
| fast root channel | `tv/chan.py serve`, `tv/chan.py run 'cmd'` | 1.3 s bring-up, ~10 ms/command, raw file transfer |
| screenshot, incl. hardware video plane | `bin/snap out.jpg` | 720x404 JPEG, 0.32 s |
| capture a burst of frames | `bin/stream --frames 60` | 12.7 fps end to end |
| images, animated GIF, text, shapes over anything | `bin/cftv image/text/rect …` | per-pixel alpha; remote keeps working |
| video files or stream URLs on the TV | `bin/cftv video file.mp4\|URL` | plays; HDMI picture and Bluetooth audio are restored afterwards |
| sound files or URLs | `bin/cftv sound file.mp3` | heard |
| make the TV speak | `bin/cftv say "text"` | heard |
| notification cards, top right | `bin/cftv notify "text" --title T --icon i.png --sound` | slide in, stack, chime heard |
| recognition HUD over the picture | `bin/hud live`, a client of cf-vision, a separate recognition service (not part of this repo) | faces (named from cf-vision's photo galleries, or remembered as `SUBJECT 0042` until named; age, sex, landmarks), people with skeletons, hand gestures and objects tracked 13–16 times a second, an ENHANCE inset and a scene caption, movie-style (`docs/03-video/hud.md`) |
| remote keys, TV-internal volume | `bin/control` | D-Bus key injection |
| Bluetooth speaker reconnects when the TV turns on | `payload/on-boot.d/50-btconnect` (boot/wake hook), `bin/bt-reconnect` | on wake: yes. After a power loss: root cause found (TV-side authorization race), fix deployed, not yet verified (`docs/04-system/bluetooth.md`) |
| boot/wake hooks: shell text run as root on every boot or wake | `payload/on-boot.d`, `payload/on-wake.d`, `tv/root.py boot install` | `payload/README.md` |
| see every D-Bus call with the caller's identity | `busctl --system monitor` as root | the bus is kdbus; `dbus-monitor` sees only signals |
| breakpoint-trace a running process | `apps/bt-trace.cs` | ptrace, Thumb breakpoints, args + backtrace |

## What does not work, and why

| tried | result |
|---|---|
| `execve` of our own ELF | blocked by the UEP kernel check (C#/P/Invoke, shellcode and `elfload.dll` work instead: `docs/01-root/native-code.md`) |
| modified firmware | signed; no key |
| writing `/` | VDFS volume flagged read-only, unsigned volumes refused |
| per-pixel-alpha **video** | no VP9/AV1 decoder, no Matroska demuxer |
| installing our own app package | `Check certificate error [-12]`; we replace a shipped app's DLL instead (`docs/03-video/app-build.md`) |
| changing TV channel by key | `XF86RaiseChannel` opens Accessibility Shortcuts instead |

Details: `docs/04-system/capability-matrix.md`.

---

## Layout

```
bin/        commands you run: sh wrappers that start tv/*.py in .venv (and snap)
tv/        host Python: root.py (run commands as root), chan.py (fast channel), cftv.py,
            hud.py, bt.py, control.py, stream.py, bt_reconnect.py
apps/       C# that runs ON the television: cftv (display agent), bt-trace, capture probe, chattr
payload/    what runs on the TV at boot: root daemon, boot/wake hooks (see payload/README.md)
probes/     one-off experiments: dotnet/ (memprobe, injector, injectvictim)
docs/       all written findings, numbered by topic
docker/     container for the firmware toolchain
```

`docs/` is ordered by topic, not alphabetically:

```
docs/01-root/       root access, contexts, native code, recovery
docs/02-capture/    screen capture and how to read frames
docs/03-video/      overlay, video in/out, TV app build
docs/04-system/     device, capability matrix, debloat, telemetry
docs/05-firmware/   firmware container format, remotepc chain
docs/06-security/   UEP, PS agent authz, threat model, RE techniques
docs/07-prior-art/  Tiz0wn
docs/90-log/        dated session records
docs/INDEX.md       full index of every page
```

Third-party git projects are submodules; other third-party files and the
extracted firmware are **not** in the repo. See `VENDOR.md`.

---

## Setup

Host tools are pinned in `mise.toml` (Python, uv, ruff, prek); Python
packages in `pyproject.toml` and `uv.lock`. `bin/cftv sound` and `notify --sound FILE`
need `ffmpeg` on the host (sounds are converted to WAV). `bin/hud live` also needs
cf-vision, a separate recognition service (not part of this repo), reachable from
this computer.

```sh
git clone --recurse-submodules https://github.com/CalebFenton/cf-tv-daemon.git
cd cf-tv-daemon
cp .env.example .env                # optional single-TV fallback: CF_TV_IP
tv/devices.py scan                  # name the TVs on the LAN (devices.toml); add `speaker = "AA:BB:..."` per TV for the reconnect hook
# restore vendor/sdb and build/tv-framework per VENDOR.md (not in git)
mise trust && mise install python uv ruff prek
uv sync                             # .venv, including the samsung_tv_root submodule
prek install                        # git pre-commit hook: ruff, shell and awk syntax
```

Pick the TV once, then every command finds it by name:

```sh
tv/devices.py scan                  # writes devices.toml: names + the TVs' fixed ids
tv/devices.py list                  # up/down, sdb, model
tv/devices.py default qn85d         # the TV commands use when --tv is absent
```

`devices.toml` (not in git) stores each TV's fixed `id` from its REST API, not
its address. A DHCP move is handled: the stored address is tried, then the /24
is scanned for that id and the file is updated. Select per command with
`--tv NAME`, or with `CF_TV_NAME` in the environment. With no name selected,
`CF_TV_IP` from `.env` still works for a single-TV setup (`tv/project_env.py`).

Every command runs in `.venv` however it is started: `bin/` commands are
`sh` wrappers around `tv/*.py`, and `tv/root.py`, `tv/chan.py`,
`tv/cftv.py` re-run themselves under `.venv/bin/python` when started by another
Python (`tv/project_env.py`). Checks by hand: `prek run --all-files`.

## Quick start

```sh
# 1. run something as root (channel if running, else the daemon)
tv/root.py doctor                   # health check: is everything working?
tv/root.py probe                    # which paths are live right now
tv/root.py exec 'id'                # daemon ~0.75 s; --via exploit forces the recovery path
tv/chan.py serve &                  # fast channel (needed by snap/stream/cftv), 1.3 s bring-up
tv/root.py reboot --report          # reboot, wait until usable, show what happened at boot
tv/root.py report                   # this boot on one timeline

# 2. capture
bin/snap /tmp/shot.jpg              # one frame
bin/stream --frames 60 --outdir /tmp/frames

# 3. run something on the TV
bin/cftv image logo.png --at 100,80 # put an image on screen
bin/cftv text "hi" --at 60,900      # text
bin/cftv notify "Pasta is ready" --title Timer --sound  # slide-in card, top right, with the TV's chime
bin/hud live                        # movie-style HUD: faces, people, gestures, objects tracked on screen (Ctrl-C ends)
bin/cftv clear                      # remove everything
```

## Naming

The project is `cf-tv-daemon` (`pyproject.toml`, this repo). On the device the
same name is written `cf_tv_daemon`: the install directory, the systemd unit
and the logs.

**On the device** (in `/opt`, fixed by what the boot payload installs):

| path | what |
|---|---|
| `/opt/cf_tv_daemon/` | everything `boot install` deploys: launcher, daemon and hook runner scripts, `on-boot.d/` and `on-wake.d/` (hooks), `disabled/` (hooks switched off), `chattr/` (`Chattr.dll`), `parseinfo.ORIGINAL.sh` (vendor backup), `btconnect.addr` (speaker to reconnect) |
| `/opt/cf_tv_daemon/state/` | runtime state the daemon recreates: command queue (`req/ out/ rc/ stage/`), heartbeat, pid, boot id, hook locks |
| `/opt/cmdq` | one-shot boot queue (a file) |
| `/opt/cap/` | `CaptureProbe.dll` (deployed by `boot install`); scratch for captures, delete after pulling |
| `/opt/cf_tv_daemon.log`, `/opt/cf_tv_hooks.log` | daemon log, hook log |

Until 2026-11-05 the deployed files lived in `/opt/rootkit/` and the state
directly in `/opt/cf_tv_daemon/`; older dated records may still use those paths.

**On the host** (this repo): `tv/chan.py` is the fast root channel, `tv/root.py`
the path chooser and payload manager. These never appear on the device.

---

## Provenance

The root exploit, root agent and SDB client are a third-party project, the
submodule `vendor/samsung-tv-root-source/` (public domain, `VENDOR.md`).
`tv/`, `bin/`, `apps/`, `payload/` and `docs/` is work done against this
specific set.

## License

Unlicense (public domain); see `LICENSE`. Submodules and the font keep their own licenses (`VENDOR.md`).
