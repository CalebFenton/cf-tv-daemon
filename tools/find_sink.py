#!/usr/bin/env python3
"""List every call site of a given sink across a tree, with the enclosing function."""
import sys, os
from arm_sink import Elf, decode_branches, is_sink

def is_elf(p):
    try:
        with open(p,'rb') as f: return f.read(4)==b'\x7fELF'
    except Exception: return False

want=sys.argv[1]
root=sys.argv[2] if len(sys.argv)>2 else 'livefs/usr/bin'
for dirpath,_,files in os.walk(root):
    for fn in sorted(files):
        p=os.path.join(dirpath,fn)
        if not is_elf(p): continue
        try: e=Elf(p)
        except Exception: continue
        if e.machine!='EM_ARM': continue
        br=decode_branches(e)
        funcs=sorted(f[0] for f in e.local_funcs)
        def func_of(va):
            lo,hi=0,len(funcs)-1; best=None
            while lo<=hi:
                m=(lo+hi)//2
                if funcs[m]<=va: best=funcs[m]; lo=m+1
                else: hi=m-1
            return best
        hits=[]
        for addr,tgt,kind in br:
            if kind not in ('bl','blx'): continue
            sym=e.pltmap.get(tgt)
            if sym==want:
                fn_a=func_of(addr)
                nm=e.syms.get(fn_a, f'sub_{fn_a:x}') if fn_a else '?'
                hits.append((addr,nm))
        if hits:
            print(f"\n{p}")
            for a,nm in hits:
                print(f"   {a:#08x}  in {nm[:70]}")
