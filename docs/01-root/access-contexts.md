# Root access: contexts, paths, and the tools

Current as of 2026-10-24. Device: QN75QN85DAFXZA, `T-NKM2AKUC-2301.1`, ARMv7,
.NET Core 3.1. How root persists across boots is in `persistent-root.md`, and
what to do when something breaks is in `recovery.md`.

## 1. Identity by context

Most wrong conclusions in this repo's history came from mixing these up.

| context | uid | SMACK label | caps |
|---|---|---|---|
| `sdb shell`, every sdb injection (`tv/root.py inject`) | **901 `sdk`** | `System` | none |
| our app in the borrowed slot (`tv/cftv.py` `SLOTS`: `com.samsung.tv.social-chat-app` on the QN85D, `com.samsung.tv.samsungdex` on the CU7000) | 5001 | `User::Pkg::<slot>` | none |
| boot payload, `cf_tv_daemon`, boot/wake hooks | **0** | `System::Privileged` | full |
| channel commands (`tv/chan.py`) | 0 | `System::Privileged` (raised per command) | full |
| channel agent process / anything exec'ing `dotnet` | 0 | `User` | full |
| exploit (archive-root) | 0 | `User` | full |

`/usr/bin/dotnet` carries `SMACK64EXEC="User"`, so any process that execs it
becomes `User`, whatever started it; `systemd-run -p SmackProcessLabel=` does
not override it. Root with `CAP_MAC_ADMIN` may relabel itself, which is what
`tv/chan.py` does: it prefixes every command with
`printf System::Privileged > /proc/self/attr/current`.

## 2. The ways in

| path | mechanism | speed | needs exploit |
|---|---|---|---|
| **channel** | `tv/chan.py serve`: the daemon starts the root agent as transient unit `cf_tv_agent`; it calls back to the host, which serves `$XDG_RUNTIME_DIR/cf-tv/<ip>/chan.sock` | **1.3 s bring-up, ~10 ms/command**, 14.6 MiB/s up, 9.7 MiB/s down | no (daemon only; down when the daemon is) |
| **daemon** | `cf_tv_daemon`, directory queue in `/opt/cf_tv_daemon/state`, reached by sdb injection (uid 901) | ~0.75 s/command | no |
| boot queue | `/opt/cmdq`: each line runs as root at the **next** boot (`persistent-root.md`); manual recovery only, there is no "run now" | one shot | no |
| exploit | archive-root through sdb: the public SVE-2025-50109 SDB package-name injection gives uid 901, then archive-root gives uid 0 (`vendor/samsung-tv-root-source/README.md`) | ~7 s/call | yes |

Measured 2026-10-24/25.

**Channel**: the fast path for anything interactive or bulky. `bin/snap`,
`bin/stream` and `bin/cftv` use it.

```sh
tv/chan.py serve &                      # bring up, through the daemon only
tv/chan.py run 'id' 'uname -a'          # commands, one round trip
tv/chan.py put file1 file2              # WRITEFILE into the agent's staging dir (4 MiB/file)
tv/chan.py pull /path/in/staging        # READFILE, staging dir or agent runtime dir only
tv/chan.py status                       # up?, label, staging dir
```

Limits:
- Each command may run ~30 s.
- `put`/`pull` work only inside the agent's staging dir
  (`/home/owner/share/tmp/sdk_tools/cf-agent-*/`); copy into place with a
  command.
- The agent dies on suspend or reboot without closing its socket. The serve
  process pings an idle agent before each request and reconnects first, and a
  15 s keepalive reconnects through the daemon once the TV answers. After a
  reboot the channel was back 16 s after sdb, unused, and the first command
  worked (2026-10-25). Before, the first command after a reboot failed with
  `channel_down`.

**`tv/root.py`**: picks the channel if `tv/chan.py serve` is running, else the
daemon (needs nothing but sdb). It never picks the exploit by itself.

```sh
tv/root.py doctor                         # health: sdb, daemon, channel, unit, queue, dlog, space, payload, hooks
tv/root.py probe                          # which paths are live, heartbeat age
tv/root.py exec 'id' 'uname -a'           # channel, else daemon; commands in order
tv/root.py exec --timeout 60 'sleep 30'   # per-command runtime limit (default 20 s)
tv/root.py exec --via exploit 'id'        # the recovery path, only when asked
tv/root.py reboot --report                # reboot, wait until usable, print the boot report
tv/root.py report                         # this boot on one timeline (daemon, hooks, logging, speaker)
tv/root.py dlog arm | fetch | disarm      # vendor dlog for the next boot only (techniques.md)
```

- **Exit status like ssh:** the first failing command's status, 0 if all
  succeeded, 255 if the command could not be run or its result was lost.
  A failed command also prints `[exit N]` as the last line, on every path.
- **No silent fallback.** A failed command is not retried on another path (it
  may already have run). With no usable path, the error says why (TV off,
  sdb, daemon heartbeat age) and what to run.
- **sdb after a reboot:** the host's sdb server keeps the dead link and
  `sdb connect` fails although the TV answers. `tv/root.py` (and `tv/chan.py`)
  restart the server and retry by themselves (`sdb_ensure`, 2026-10-25).
- **sdb is serial:** sdbd runs one injection at a time. A daemon request
  submits and waits for its result in one injection, at most 3 s per wait
  (`COLLECT_STEP`); a longer command is polled again. Parallel `tv/root.py`
  calls queue behind each other; the channel does not have this limit.
- **`reboot`** prints when the TV went down, when sdb answered, and when the
  daemon responded (2026-10-25: down 5 s, sdb 23 s, daemon 30 s after the
  request).

## 3. The daemon

`/opt/cf_tv_daemon/cf_tv_daemon.awk`, unit `cf_tv_daemon`: the one long-running root
process. It drains the command queue and raises the `boot` and `wake` events.
Log: `/opt/cf_tv_daemon.log`, appended and capped at 64 KiB (lines
`start pid N`, `event boot`, `wake, suspended ~Ns`, `clock set`). The unit
restarts on failure after 2 s (`Restart=on-failure`, 2026-10-25), so a crash
shows as a second `start pid` line without a new boot.

### Command queue

awk has no sockets, so IPC is a directory queue. `req/`, `stage/`, `out/` and
`rc/` are `sdk:sdk` 0700: only uid 901 (the sdb injection `tv/root.py` submits
through) and root can use them. Installed apps run as uid 5001 with no
capabilities and are locked out. Who else can still write them:
`docs/06-security/threat-model.md` §2.

| path | meaning |
|---|---|
| `/opt/cf_tv_daemon/state/req/<id>` | a request: **one line** of shell (the daemon `getline`s only the first line) |
| `/opt/cf_tv_daemon/state/out/<id>` | that command's stdout+stderr |
| `/opt/cf_tv_daemon/state/rc/<id>` | exit status, written last |
| `/opt/cf_tv_daemon/state/stage/<id>` | full command text staged by `tv/root.py`; the request is `eval "$(cat …stage/<id>)"` |
| `/opt/cf_tv_daemon/state/heartbeat` | uptime at the last loop; the only liveness signal |

`tv/root.py` writes requests as `.id` and renames them; the daemon's `ls`
skips dotfiles, so it never reads a half-written request. Each request runs
in the background (`( ( { cmd; } > out ) ; echo $? > rc ) &`); the loop paces
at 200 ms. Until 2026-10-25 the `&` covered only the final `echo`, so every
command ran in the foreground: one slow command (a recursive `grep`) froze the
queue, the heartbeat and wake detection for minutes. Fixed and verified: a
quick request finished while a 20 s one was still running.

**Single instance:** `mkdir /opt/cf_tv_daemon/state/.lock` plus `daemon.pid`, both on
`/opt`, so they survive reboots. An old pid only counts as "already running" if
`/proc/<pid>/cmdline` names `cf_tv_daemon.awk`. A bare `kill -0` once matched a
reused pid (`storaged`) after a reboot, and the daemon silently never started.
Fixed 2026-10-24; such an exit is now logged in `/opt/cf_tv_daemon.log`.

### Events and hooks

| event | raised |
|---|---|
| `boot` | once per boot: the daemon compares `/proc/sys/kernel/random/boot_id` with `/opt/cf_tv_daemon/state/boot_id`, so a daemon restart (e.g. by `boot install`) does not rerun boot hooks |
| `wake` | when deviced's power state `memory/pm/state` goes from 3 (screen off) or 4 (standby) to 1 (on), read every 2 s with `buxton2ctl` (~26 ms a read). Until 2026-11-25 this was a jump in wall clock minus uptime, which needs a kernel suspend: a short off/on blanks the screen without one, and on kernel 5.4 (CU7000) `/proc/uptime` counts suspended time anyway, so no wake was ever seen there |

On an event the daemon runs the hook runner `/opt/cf_tv_daemon/cf_tv_hooks.sh`
detached, which runs the files in `/opt/cf_tv_daemon/on-boot.d/` or `on-wake.d/`
as root. Writing, installing and removing hooks: `payload/README.md`.

```sh
tv/root.py hooks boot                  # run the boot hooks now, through root
tv/root.py hooks wake                  # same for the wake hooks; log: /opt/cf_tv_hooks.log
tv/root.py hooks list                  # enabled and disabled hooks on the TV
tv/root.py hooks disable 50-btconnect  # off for both events; `boot install` keeps it off
tv/root.py hooks enable 50-btconnect
```

### Deploying: `tv/root.py boot`

```sh
tv/root.py boot install   # deploy payload/, apply owners and modes, restart cf_tv_daemon
tv/root.py boot status    # md5 vs repo (ok/DIFFERS/MISSING), modes (ok/WANT …), immutable flag; exit 1 on drift
tv/root.py boot restore   # vendor parseinfo.sh back; run before a firmware update (persistent-root.md)
```

- All three run through root (channel, else daemon; `--via exploit` for
  recovery). `status` reads md5s and modes as uid 901 and the immutable flag
  through root.
- `install` restarts the daemon through the transient unit `cf_tv_restart`
  (`/bin/sh -c 'sleep 2; exec awk -f cf_tv_daemon_start.awk'`). Stopping
  `cf_tv_daemon` kills its whole cgroup, including the request that asked for
  the restart, so the restart runs in its own unit after that request has
  returned. `install` then waits for a new pid with a fresh heartbeat: 6.5 s,
  pid 1364 → 7449 (2026-10-24). If it does not come back: `recovery.md`.

### Why it's built this way

- **awk, not sh:** `.sh` files are refused by UEP; gawk is not hooked. The
  hook runner and hooks are shell text the daemon `eval`s, never executed.
- **`systemd-run --no-block`, not `&`/`setsid`:** systemd kills the starting
  unit's whole cgroup. systemd-run has no shell, so the service opens its own
  log; never put `>` in its argv.
- **Queue modes re-applied on every start:** `mkdir -p` does not fix the mode
  of an existing directory.
- **Status captured inside the group:** `( { cmd ; } > out 2>&1 ) ; echo $? > rc`;
  an outer `echo $?` never runs if `cmd` calls `exit`.
- **Liveness from the heartbeat:** the pid file and dirs survive reboots and
  prove nothing.
- **New behaviour goes in a hook, not the daemon:** the daemon is the root
  lifeline; a broken daemon means falling back to the exploit.

## 4. Limits and permissions

| fact | detail (verified 2026-10-24 unless noted) |
|---|---|
| sdb injection size | 510 bytes per appinstall argument. Longer commands are `sdb push`ed into `sdk_tools` as a script and sourced; that works for uid 901 (needs free space on `/opt`) |
| writable by uid 901 | `/opt/cf_tv_daemon/state/{req,stage,out,rc}` (`sdk:sdk` 0700), `/opt/cmdq`, `/opt/cmdout` (`sdk:sdk` 0600). Everything else of ours is root-owned (`docs/06-security/threat-model.md` §2) |
| not writable by uid 901 | `/etc/systemd/system`, `/run/systemd/system` |
| systemd control | uid 901: `Access denied`. Root: works; `systemctl start` on an already-run `Type=simple` unit is a no-op, so use `restart` |
| `/` | read-only VDFS; unit files cannot be added there |
| `/opt` | ~3.9 GB, ~87% used (541 MB free after the 2026-10-24 cleanup); filling it breaks injections and channel writes (`recovery.md`) |

## 5. Suspend, not shutdown

With `db/boot/instant_on = 1`, the remote's power button does not shut down. A
short "off" only blanks the screen (`memory/pm/state` 1 → 4, kernel still
running; CU7000 2026-11-25); a longer standby also suspends the kernel
(`/sys/power/state`: `freeze mem`). Either way processes, including the daemon,
survive. Boot hooks run only after a real power loss or reboot; anything meant
to happen "when the TV turns on" needs the daemon's `wake` event (§3).

Related clock facts:
- The wall clock reads 1969 at boot until time sync.
- `btime` in `/proc/stat` drifts by the time spent suspended.
- `/proc/<pid>/stat` start times are in 250 Hz ticks.

## 6. Traps

- **`/dev/tcp/host/port` gives false "closed" in some shells.** Use
  `nc -z -w2 $CF_TV_IP 26101`.
- **The sdb link drops on sleep or power events.** Injections then fail after
  25 s with "TV did not connect to callback … closed"; the message blames the
  firewall, but it never was. Run
  `vendor/sdb/data/tools/sdb connect $CF_TV_IP:26101`, or use the channel.
- **sdb host authorization once reset after a reboot** (handshakes reset, only
  a power cycle cleared it). Seen 2026-10-22, not since.
- **Backgrounding inside an sdb or exploit command hangs the caller** unless fd
  3 (the callback socket) is closed on the child.
- **`pkill -f "chan.py serve"` kills the shell running it.** Kill by pid.
- **`/opt/masklist` holds unit names only;** it is re-applied every boot
  (`docs/04-system/masking-daemons.md`).
