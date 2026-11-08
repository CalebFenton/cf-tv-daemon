# `ps_agent` authorization model (static)

`ps_agent` is the root helper that runs `mount`, `mount.smb.sh`, `insmod` and
similar for selected callers. Its policy is compiled into the binary. This page
is the policy; the Remote PC chain that uses it is
`docs/05-firmware/remotepc-dbus.md`.

Binary: `/usr/apps/privileged-service/bin/ps_agent`, 289,260 bytes, md5
`9216637a347b8555f1191cd970284d9a` (same in `livefs/` and on the TV, verified
2026-10-24). Running as pid 1170, `System::Privileged`. Analysis below is
static, from `livefs/` (2026-10-20), re-checked against the strings and
dynamic symbols on 2026-10-24.

## Verdict

- **The gate is the caller's identity (Smack label and/or exe path) plus a
  regex over the command string**, not the D-Bus member name.
- **Only `User::Pkg::org.tizen.remotepc` has the CIFS rules** that reach
  `mount.smb.sh`. Our root contexts (`System::Privileged`, `User`, `System`)
  do not.
- **No capability gain for us**: we are already root outside every zone. The
  value is as a disclosure (`docs/05-firmware/remotepc-dbus.md`).

## Calling convention

The client library `libprivileged-service-client.so` names one entry point,
`PS_CommunicationClient::CallCommand`, and passes the operation as data.
Strings in the library:

```
0x0119f0  CallCommand
0x011a24  PRIVSERV
0x011e60  /com/safe/ps_agent      object path
0x0120d0  com.safe.ps_agent       destination
0x012234  ps_systemctl  ps_rmmod  ps_mknod  ps_umount_cifs
          ps_umount  ps_mount_cifs  ps_mount  ps_insmod      (operation names)
```

Status strings: `COMM_OK`, `COMM_SERVICE_EXISTS`, `COMM_NO_SERVICE`,
`COMM_FAILED`, `RULE_NO_FOUND`, `WAITPID_FAILED`, `FORK_FAILED`, `FAIL`,
`FEATURE_ISNT_IMPLEMENTED`, `INVALID_REGEX`.

Calling `com.safe.ps_agent.ps_mount_cifs` as a D-Bus method returned
`UnknownMethod` (2026-10-20): the `ps_*` strings are arguments, not members.
`PS_DBusIAdaptor::Dispatch` (`0x1c09c`) looks the member up in a map. The exact
D-Bus member name is not established; `CallCommand` is the client's C++ method
name (unverified as the wire member). `/com/safe/ps_agent` introspects as an
empty node (verified 2026-10-24).

Operation → program (strings in `ps_agent`):

```
ps_mount        /bin/mount
ps_mount_cifs   /usr/apps/privileged-service/bin/mount.smb.sh
ps_umount       /bin/umount
ps_umount_cifs  /usr/apps/privileged-service/bin/umount.smb.sh
ps_mknod        /bin/mknod
ps_rmmod        /sbin/rmmod
ps_systemctl    /bin/systemctl
ps_insmod       /sbin/insmod
```

## The credential table

A flat string blob at `.rodata` `0x3c9d0`–`0x41200`: a Smack label, then exe
paths, then regex rules, repeated. Labels in layout order (offsets verified
2026-10-24); the exe/rule grouping is read from layout, not from code:

| offset | label | exe paths that follow (examples) |
|---|---|---|
| `0x3c9d0` | `User::Pkg::org.tizen.ep-hotel-hms` | `/opt/usr/apps/org.tizen.ep-hotel-hms/bin/` |
| `0x3cb48` | `System::Privileged` | nfc-daemon, `tztv-tagpro-daemon`, `tztv-tagpro-priv-cmd`, … |
| `0x3cf00` | `User::App::org.tizen.netflix-app` | `netflix-app` |
| `0x3d01c` | `User::Pkg::org.tizen.netflix-app` | |
| `0x3e604` | `User::App::org.tizen.ignition` | `ignitionLaunch` |
| `0x3e740` | `User::Pkg::org.tizen.ignition` | `contents-recognition-service`, … |
| `0x3ef74` | `System` | `vddebugmenu`, `waapp-swupgrade` |
| `0x3f0c0` | `User::Pkg::com.samsung.tv.cobalt` | `cobalt_launcher`, `vdsystem-update-manager`, … |
| `0x3fd94` | `User::Pkg::com.samsung.tv.aoc-enabler` | `aoc-enabler` |
| `0x3fedc` | `User::App::com.samsung.tv.remote-workspace` | |
| `0x3ff6c` | `User::Pkg::com.samsung.tv.remote-workspace` | |
| `0x3ffa0` | `User` | `chromium-efl-install`, `aifw_core_abm_updater` |
| `0x402f4` | `User::Pkg::org.tizen.remotepc` | `remotepc_uilauncher`; 58 rules |

`System` is the sdb shell's label (uid 901) and `User` is the channel agent's
label before it raises itself. Their rule sets (vdfs mounts of
`/dev/mmcblk0p18`/`p19`, chromium-efl squashfs loops, a CIFS mount with
`username=`/`password=` restricted to `[A-Za-z0-9_.:-]+`) contain nothing
that reaches a shell. Not tested live.

## The CIFS rules (all in the `remotepc` block)

```
^\-o\s+credentials\=\/tmp\/[A-Za-z0-9_.:-]+\,vers\=[A-Za-z0-9_.:-]+\s+\/\/192\.168\.0\.13\/\[\:SMBPATH\:\]\s+\/opt\/usr\/home\/owner\/content\/smb\/\[\:SMBPATH\:\]$
^\-o\s+ro\,nosuid\,noexec\,nodev\,credentials\=\/tmp\/[A-Za-z0-9_.:-]+\,vers\=2\.1\s+\/\/[A-Za-z0-9_.:-]+\/\[\:SMBPATH\:\]\s+\/opt\/usr\/home\/owner\/content\/smb\/\[\:SMBPATH\:\]$
^credentials\=\/tmp\/[A-Za-z0-9_.:-]+\s+\/opt\/usr\/home\/owner\/content\/smb\/\[\:SMBPATH\:\]$
```

- The regex constrains the credential **filename** (`/tmp/[A-Za-z0-9_.:-]+`);
  `mount.smb.sh` executes the file's **contents**. Check and use are on
  different objects. That is the bug.
- The first rule hardcodes server `192.168.0.13`, a developer LAN address, in
  production policy.
- The `remotepc` block also ends with `^[A-Za-z0-9_.:-]+$` (`0x41100`), which
  matches any single token. If rules are OR-ed, the `remotepc` label is very
  loose for any operation taking one token.

## The check

```
PS_CredentialManager::MatchAuth(string const&, vector<…>&, PS_AuthInfo const&)  0x12804
PS_AuthInfo::Match(bool&, PS_AuthInfo const&) const                             0x12630
PS_Rule::Match(bool&, string const&)                                            0x19f28
Auth::GetSmackLabel(int, string&)                                               0x17e64
Auth::GetPath(int, string&)                                                     0x181e8
```

`PS_AuthInfo` holds two strings (label, exe path; offsets `0x00`, `0x18`).
Rules are compiled with `boost::regex`. **Open:** whether label and exe must
both match or either one; the combining code is inlined and was not read. The
cheap live test is a deliberately invalid `CallCommand` from two labels,
reading back `RULE_NO_FOUND` vs `COMM_FAILED` vs `INVALID_REGEX`.

## `insmod` wildcards (dead end)

`System::Privileged` has three wildcard insmod rules:

```
^\/usr\/lib\/modules\/4\.1\.10\/kernel\/fs\/[A-Za-z0-9_.:-]+\/[A-Za-z0-9_.:-]+$
^\/usr\/lib\/modules\/4\.1\.10\/kernel\/net\/[A-Za-z0-9_.:-]+\/[A-Za-z0-9_.:-]+$
^\/usr\/lib\/modules\/4\.1\.10\/kernel\/[A-Za-z0-9_.:-]+$
```

`/usr/lib/modules` is on the read-only root (`3.10.30` and `linux` are
symlinks to `4.1.10`), so these only allow loading shipped modules. Not a path
to kernel code unless a writable module path appears.

## Why the label cannot be borrowed

`remotepc` runs inside an LXC container, not as a plain labelled process
(`livefs/usr/lib/systemd/system/org.tizen.remotepc.service`):

```
User=root
ExecStart=/usr/bin/zone-template-launcher -n rdpzone \
          -c /usr/apps/org.tizen.remotepc//bin/remotepc_manager -pi \
          -m rw:/opt/zone/template/securedata/rdpzone/opt/usr/apps/org.tizen.remotepc:… -e …
PIDFile=/tmp/rdpzone.pid
```

`zone-template-launcher` uses `lxc_container_new` and calls
`zone_verify_uep_sign` / `zone_verify_uep_level_form_pid` /
`zone_check_permission`, so zone templates are signed; we cannot author a zone
that inherits the label. Entering `rdpzone` is blocked
(`docs/04-system/lxc-zones.md`). The service is active and
`/tmp/rdpzone.pid` exists (verified 2026-10-24).

Trust boundary this exposes: `ps_agent` and `mount.smb.sh` run outside the
zone as root and execute content supplied by the zone (and, through Remote PC,
by a remote SMB setup). That is container-content → host-root injection.
