#!/usr/bin/env python3
"""Find system()/popen()/execl*() call sites and report:
   - enclosing binary + function
   - nearby resolved string constants (the command/format being built)
This is the guard-quality query: a shell call whose argument is built by
sprintf from external data is an injection candidate.
"""
import sys, os, struct
from arm_sink import Elf
from capstone import *

SHELL = ('system','popen','execl','execlp','execle','execvp','execv')

def is_elf(p):
    try:
        with open(p,'rb') as f: return f.read(4)==b'\x7fELF'
    except Exception: return False

def branch_target(i):
    if i.mnemonic not in ('bl','blx'): return None
    b=i.bytes
    if len(b)==4:
        h1,h2=struct.unpack('<HH',b)
        if (h1>>11)==0x1e and (h2>>11) in (0x1d,0x1f):
            S=(h1>>10)&1; i10=h1&0x3ff; J1=(h2>>13)&1; J2=(h2>>11)&1; i11=h2&0x7ff
            I1=(1-J1) if J1!=S else J1; I2=(1-J2) if J2!=S else J2
            imm=(S<<24)|(I1<<23)|(I2<<22)|(i10<<12)|(i11<<1)
            if S: imm-=1<<25
            return (i.address+4+imm)&0xffffffff
    return None

def ldr_lit(i):
    b=i.bytes
    if len(b)==4:
        h1,h2=struct.unpack('<HH',b)
        if (h1 & 0xff7f)==0xf85f: return ((i.address+4)&~3)+(h2&0xfff)
    elif len(b)==2:
        h=struct.unpack('<H',b)[0]
        if (h & 0xf800)==0x4800: return ((i.address+4)&~3)+((h&0xff)*4)
    return None

def scan(p):
    e=Elf(p)
    if e.machine!='EM_ARM': return []
    out=[]
    for v,o,s,_ in e.execs:
        blob=e.data[o:o+s]
        md=Cs(CS_ARCH_ARM, CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN); md.skipdata=True
        ins=list(md.disasm(blob, v))
        for idx,i in enumerate(ins):
            t=branch_target(i)
            if not t: continue
            sym=e.pltmap.get(t)
            if sym not in SHELL: continue
            # gather strings in the preceding 40 instructions
            strs=[]
            for j in ins[max(0,idx-40):idx]:
                lit=ldr_lit(j)
                if lit is None: continue
                off2=o+(lit-v)
                if not (0<=off2<len(e.data)-4): continue
                w=struct.unpack('<i',e.data[off2:off2+4])[0]
                for cand in ((lit+w)&0xffffffff,(lit+4+w)&0xffffffff):
                    bb=e.bytes_at(cand,80).split(b'\0')[0]
                    if len(bb)>=4 and all(32<=c<127 for c in bb):
                        strs.append(bb.decode())
                        break
            out.append((i.address, sym, strs[-3:] if strs else []))
    return out

if __name__=='__main__':
    root=sys.argv[1]
    for dp,_,fs in os.walk(root):
        for fn in sorted(fs):
            p=os.path.join(dp,fn)
            if not is_elf(p): continue
            try: res=scan(p)
            except Exception: continue
            if res:
                print(f"\n### {p}  ({len(res)} shell calls)")
                for addr,sym,strs in res[:12]:
                    print(f"  {addr:#08x} {sym:8s} {strs}")
