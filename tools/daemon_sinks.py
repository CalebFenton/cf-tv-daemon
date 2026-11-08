#!/usr/bin/env python3
"""Focused: shell sinks in Tizen/Samsung daemons only, with the command string."""
import sys, os, struct
from arm_sink import Elf
from capstone import *
from shell_sinks import branch_target, ldr_lit, SHELL

TARGETS = ('stc-manager','ps_agent','remotepc','smbclientd','smbmgrd','tzdaemon',
           'security-manager','pkgmgr','cert-server','vconf','wrt','launchpad',
           'amd','aifw','tagpro','nfc-daemon','micom','swu','dlog','sdbd')

def is_elf(p):
    try:
        with open(p,'rb') as f: return f.read(4)==b'\x7fELF'
    except Exception: return False

def scan(p):
    e=Elf(p); out=[]
    for v,o,s,_ in e.execs:
        md=Cs(CS_ARCH_ARM, CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN); md.skipdata=True
        ins=list(md.disasm(e.data[o:o+s], v))
        for idx,i in enumerate(ins):
            t=branch_target(i)
            if not t: continue
            sym=e.pltmap.get(t)
            if sym not in SHELL: continue
            strs=[]
            for j in ins[max(0,idx-60):idx]:
                lit=ldr_lit(j)
                if lit is None: continue
                off2=o+(lit-v)
                if not (0<=off2<len(e.data)-4): continue
                w=struct.unpack('<i',e.data[off2:off2+4])[0]
                for cand in ((lit+w)&0xffffffff,(lit+4+w)&0xffffffff):
                    bb=e.bytes_at(cand,90).split(b'\0')[0]
                    if len(bb)>=5 and all(32<=c<127 for c in bb):
                        strs.append(bb.decode()); break
            out.append((i.address,sym,strs))
    return out

seen=set()
for dp,_,fs in os.walk('livefs/usr/bin'):
    for fn in sorted(fs):
        if not any(t in fn for t in TARGETS): continue
        p=os.path.join(dp,fn)
        if not is_elf(p) or p in seen: continue
        seen.add(p)
        try: res=scan(p)
        except Exception as ex: continue
        if not res: continue
        print(f"\n### {fn}  ({len(res)})")
        for addr,sym,strs in res:
            # only show strings that look like commands/formats
            good=[x for x in strs if any(c in x for c in ('%','/','|','>',';','&&','$'))]
            print(f"  {addr:#08x} {sym:7s} {good[-2:] if good else strs[-1:] if strs else []}")
