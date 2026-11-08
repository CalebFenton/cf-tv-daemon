# Vendor dependencies

Third-party git projects are **submodules** (`.gitmodules`). Clone with
`git clone --recurse-submodules`, or run `git submodule update --init` in an
existing checkout. Everything else third-party (Samsung's SDB), the extracted firmware and the toolchains are **not** tracked:
restore them as below.

## Submodules

| path | upstream | pinned at | what |
|---|---|---|---|
| `vendor/samsung-tv-root-source/` | `chris-ritsen/samsung-tv-root` (Unlicense) | `3b97edf` (v0.0.12), unpatched | The root exploit, root agent and SDB client. **Nothing runs without it**: `uv sync` installs it into `.venv` (editable, `pyproject.toml`), and `tv/root.py`, `tv/chan.py`, `tv/cftv.py` import `samsung_tv_root`. |
| `tools/vdfs-unpack/` | `gtors/vdfs-unpack` (MIT), via the public fork `CalebFenton/vdfs-unpack` | branch `cf-tv-daemon`: upstream `0bc1e65` + one commit (hZip files decompressed per extent table; offered upstream) | VDFS4 unpacker for the firmware's root filesystem (build: `cargo build --release`) |
| `tools/unixtract/` | `theubusu/unixtract` | `144cf51` | MSD firmware container extraction (`docs/05-firmware/firmware-format.md`) |
| `tools/vdfs-tools/` | `HinTak/vdfs-tools` | `b87531e` | reference VDFS4 implementation |
| `tools/samsung-q60t-exploit/` | `synacktiv/samsung-q60t-exploit` | `db36974` | `firmware/decrypt.py`: MSDU11 decryptor with the 2021 passphrase |
| `docs/reference/samsung-tizen-tv-debloat/` | `ardazeytin/samsung-tizen-tv-debloat` | `0a5d085` | reference reading |
| `docs/reference/quiet-tizen/` | `jss8649/quiet-tizen` | `cf518d7` (v0.1.0) | reference reading |

The rest of `tools/` (`*.py`) is ours: ELF sink analysis and MSDU11 probes
(`docs/04-system/static-analysis.md`, `docs/05-firmware/`).

## Not tracked — restore into `vendor/`

| path | what |
|---|---|
| `vendor/sdb/` | Samsung SDB from Tizen Studio. `tv/*.py` force `SDB` to this path. |

Tracked third-party file: `apps/assets/fonts/ShareTechMono-Regular.ttf`
(Carrois Type Design, SIL Open Font License 1.1, `OFL.txt` beside it), the
HUD's font, deployed with the cftv agent.

## Toolchains — restore at repo root

| path | what |
|---|---|
| `.dotnet8/` | host SDK that builds `apps/*.csproj` (targets `netcoreapp3.1`) |
| `.venv/` | created by `uv sync` (README, Setup); not restored by hand |
| `build/tv-framework/` | reference DLLs pulled **from the TV** (see below) |

`build/tv-framework/` needs eight DLLs from `/usr/share/dotnet.tizen/framework/`
on the device: `Tizen.NUI.dll`, `Tizen.Applications.Common.dll`,
`Tizen.System.dll`, `Tizen.dll`, `Tizen.Multimedia.dll`,
`Tizen.Multimedia.MediaPlayer.dll`, `Tizen.Multimedia.AudioIO.dll`,
`ElmSharp.dll`; and `Tizen.TV.Multimedia.ESPlayer.dll` from
`/usr/apps/csapi-tv-tizenfx/shared/lib/`. The files are world-readable, so
`sdb pull` copies them directly (used 2026-10-24):

```sh
for f in Tizen.NUI Tizen.Applications.Common Tizen.System Tizen Tizen.Multimedia Tizen.Multimedia.MediaPlayer Tizen.Multimedia.AudioIO ElmSharp; do
  vendor/sdb/data/tools/sdb -s $CF_TV_IP:26101 pull \
    /usr/share/dotnet.tizen/framework/$f.dll build/tv-framework/$f.dll
done
vendor/sdb/data/tools/sdb -s $CF_TV_IP:26101 pull \
  /usr/apps/csapi-tv-tizenfx/shared/lib/Tizen.TV.Multimedia.ESPlayer.dll build/tv-framework/
```

## Regenerated, not restored

| path | how |
|---|---|
| `build/payloads-netcoreapp3.1/` | built from `vendor/samsung-tv-root-source/` |
| `build/cftv/` | `bin/cftv` builds it on demand from `apps/cftv.cs` |

## Extracted firmware — regenerate by hand (`docs/05-firmware/firmware-format.md`)

| path | what it is |
|---|---|
| `firmware/*.zip` | the downloaded firmware package |
| `work/` | intermediate: decrypted MSDU11 container, unpacked pieces |
| `livefs/` | the extracted VDFS4 root filesystem |
| `live/` | kernel module blobs pulled from the device |

Format and keys: `docs/05-firmware/firmware-format.md`.
