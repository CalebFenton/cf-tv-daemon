# Zones — `appzone`, `rdpzone`, the LXC launcher, and `zone-manager`

Samsung runs some apps in LXC containers ("zones") that share the host rootfs
and differ by namespace and Smack label. Root (`uid 0`, `System::Privileged`,
full caps) cannot enter them, read their `/proc` entries, or read the
`/zone` secure directories. No working way in is known.

## What runs where (verified 2026-10-24)

| Zone | Started by | Contents |
|---|---|---|
| `appzone` | `zone-manager` (`zone-init` pid 999, label `System::Privileged`) | store apps: Netflix (`GIBBON_MAIN`, `User::Pkg::org.tizen.netflix-app`), YouTube (`cobalt_launcher`, `User::Pkg::com.samsung.tv.cobalt`), Prime Video (`primevideo`) |
| `rdpzone` | `org.tizen.remotepc.service` ("FreeRDP client", `Type=forking`) | `/usr/apps/org.tizen.remotepc/bin/remotepc_manager` |

`org.tizen.remotepc.service`:

```
ExecStart=/usr/bin/zone-template-launcher -n rdpzone \
  -c /usr/apps/org.tizen.remotepc//bin/remotepc_manager -pi \
  -m rw:/opt/zone/template/securedata/rdpzone/opt/usr/apps/org.tizen.remotepc:/opt/usr/apps/org.tizen.remotepc \
  -e ELM_PROFILE=tv -e ELM_DISPLAY=wl … (EFL/Ecore env)
```

Its main pid (1871 this boot): `/proc/1871/comm`, `zone`, `attr/current` all
return `Operation not permitted` to root `System::Privileged`.

Units: `zone-manager.service` (active, `UnitFileState=disabled`, pulled in as a
dependency), `zone-security-manager.service` (active).

## `/proc/<pid>/zone`

Two integers. Counts across all pids on 2026-10-24: `0 0` kernel threads,
`50 x` most host processes (our shell reads `50 0`), `55 0` systemd (pid 1 and
the user instance), `57 0` a kworker, `51 0` `tts-engine-vd-s`, `51 1` every
`appzone` process. Meaning of the numbers is not established. A
`<id> <n>:<name>` form was assumed earlier from the launcher's `':'` test but
was never observed.

## Filesystem

`/zone` is on the rootfs (`/dev/root`); `/opt/zone` is on `/opt` (both vdfs).

| Path | As root, `System::Privileged` (2026-10-24) |
|---|---|
| `/zone`, `/zone/appzone`, `/zone/bin`, `/zone/template`, `/zone/template/templatezone` | readable |
| `/zone/config`, `/zone/securezone`, `/zone/template/securedata` | `stat: Permission denied` |
| `/opt/zone`, `/opt/zone/appzone`, `/opt/zone/template` | readable |
| `/opt/zone/securezone`, `/opt/zone/template/securedata` | `Permission denied` (`ls` shows `d?????????`) |

The denial is not Smack labels, DAC, path, namespace, or the UEP userspace
library (tested 2026-10-21: no `security.SMACK64` on the dirs, relabel to `_`
and `System::Privileged`, `CAP_DAC_OVERRIDE`, `mount --bind` of the dir (the
bind succeeds, access still denied), `/proc/1/root/zone/config`, UEP stub
bound over `libkUEPUser.so`). It follows the inode, on the read-only rootfs
and on writable `/opt` alike, so it is enforced per-inode in the vdfs driver
(or a security module keyed on the inode; unverified which).

`getfattr` and `python3` are not installed.

## `zone-template-launcher`

`/usr/bin/zone-template-launcher`: 33,210 B, `root:root 0750`, signed rootfs
binary; uses `liblxc` (`lxc_container_new`). Options string
`n:c:m:f:t:e:u:g:pi` (`-n` name, `-c` command, `-m` mount `rw:<src>:<dst>`,
`-e` env, `-p`/`-i` pidfile/init flags, others unknown). Strings include
`/zone/template/templatezone/config_templatezone`,
`Insufficent privileges to control templatezone`, `/proc/%d/zone`,
`/usr/lib/libkUEPUser.so`, `verifyFile`, `UserCaller` (verified 2026-10-24).

Permission gate (disassembled 2026-10-21 at `0x1840`–`0x18f0`, not re-checked):
reads `/proc/<ppid>/zone` and tests a byte for `':'`; then
`dlopen("/usr/lib/libkUEPUser.so")` and calls
`verifyFile(realpath(path), "UserCaller", …)`. If the `dlopen` fails it
returns without checking (fail-open, same pattern as bash's `uep_verify`).

`/zone/template/templatezone/config_templatezone` (865 B, md5
`db48143359c94603fdef68a6a261e9dc`):

```
lxc.group = tizen
lxc.rootfs.path = /
lxc.console.path = none
lxc.mount.entry = /tmp tmp none rw,bind 0 0
lxc.mount.entry = /dev dev none rw,bind 0 0
lxc.mount.entry = /dev/pts dev/pts none rw,bind 0 0
lxc.mount.entry = /dev/shm dev/shm none rw,bind 0 0
lxc.mount.entry = /dev/mqueue dev/mqueue none rw,bind 0 0
lxc.mount.entry = /sys sys none rw,bind 0 0
lxc.mount.entry = /sys/kernel/security sys/kernel/security none rw,bind,optional 0 0
lxc.mount.entry = /sys/fs/kdbus sys/fs/kdbus none rw,bind 0 0
lxc.mount.entry = /sys/fs/smackfs sys/fs/smackfs none rw,bind 0 0
lxc.mount.entry = /sys/fs/cgroup sys/fs/cgroup none rw,bind 0 0
lxc.mount.entry = /run run none rw,bind 0 0
lxc.mount.entry = /run/user/5001 run/user/5001 none rw,bind 0 0
lxc.mount.entry = /mnt/drm mnt/drm none rw,bind 0 0
```

(comment lines omitted). `lxc.rootfs.path = /`: the container sees the host
rootfs.

## `zone-manager` D-Bus API

`org.tizen.zoneframework`, object `/org/tizen/zoneframework`, interface
`org.tizen.zoneframework.interface` (`gdbus introspect`, 2026-10-24):

`GetVersion(s)`, `GetZoneList() → as`, `ZoneGetState(s)`, `ZoneGetPid(s)`,
`ZoneGetInitPid(s)`, `ZoneStart(s)`, `ZoneStop(s)`, `ZoneFreeze(s)`,
`ZoneUnfreeze(s)`, `ZoneLock(s)`, `ZoneUnlock(s)`,
`ZoneAttach(s zoneName, as cmdline, as environment, i uid, i gid) → b`,
`ZoneAttachWait(s, as, …)`, signal `ZoneStateChangedSignal`.

The binary (`/usr/bin/zone-manager`, 187,986 B) has handlers for all of them,
including `ZoneManagerDBusInterface::attachZone` and `attachWaitZone`, and
per-type checks `{Hybrid,Isolate,Protect}Zone::check{Zone,ROZone,RWZone}SmackLabel`
/ `checkAutoStart`, with messages `Process Permission is not matched`,
`Process Permission is allowed`, `There is no access permission to protection
zone`. The refusal is a Smack-label comparison on the caller.

Measured 2026-10-21 (not repeated):

| Call | Result |
|---|---|
| `ZoneGetState appzone` | `int32 3` (running) |
| `ZoneGetState rdpzone` / `templatezone` / `testzone` | `-1` (on 2026-10-21; rdpzone was running on 2026-10-24 as pid 1871, so this was either the state that day or zone-manager does not track template-launched zones; unverified) |
| `ZoneGetInitPid appzone` | the `zone-init` pid |
| `ZoneStart <name>` | `false`, daemon healthy |
| `ZoneAttach appzone, ["/tmp/az.sh"], [], 0/0 or 5001/5001` (caller `User` or `System::Privileged`) | `false`, or `NoReply` and the daemon wedges |
| `GetZoneList` | daemon **SIGABRTs** |

### Hazards — do not call

- **`GetZoneList`** kills `zone-manager` (`code=killed, signal=ABRT`); it does
  not restart on its own. Recovery:
  `systemctl reset-failed zone-manager.service; systemctl start zone-manager.service`.
- **`ZoneAttach`** can leave `zone-manager` stuck in `deactivating`; recovery
  needed `kill -9 <pid>`, `reset-failed`, `stop`, `start`. Cause unknown; a
  handler does exist (an earlier note claiming it was missing was wrong).

Both incidents left `org.tizen.remotepc` (rdpzone) and the picture unaffected.

## Open questions

- `appzone`'s required caller Smack label (set by `setContainerSmackLabel`);
  a static-analysis task on `zone-manager`.
- What the vdfs inode rule keys on, and whether a process inside a zone can
  read `/zone/config` etc.
- Whether `ZoneStart` with the right label is the usable entry point.
