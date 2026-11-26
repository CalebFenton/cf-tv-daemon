# Root on the panel: the mechanism, and how to check for it

2026-11-26. This is the answer to "I want root", assembled from the TV root
work that is *newer than the fridge panel*, not older.

## 1. The mechanism that matters

Every Samsung Tizen TV in this ecosystem, back to Tizen 6.0 and forward to
Tizen 9 (2024+), has a vendor systemd unit:

```ini
# AT-HUB.service
[Service]
ExecStart=/opt/etc/parseinfo.sh &
WantedBy=starter.target
```

Three facts about it, each verified by other people on real hardware:

1. **It runs as root with no `User=` line**, and it is started at boot through
   `/usr/lib/systemd/system/starter.target.wants/AT-HUB.service`. `systemctl
   is-enabled` reports `disabled` while the vendor dependency still starts it.
   (chris-ritsen issue #17, Tizen 6.0, 2026-10-25.)
2. **`/opt/etc/parseinfo.sh` lives on a writable partition** (`/opt`, which is
   `vdfs rw`).
3. **`gawk` is not hooked by UEP** — `#!/usr/bin/gawk -f` scripts run, and
   `gawk`'s `system()` executes shell commands without going through the shell's
   checked script-loading path.

Together that is: replace one script on a writable partition, get root commands
at every boot, with no exploit.

This is exactly what `payload/parseinfo.awk` in this repo already does on the
QN85D and CU7000.

## 2. The second mechanism, which is new to me

From the same issue, a **read-only VDFS bind defeats UEP for native binaries**:

```sh
mkdir -p /opt/persist /opt/persist-ro
mount --bind /opt/persist /opt/persist-ro
mount -o remount,ro,bind /opt/persist-ro
```

On that build `SfdUepCheckRWVDFS()` accepts a file reached through a read-only
mount whose filesystem type is VDFS, so an **unsigned static ARM ELF runs**. They
ran an unsigned static ELF and an unsigned `toybox` PIE through that view after a
reboot. Direct execution from `/tmp` was denied.

That matters because it means **unsigned native code**, not just scripts. For
this repo it would remove the "awk only" constraint entirely.

Trap recorded by them: UEP caches by device/inode and records the mount's
writable state. Executing an inode through its writable path first can make
execution through the read-only view fail afterwards. Stage a fresh inode and
execute only through `/opt/persist-ro`.

## 3. Why the fridge is plausible, not proven

The claim to check is whether the panel has the same `AT-HUB.service` →
`/opt/etc/parseinfo.sh` chain.

Evidence for:

- The fridge's partition table is the TV shape: `p17 csa`, `p20 opt` (219 MB),
  `p21 usr` (2.9 GB) — measured from its own `eMMC Information` screen.
- Its OS is Tizen 7.2.1 on the same One UI code line as 2024 TVs.
- Other people have hit the same chain on Tizen 6.0 (2021), 6.5 (2022) and
  Tizen 9 (2024+), so it is not a one-generation quirk.
- Someone has already built a boot hook that **"replaces a vendor startup script
  to launch the server in a private read-only VDFS mount, while keeping the
  stock systemd unit intact"** on a Tizen 6.0 Q70A — `ismkdc/atv-core-tizen`.
  That is the whole technique, packaged, for a consumer Samsung panel.

Evidence against, or at least caution:

- **One documented failure.** `OsloSwede88/frame-privacy/docs/findings.md`
  reports that on a **2022 Frame, firmware 1720.7, Tizen 6.5.0**, UEP *did*
  block the unsigned `parseinfo.sh` with `[uep][bash] the file is NOT signed!!`,
  so the boot-hook method failed there. It works on other firmwares. So this is
  firmware-dependent, and the fridge is a different firmware again.
- Nobody has reported trying it on a Family Hub.

## 4. How to check, without root

The whole question is whether two paths exist and are what they are:

```text
/usr/lib/systemd/system/starter.target.wants/AT-HUB.service
/opt/etc/parseinfo.sh
```

The panel has no shell, so this has to be read some other way. Options, cheapest
first:

1. **`eMMC Information`** proved the partitions exist. Any other Developer Mode
   screen that lists running services or units would answer it directly —
   `KPI Logger (SSM) Server` and `Dump History Tracer Log` are the candidates.
2. **The Web View** cannot read `file://`, so it cannot list these.
3. **A firmware image** would answer it statically, but the fridge's archive is
   not publicly obtainable.
4. **Physical access** answers it definitively and is the only route with
   published precedent on a fridge panel.

## 5. Honest summary

There is a well-established root mechanism on Samsung Tizen panels — four
independent groups have used it across 2021–2026 hardware — and it depends on a
vendor service and a writable `/opt` that the fridge provably has.

Whether the fridge runs that exact service, and whether its UEP build blocks the
script the way one 2022 Frame firmware did, is **unmeasured**. It is not
"impossible", and I should not have been treating it that way.
