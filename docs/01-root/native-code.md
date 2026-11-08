# Running native code on the TV

Merged 2026-10-24 from `native-code.md`, `elf-loading.md`,
`elf-loader-impl.md` and `exec-blocked.md`. QN75QN85DAFXZA,
`T-NKM2AKUC-2301.1`, .NET Core 3.1.3, ARMv7.

## Summary

| want to run | how | status |
|---|---|---|
| a script | `#!/usr/bin/awk -f` (gawk is not UEP-hooked); `.sh`/bash/node are refused | works (`persistent-root.md`) |
| C# code | `/usr/bin/dotnet our.dll`, target `netcoreapp3.1` | works; **the recommended route** |
| a native library function | P/Invoke from C# into libraries already on the TV | works (`apps/cftv.cs`, `apps/bt-trace.cs`) |
| raw machine code | C#: anonymous `mmap` RW, copy, `mprotect` RX, call through a delegate | works, verified 2026-10-20 |
| our own ELF | `dotnet /opt/el/elfload.dll our.elf` (mmap gap) | works for **freestanding** code only |
| our own ELF via `execve` | anywhere writable | **blocked** |

Prefer C# with P/Invoke for anything real: it needs no loader, gets argv and a
normal process, and can call any library the TV ships.

## `execve` of a new binary: blocked everywhere

Every writable location refuses to `execve` a newly placed binary, including
byte-identical copies of binaries that run fine from `/usr`:

```
cp /usr/bin/dotnet /opt/dotnet-copy   -> Operation not permitted
cp /bin/sh         /opt/shtest        -> Operation not permitted
```

Tried, no effect (2026-10-22):
- SMACK file label and execute attribute (`chsmack -a`, `chsmack -e`)
- a `User User rwxat` rule in `/sys/fs/smackfs/load2` (accepted)
- the binary's name in `/proc/vd_signal_policy_list` (accepted)
- renaming to listed names (`dotnet-loader`, `corerun`)

`/tmp` and `/opt` are not mounted `noexec`. The check is content-based and
independent of the label: it holds even as `System::Privileged`. Cross-compiling
works (Docker `tvhack` image, `arm-linux-gnueabihf-gcc`); the TV just won't
`execve` the result. Replacing a UEP-signed `.so` in a writable
`LD_LIBRARY_PATH` fails with `failed to map segment from shared object`.

Not tested: `execve("/lib/ld-linux.so.3", [..., "/opt/ourbinary"])`, i.e. the
signed loader mapping an unsigned program.

## Raw machine code from C# (verified 2026-10-20)

```
/usr/bin/dotnet our.dll
  mmap(NULL, 4096, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS)
  Marshal.Copy(arm_code, ...)                 never touches a file
  mprotect(page, 4096, PROT_READ|PROT_EXEC)
  Marshal.GetDelegateForFunctionPointer<Fn>(page)()
```

Evidence: shellcode returning `0x1337`, and `mov r7,#20; svc #0; bx lr`
(`getpid`) returning a live pid. UEP checks files at `execve`; memory that never
came from a file is not checked.

- **Example:** `build/payloads-netcoreapp3.1/exectest/` (source `Program.cs`);
  deployed at `/opt/usr/home/owner/share/dotnet/exectest.dll`.
- **Code:** ARM state (not Thumb) ending in `bx lr`.
- **Errors:** `mmap` failure is `(long)ptr == -1`, not 0.
- **Crashes:** a bad PC-relative literal hangs only our process, not the TV.

## Our own ELF: `elfload.dll` (verified 2026-10-21)

The kernel blocks `execve` of an unsigned ELF but not `mmap(PROT_EXEC)` of a
file on the same filesystem. `/opt/el/elfload.dll` exploits that gap. It's a
managed mini-loader that:
- maps each `PT_LOAD` segment
- applies a load bias for PIE
- `mprotect`s each segment to its real permissions
- calls `entry + bias` as a function

```
/usr/bin/dotnet /opt/el/elfload.dll /opt/bin/our.elf
```

Verified: a static PIE whose `entry()` returns 1337 returned 1337.

Limits:
- **Freestanding code only.** The entry is called as a plain function: no
  argv, envp, auxv or TLS. A normal `-static` glibc program needs those in
  `_start` and segfaults (observed 2026-10-23, rc 139). Dynamic ELFs need a
  real `ld.so` (relocations, `DT_NEEDED`, PLT/GOT) and are not supported.
- **The entry must return.** An entry that never returns hangs the loader;
  alternatively, call `exit_group` yourself.
- **Process identity stays `dotnet`:** `/proc/self/exe`, argv and env are the
  host's.
- **The source is not in this repo.** Only the deployed DLL exists
  (`/opt/el/elfload.dll`, 8704 bytes, verified present 2026-10-24).

## P/Invoke into the TV's own libraries

Any NUI app already has `libecore_wl2.so.1` mapped; `cftv` calls
`ecore_wl2_window_raise` this way (`apps/cftv.cs`). `apps/bt-trace.cs` calls
`ptrace`, `waitpid` and `__errno_location` from libc. Signatures must match the
ARM32 ABI (`long` and pointers are 32-bit).
