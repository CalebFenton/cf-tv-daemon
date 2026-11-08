# Recovery runbook

What to do when something on the TV is broken. Every entry was hit for real;
dates are when the fix was verified.

## Can't reach the TV

| symptom | cause | fix |
|---|---|---|
| no ping, 26101/8001 closed | TV asleep or off (`TV off` is a kernel suspend) | wake it with the remote; WoL does not wake it |
| ping works, 26101/8001 closed | apps still starting after wake/boot | wait ~30 s |
| `failed to connect` although 26101 answers, after a TV reboot | the host's sdb server keeps the dead link | `tv/root.py` and `tv/chan.py` restart it and retry by themselves (2026-10-25); by hand: `vendor/sdb/data/tools/sdb kill-server`, then `connect` |
| "TV did not connect to callback … closed" | TV offline, or `/opt` full (see below); **not** a firewall | check the two rows above, then `df -h /opt` |

Check reachability with `nc -z -w2 $CF_TV_IP 26101`, not `/dev/tcp`.

## No root path works

```sh
tv/root.py doctor                     # start here: one line per check, FAIL/WARN with the fix
tv/root.py probe                      # paths, daemon heartbeat age, and why not
tv/root.py report                     # what happened at this boot
tv/root.py exec --via exploit 'id'    # recovery path, needs only sdb (~7 s)
```

`tv/root.py` never switches paths by itself; when none works it prints why
and the command to run.

- **Daemon heartbeat stale:** until 2026-10-25 any slow command froze the
  daemon (it ran commands in the foreground); fixed. If it happens anyway:
  `/opt/cf_tv_daemon.log` shows why (e.g. `exit: already running as pid N`).
  As root: `/usr/bin/awk -f /opt/cf_tv_daemon/cf_tv_daemon_start.awk` restarts it.
- **Daemon not back after `boot install`** (`cf_tv_daemon did not come back
  within 40 s`): restart it through a path that does not need it, then check
  the deployment:

  ```sh
  tv/root.py exec --via channel '/usr/bin/awk -f /opt/cf_tv_daemon/cf_tv_daemon_start.awk'   # if a channel is up
  tv/root.py exec --via exploit '/usr/bin/awk -f /opt/cf_tv_daemon/cf_tv_daemon_start.awk'   # otherwise
  tv/root.py boot status                                                                # exit 0 = all ok
  ```
- **Channel down** (`channel_down`, `root-agent disconnected`): since
  2026-10-25 the serve process reconnects by itself (before each request and
  every 15 s), through the daemon only; `channel_down` means that bring-up
  failed (see `/tmp/tvchan.log`), so fix the daemon first (above). The
  channel never runs the exploit itself: an automatic exploit bring-up once
  left a hung `sdbd-tarlauncher … archive-root` process that blocked every
  later sdb injection, the daemon's queue included (2026-11-05). If one is
  left over, kill it as root (`ps -eo pid,etimes,args | grep tarlauncher`).
  If the serve process itself died, start it again; kill an old one by
  **pid**, not `pkill -f "chan.py serve"` (that matches its own shell).
- **A long-running `chan.py serve` keeps the code it started with.** After
  changing `tv/root.py` or `tv/chan.py` (paths especially), restart it.

## `/opt` full

`No space left on device`; channel file writes fail; injections over 510 bytes
fail (they are pushed as a script). Bus captures are the usual cause (154 MB in
10 min). Fix with **short** commands (under 510 bytes), as uid 901 if necessary:

```sh
df -h /opt
ls -la /opt/cap/*.txt
rm -f /opt/cap/<capture>.txt      # pull first if wanted: sdb pull /opt/cap/x.txt
```

## Bluetooth wedged

The Sound Output menu stalls on "Connecting…"; `bt-service` answers connect
requests with `-29` (in progress) or `-9`. Seen 2026-10-23 after repeated
connect retries. Neither stopping our reconnect loop of that time nor a `181`
disconnect cleared it. **A power cycle (unplug ~15 s) does.** Prevention:
never retry `180` in a loop (`docs/04-system/bluetooth.md`).

## Screen black after a cftv video

The HDMI/tuner picture lost the hardware video path. `bin/cftv` restores it
automatically on `remove` and `clear`. By hand: press Source, pick
**another** input, then the original one; re-selecting the active input does
nothing. Details: `docs/03-video/cftv.md`.

## Audio on the wrong output

If sound went to HDMI (the AVR receiver) instead of the Bluetooth speaker,
for example after an input switch:

```sh
bin/bt-reconnect disconnect && bin/bt-reconnect connect
```

The TV re-routes audio to the speaker when it connects. `bin/bt-reconnect
status` shows whether it's connected; the saved route key is not live.

## Smart Hub bar over the picture

An app exited (any exit: `Exit()`, `app_launcher -t`, `-k`) and focus went to
Smart Hub. Resume the app that was showing, normally the live picture:

```sh
tv/root.py exec 'app_launcher -s org.tizen.tv-viewer'
```

Do not send Back (`bin/control key XF86Back`) for this: on HDMI, `tv-viewer`
forwards keys to the HDMI device over CEC, and a Back on 2026-11-05 took the
NVIDIA Shield out of what was playing into Prime Video.

## A hook misbehaves

Hook output is in `/opt/cf_tv_hooks.log` (`<event>/<hook>:` prefix, `start`
and `exit N` per run); daemon events in `/opt/cf_tv_daemon.log`. To stop a
hook, delete its file as root, and from `payload/` so the next `boot install`
does not put it back:

```sh
tv/root.py exec 'rm -f /opt/cf_tv_daemon/on-boot.d/<hook> /opt/cf_tv_daemon/on-wake.d/<hook>'
```

## Before a firmware update, or to undo the boot payload

```sh
tv/root.py boot restore
```

`parseinfo.sh` is immutable, so a plain `cp` over it fails; `restore` clears
the flag first (`persistent-root.md`). After the next boot no daemon and no
hooks run; root is then only available through the exploit.

## History

On 2026-10-20 the TV went unreachable after a test bind-mounted an empty stub
over `/usr/lib/libkUEPUser.so` and ran `systemctl daemon-reload`. A power cycle
recovered it; bind mounts don't survive reboot. **Don't bind-mount over system
libraries that boot-path services `dlopen`.**
