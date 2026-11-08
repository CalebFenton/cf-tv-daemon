# Remote PC → `ps_agent` → `mount.smb.sh`: root command injection

A shipped command-injection path to uid 0, independent of UEP. Proven on a
Q60T by `tiz0wn` (`docs/07-prior-art/tiz0wn.md`); present byte-for-byte on this
build; **not exercised on this TV**. It adds no capability for us (we are
already root); its value is as a disclosure. Policy details:
`docs/06-security/ps-agent-policy.md`.

## The chain

```
Remote PC UI (remotepc_uilauncher)  -- user enters SMB username/password
   │  D-Bus org.tizen.remotepc /org/tizen/remotepc/Handler
   │  org.tizen.remotepc.Interface.PutMessage(ay Message, i MessageSize)
   ▼
remotepc_manager   inside LXC zone rdpzone, label User::Pkg::org.tizen.remotepc
   │  credential file /tmp/<name> written, ps_agent called with ps_mount_cifs
   │  (which remotepc binary does each step: unverified)
   ▼
ps_agent           com.safe.ps_agent, uid 0, System::Privileged, outside the zone
   │  regex checks the option string (filename only)
   ▼
/usr/apps/privileged-service/bin/mount.smb.sh
   │  reads the credential file, splices its CONTENTS into a command
   ▼
/bin/bash -c "$UTIL $UTIL_OPT"      as root
```

## Verified on this TV / build

- **Interface** (verified 2026-10-24, `gdbus introspect`; service active):
  ```
  interface org.tizen.remotepc.Interface {
    methods:
      PutMessage(in  ay Message, in  i MessageSize,
                 out ay Response, out i ResponseSize);
  };
  ```
  D-Bus activation: `org.tizen.remotepc.dbus.service` → systemd
  `org.tizen.remotepc.service` (`zone-template-launcher -n rdpzone`).
- **Sink** (`livefs/usr/apps/privileged-service/bin/mount.smb.sh`, md5
  `ff07e88da38b80ed6fbe8f84923b0582`, identical on the TV and in 2210.0):
  ```bash
  function read_credentials() {
      local filename=$1
      join_by , $(sed "/$SMB_PATH_OPT/d" $filename)
  }
  FileContent=$(read_credentials $FileName)
  UTIL_OPT=${UTIL_OPT/credentials=${FileName}/$FileContent}
  [[ "$debug" == "false" ]] && /bin/bash -c "$UTIL $UTIL_OPT"
  ```
  No quoting: a credential value containing `$(...)` runs as root.
- **Policy**: the CIFS rules exist only for `User::Pkg::org.tizen.remotepc`;
  they constrain the credential *filename*, never its contents.
- **Vocabulary**: `SAMBA_MOUNT`, `SAMBA_UNMOUNT`, `USERNAME`, `LAUNCH_SCAN`,
  `LAUCH_RDP_CLIENT`, `NOTALLOWED`, `ss_init_credential` in `remotepc_manager`;
  `PutMessage`, `PASSWORD`, `SMBPATH` only in `remotepc_uilauncher` (the
  client that builds messages). Messages carry Tizen `bundle` parameters.

## Why calling it directly does not help us

- `ps_agent` gates on the caller's label/exe. Our contexts
  (`System::Privileged`, `User`, `System`) have no CIFS rule, so a direct call
  would fail with `RULE_NO_FOUND` (static reading; not tested live).
- `PutMessage` from our shell reaches `remotepc_manager`, which then calls
  `ps_agent` with its own zone label. Whether `remotepc_manager` accepts a
  `SAMBA_MOUNT` from a non-UI caller, and the message layout, are unknown.
- `tiz0wn`'s proof needed one real Shared Folder click in the Remote PC UI.

## Open

1. `SAMBA_MOUNT` wire layout: diff `remotepc_uilauncher` (builds) against
   `remotepc_manager` (parses).
2. Does `remotepc_manager` reject non-UI callers?
3. Any live test must write the marker payload with single quotes. An early
   "success" here was the local shell expanding `$(...)` while writing the
   test file; `ps_agent` never saw it.
