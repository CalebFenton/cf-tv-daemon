# Techniques — reusable methods

Methods that produced results on this TV, so they need not be rediscovered.
Facts about the device live in the linked docs; this page is about *how*.

## Running commands and moving files

Use the tooling; see `docs/01-root/access-contexts.md` for paths and timings.

```sh
.venv/bin/python tv/chan.py run 'id' 'uname -a'   # channel: ~10 ms/command, System::Privileged
.venv/bin/python tv/root.py exec 'id'            # picks the cheapest path (daemon first)
```

Channel limits (`tv/chan.py`, vendored agent): 30 s per command, 64 KB per
command string, 1 MiB output. Run longer jobs in the background and poll
(example below). `tv/root.py exec` takes any string: it stages commands as
base64 and `eval`s them, so quoting, newlines, and length do not matter.

Scripts: a `#!/bin/sh` or `#!/bin/bash` file on a writable path is refused by
UEP; `#!/usr/bin/awk -f` runs (`docs/06-security/uep-bypass.md`). Inline the
commands, `eval "$(cat file)"` the text (the boot/wake hooks run this way,
`payload/README.md`), or write awk.

Files:

| need | method |
|---|---|
| upload ≤ 4 MiB | `tv/chan.py put FILE…` writes into the agent's staging dir; `cp` it into place |
| download ≤ 4 MiB | `tv/chan.py pull PATH… --outdir DIR`; the agent only reads under its staging/runtime dirs, so `cp` the file into the staging dir first |
| world-readable file of any size | `vendor/sdb/data/tools/sdb pull /path local` |
| bulk (whole trees) | `/dev/tcp` stream to `tools/receiver.py`, below |

Bulk pull. The TV has no `nc`, `socat`, `busybox`, or `timeout`; bash
`/dev/tcp` and `base64` work.

```sh
python3 tools/receiver.py 19999 /tmp/out.tar        # host; ignores 127.x health probes
tar cf - /usr 2>/dev/null | cat > /dev/tcp/<host IP>/19999   # TV, as root
```

- Commands are time-limited (channel 30 s; `tv/root.py exec --timeout N`):
  split by directory or background the `tar`.
- `/bin`, `/sbin`, `/lib` are symlinks into `/usr`; pull `/usr`.
- Extracted trees keep TV modes (`dr-xr-xr-x`); `chmod -R u+rwX` before
  `rm -rf` on the host.
- `livefs/` (`etc/` + `usr/`, 50,347 files) is a rootfs already pulled from
  the TV; check it before pulling again.

## Find out how a vendor app does something, then do it yourself (2026-10-23)

**Watch the bus while a person does the thing in the TV's own UI, then replay
the exact message as root.** This solved the Bluetooth reconnect after ten
failed guesses. Worked example: `docs/04-system/bluetooth.md`.

### 1. Watch: `busctl monitor`, not `dbus-monitor`

The system bus is **kdbus** (`/sys/fs/kdbus/0-system`, `.busname` units).
`dbus-monitor` prints `unable to enable new-style monitoring … Falling back to
eavesdropping` and then delivers **signals only**; every method call is
missing. `busctl` (systemd 244) works as root:

```sh
( busctl --system monitor > /opt/cap/bus.txt 2>&1 & p=$!; sleep 600; kill $p ) >/dev/null 2>&1 &
```

**Size: 1–15 MB per minute.** A busy 10 minutes (input switches, many apps)
produced 154 MB and filled `/opt` ("No space left on device"), which broke the
channel's file writes. Keep captures short, check `df -h /opt` first, and pull
with `sdb pull /opt/cap/x.txt` directly (the files are world-readable);
copying them elsewhere on the TV first doubles the space used. Delete them from
the TV once pulled; host copies go in `.local/captures/` (git-ignored).

kdbus attaches the **sender's credentials to every message**: `PID`, `Comm`,
`CommandLine`, `UID`, SMACK `Label`, and caps. You see *which app* sent each
call. The Bluetooth connect came from the Sound Output list app
(`btaudiolist`), not the daemon everyone suspected.

Parse on the host in Python: split on `\n(?=‣ Type=)`, read the header
`key=value` pairs, and match `method_return` to calls by
`Destination` + `ReplyCookie` == the call's `Sender` + `Cookie`. Match
arguments (`busctl monitor org.foo`) do not filter on kdbus; capture
everything.

### 2. Replay: `busctl call`; read replies with `gdbus call`

```sh
busctl --system call <dest> <path> <iface> <method> <signature> <args…>
# "ay" args: length, then each byte in decimal
```

`busctl` cannot print replies containing a variant: it says `Failed to parse
bus message: Bad message` and exits 1 even though the call succeeded (leading
fields such as `iv 0` still print). `gdbus` takes GVariant text and prints
everything:

```sh
gdbus call --system --dest D --object-path P --method IFACE.METHOD 1 4180 0 '[byte 0x20,0x64]' '@ay []'
```

Replay as root through `tv/chan.py run` or `tv/root.py exec`
(`System::Privileged`). Some services answer uid 901 with a bare error code
(`bt-service` returned `-9`).

### 3. Don't hammer a vendor service

A request that never completes, retried, can wedge the service for every
client. Thirty-second retries of the Bluetooth connect left the TV's own menu
stuck on "Connecting…" until a power cycle. Send once, check, stop.

## Breakpoint-trace a running process (2026-10-23)

No `gdb`, `strace`, `ltrace` or `perf` on the device, but ptrace is unrestricted
for root (no Yama). `apps/bt-trace.cs` (assembly `BtTrace`) is a small tracer
that runs under the signed `dotnet`:

```sh
dotnet /opt/cap/bt-trace/BtTrace.dll PID SECONDS LIB:FILEOFF[:LABEL] ...
```

- **FILEOFF** = `st_value` − the first `PT_LOAD` vaddr of the library (the libs
  are prelinked, e.g. `0x46320000`). The tracer finds the `/proc/PID/maps`
  line that covers that file offset, so no load-bias math.
- **Breakpoint:** Thumb `0xDE01`, the kernel's `thumb_break_hook`; gives
  SIGTRAP. `0xDE00` gives **SIGILL** and can kill the target.
- **No single-step on ARM Linux.** Step over a hit by restoring the original
  instruction in place and planting a temporary breakpoint after it. Executing
  in place keeps PC-relative first instructions (`ldr.w r2,[pc,#…]`) correct.
- `PTRACE_SEIZE` + `TRACECLONE` on every thread. All ptrace calls must come
  from the seizing thread, so SIGTERM cleanup is routed to the main loop.
- Verified: attached to 52 processes; SIGTERM cleanup restored code in all of
  them; targets stayed alive with `TracerPid: 0`.

Write such tools in C# with P/Invoke, not as native ELFs: `elfload.dll` calls
an ELF's entry as a plain function with no argv/auxv/TLS, so a static-glibc
program segfaults (`docs/01-root/native-code.md`).

**Trace the right process.** The tracer caught `bt.connectionmanager` calling
`bt_audio_connect(addr, 1)`, but that was a follow-up HFP attempt; the real
connect came from another app. Run the bus monitor first to find who acts,
then trace that process.

## Read a vendor daemon's own log: dlog (2026-10-24)

When the bus shows *that* something fails but not *why*, the daemon's own log
usually says. Samsung ships platform logging off: `plog=0` in
`/opt/etc/dlog.conf`, so `dlogutil` reads empty buffers. libdlog reads
`/run/dlog.conf` first (tmpfs, gone at reboot), then `/opt/etc/dlog.conf`.

- **Boot-time behaviour, the normal way:**
  `tv/root.py dlog arm [--filter REGEX] [--seconds N]`, then
  `tv/root.py reboot --report`, then `tv/root.py dlog fetch` (→
  `.local/dlog/`, deleted on the TV). `arm` backs up `dlog.conf`, sets
  `plog=1` and installs the one-shot hook `00-dlog-once`. At boot that hook
  deletes itself, keeps the matching lines for N s (default 300; filter
  default: the Bluetooth stack) in `/opt/cap/dlog-boot.txt`, then restores
  `plog=0`. `tv/root.py report` shows `plog` and warns while it is on;
  `tv/root.py dlog disarm` undoes an `arm` that never booted. Verified end to
  end 2026-10-25.
- Running process: write `/run/dlog.conf` (a copy with `plog=1`) and restart
  the process. Nothing persists.
- Read by hand: `dlogutil -b main -b system -b radio -v kerneltime -d` (`-d`
  dumps and exits). The time column is uptime. Buffers are RAM rings (2 MB).

This found the Bluetooth power-loss race, which no bus capture could show
(`docs/04-system/bluetooth.md`, Power loss).

## Reproduce a power loss without touching the TV (2026-10-24)

`tv/root.py reboot --report`: reboots through root (a transient unit runs
`sleep 2; reboot`, so the request returns first), waits until sdb and the
daemon answer, and prints the boot timeline. For Bluetooth this reproduced the
unplugged state exactly (the speaker's own reconnect, the authorization race).
The TV comes back on by itself; the daemon and boot hooks run as on a cold
start.

## Working safely on the TV

- `/` is `vdfs ro` and 100% full; `/opt` (3.9 GB) is the only writable
  persistent mount, 87% full after the 2026-10-24 cleanup. Check `df -h /opt`
  before writing anything large.
- Back up before overwriting (`cp x x.orig`), record `md5sum`, verify restores
  by md5 and header (`head -c 4 | od -An -c` shows `ELF`).
- Prefer adding a file over modifying one.
- Re-check `systemctl is-active` on any unit you disturbed.

## Checking a privilege or UEP claim

- **Does a launcher grant privilege?** `getcap` settles it (verified
  2026-10-24):
  ```
  /usr/bin/dotnet            = cap_setgid,cap_sys_admin+ei   (no cap_setuid)
  /usr/sbin/sdbd-tarlauncher = cap_setgid,cap_setuid+eip
  ```
  No `cap_setuid` and no setuid bit means the interpreter elevates no one: a
  "root payload" is root only because its caller was.
- **Is an interpreter UEP-checked?** `grep -c libkUEPUser /usr/bin/<interp>`;
  test with a same-directory, same-mode pair (`#!/usr/bin/awk -f` vs
  `#!/bin/bash`). Check the interpreter, not the script.
  Model: `docs/06-security/uep-bypass.md`.
- **Is a file signed?** Look for the `##UEP…:UEP` trailer at EOF.
- **Run a positive control** before trusting a negative result. A BL-caller
  scanner reported 0 callers for a function visibly running in dmesg.
- **Look for a simpler explanation** when a bypass "works": a script with no
  `#!` line runs via the shell's fallback, not `execve`. That produced one
  false "native exec works" conclusion.

## Enumerating what runs as root at boot

For each **enabled** unit, resolve `ExecStart` → is the path writable, what is
its file type, what is `User=`? Then cross-reference `.path` units: a
`PathModified=` on a writable file is an auto-reload primitive. That found
`samsunghealth-service-tv.path`, ruled out by `User=owner`. Result of the full
audit (2026-10-20): `AT-HUB.service` was the only unit that executes from `/opt`
as root at boot; every other `/opt` boot exec runs as `User=owner`, and the
units with file capabilities never exec from writable paths.

`systemctl is-enabled` is unreliable here: `starter.target` reports
`UnitFileState=disabled` while its `.wants/` symlinks exist and it is active
(verified 2026-10-24).

## Building code for the TV

- Device runtime: .NET Core **3.1.3**. Project settings as in
  `apps/cftv.csproj`: `<TargetFramework>netcoreapp3.1</TargetFramework>`,
  `UseAppHost=false`, `EnableDefaultCompileItems=false`.
- Host SDK: `.dotnet8/dotnet` (8.0.426), with `DOTNET_CLI_HOME=/tmp/dnethome8`
  (see `tv/cftv.py`, `docs/03-video/app-build.md`).
- `-p:TargetFramework=…` on a project restored for another TFM fails with
  `NETSDK1005`; use a separate project/obj dir. Several `.cs` with `Main` in
  one project → `CS0017`; isolate or set `<StartupObject>`.
- P/Invoke `ioctl`: marshal the request as **`uint`**, not `ulong`. With
  `ulong` the ARM EABI splits it across two registers and every call returns
  `EINVAL`, which looks like a driver type-check rejection.
- Native ARM: `gcc-arm-linux-gnueabihf` and binutils are in the `tvhack`
  Docker image (`docker/Dockerfile`; run with `docker/run.sh <cmd>`, repo
  mounted at `/work`, `livefs` as `/sysroot`). A new ELF cannot be `execve`d on
  the TV; it runs only through `elfload.dll`.

## Static reverse engineering (reference, 2026-10-20)

### Firmware containers

- Check `theubusu/unixtract`'s `src/keys.ukf` first (`tools/unixtract/`). It
  has the `NikeM2` key that `bugficks/msddecrypt` and the Synacktiv table
  lack. `unixtract -o msd:print_ouith` parses the OUIT header and extracts
  every partition. Procedure: `docs/05-firmware/firmware-format.md`.
- Key derivation depends on key length (`tools/unixtract/src/formats/msd/mod.rs`):
  16-byte keys → AES-128-CBC, IV = `md5(salt)`; 32-byte keys (NikeM2) →
  AES-256-CBC, IV = first 16 bytes of `sha256(salt)`. The older
  `md5(passphrase)` scheme in Synacktiv's
  `tools/samsung-q60t-exploit/firmware/decrypt.py` does not apply to T-NKM2.
- **A wrong key can look partly right**: CBC block 1 onward decrypts the same
  whatever the IV, so partial output is not evidence. Require the OUIT string
  `Tizen Software Upgrade Tree Binary Format`.
- VDFS4 compressed-file layout and the `vdfs-unpack` fix:
  `docs/05-firmware/firmware-format.md`.

### Disassembly

Binary Ninja / `bintools` is unreliable on this firmware:

- Base-0 shared objects (`LOAD Offset 0x0 VirtAddr 0x0 Align 0x10000`) are
  reported at **+0x10000** (`libtransport.so` `SfDeserializePacket` real
  `0x245c`, reported `0x1245c`). Prelinked `.so` addresses are correct;
  `.ko` addresses are meaningless. Convert with
  `tools/addrfix.py <binary> <addr>...` (tries deltas 0/0x10000/0x20000 and
  keeps the one inside `.text`).
- It decodes **Thumb as ARM**: `cdp2`/`mrc2`/`ldc2l` coprocessor garbage is
  the signature. No base or architecture override exists.
- A reported address ≥ file size means it merged another object; discard the
  analysis and use `readelf --dyn-syms` plus capstone at the symbol address.

Use `tools/armdis.py <binary> <start_hex> <length_hex>` (capstone, correct
mode, PLT resolved, literals annotated; needs `llvm-readelf`). Host `objdump`
cannot disassemble ARM. Cross-check in the `tvhack` image:

```sh
docker/run.sh arm-linux-gnueabihf-objdump -d --start-address=0x15d0 \
  --stop-address=0x1620 livefs/usr/lib/libtransport.so
```

Traps:

1. **ARM PLT stub base comes from the FIRST `add`** (`add ip, pc, #0, #12`;
   base = that instruction's PC + 8), not the second. Using the second shifts
   every resolved symbol by one reloc slot (a call "to"
   `SfSerializePacketEnvironment` was really `nla_parse_nested`).
2. **Never name a helper `dis.py`.** It shadows stdlib `dis`, which capstone
   imports: `ImportError: cannot import name 'Cs' from partially initialized
   module 'capstone'`.
3. **Literal-pool reads of relocated pointers are garbage.** `ldr rX,[pc,#imm]`
   slots hold unrelocated values (no `R_ARM_RELATIVE` in the file), e.g.
   `0x1b4edf83`. Resolve calls through the PLT instead.

### Kernel image

- The `uImage` load address `0x80008000` is physical; the kernel is **linked
  at `0xC0008000`**. Derive it: reconstruct `movw`/`movt` pairs and take the
  mode of the high byte (36,627 of 39,416 pairs were `0xc0xxxxxx`). With the
  wrong base every string xref comes back empty.
- Make an ELF from the raw image:
  ```sh
  arm-linux-gnueabihf-objcopy -I binary -O elf32-littlearm -B arm \
    --rename-section .data=.text,alloc,load,readonly,code \
    --change-addresses=0xC0008000 kernel.bin kernel.elf
  ```
  Disassemble with capstone in **ARM** mode. Binary Ninja recovers 0 functions
  even from this ELF. (`work/unix/kernel.elf` is the result for 2210.0.)
- String xrefs are `movw`+`movt` pairs, not literal pools; search for the pair.
- Linear `.disasm()` of the whole image derails on embedded data; disassemble
  per function window. `movw`'s immediate is `(imm4 << 12) | imm12`. Do not
  drop registers written by `mov` when tracking a `movt` target.
