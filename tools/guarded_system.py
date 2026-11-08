#!/usr/bin/env python3
"""For each system()/popen() site, classify the argument:
   CONST   - r0 resolves to a fixed string (safe, no injection)
   VAR     - r0 comes from a register/stack slot (candidate)
This separates real candidates from noise."""
import sys, os, struct
from arm_sink import Elf
from shell_sinks import branch_target
from capstone import *

SHELL=('system','popen')
def is_elf(p):
    try:
        with open(p,'rb') as f: return f.read(4)==b'\x7fELF'
    except Exception: return False

def classify(p):
    e=Elf(p); out=[]
    for v,o,s,_ in e.execs:
        md=Cs(CS_ARCH_ARM, CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN); md.skipdata=True
        ins=list(md.disasm(e.data[o:o+s], v))
        for idx,i in enumerate(ins):
            t=branch_target(i)
            if not t: continue
            sym=e.pltmap.get(t)
            if sym not in SHELL: continue
            # walk back to find last write to r0
            kind='VAR'; what=''
            for j in reversed(ins[max(0,idx-14):idx]):
                op=j.op_str
                if not op.startswith('r0'): continue
                if j.mnemonic in ('ldr.w','ldr'):
                    if '[pc' in op:
                        h1,h2=struct.unpack('<HH',j.bytes)
                        if (h1&0xff7f)==0xf85f:
                            l=((j.address+4)&~3)+(h2&0xfff)
                            if 0<=l<len(e.data)-4:
                                val=struct.unpack('<i',e.data[l:l+4])[0]
                                cand=(l+4+val)&0xffffffff
                                bb=e.bytes_at(cand,90).split(b'\0')[0]
                                if len(bb)>3 and all(32<=c<127 for c in bb):
                                    kind='CONST'; what=bb.decode()
                    break
                if j.mnemonic=='add' and 'pc' in op:
                    continue
                break
            out.append((i.address,sym,kind,what))
    return out

tot=0
for dp,_,fs in os.walk(sys.argv[1] if len(sys.argv)>1 else 'livefs/usr/bin'):
    for fn in sorted(fs):
        p=os.path.join(dp,fn)
        if not is_elf(p): continue
        try: r=classify(p)
        except Exception: continue
        cand=[x for x in r if x[2]=='VAR']
        if cand:
            tot+=len(cand)
            print(f"\n### {fn}")
            for a,s,k,w in cand[:8]:
                print(f"   {a:#08x} {s} (arg not a literal)")
print(f"\nTOTAL non-literal system/popen args: {tot}")
