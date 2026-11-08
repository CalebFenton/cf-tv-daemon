# `/opt/masklist` — boot-time runtime unit masks

A list of unit names on `/opt` that the boot payload masks and stops on every
boot. For services that have a vendor `ConditionPathExists=!` marker, use the
marker instead (`docs/04-system/debloat.md`); the masklist is for units
without one.

## Current state (verified 2026-10-24)

```
wc -c /opt/masklist          -> 0          (empty)
ls -la /run/systemd/system/  -> empty
```

Nothing is masked. The repo copy `payload/masklist` is also empty.

## Mechanism (`payload/parseinfo.awk`, deployed as `/opt/etc/parseinfo.sh`)

`AT-HUB.service` runs `/opt/etc/parseinfo.sh` as root, `System::Privileged`,
early in every real boot (monotonic ~9–10 s). It:

1. reads `/opt/masklist`, one name per line, skipping empty lines;
2. for each name runs `ln -sf /dev/null /run/systemd/system/<name>.service`;
3. runs `systemctl daemon-reload` if any line was read;
4. reads the list again and runs `systemctl stop <name>.service`.

This equals `systemctl mask --runtime <name>.service` plus `systemctl stop`.
Then it runs `/opt/cmdq` and starts the root daemon (`payload/README.md`).

Consequences:

- **The masks persist across reboots** because the list on `/opt` is
  re-applied at every boot. Rebooting alone does not undo a mask; emptying
  the list does.
- The symlinks live in tmpfs `/run`, so the system image and `/opt` unit
  files are never modified.
- Units that started before AT-HUB run briefly, then get stopped.
- Kernel suspend ("TV off" on the remote) does not re-run the payload; edits to
  the list take effect at the next real boot unless applied by hand.
- Each line becomes `<line>.service` verbatim. Only bare unit names (no
  `.service` suffix, no comments, no whitespace): a `# comment` line produced
  `ln: target 'probe.service' is not a directory`.
- Only `.service` units can be masked this way (`.socket`, `.path`, and
  `.busname` units cannot).

## Use

Mask (as root, via `tv/chan.py run` or `tv/root.py exec`; the list is
`root:root` 0644, `docs/06-security/threat-model.md` §2):

```sh
echo <unit-name> >> /opt/masklist                       # persists
ln -sf /dev/null /run/systemd/system/<unit-name>.service # apply now, same as boot
systemctl daemon-reload
systemctl stop <unit-name>.service
systemctl is-active <unit-name>.service                  # -> inactive
```

Unmask:

```sh
grep -vx '<unit-name>' /opt/masklist > /tmp/ml; cat /tmp/ml > /opt/masklist
rm -f /run/systemd/system/<unit-name>.service
systemctl daemon-reload
systemctl start <unit-name>.service
```

## Never mask

- `sdbd` — every root path starts with an SDB injection
  (`docs/01-root/access-contexts.md`). Because the mask is re-applied each boot,
  masking it would cut remote access permanently, not until the next power
  cycle. Recovery would need a way to edit `/opt` without SDB, and none is
  known.
- `deviced`, D-Bus/systemd core units, `AT-HUB`, the network stack, and the
  input/launcher services (`launchpad-process-pool`, etc.). A unit that breaks
  boot stays broken on every boot for the same reason.

Mask one unit at a time and check the TV (picture, apps, `sdb` reachable,
`tv/root.py probe`) before the next.

## Removed: the `masker.awk` copy

Until 2026-10-25 the TV had a second copy of the masklist code at
`/opt/masker.awk` and in place of the vendor
`/opt/usr/apps/com.samsung.tv.pisa-service/bin/launch_script.sh`, which
`adagent-service.service` runs. It never ran: `adagent-service` has
`ConditionPathExists=!/opt/usr/share/systemd/system/pisa-service` and that
marker is present. Both were removed and the vendor script restored (md5
`017617cb08b5931f85f4424e16f76617`, owner `tizenglobalapp`, mode 755; copy in
`archive/originals/`). The masklist is applied only by `parseinfo.sh`.
