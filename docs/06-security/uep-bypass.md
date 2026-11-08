# UEP: what is enforced, and the userspace bypass

UEP is Samsung's file-signature enforcement. It has two independent layers.
This page is the single place for the UEP model; other docs link here.

## Status

- **The current root setup does not use the bypass on this page.** The boot
  chain is gawk scripts (gawk has no UEP check) and the channel runs a .NET
  assembly under the signed `/usr/bin/dotnet` (`docs/01-root/access-contexts.md`).
- The bind mount is **not installed** (verified 2026-10-24: no `libkUEPUser`
  line in `/proc/mounts`; `/usr/lib/libkUEPUser.so` is the real 42,940-byte
  file). The stub directory `/opt/uepstub/` was deleted 2026-10-24.
- Hazard: the bind mount was live when the TV went offline on 2026-10-20 and is
  the suspected cause (`docs/01-root/recovery.md`). Other callers that
  `dlopen` the library expect real functions. Do not bind it and then
  `daemon-reload` or restart services.

## The two layers

| Question | Enforcer | Bypassed by the stub? |
|---|---|---|
| May bash/node run this script? | `uep_verify` inside the interpreter, via `libkUEPUser.so` `verifyFile` | **yes** |
| May the kernel `execve` this ELF? | builtin kernel UEP (`SfdUepVerifyExecutableBinary`) | no |
| May this file be `mmap(PROT_EXEC)`'d? | builtin kernel UEP | no (`ld-linux.so.3 /opt/x` → `failed to map segment from shared object`) |

- The kernel layer is content-based and label-independent: a byte copy of a
  signed ELF (`/bin/true` → `/opt/trcopy`) gets `Operation not permitted`, also
  as `System::Privileged` (2026-10-20). Kernel symbols: `docs/05-firmware/firmware-format.md`.
- The userspace layer exists only in interpreters that call it. **gawk and
  sed do not**, so `#!/usr/bin/awk -f` scripts on `/opt` run unsigned. That is
  the basis of the boot payload (`docs/01-root/persistent-root.md`).
- Signed scripts end with a `##UEP<base64>:UEP` trailer (e.g. `...AAACAg==:UEP`).
  The signature covers content, not path: a signed script copied elsewhere
  still runs; one appended line breaks it.

### Which binaries carry the userspace check

Verified against `livefs/` 2026-10-24 (`grep -l libkUEPUser`):

| binary | `libkUEPUser` string | notes |
|---|---|---|
| `/usr/bin/bash` (`/usr/bin/sh` → bash) | yes | exports `uep_verify` at `0x1b304` |
| `/usr/bin/node` | yes | |
| `/usr/bin/smack-logger`, `/usr/bin/tzdaemon` | yes | |
| `/usr/bin/zone-template-launcher` | yes | builds `rdpzone` |
| `/usr/bin/awk` → gawk 3.1.5, `/usr/bin/sed` | **no** | `system()` / sed `e` give a shell |

`/usr/bin/sfpmd` holds the USB disable path instead (`SfKUEPSwitcher::FindUEPDiableFile`,
`DisableKUEP`, `PlaceDUIDToUSBStorage`): a `VDUEPOFF.cer` on a real USB
mass-storage volume. Not tested.

## The bug: `uep_verify` fails open

`/usr/bin/bash` does not link `libkUEPUser` (`readelf -d` shows no such
`NEEDED`); it `dlopen`s `/usr/lib/libkUEPUser.so` and `dlsym`s `verifyFile`
itself. Disassembly of `uep_verify` (`0x1b304`):

```c
int uep_verify(const char *path)
{
    if (s_fn)
        return s_fn(path, ...);
    if (!s_handle) {
        s_handle = dlopen("libkUEPUser.so", RTLD_LAZY);
        if (!s_handle)
            return 1;               /* FAIL OPEN */
    }
    s_fn = dlsym(s_handle, "verifyFile");
    if (dlerror()) {
        dlclose(s_handle);
        s_handle = NULL;
        return 1;                   /* FAIL OPEN */
    }
    return s_fn(path, ...);
}
```

`1` means allowed. bash also references `/usr/lib/key.pub`, which does not
exist on the device (verified 2026-10-24), yet the real library still rejects
unsigned scripts, so its key comes from elsewhere (unverified where). The
fail-open needs the library or its symbol to be missing, not the key.

## Trigger, proof, reversal (verified 2026-10-21)

```sh
: > /opt/uepstub/libkUEPUser.so                                   # zero-byte stub
mount --bind /opt/uepstub/libkUEPUser.so /usr/lib/libkUEPUser.so  # dlopen now fails
/opt/a.sh            # unsigned #!/bin/sh script -> runs (SHELL_WORKS)
umount /usr/lib/libkUEPUser.so
/opt/a.sh            # -> [uep][bash] the file is NOT signed!! : /opt/a.sh
```

Bound → allowed, unbound → denied, repeated twice. Nothing on `/` is
modified.

| With the stub bound | Result |
|---|---|
| `/bin/sh` script from `/opt` or `/tmp` | runs |
| `node /opt/b.js` | runs |
| ELF from `/opt` | still `EPERM` |
| `dotnet` + `dlopen` of `/opt/*.so` | still fails |

Bind mounts are per mount namespace. The daemon and the channel agent run in
the initial namespace (`mnt:[4026531840]`, same as pid 1; verified 2026-10-24),
so a bind made from either is system-wide until reboot. The 2026-10-21
`/opt/cmdq` boot-time install of this bind is not deployed.

## Open

- Whether the stub changes anything for `zone-template-launcher` /
  `rdpzone` (untested; see `docs/04-system/lxc-zones.md`).
