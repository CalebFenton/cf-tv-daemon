"""dll_members - list the public methods of named types in a .NET assembly, with parameter names.

usage: mise exec uv -- uv run --with dnfile python tools/dll_members.py ASSEMBLY.dll TYPE [TYPE ...]

Reads metadata only (dnfile), so it works on the TV's framework DLLs
(build/tv-framework/) without a .NET runtime. Enum types list their members.
"""

import sys

import dnfile


def main() -> None:
    pe = dnfile.dnPE(sys.argv[1])
    wanted = set(sys.argv[2:])
    for t in pe.net.mdtables.TypeDef:
        if str(t.TypeName) not in wanted:
            continue
        print(f"== {t.TypeNamespace}.{t.TypeName}")
        fields = [str(f.row.Name) for f in t.FieldList if str(f.row.Name) != "value__"]
        if fields:
            print("   fields:", ", ".join(fields))
        for m in t.MethodList:
            if m.row.Flags.mdPublic:
                print(f"   {m.row.Name}({', '.join(str(p.row.Name) for p in m.row.ParamList)})")


main()
