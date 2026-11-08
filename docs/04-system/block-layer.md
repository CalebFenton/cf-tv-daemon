# Block layer — `/` is read-only by VDFS superblock flag, not by the media

**Summary:** the eMMC and the root partition accept raw writes. `/` is
read-only because its VDFS volume was built with `mkfs.vdfs --read-only` and is
signed; the driver refuses to remount it `rw`. Modifying the rootfs would need
a re-signed volume, which needs Samsung's key.

## eMMC identity (verified 2026-10-24)

`/sys/bus/mmc/devices/mmc0:0001/`:

| Field | Value |
|---|---|
| `type` | `MMC` |
| `name` | `8GTF4R` |
| `manfid` | `0x000015` (Samsung) |
| `oemid` | `0x0100` |
| `date` | `06/2021` |
| `serial` | (omitted) |
| `cid` | (omitted) |
| `fwrev` | `0x0700000000000000` |
| `ffu_capable` | `1` |
| `raw_rpmb_size_mult` | `0x4` (RPMB present: `/dev/mmcblk0rpmb`, 179:96) |

Size: 15,269,888 sectors (≈7.3 GiB). Partition sizes (512 B sectors):
`p17` 24,576 · `p19` 3,481,600 (1,740,800 KiB) · `p20` 40,960 · `p21` 8,066,560
(4,033,280 KiB) · `p23` 24,576. Mount table: `docs/04-system/device.md`.

## The write-protect is not in the block layer

| Probe | Result | Checked |
|---|---|---|
| `blockdev --getro /dev/mmcblk0p19` | `0` | 2026-10-24 |
| `/sys/block/mmcblk0/ro` | `0` | 2026-10-24 |
| `/sys/block/mmcblk0/mmcblk0p19/ro` | `0` | 2026-10-24 |
| `/sys/block/mmcblk0/force_ro` | `0` | 2026-10-24 |
| `dd` 512 B to p19 offset 0, `conv=notrunc`; marker at last sector survives `drop_caches` + re-read | succeeded | 2026-10-21, not repeated (write test) |
| `mount -o remount,rw /` | `cannot remount ... is write-protected` | 2026-10-21, not repeated |

Raw writes to the partition holding `/` work from root. A bad write can leave
the TV unbootable with no recovery console. Do not write `mmcblk0p19` or boot
partitions casually.

## The cause: the superblock

First bytes of each volume (`head -c 400 /dev/mmcblk0pN`, 2026-10-24):

```
p19 (/):    VDFS2007 mkfs.vdfs -i -h sha256 -V -s /tmp/Tizen_VDFS_2021.product.key
            -z 1740800K -c zlib --read-only -r /var/tmp/mic-tmp-r14RUl
p21 (/opt): VDFS2007 /home1/releases/tools/Common/vdfs/bin/2021/mkfs.vdfs -i -V
            -z 4033280K -r /home1/working/OTP_TIZEN-TRUNK2021-NikeM2-HOTFIX-RELEASE_20210628.1_ATSC/T-NKM2AKUC-REL_otp/vdfs_data
```

`/` was built with `--read-only`, a sha256 hash table (`-h sha256`) and a
product signing key (`-s …product.key`). `/opt` has none of these and mounts
`rw,dncs`.

## Why a patched volume would not mount

The kernel's VDFS driver contains (found in `work/unix/kernel.bin`, extracted
from firmware `T-NKM2AKUC-2210.0`, not the running 2301.1):

```
"Driver supports only signed volumes"
"Image(%s) is RO image. But no hashtable."
"vdfs4-WARN: dncs cannot be used with ro"
"vdfs4-WARN: cannot remount readonly cause of dncs flag set"
"Bad version of extended super block"
symbols: vdfs4_get_and_cmp_hash, vdfs4_verify_file_signature,
         vdfs4_check_and_sign_dirty_bnodes, vdfs4_sign_mapping_pages
```

Clearing `--read-only` or rebuilding the volume yields an unsigned volume the
driver refuses. Firmware sections are separately RSA-signed
(`OURSAValidationDesc`, `docs/05-firmware/firmware-format.md`), and UEP refuses
unsigned native code at `execve`/`mmap`.

## Open questions

1. Does the boot chain also check RPMB (counter or device binding)? Unverified.
   It would make offline image substitution detectable independently of the
   filesystem signature.
2. Is there a service/factory mode that accepts an unsigned image? Unknown.
3. Can a volume stay signed with `--read-only` cleared? Probably needs the
   product key; untested.
