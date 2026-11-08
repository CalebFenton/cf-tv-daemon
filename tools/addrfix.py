#!/usr/bin/env python3
"""Map a bintools-reported address to a file offset, auto-detecting the base convention.

bintools reports different things depending on the binary:
  * base-0 .so  -> reported = real + 0x10000   (must subtract 0x10000)
  * prelinked   -> reported = real (already includes the link vaddr)
  * .ko         -> addresses are meaningless; use section offsets

Usage:
  tools/addrfix.py <binary> <addr> [<addr> ...]
"""
import subprocess, re, sys, os

def sections(path):
    out = subprocess.run(['llvm-readelf','-SW',path],capture_output=True,text=True).stdout
    secs = {}
    for l in out.splitlines():
        p = l.split()
        if len(p) > 6 and p[1].startswith('.'):
            try:
                secs[p[1]] = (int(p[3],16), int(p[4],16), int(p[5],16))
            except ValueError:
                pass
    return secs

def main():
    if len(sys.argv) < 3:
        print(__doc__); return 1
    path = sys.argv[1]
    size = os.path.getsize(path)
    secs = sections(path)
    if '.text' not in secs:
        print(f"{path}: no .text section - for .ko use the symbol's section offset directly")
        return 1
    tv, to, ts = secs['.text']
    print(f"{os.path.basename(path)}  file=0x{size:x}  .text vaddr=0x{tv:x}..0x{tv+ts:x}")
    for a in sys.argv[2:]:
        reported = int(a, 0)
        found = False
        for delta in (0, 0x10000, 0x20000):
            cand = reported - delta
            if tv <= cand < tv + ts:
                fo = to + (cand - tv)
                print(f"  bintools 0x{reported:x} -> real 0x{cand:x} (delta 0x{delta:x})  file offset 0x{fo:x}")
                found = True
                break
        if not found:
            print(f"  bintools 0x{reported:x} -> OUTSIDE .text. Likely stale/bogus; "
                  f"re-derive from `readelf --dyn-syms`")
    return 0

if __name__ == '__main__':
    sys.exit(main())
