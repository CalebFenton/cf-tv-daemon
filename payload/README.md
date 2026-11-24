# Boot payload

Verified against the device 2026-10-24: md5 of each repo file == the deployed
file (`tv/root.py boot status`). Boot chain: `docs/01-root/persistent-root.md`.
Owners and modes on the TV: `docs/06-security/threat-model.md` §2.

| file | md5 | deployed at | role |
|---|---|---|---|
| `parseinfo.awk` | `74f4897c42dc9f7fb00153205e8f5f9f` | `/opt/etc/parseinfo.sh` (immutable) | run by `AT-HUB.service` at boot as root; vendor behaviour (`/proc/target_version`), `/opt/masklist`, `/opt/cmdq`, then the launcher |
| `cf_tv_daemon_start.awk` | `cb7c6afb4349f26c851236bf4f94f79b` | `/opt/cf_tv_daemon/` | launcher: stops, resets and `systemd-run`s the unit `cf_tv_daemon` (`Restart=on-failure`). Run by `parseinfo.sh` at boot and by `tv/root.py boot install` |
| `cf_tv_daemon.awk` | `a49bb68835323485fdd94d5214dddee9` | `/opt/cf_tv_daemon/` | unit `cf_tv_daemon`: root command queue in `/opt/cf_tv_daemon/state/`, and the `boot`/`wake` events (`docs/01-root/access-contexts.md` §3) |
| `cf_tv_hooks.sh` | `0808839fcb2a1bc078a96be4a36b8d69` | `/opt/cf_tv_daemon/` | hook runner: shell text the daemon `eval`s on each event ([Hooks](#boot-and-wake-hooks)) |
| `on-boot.d/50-btconnect` | `1a39b358eb2654b6ef0a18a5cec05a0f` | `/opt/cf_tv_daemon/on-boot.d/` | reconnects the Bluetooth speaker (`docs/04-system/bluetooth.md`) |
| `on-wake.d/50-btconnect` | same (git symlink to `../on-boot.d/50-btconnect`) | `/opt/cf_tv_daemon/on-wake.d/` (a regular file on the TV) | same hook on wake |
| `../apps/chattr.cs` → `build/chattr/Chattr.dll` | `bfcd98547fbcfd1c141347ced28e0503` | `/opt/cf_tv_daemon/chattr/` (with `Chattr.runtimeconfig.json`) | sets and clears the immutable flag on `parseinfo.sh` |
| `btconnect.addr` | written from `speaker` in the TV's `devices.toml` entry (`docs/04-system/devices.md`) | `/opt/cf_tv_daemon/btconnect.addr` | the speaker `50-btconnect` reconnects; removed from a TV without a `speaker` |
| `../apps/capture-probe.cs` → `build/capture/CaptureProbe.dll` | built locally | `/opt/cap/` (with `CaptureProbe.runtimeconfig.json`) | screen capture for `bin/snap`, `bin/stream` |
| `masklist` | empty | `/opt/masklist` | unit names masked by `parseinfo` **at every boot**: a mask persists until the line is removed (`docs/04-system/masking-daemons.md`) |

`tv/root.py boot install` deploys every file above except `masklist`
(each written as `.new`, chowned root, chmodded, renamed
into place), applies the modes, and restarts the daemon. It builds the C#
tools first if `build/` lacks them (`APP_BUILDS` in `tv/root.py`; recipe
`docs/03-video/app-build.md`).

**The TV holds nothing else of ours** (checked 2026-10-25): everything on it
comes from this table, plus runtime state the daemon recreates
(`/opt/cf_tv_daemon/state/`, the logs). `tv/root.py doctor` and `boot status`
compare the TV to the repo and list `EXTRA` hook files.

**Wiped TV / first install:** restore `vendor/`, `build/payloads-netcoreapp3.1`
and `.venv` per `VENDOR.md`, then

```sh
tv/root.py boot install --via exploit   # no daemon yet: root through the exploit; starts the first daemon
tv/root.py doctor                        # everything ok?
```

Everything is awk, or shell text that is `eval`'d, because executing a `.sh`
file is refused by UEP and gawk is not hooked.

Vendor originals: `/opt/cf_tv_daemon/parseinfo.ORIGINAL.sh` (md5
`2c4ccd56e3f89818e9bafd6bef809262`, written by the first install) and
`../archive/originals/`.

## Boot and wake hooks

On each `boot` and `wake` event (`docs/01-root/access-contexts.md` §3) the
daemon runs `( CF_EVENT=<event>; eval "$(cat /opt/cf_tv_daemon/cf_tv_hooks.sh)" ) &`.
The runner runs every regular file in `/opt/cf_tv_daemon/on-<event>.d/` in
parallel, each `eval`'d in its own subshell as root, `System::Privileged`.

Add a hook:

1. Write shell text to `payload/on-boot.d/<NN-name>`, `payload/on-wake.d/<NN-name>`,
   or both (one as a symlink to the other, like `50-btconnect`). No shebang or
   exec bit: the text is `eval`'d, never executed.
2. `tv/root.py boot install`, then `tv/root.py boot status` (exit 1 on any drift).
3. `tv/root.py hooks boot` or `tv/root.py hooks wake` runs the runner now,
   through root.

Rules a hook follows:

- **Log with `say TEXT`.** All hook output goes to `/opt/cf_tv_hooks.log`;
  `say` writes `YYYY-MM-DD HH:MM:SS <event>/<hook>: TEXT`. The runner adds an
  `exit N` line per hook. The log is cut to its last 512 KiB when it exceeds
  1 MiB (a normal boot logs ~1 KB); plain text, no compression.
- **One run at a time per file name; a newer event wins.** The runner takes
  `flock` on `/opt/cf_tv_daemon/state/locks/<hookname>` (root 0700). If a run from
  an earlier event still holds it, the runner kills that run's process group
  (pid in `<hookname>.pid`) and starts the hook again; the log shows
  `stopped: replaced by a newer event`, then `replaced a run from an earlier
  event`. Reason: the TV suspending mid-run froze a speaker scan, which then
  blocked the next two wakes (2026-11-02). So a hook must tolerate being
  killed at any point. A name present in both `on-boot.d` and `on-wake.d`
  shares one lock.
- **Long-lived processes go through `systemd-run`.** Anything else a hook
  starts is killed with it, and dies when `cf_tv_daemon` restarts.
- `exit` ends only that hook.

Switch a hook off without deleting it: `tv/root.py hooks disable NAME` moves
it to `/opt/cf_tv_daemon/disabled/on-<event>.d/` (both events); `hooks enable NAME`
moves it back, `hooks list` shows both. `boot install` updates a disabled hook
where it is and leaves it off; `boot status` marks it `(disabled)`. Before
2026-10-25 an install silently re-enabled hooks switched off by hand.

Remove a hook: delete it from `payload/` and, as root, from
`/opt/cf_tv_daemon/on-<event>.d/`. `boot install` does not delete hooks on the TV
that are not in the repo; `boot status` and `doctor` list them as `EXTRA`.

## Known problems

- `Chattr.dll` cannot run as uid 901: `dotnet` has `SMACK64EXEC=User`, and
  `User` lacks read on files the daemon wrote (`System::Privileged`; rule
  `User System::Privileged wx`). Use a root path.
