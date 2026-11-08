#!/usr/bin/env python3
"""Reliable ARM/Thumb disassembly with PLT symbol resolution.

Why this exists: bintools/Binary Ninja loads these base-0 .so files at
+0x10000 and decodes Thumb as ARM, producing coprocessor garbage. Host
objdump has no ARM support. arm-linux-gnueabihf-objdump (in tvhack) is
correct, but needs Docker. This script uses capstone directly with the
right mode and resolves PLT stubs by parsing .rel.plt, so every call
target carries a name.

Usage:  ./tools/dis.py <binary> <start_hex> <length_hex>
"""
import sys, re, struct, subprocess

try:
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_ARM
except ImportError:
    sys.exit("capstone required")


def load_elf_info(path):
    """Return (text_vaddr, text_off, text_size, plt_meta, relocs)."""
    out = subprocess.run(["llvm-readelf", "-SW", path],
                         capture_output=True, text=True).stdout
    secs = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) > 6 and p[0].startswith("["):
            try:
                secs[p[1]] = (int(p[3], 16), int(p[4], 16), int(p[5], 16))
            except ValueError:
                pass
    rel = {}
    rout = subprocess.run(["llvm-readelf", "-rW", path],
                          capture_output=True, text=True).stdout
    for line in rout.splitlines():
        p = line.split()
        if len(p) >= 5 and "JUMP_SLOT" in p[2]:
            try:
                rel[int(p[0], 16)] = p[4]
            except ValueError:
                pass
    return secs, rel


def plt_stubs(data, secs, rel):
    """Map PLT stub address -> imported symbol name (ARM encoding)."""
    stubs = {}
    if ".plt" not in secs:
        return stubs
    vaddr, off, size = secs[".plt"]
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    ins = list(md.disasm(data[off:off + size], vaddr))
    i = 0
    while i < len(ins) - 2:
        # pattern: add ip,pc,#0,#12 / add ip,ip,#imm / ldr pc,[ip,#imm]
        if (ins[i].mnemonic == "add" and "ip, pc" in ins[i].op_str
                and i + 2 < len(ins)):
            hi = ins[i + 1]
            ld = ins[i + 2]
            m_hi = re.search(r"#0x([0-9a-f]+)", hi.op_str)
            m_ld = re.search(r"#(0x[0-9a-f]+|\d+)\]", ld.op_str)
            if m_hi and m_ld:
                # PLT idiom bases off the FIRST add's PC (ins[i].address + 8),
                # not the second. Using hi.address here is an off-by-one PLT slot.
                base = (ins[i].address + 8) + int(m_hi.group(1), 16)
                slot = base + int(m_ld.group(1), 0)
                stubs[ins[i].address] = rel.get(slot, f"got_{slot:x}")
            i += 3
            continue
        i += 1
    return stubs


def main():
    if len(sys.argv) < 4:
        sys.exit("usage: dis.py <binary> <start_hex> <length_hex> [thumb|arm]")
    path, start_s, len_s = sys.argv[1], sys.argv[2], sys.argv[3]
    mode = sys.argv[4] if len(sys.argv) > 4 else "thumb"
    start = int(start_s, 16)
    length = int(len_s, 16)

    data = open(path, "rb").read()
    secs, rel = load_elf_info(path)
    stubs = plt_stubs(data, secs, rel)

    md = Cs(CS_ARCH_ARM,
            CS_MODE_THUMB if mode == "thumb" else CS_MODE_ARM)
    md.detail = True

    print(f"# {path} [{mode}] 0x{start:x}..0x{start+length:x}")
    for insn in md.disasm(data[start:start + length], start):
        line = f"0x{insn.address:05x}  {insn.bytes.hex():<12} {insn.mnemonic:<8} {insn.op_str}"
        if insn.mnemonic in ("bl", "blx") and insn.op_str.startswith("#"):
            tgt = int(insn.op_str[1:], 16)
            for delta in (0, 1, -1):
                if tgt + delta in stubs:
                    line += f"   -> {stubs[tgt + delta]}"
                    break
            else:
                line += f"   -> sub_{tgt:x}"
        # resolve pc-relative literal loads
        if "[pc," in insn.op_str:
            m = re.search(r"#(0x[0-9a-f]+|\d+)\]", insn.op_str)
            if m:
                slot = ((insn.address + 4) & ~3) + int(m.group(1), 0)
                if slot + 4 <= len(data):
                    val = struct.unpack_from("<I", data, slot)[0]
                    note = f"  ; lit=0x{val:x}"
                    if 0 <= val < len(data):
                        s = data[val:val + 60].split(b"\0")[0]
                        if len(s) > 3 and all(32 <= c < 127 for c in s):
                            note += f' "{s.decode()}"'
                    line += note
        print(line)


if __name__ == "__main__":
    main()
