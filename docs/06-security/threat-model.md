# Threat model: what the current setup exposes

What our root setup adds to the TV's attack surface, what is locked, what we
can and cannot reach, and what stays open. Access paths themselves are
documented in `docs/01-root/access-contexts.md`; boot payload files in
`payload/README.md`.

## 1. Current access, in one table

| path | how | identity |
|---|---|---|
| daemon | sdb injection (uid 901) writes a request into `/opt/cf_tv_daemon/state/req/`; `cf_tv_daemon.awk` runs it | uid 0, `System::Privileged` |
| channel | `tv/chan.py serve`: the daemon starts `SamsungTvRootAgent.dll` as unit `cf_tv_agent`; the agent dials back to the host | uid 0, agent `User`, each command raised to `System::Privileged` |
| exploit | `tv/root.py exec --via exploit` (archive-root), recovery only, never chosen automatically | uid 0, `User` |

The daemon starts at boot from `AT-HUB.service` → `/opt/etc/parseinfo.sh`
(gawk). No exploit is needed while the boot payload is in place.

## 2. On the device: what runs as root, and who can change it

Owners and modes, verified 2026-10-24 (`tv/root.py boot status`: all ok).
`tv/root.py boot install` enforces them; the daemon re-applies the queue
directory modes at every start.

| path | owner, mode | effect of writing it |
|---|---|---|
| `/opt/etc/parseinfo.sh` | `root:root` 0755 + immutable flag | runs as root at next boot |
| `/opt/cf_tv_daemon/` and `chattr/`, `on-boot.d/`, `on-wake.d/`, `disabled/` | `root:root` 0755, all files 0644 (`boot install` enforces this on everything except `state/`) | launcher, daemon, hook runner and hooks run as root at boot and wake |
| `/opt/cf_tv_daemon/state/` | `root:root` 0755 (`boot_id`, `daemon.pid`, `heartbeat`, `.lock`; `locks/` root 0700), set by the daemon at start | |
| `/opt/cf_tv_daemon/state/{req,stage,out,rc}` | `sdk:sdk` 0700 | a request or staged command runs **now** as uid 0 `System::Privileged` |
| `/opt/cmdq`, `/opt/cmdout` | `sdk:sdk` 0600 | each `cmdq` line runs as root at next boot |
| `/opt/masklist` | `root:root` 0644 | unit names masked at boot (denial of service) |
| `/opt/cap/` | `root:root` 0755, `CaptureProbe.dll` 0755 | the channel runs the probe as root (`bin/snap`, `bin/stream`). It was **0777 until 2026-10-25**: any app could have swapped the probe for root code |

Smack does not protect these: 374 subject labels may write
`System::Privileged` objects, including `User`, `System` (the sdb shell,
uid 901), `_`, `^`, and 368 `User::Pkg::*` installed-app labels; 373 may write
`User` objects (`/sys/fs/smackfs/load2`, 2026-10-24). DAC is the barrier.

**Why `sdk`-owned 0700 works:** `tv/root.py` submits as uid 901 through the
sdb injection, so no new submission path was needed. The boot-queue recovery
path still works for uid 901.

**Who can still write the queue:** root, and non-root processes holding
`CAP_DAC_OVERRIDE`. Process census 2026-10-24: 353 processes, 128 of them uid
5001; all 51 app processes (labels `User::Pkg::*`, incl. our cftv agent in
`social-chat-app`) have `CapEff 0`. The non-root holders of
`CAP_DAC_OVERRIDE` are vendor daemons only: `npumgr_server`, `wifi-monitor`,
`tztv-tagpro-daemon` (uid 5001), `muse-server` (451), `data-provider-master`
and `amd` (301), `sdbd` (901).

### Attack test, 2026-10-24

As uid 5001 with `CapEff 0` (`capsh --gid=100 --groups=100 --uid=5001`; Smack
label left at `System::Privileged`, more permissive than a real app). All 10
attempts were blocked: write `req/`, write `stage/`, list `out/`, overwrite
`parseinfo.sh`, rename over `parseinfo.sh`, delete `parseinfo.sh`, add a boot
hook, replace `cf_tv_daemon.awk`, append to `/opt/cmdq`, append to
`/opt/masklist`. (`runuser` fails here: PAM "Failure setting user
credentials".)

### Vendor hole: `/opt/etc/parseinfo.sh`

Stock firmware ships `/opt/etc/parseinfo.sh` mode 0777 (the vendor original,
saved as `/opt/cf_tv_daemon/parseinfo.ORIGINAL.sh`, 805 bytes, was 0777;
`/opt/etc/parseinfo.sh.bak2`, 805 bytes, 0777, is also present). `/opt/etc` is
`drwxrwxrwx root:system_share`, Smack `System::Shared` transmute.
`AT-HUB.service` runs the file as root at every boot
(`ExecStart=/opt/etc/parseinfo.sh &`). So on a stock TV any app can replace it
and get root at the next boot. Verified 2026-10-24: uid 901 could `mv` a file
over a root-owned 0755 file in `/opt/etc` (`mv_rc=0`), so mode 0755 alone
does not protect it. Most of `/opt/etc` is 0777 (`resolv.conf`, `passwd`,
`shadow`, `dlog.conf`, `duid-gadget`, `get_top_process.sh`,
`ira_encrypt.sh`).

Fixes considered:

| fix | verdict |
|---|---|
| sticky bit on `/opt/etc` | rejected: likely breaks vendor writers (e.g. the timezone `localtime` symlink is root-owned and probably replaced by non-root services) |
| bind mount over the file | rejected: bypassable from another mount namespace |
| immutable flag (`FS_IMMUTABLE_FL`) | **chosen**: the kernel refuses unlink, rename-over and writes of an immutable inode regardless of directory permissions; only `CAP_LINUX_IMMUTABLE` (root) clears it |

Test on a scratch file 2026-10-24: uid 901 `mv` over → "Operation not
permitted", `rm` → "Operation not permitted", write → "Permission denied";
root's write also fails until the flag is cleared. Tool and update procedure:
`docs/01-root/persistent-root.md`.

### Residual risk

- Root processes and the vendor daemons holding `CAP_DAC_OVERRIDE` (above)
  can still write the queue and our files. They are already system-level.
- `sdbd` runs as uid 901, which owns the queue: sdb access is root (§3).
- `/opt/etc/parseinfo.sh.bak2` (0777) and the other 0777 files in `/opt/etc`
  are vendor state, unchanged. AT-HUB runs `parseinfo.sh`, not the `.bak2`.

## 3. On the LAN

- **Our setup adds two token-protected TCP listeners on the TV (since
  2026-11-06).** Before that it added none (verified 2026-10-24: every
  listening TCP socket belonged to a vendor process or `sdbd`).
  - `:7741`, the cftv agent (uid 5001, `User::Pkg::com.samsung.tv.social-chat-app`):
    whatever it can draw, play or say on the TV. The first line must be the
    32-byte random token in `cftv/token` (mode 0600, uid 5001), new at each
    agent start, compared in constant time. The commands cannot run code.
  - `:7742`, the capture probe while `bin/hud live` runs (started as root over
    the channel, label `User` after dotnet's exec relabel; exits 30 s after
    its last request): the TV picture. Its 16-byte token comes from
    `bin/hud` in the `CF_CAPTURE_TOKEN` environment variable, readable in
    `/proc/<pid>/environ` by root and the same uid only.
  Smack lets LAN packets (`netlabel 0.0.0.0/0 System`) reach the app label
  (`System User::Pkg::com.samsung.tv.social-chat-app rwxat`). Neither
  listener is encrypted: anyone who can sniff the Wi-Fi sees the token.
- **`sdbd` on `:26101` (all interfaces) is the way in.** Developer mode is on
  (`db/sdk/develop/mode = 1`) with `db/sdk/develop/ip` set to this
  host. The sdb appinstall injection gives uid 901 commands, and uid 901 can
  write the daemon queue, so sdb access means root. Whether `sdbd` refuses
  connections from other source IPs is unverified.
- **Host side:** `/tmp/tvchan.sock` is mode 0600 (`tv/chan.py` chmods it).
  Any process of the owning user gets root on the TV through it. The agent's
  callback listener on the host takes a fresh single-use secret per bring-up.

## 4. What root on this TV can do

| capability | status | evidence |
|---|---|---|
| uid 0, `CapEff 0000003fffffffff` | yes | `/proc/self/status` via channel, 2026-10-24 |
| Raise own label to `System::Privileged` | yes | `tv/chan.py` prefix; `CAP_MAC_ADMIN` |
| ptrace any process | yes, no Yama (`/proc/sys/kernel/yama` absent, 2026-10-24) | `apps/bt-trace.cs` |
| `/proc/<pid>/mem` read+write, incl. `System::Privileged` pid 449 | yes (2026-10-20) | `probes/dotnet/memprobe/` |
| Execute injected code in another process | yes (2026-10-20) | `probes/dotnet/injector/`, victim `probes/dotnet/injectvictim/` |
| Anonymous RWX native code under `dotnet` | yes | `docs/01-root/native-code.md` |

Smack's `User System::Privileged wx` rule (no `r`) blocks file reads of
`/proc/449/maps` etc. from `User`, but `/proc/<pid>/mem` is governed by
`ptrace_may_access` (uid 0 + `CAP_SYS_PTRACE`, no Yama), not that rule. Writes
through `/proc/<pid>/mem` ignore the target page's W^X. UEP checks files at
`execve`/`mmap` only, never live memory.

Injection notes, each of which silently produced zeros:

1. Use `pread64`/`pwrite64`. ARM EABI `pread` takes a 32-bit `off_t`, so
   libc at `0xb5f91000` truncates negative.
2. Locate libc functions by symbol offset, not by scanning for instruction
   patterns.
3. Detach long-lived victims with `systemd-run --unit=… --collect`;
   `nohup`/`setsid` inside an sdb command held the channel open.

## 5. What we still cannot reach

| context | status | evidence |
|---|---|---|
| Executing a new or modified ELF from writable storage | blocked by kernel UEP | `docs/06-security/uep-bypass.md` |
| Loading our own kernel module | blocked by the module verifier | `docs/04-system/capability-matrix.md` (kernel rows) |
| Writing `/` | `vdfs ro` (`/proc/mounts`, 2026-10-24) | |
| `/zone/config`, `/zone/securezone`, `/zone/template/securedata` | `Permission denied` as root `System::Privileged` (2026-10-24) | per-inode refusal in vdfs; `docs/04-system/lxc-zones.md` |
| Inside `rdpzone` | `/proc/<rdpzone pid>/zone` → `Operation not permitted` (2026-10-24) | `docs/04-system/lxc-zones.md` |

The static sink census (`docs/04-system/static-analysis.md`) models an
attacker without root. For us it matters only for contexts in this table.

## 6. Still open, and optional hardening

Open, not changed:

- `sdbd` on `:26101` = uid 901 = root, by design while developer mode is on
  (§3).
- Host socket `/tmp/tvchan.sock`, mode 0600: any process of the owning user
  gets root on the TV (§3).
- Root processes and vendor daemons with `CAP_DAC_OVERRIDE` can write the
  queue (§2).

Optional:

- Turn off developer mode when sdb is not needed. All paths above start with
  sdb, so this also cuts our own access until it is re-enabled on the TV.
