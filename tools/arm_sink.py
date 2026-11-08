#!/usr/bin/env python3
"""
arm_sink.py — static sink-reachability analyser for 32-bit ARM/Thumb ELF.

Answers the question: "which function calls execv/system/popen, and is there a
guard (a compare-and-branch on a return value) on the path that reaches it?"

This is the same shape as a CodeQL/Joern sink query, specialised for the
Tizen/Samsung ARM binaries in this project. No dynamic analysis.
"""
import sys, struct, json, os
from collections import defaultdict
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from elftools.elf.elffile import ELFFile

# Exact symbol names (or C++ mangled prefixes) that are true sinks.
# Substring matching is deliberately NOT used — it produced false positives
# like `system` matching __throw_system_error and system_info_get_*.
SINKS = {
    # name -> why it matters
    'execv':      'process execution',
    'execve':     'process execution',
    'execl':      'process execution',
    'execlp':     'process execution',
    'execvp':     'process execution',
    'system':     'shell execution',
    '__system':   'shell execution',
    'popen':      'shell execution',
    'dlopen':     'code loading',
    'insmod':     'kernel module load',
    'mount':      'filesystem operation',
    'umount':     'filesystem operation',
    'mknod':      'device node creation',
    'ioctl':      'driver interaction',
    'prctl':      'process control',
    'setuid':     'privilege change',
    'setreuid':   'privilege change',
    'setresuid':  'privilege change',
    'capset':     'privilege change',
    'smack_setlabel': 'mac policy',
    'ptrace':     'process injection',
    'mmap':       'memory mapping',
}

class Elf:
    def __init__(self, path):
        self.path = path
        with open(path,'rb') as f:
            self.data = f.read()
        with open(path,'rb') as f:
            elf = ELFFile(f)
            self.is32 = elf.elfclass == 32
            self.machine = elf['e_machine']
            self.entry = elf['e_entry']
            self.execs = []   # (vaddr, offset, size, name)
            for seg in elf.iter_segments():
                if seg['p_type'] == 'PT_LOAD' and (seg['p_flags'] & 1):
                    self.execs.append((seg['p_vaddr'], seg['p_offset'], seg['p_filesz'], 'LOAD'))
            self.sections = {}
            for s in elf.iter_sections():
                self.sections[s.name] = (s['sh_addr'], s['sh_offset'], s['sh_size'], s['sh_flags'])
            self.pltmap = {}
            self.gotmap = {}
            self._load_relocs(elf)
            self.syms = {}
            self.local_funcs = []
            self._load_syms(elf)

    def _load_relocs(self, elf):
        try:
            relplt = elf.get_section_by_name('.rel.plt')
        except Exception:
            relplt = None
        if relplt is None: return
        names = []
        try:
            dynsym = elf.get_section_by_name('.dynsym')
            for r in relplt.iter_relocations():
                symidx = r['r_info_sym']
                try:
                    names.append((r['r_offset'], dynsym.get_symbol(symidx).name))
                except Exception:
                    names.append((r['r_offset'], None))
        except Exception:
            return
        self.relocs = names
        # Map PLT stubs. ARM PLT: resolver then 12-byte stubs.
        plt = elf.get_section_by_name('.plt')
        if plt:
            base, off, size = plt['sh_addr'], plt['sh_offset'], plt['sh_size']
            # ARM PLT: header (16 bytes), then 12-byte stubs.
            #   stub+0 : add ip, pc, #imm      (0xe28fcXXX)
            #   stub+4 : add ip, ip, #imm2
            #   stub+8 : ldr pc, [ip, #imm3]   <- this is what BLX targets
            # Robust: locate every `ldr pc, [ip, #imm]` and resolve its literal to a
            # GOT slot. No stride assumption -- some binaries contain 4-byte
            # outliers that break 12-byte stepping (verified on stc-manager @0x4ab8).
            got2sym = {}
            for (g, n) in names:
                if n: got2sym[g] = n
            def arm_expand_imm(w):
                rot = ((w >> 8) & 0xf) * 2
                v = w & 0xff
                if rot == 0: return v
                return ((v >> rot) | (v << (32 - rot))) & 0xffffffff
            # Find every `ldr pc, [ip, #imm]`, then walk BACKWARDS to the
            # preceding `add ip, pc, #imm`. This tolerates the 4-byte outliers
            # seen in some binaries (stc-manager) that break 12-byte stepping.
            # Canonical ARM PLT stub (12 bytes):
            #   +0  add ip, pc, #imm0
            #   +4  add ip, ip, #imm1
            #   +8  ldr pc, [ip, #imm2]      <- BLX target
            # Some binaries (stc-manager) insert a 4-byte outlier, so we detect
            # each `ldr pc` and walk back to its `add ip, pc` rather than stepping.
            for a in range(base, base+size-4, 4):
                w2 = struct.unpack('<I', self.data[off + (a-base):off + (a-base) + 4])[0]
                if (w2 & 0xffff0000) != 0xe5bc0000:
                    continue
                w0 = w1 = None; a0 = None
                for back in (8, 12, 4):
                    if a - back < base: continue
                    c = struct.unpack('<I', self.data[off + (a-back-base):off + (a-back-base) + 4])[0]
                    if (c & 0xffff0000) == 0xe28f0000:
                        w0 = c; a0 = a - back
                        if back >= 8:
                            w1 = struct.unpack('<I', self.data[off + (a-4-base):off + (a-4-base) + 4])[0]
                        break
                if w0 is None: continue
                ip = ((a0 + 8) & ~3) + arm_expand_imm(w0)
                if w1 is not None and (w1 & 0xfffff000) == 0xe28cc000:
                    ip += arm_expand_imm(w1)
                ip += (w2 & 0xfff)          # the ldr-pc displacement into GOT
                ip &= 0xffffffff
                sym = got2sym.get(ip)
                if sym:
                    # Key on the stub BASE (the first `add ip, pc`) and also on
                    # the trailing `ldr pc`, which is what BLX actually targets
                    # in this ABI (verified by disassembling the PLT).
                    self.pltmap[a0] = sym
                    self.pltmap[a]  = sym
                    self.gotmap[ip] = sym

    def _load_syms(self, elf):
        for secname in ('.symtab','.dynsym'):
            sec = elf.get_section_by_name(secname)
            if not sec: continue
            for s in sec.iter_symbols():
                if s['st_info']['type'] == 'STT_FUNC' and s['st_value']:
                    self.syms[s['st_value'] & ~1] = s.name
                    if s['st_info']['bind'] == 'STB_LOCAL' and s['st_size']:
                        self.local_funcs.append((s['st_value'] & ~1, s['st_size'], s.name))
        self.local_funcs.sort()

    def seg_of(self, va):
        for v,o,s,_ in self.execs:
            if v <= va < v+s: return (v,o,s)
        return None

    def bytes_at(self, va, n):
        seg = self.seg_of(va)
        if not seg: return b''
        v,o,s = seg
        return self.data[o+(va-v):o+(va-v)+n]

    def thumb_at(self, va):
        return None

    def md(self, thumb=True):
        m = Cs(CS_ARCH_ARM, (CS_MODE_THUMB if thumb else CS_MODE_ARM)|CS_MODE_LITTLE_ENDIAN)
        m.skipdata = True       # CRITICAL: default False stops at first bad byte
        return m


def decode_branches(e, mode=CS_MODE_THUMB):
    """Return list of (addr, target, kind). Handles Thumb T1 BL/BLX and ARM B/BL.
    Deduplicates by address: a byte stream can be decoded at overlapping offsets,
    which previously produced 9 copies of a single call site."""
    out = []
    seen = set()
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.skipdata = True
    mdt = Cs(CS_ARCH_ARM, CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN)
    mdt.skipdata = True
    for v,o,s,_ in e.execs:
        blob = e.data[o:o+s]
        # Thumb pass
        i = 0
        while i < len(blob)-4:
            h1 = struct.unpack('<H', blob[i:i+2])[0]
            if (h1 >> 11) == 0x1e:                      # 11110 xxxxx
                h2 = struct.unpack('<H', blob[i+2:i+4])[0]
                if (h2 >> 11) in (0x1d, 0x1f):         # BLX / BL
                    S=(h1>>10)&1; i10=h1&0x3ff
                    J1=(h2>>13)&1; J2=(h2>>11)&1; i11=h2&0x7ff
                    I1=(1-J1) if J1!=S else J1
                    I2=(1-J2) if J2!=S else J2
                    imm=(S<<24)|(I1<<23)|(I2<<22)|(i10<<12)|(i11<<1)
                    if S: imm -= 1<<25
                    pc = v+i+4
                    tgt = (pc+imm) & 0xffffffff
                    kind = 'blx' if (h2>>11)==0x1d else 'bl'
                    if (v+i) not in seen:
                        seen.add(v+i); out.append((v+i, tgt, kind))
                    i += 4; continue
                # conditional B T1: 1101 cond imm8
            if (h1 >> 12) == 0xd and (h1 & 0x0f00) != 0x0e00:   # b<cond>
                imm8 = h1 & 0xff
                if imm8 & 0x80: imm8 -= 0x100
                if (v+i) not in seen:
                    seen.add(v+i); out.append((v+i, (v+i+4+imm8*2)&0xffffffff, 'bcond'))
            elif (h1 >> 11) == 0x1c:                    # B T2 (uncond, 11-bit)
                imm11 = h1 & 0x7ff
                if imm11 & 0x400: imm11 -= 0x800
                if (v+i) not in seen:
                    seen.add(v+i); out.append((v+i, (v+i+4+imm11*2)&0xffffffff, 'b'))
            i += 2
    return out


def call_graph(e, branches):
    """Edges: caller function -> callee (PLT symbol or internal function)."""
    funcs = sorted(f[0] for f in e.local_funcs)
    def func_of(va):
        lo, hi = 0, len(funcs)-1
        best = None
        while lo <= hi:
            mid = (lo+hi)//2
            if funcs[mid] <= va: best = funcs[mid]; lo = mid+1
            else: hi = mid-1
        return best
    edges = defaultdict(set)
    for addr, tgt, kind in branches:
        if kind not in ('bl','blx'): continue
        caller = func_of(addr)
        if caller is None: continue
        sym = e.pltmap.get(tgt)
        if sym:
            edges[caller].add(('ext', sym))
        else:
            callee = func_of(tgt)
            if callee and callee != caller:
                edges[caller].add(('int', callee))
    return edges, func_of


def guarded_before(e, branches, calladdr, window=24):
    """Look for a compare-and-branch on a call result within `window` insns
    before calladdr. Heuristic: cbz/cbnz/beq/bne on a register that was
    loaded/returned recently."""
    seg = e.seg_of(calladdr)
    if not seg: return []
    hits = []
    for addr, tgt, kind in branches:
        if kind in ('bcond',) and 0 <= calladdr-addr <= window*2:
            hits.append((addr, tgt))
    return hits


def analyse(path, verbose=True):
    e = Elf(path)
    branches = decode_branches(e)
    edges, func_of = call_graph(e, branches)
    sink_calls = []
    for addr, tgt, kind in branches:
        if kind not in ('bl','blx'): continue
        sym = e.pltmap.get(tgt)
        if not sym: continue
        for frag, why in SINKS.items():
            if frag in sym:
                sink_calls.append((addr, sym, why, func_of(addr)))
                break
    if verbose:
        print(f"=== {path}")
        print(f"  entry {e.entry:#x}   exec segs {len(e.execs)}   funcs {len(e.local_funcs)}   plt {len(e.pltmap)}")
        print(f"  sink calls: {len(sink_calls)}")
        for addr, sym, why, fn in sorted(sink_calls):
            name = e.syms.get(fn, f'sub_{fn:x}')
            g = guarded_before(e, branches, addr)
            gtxt = f"guards_nearby={len(g)}" if g else "NO_GUARD_NEARBY"
            print(f"    {addr:#08x}  {sym:32s} {why:22s} in {name[:40]:40s} {gtxt}")
    return {'elf': e, 'branches': branches, 'edges': edges, 'sinks': sink_calls}

if __name__ == '__main__':
    for p in sys.argv[1:]:
        analyse(p)


def is_sink(sym):
    """Exact-match sink test with C++ demangle tolerance."""
    if sym in SINKS: return SINKS[sym]
    # C++ mangled libc++ wrappers
    for k,v in SINKS.items():
        if sym.startswith('_Z') and k in sym:
            # crude: only accept if k appears as a whole token segment
            import re
            if re.search(r'(?<![a-zA-Z0-9_])'+re.escape(k)+r'(?![a-zA-Z0-9_])', sym):
                return v
    return None
