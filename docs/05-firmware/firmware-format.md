# T-NKM2 firmware: decryption, extraction, and what it shows

- The public USB firmware for this model, **T-NKM2AKUC 2210.0**, decrypts fully
  and its VDFS4 rootfs extracts with no failures (2026-10-19).
- The TV runs **T-NKM2AKUC-2301.1** (`/etc/prd_info.ini`,
  `Build=TIZEN-TRUNK2021-NikeM2-RELEASE_20251112.1`; verified 2026-10-24).
  Samsung publishes no 2301.1 download.
- **`livefs/` is the authoritative rootfs**: `etc/` + `usr/` pulled from the
  TV, 50,347 files; spot-checked md5s match the TV (2026-10-24). Use the
  2210.0 image only for what the TV does not expose (boot chain, kernel image,
  signatures).

## Key

`tools/unixtract/src/keys.ukf` (from `theubusu/unixtract`) carries the NikeM2
key, which `bugficks/msddecrypt` and `synacktiv/samsung-q60t-exploit` lack:

```
"NikeM":  {x"dd18df36fdbbd957bcdf1a69aa5c06d6"}   # T-NKM*  - 2020
"NikeL":  {x"5bab1098dab48792619ebd63650d929f"}   # T-NKL*  - 2020
"NikeM2": {x"1c7b83f91c03b7cb942e18a7ad90b0bfa7cafef83637129188c12931af145f46"}  # T-NKM2* - 2021
```

NikeM2 is 32 bytes (AES-256-CBC, IV = first 16 bytes of `sha256(salt)`);
NikeM/NikeL are 16 bytes (AES-128-CBC, IV = `md5(salt)`). See
`tools/unixtract/src/formats/msd/mod.rs`. `tiz0wn` found the NikeL key still
valid for a 2026 NikeL image.

## Reproduce

```bash
# 1. firmware (URL answered 200, 1,656,842,556 bytes, on 2026-10-24)
curl -o firmware/T-NKM2AKUC-2210.0.zip \
  https://downloadcenter.samsung.com/content/FM/202401/20240130172354029/T-NKM2AKUC.zip
unzip firmware/T-NKM2AKUC-2210.0.zip -d work/T-NKM2AKUC     # -> image/upgrade.msd

# 2. decrypt + extract all 9 sections (key built in)
cargo build --release --manifest-path tools/unixtract/Cargo.toml
tools/unixtract/target/release/unixtract -o msd:print_ouith \
  work/T-NKM2AKUC/image/upgrade.msd work/unix

# 3. unpack the VDFS4 rootfs (patched decoder, below)
tools/vdfs-unpack/target/release/vdfs-unpack work/unix/platform.img -o work/rootfs
```

Kept locally (gitignored, not published): `firmware/T-NKM2AKUC-2210.0.zip`, `work/unix/` (the 9
sections, plus `kernel.bin` = uImage payload and `kernel.elf` built per
`docs/06-security/techniques.md`), and built binaries of both tools.
`work/T-NKM2AKUC/` and `work/rootfs/` are not present; rerun steps 1 and 3 to
recreate them. Synacktiv's `tools/samsung-q60t-exploit/firmware/decrypt.py`
does not work for this firmware: it only knows the 16-byte md5 scheme.

## Container (2210.0)

```
magic        MSDU11, OUIT "Tizen Software Upgrade Tree Binary Format ver. 1.9"
model        T-NKM2AKUC
version      2210.0, build 2024-01-24
key type     "NikeM2 rel key decrypted aes key / NikeM2 rel key private rsa key"
cipher       aes-256-cbc-sha512
header       offs 0xe5, size 0xfa8, salt a3d7de565be93218
sections     ddr.init, seret.bin, uImage, dtb.bin, sign.bin,
             secos.bin, secos_drv.bin, platform.img, factory_peq.img
```

Every section carries an `OURSAValidationDesc` (256-byte RSA signature, public
key id `3349761644`). **Decryption does not imply forgeability**: a modified
image will not pass verification. `platform.img` is VDFS4 layout `2007`
(`VDFS2007` superblock at 0x400), zlib, read-only, not encrypted.

Kernel: 2210.0 is `Linux 4.1.10 … Sat Oct 28 17:53:28 UTC 2023`; the TV runs
`Linux 4.1.10 … Tue Jul 29 18:04:31 UTC 2025` (`/proc/version`, 2026-10-24).

## VDFS4: the `vdfs-unpack` fix

Upstream `gtors/vdfs-unpack` failed 61 of 50,297 files with
`corrupt deflate stream`, including `bash`, `node`, `sqlite3`,
`libmali.4k.so`, and `System.Private.CoreLib.dll`.

Per the reference implementation (`tools/vdfs-tools/lib/decompress.c`):

- `magic[0]` is an **authentication type**: `'I'` MD5, `'H'` SHA1, `'h'`
  SHA256, none = no auth.
- With auth the file is `[data area][extent table][hashes][signature][descriptor]`.
  Hash count is `ext_n + 1`; signature length from `sign_type` (RSA1024 = 128,
  RSA2048 = 256).

Fix in `tools/vdfs-unpack/src/decompress.rs` (SHA256 `'h'` case):

1. Table offset = `len - (40 + ext_n*16 + hash_len*(ext_n+1) + sign_len)`.
2. Extent = `{u16 magic, u16 flags, u32 len_bytes, u64 start}`, 16 bytes.
3. **Table order is logical order.** Sorting by offset gives a plausible-length
   but wrong file.
4. `flags != 0` → chunk stored uncompressed; pass it through.
5. Inflate the other chunks independently and concatenate.

Validation: `ImplNUI.ni.dll` reconstructs to exactly 1,638,400 bytes (the
descriptor's `unpacked_size`) and `file` reports a .NET PE32 DLL. The
descriptor `crc` (0x3935ad42) does not match `crc32()` of the result
(0x1a15e646); do not use that field for validation (what it covers is
unknown).

## What the firmware shows

### 2210.0 vs live 2301.1 (md5, same path)

| file | 2210.0 | 2301.1 (live = `livefs/`, verified 2026-10-24) |
|---|---|---|
| `usr/lib/modules/4.1.10/kernel/soc/sdp_gpu.ko` | `97681419…` | `4edea7c3…` |
| `usr/apps/privileged-service/bin/ps_agent` | `e571b3e9…` | `9216637a…` |
| `usr/lib/driver/libmali.4k.so` | `a3b97140…` | `60e55f40…` |
| `usr/apps/privileged-service/bin/mount.smb.sh` | `ff07e88d…` | `ff07e88d…` (same) |
| `usr/bin/bash` | `58e5f645…` | `58e5f645…` (same) |

The Remote PC/CIFS injection surface is unchanged between builds:
`docs/05-firmware/remotepc-dbus.md`.

### UEP is in the kernel image

The 2210.0 `uImage` contains `SfdOpenUepContext`, `SfdUepReadFile`,
`SfdUepVerifyFileSignature`, `SfdUepCheckFileType`,
`SfdUepVerifyDynamicLibrary`, `SfdUepVerifyExecutableBinary`,
`SfdUepPacketHandler`, `SfdUepHandleVerificationResult`, and the strings
`[%s] UEP has been disabled`, `[%s] Blocked! cur pid:%u ...`. UEP is built in,
not a module, so turning it off needs a kernel write. The live kernel has no
`/proc/kallsyms`; the same symbols on 2301.1 are (unverified). Model of both
UEP layers: `docs/06-security/uep-bypass.md`.

### `/opt` boot surface

Enabled units execute from the writable `/opt` (`vdfs rw,dncs`, the only
writable persistent mount), e.g. `bms-service`, `canalysis-daemon`,
`context-aware-service`, `pisa-service`, `vision-data-recognition-service`
under `/opt/usr/apps/*/bin/`, and the script `/opt/etc/parseinfo.sh`. The
binaries are signed ELFs (0755, owner `tizenglobalapp`; verified 2026-10-24);
replacing one with our own ELF fails kernel UEP. The script is the usable
hook: `docs/01-root/persistent-root.md`.

### Mali: r23p0, both public CVEs closed

`/dev/mali0` is `crw-rw-rw- root display` (verified 2026-10-24), from
`usr/lib/udev/rules.d/99-GPU-Acceleration.rules`:

```
KERNEL=="mali*", MODE="0666", GROUP="display", SECLABEL{smack}="*"
```

`sdp_gpu.ko` is not stripped and reports `r23p0-01rel0`, UK 11.18. That is
below r42p0, but Samsung backports fixes, and the **live 2301.1 module**
disassembles to:

- **CVE-2021-44828 fixed.** `kbase_jit_allocate_process` (`0x13578`) calls
  `kbase_vmap_prot` (reloc `0x13754`), the fixed form.
- **CVE-2022-46395 race absent.** `kbase_soft_event_update` (`0x141f0`) takes
  `kbase_gpu_vm_lock` itself (`0x142b0`, unlock `0x1435c`), maps through
  `kmap`, and copies with `kbase_mem_copy_to_pinned_user_pages`; it never calls
  `kbase_vmap`. The GHSL-2022-127 lock split does not exist here.

`tiz0wn`'s UK 11.6 ABI (sticky map/unmap `0x4010801d`/`0x4010801e`,
soft-event `0x4010801c`) is not this driver's ABI. A Mali physmap primitive
here would need a different bug.

## Device facts (2026-10-24)

```
/        /dev/root vdfs ro, 1.6G, 100% full
/opt     /dev/mmcblk0p21 vdfs rw,dncs, 3.9G, 87% full
.NET     /usr/share/dotnet/shared/Microsoft.NETCore.App/ 2.0.0 2.1.0 2.1.1 2.1.4 3.0.0 3.1.3
modules  /usr/lib/modules/4.1.10 (3.10.30 and linux are symlinks to it)
```
