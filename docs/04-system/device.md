# Device — identity, storage, network, power, developer mode

All values read on the TV 2026-10-24 unless marked.

Two TVs are rooted and driveable: this QN85D and a CU7000 added 2026-11-24.
The facts below are the QN85D's; the CU7000's differences are in
[`device-cu7000.md`](device-cu7000.md). `tv/devices.py` names both and finds
either after a DHCP address change (`devices.md` in this directory).

## Identity

| Item | Value | Source |
|---|---|---|
| Model | `QN75QN85DAFXZA` | `GET http://$CF_TV_IP:8001/api/v2/` → `device.modelName` |
| Platform model | `T-NKM2AKUC` | `/etc/prd_info.ini` `Model=` |
| Firmware | `T-NKM2AKUC-2301.1` | `/etc/prd_info.ini` `Version=` |
| Build | `TIZEN-TRUNK2021-NikeM2-RELEASE_20251112.1`, `BuildType=release`, built 2025-11-11 21:42:20 | `/etc/prd_info.ini` |
| Kernel | `Linux 4.1.10 #1 SMP PREEMPT Tue Jul 29 18:04:31 UTC 2025 armv7l` | `uname -a` |
| RAM | `MemTotal: 1851936 kB` | `/proc/meminfo` |
| eMMC | Samsung `8GTF4R`, 8 GB (15,269,888 sectors) | `docs/04-system/block-layer.md` |

`/proc/target_version` reads `__TIZEN-TRUNK2021-NikeM2-RELEASE_20251112.1__T-NKM2AKUC`;
the boot payload writes it from `prd_info.ini` (`payload/parseinfo.awk`).

The REST API reports `firmwareVersion: "Unknown"`; use `prd_info.ini`.

## Storage

Kernel command line: `root=/dev/mmcblk0p19 rootfstype=vdfs ro … model=4kbtin`.

| Device | Mount | FS / options | Size | Used | Free |
|---|---|---|---|---|---|
| `/dev/mmcblk0p19` (`/dev/root`) | `/` | `vdfs ro` | 1.6G | 1.6G | 0 (100%) |
| `/dev/mmcblk0p21` | `/opt` (also bind at `/opt/usr/home/owner/content`) | `vdfs rw,relatime,dncs` | 3.9G | — | 541M (87%) |
| `/dev/mmcblk0p20` | `/mnt/systemrw` | `vdfs rw,dncs` | 20M | 4.2M | 16M |
| `/dev/mmcblk0p17` | `/mnt/drm` | `vdfs rw,dncs` | 12M | 2.5M | 9.6M |
| `/dev/mmcblk0p23` | `/factory_peq` | `squashfs ro` | 3.7M | 3.7M | 0 |
| `/dev/loop0`, `/dev/loop1` | `/usr/share/chromium-efl`, `…/upgrade` | `squashfs ro` | 40M, 57M | full | — |
| `/dev/loop2`, `/dev/loop3` | app images under `/opt/usr/apps/…` | `squashfs ro` | 256K, 128K | full | — |
| tmpfs | `/tmp`, `/run`, `/dev/shm`, `/opt/media` | RAM | 905M each | — | — |

`/opt` is the only large writable store and it is nearly full (87%, 541 MB
free after the 2026-10-24 cleanup of old test files; it has reached 96%). A
`busctl monitor` capture filled it once (154 MB in 10 min) and broke channel
writes. Do not write large files there.
Known space users of ours: `/opt/dbspace/.pkgmgr_parser.db.omp-backup` (2.4 MB,
2026-10-21) and the snapshots in `/opt/usr/share/tv-backups/`
(`docs/04-system/debloat.md`).

`/run` is tmpfs: anything written there (including runtime unit masks) is gone
after a real reboot.

## Network

| Item | Value |
|---|---|
| Interface | `wlan0` (Wi-Fi), the TV's LAN address (`CF_TV_IP`), /24 |
| Gateway | the LAN gateway |
| DNS (`/etc/resolv.conf`) | the LAN gateway, `8.8.8.8`, one IPv6 resolver |

Listening TCP (`netstat -ltn` on the TV, 2026-10-24): `4000, 8001, 8002, 8080,
9999, 15002, 15500, 26101` on all addresses; `7678, 8187, 9119, 9197` on the LAN
address; plus several ephemeral high ports that change per boot.

| Port | Service |
|---|---|
| 8001 | REST API + WebSocket. `GET /api/v2/` is unauthenticated; the control channel needs on-TV pairing (`ms.channel.unauthorized`) (unverified, 2026-10-21) |
| 8002 | TLS variant of 8001 (unverified) |
| 26101 | SDB, only while developer mode is on |
| 9197 | UPnP MediaRenderer (unverified) |
| 8080 | web server, 403 on probed paths (unverified, 2026-10-21) |
| 7678, 8187, 9119 | Samsung UPnP / screen sharing / DIAL (unverified) |
| 9080 | NRDP status (`status=ok`) when it is open; closed on 2026-10-24 (unverified which app opens it) |

Closed from the LAN on 2026-10-24: `8000, 1900/tcp, 2869, 7676, 9090, 52235,
55000`.

## Power

`db/boot/instant_on = 1` and `/sys/power/state` = `freeze mem`: the remote's
power button suspends the kernel; it does not shut down. Processes (including
the root daemon) survive, and boot hooks do not run on wake. Details and the
wake-detection method: `docs/01-root/access-contexts.md`.

A real boot happens only after power loss or a reboot. Signals:

| Path | Meaning |
|---|---|
| `/proc/uptime` | seconds since boot; not reset by suspend (it pauses while suspended) |
| `/opt/pwr_cnt` | cumulative power counter (134 on 2026-10-24) |
| `/opt/pwr_on_time` | cumulative on-time (unverified meaning) |
| `/opt/fl_logo_time` | boot logo timing (unverified meaning) |

The wall clock reads 1969 at boot until time sync.

`db/menu/auto_power_off = 6` (meaning of the value unverified).

## Developer mode (`12345`)

Developer mode is what exposes SDB on 26101, the starting point of every
access path (`docs/01-root/access-contexts.md`).

1. Smart Hub → **Apps**
2. Focus **App Settings**
3. Press `12345` on the remote
4. Set **Developer mode** = On, **Host PC IP** = the controller's address
5. Reboot

The digits must arrive as discrete number keys; a Samsung solar/One Remote
may not deliver them (a `BN59-01199F` IR remote is the usual workaround,
unverified here).

Current setting (2026-10-24): `developerMode: "1"`, `developerIP`: this
computer's address.

```bash
curl -s http://$CF_TV_IP:8001/api/v2/ | jq '.device | {modelName, developerMode, developerIP, PowerState}'
```

Developer mode alone gives no root shell: interactive `sdb shell` printed
`closed`, `sdb shell 0` runs only an allowlist of vendor commands, and
`sdb root on` returns `Permission denied` (all unverified since 2026-10-20).
The host-IP binding is the only access control on SDB; anyone who can use that
IP on the LAN can reach it.

Developer mode did not cut internet access on this TV (it reportedly does on
some other models).

## Re-checking access

```bash
ping -c2 $CF_TV_IP
curl -s http://$CF_TV_IP:8001/api/v2/ | jq '.device.developerMode'   # "0" → redo the 12345 dialog
nc -z -w2 $CF_TV_IP 26101 && echo sdb-reachable
tv/root.py probe                                                      # which root path works
```

Root paths and their speed: `docs/01-root/access-contexts.md`.
