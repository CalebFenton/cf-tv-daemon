# Persistent root

Root at every boot, uid 0, label `System::Privileged`, with the vendor's own
behaviour preserved. First proven 2026-10-20 (five cold boots). Boot chain
re-verified on real power cycles 2026-10-23/24.

## Why it works

UEP checks **the interpreter**, never the script. gawk (`/usr/bin/awk`, 3.1.5)
was never given the UEP hook, so any `#!/usr/bin/awk -f` file runs, including
the one `AT-HUB.service` runs as root on every boot.

| interpreter | UEP-hooked | |
|---|---|---|
| `/usr/bin/awk` (gawk 3.1.5), `/usr/bin/sed` | no (`grep -c libkUEPUser` = 0) | run any script |
| `/bin/bash`, `/bin/sh`, `/usr/bin/node` | yes | `[uep][bash] the file is NOT signed!!` |

bash does its own check in userspace (`libkUEPUser.so` `verifyFile`) and fails
**open** only if that library or symbol is missing. `/usr/lib/key.pub`, which
bash also references, doesn't exist on the device, but that does not make bash
fail open: the real library still rejects unsigned scripts
(`docs/06-security/uep-bypass.md`). ELFs are checked separately by the kernel
(`native-code.md`).

## Boot chain

```
AT-HUB.service  (Type=simple, Before=starter.target, root)
  ExecStart=/opt/etc/parseinfo.sh &          <- our awk payload (payload/parseinfo.awk), immutable
    1. vendor behaviour: writes /proc/target_version, byte-identical
       (__TIZEN-TRUNK2021-NikeM2-RELEASE_20251112.1__T-NKM2AKUC)
    2. masks units listed in /opt/masklist (docs/04-system/masking-daemons.md)
    3. runs each line of /opt/cmdq detached, output appended to /opt/cmdout
    4. /usr/bin/awk -f /opt/cf_tv_daemon/cf_tv_daemon_start.awk
         systemd-run --unit=cf_tv_daemon -> /opt/cf_tv_daemon/cf_tv_daemon.awk
           command queue                        (access-contexts.md §3)
           event boot, once per boot -> /opt/cf_tv_daemon/cf_tv_hooks.sh -> /opt/cf_tv_daemon/on-boot.d/*
           event wake, on every wake -> /opt/cf_tv_daemon/cf_tv_hooks.sh -> /opt/cf_tv_daemon/on-wake.d/*
```

The repo copies in `payload/` are the source of truth; `payload/README.md`
lists the deployed paths and md5s and how to write hooks. Measured on a real
power cycle 2026-10-24: AT-HUB at ~9–10 s after boot, `cf_tv_daemon` at ~9.4 s.

Rules the payload follows, each learned the hard way:
- **Never block boot.** AT-HUB is ordered before `starter.target`, so queued
  commands are started detached, and lines starting with `sleep` are skipped.
  A blocking `sleep 60` once held the unit `active (running)`.
- **Long-running work goes through `systemd-run`,** never `&` or `setsid`:
  systemd kills AT-HUB's whole cgroup when the unit ends.
- **Use `print`, not `printf "...\n"`,** for `/proc/target_version`: inside a
  quoted heredoc, a literal `\n` stays as backslash-n.

## Using the boot queue

The daemon is the everyday path (`access-contexts.md`). The boot queue is
for one-shot "at next boot" commands:

```sh
printf 'id\n' >> /opt/cmdq       # uid 901 or root: the file is sdk:sdk 0600
# runs at the next boot (or root: systemctl restart AT-HUB.service)
cat /opt/cmdout
```

`systemctl start AT-HUB` is a no-op once it has run this boot; `restart` is
needed. Started from a `User` shell, it inherits that shell's label.

## parseinfo.sh is immutable

`/opt/etc` is `drwxrwxrwx`, so a file mode alone does not protect
`/opt/etc/parseinfo.sh`: any process can rename a file over it and run as root
at the next boot (`docs/06-security/threat-model.md` §2). The file is root
0755 and carries the immutable flag (`FS_IMMUTABLE_FL`): the kernel refuses
unlink, rename-over and writes regardless of directory permissions, and only
`CAP_LINUX_IMMUTABLE` (root) can clear it. VDFS supports the flag (verified
2026-10-24).

The TV has no `chattr`/`lsattr` (toybox has neither). `apps/chattr.cs`,
deployed as `/opt/cf_tv_daemon/chattr/Chattr.dll`, does it (root only):

```sh
/usr/bin/dotnet /opt/cf_tv_daemon/chattr/Chattr.dll get /opt/etc/parseinfo.sh   # "0x<flags> i <path>"; "-" = not immutable
/usr/bin/dotnet /opt/cf_tv_daemon/chattr/Chattr.dll -i  /opt/etc/parseinfo.sh   # clear
/usr/bin/dotnet /opt/cf_tv_daemon/chattr/Chattr.dll +i  /opt/etc/parseinfo.sh   # set
```

## Update, restore, firmware updates

- **Update the payload:** edit `payload/`, then `tv/root.py boot install` and
  `tv/root.py boot status` (`access-contexts.md` §3). `install` clears the
  flag, renames the new `parseinfo.sh` into place and sets the flag again.
- **Restore the vendor script:** `tv/root.py boot restore`. Through root it
  clears the flag, copies `/opt/cf_tv_daemon/parseinfo.ORIGINAL.sh` (md5
  `2c4ccd56e3f89818e9bafd6bef809262`, verified 2026-10-24) to
  `/opt/etc/parseinfo.sh` and sets mode 0755. uid 901 cannot do this. The
  running daemon stays up until the next boot; after that root is only
  available through the exploit: `tv/root.py boot install --via exploit`.
- **Before a firmware update:** run `tv/root.py boot restore`, so an update
  that rewrites `/opt/etc/parseinfo.sh` is not blocked by the flag.
  [INFERENCE] Whether an update touches that file is unknown.
