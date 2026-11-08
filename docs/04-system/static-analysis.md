# Static analysis — sink census tooling, results, and libkUEPUser notes

## Tools (`tools/`)

| Tool | Does |
|---|---|
| `tools/arm_sink.py` | ELF → functions → PLT → call graph → sink call sites, 32-bit ARM/Thumb (library used by the others) |
| `tools/sweep.py <dir>` | whole-tree sink census |
| `tools/find_sink.py <sink> [dir]` | every call site of one sink, with the enclosing function when a symbol exists |
| `tools/guarded_system.py` | "non-literal `system`/`popen` argument" classifier — **rejected**, see below |

Run with the capstone venv against the pulled rootfs `livefs/`:

```
$ ./.venv2/bin/python tools/sweep.py livefs/usr/bin
files scanned: 211   sink calls: 891
    293  ioctl            101  system          39  execl     20  popen
    277  prctl             44  dlopen          33  execvp    18  mount
     13  execlp            12  execv           11  setuid     9  umount
      7  smack_setlabel     5  execve           3  setresuid  3  ptrace
      3  setreuid
```

Reproduced 2026-10-24 (≈35 s). `livefs/usr/bin` has 902 entries; 211 are ARM
ELFs.

## Durable results

| Claim | Status | Check |
|---|---|---|
| Sink census above | holds | `tools/sweep.py`, re-run 2026-10-24 |
| `launchpad-process-pool` `execv` at `0x1e434` in `__exec_loader_process` | holds | `tools/find_sink.py execv`, 2026-10-24 |
| `stc-manager` iptables argument injection | **retracted**: unreachable | see below |
| `guarded_system.py` classifier | **rejected**: 4/4 checked candidates false | see below |

### `stc-manager`: dead code

`stc-manager` has one `execv` site (`0x13ad4`, confirmed 2026-10-24) inside a
fork/exec helper (`0x1395a`) whose only callers (`0x1a164`, `0x1a342`) sit in
the function at `0x1a0ac`, which builds
`g_snprintf(buf, 0x100, "%s %s", "/usr/sbin/iptables", rule)`, splits on
spaces and execs. Nothing reaches `0x1a0ac`: no `bl`/`blx`/`b`, no literal
pointer, no `add pc`/`movw`+`movt` construction, no `.data.rel.ro` or
relocation entry. It only has an `.ARM.exidx` unwind entry (`0x250ec`). So
`stc-manager` cannot exec anything in this build.

### `guarded_system.py`: false positives

It takes the last write to `r0` before `bl system` as the argument. In
optimised C++ that lands on scratch reuse, a preceding `bl _ZdlPv`
(destructor), or misses `add rX, pc` literals. Checked candidates:

| Candidate | Reality |
|---|---|
| `tztv-tagpro-daemon` `system` ×2 | constant `system("/bin/echo 1 > /proc/tagerpro_info")` and the `0` variant |
| `targetLogClient` `system` ×3 | destructor paths misread as argument |
| `targetLogClient` `0x35a8c` | constant `system("sync")` |
| `targetLogClient` `/bin/tar -cPjf` | unresolved (disassembler desynchronised) |

A usable classifier needs real dataflow. Reading candidate functions by hand
is cheaper.

## Rules learned

Tool bugs that each produced a confident wrong answer first:

| Bug | Effect |
|---|---|
| capstone `skipdata=False` | disassembly stopped at first data word (1,296 of 39,308 insns) |
| PLT stride assumed 12 B | a 4-byte outlier at `0x4ab8` mis-mapped every later stub |
| `ldr pc` displacement ignored | stubs resolved to GOT base; zero symbols matched |
| stub mask `0xfffff000` instead of `0xffff0000` | whole PLT rejected |

- Anchor on ground truth: print one known stub and check its symbol.
- Assert totality: N PLT relocations should give ~N stubs; zero means broken.
- Cross-check against hand disassembly.
- Before writing down a sink finding, prove: (1) the sink is called, (2) the
  argument shape allows injection, (3) the calling function is reachable from
  an entry point, (4) the path carries attacker-controlled data. The
  `stc-manager` retraction came from skipping (3).

## `libkUEPUser.so` notes

`/usr/lib/libkUEPUser.so` has md5 `934a5d8eae4664c19b79ba104f765425`
(checked 2026-10-24); pull a copy with `sdb pull /usr/lib/libkUEPUser.so`. How it is
used and bypassed: `docs/06-security/uep-bypass.md`.

- `BSContext::checkFileIsInRWUser(const char*, char*)` at `0x69f4` (Thumb):
  `realpath` → `statvfs64` → `return f_flag & ST_RDONLY` (`sbfx r0, r0, #0, #1`
  at `0x6a22`). It tests whether the file sits on a read-only filesystem; it
  has no directory allowlist.
- USB disable-file path: strings `VDUEPOFF.cer` (`0x7c24`),
  `Find USBUEPPath : %s` (`0x7c42`), `Disable Script UEP` (`0x920f`),
  `/proc/self/zone` (`0x9230`); export `FindUEPDiableFile()` (`0x7685`). The
  search starts from a real USB mount path (`usb_device_get_mountpath` →
  storage manager over D-Bus); a `VDUEPOFF.cer` placed in tmpfs `/opt/media`
  or `/opt/uepstub` changed nothing (2026-10-21).
- Exports `verifyFile`, `verifyFileWihtoutKey`, `BSContext::verifyFile`,
  `BSContext::verifyFileWithDeviceId`, `BSContext::verifyFileContent`.
