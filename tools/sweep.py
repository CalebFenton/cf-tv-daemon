#!/usr/bin/env python3
"""Scan every ELF in a tree for privileged sink calls and report the
functions that reach them. Static only."""
import sys, os, glob
from arm_sink import Elf, decode_branches, call_graph, SINKS, is_sink

def is_elf(p):
    try:
        with open(p,'rb') as f: return f.read(4)==b'\x7fELF'
    except Exception: return False

def scan(root, limit=None):
    rows=[]
    n=0
    for dirpath,_,files in os.walk(root):
        for fn in files:
            p=os.path.join(dirpath,fn)
            if not is_elf(p): continue
            n+=1
            if limit and n>limit: break
            try:
                e=Elf(p)
                if e.machine!='EM_ARM': continue
                br=decode_branches(e)
                for addr,tgt,kind in br:
                    if kind not in ('bl','blx'): continue
                    sym=e.pltmap.get(tgt)
                    if not sym: continue
                    why = is_sink(sym)
                    if why:
                        rows.append((p,addr,sym,why))
            except Exception:
                pass
    return rows

if __name__=='__main__':
    root=sys.argv[1]
    lim=int(sys.argv[2]) if len(sys.argv)>2 else None
    rows=scan(root,lim)
    from collections import Counter
    c=Counter((r[2],r[3]) for r in rows)
    print(f"files scanned: {len(set(r[0] for r in rows))}   sink calls: {len(rows)}")
    print("\n=== sink frequency ===")
    for (sym,why),k in c.most_common(40):
        print(f"  {k:5d}  {sym:34s} {why}")
