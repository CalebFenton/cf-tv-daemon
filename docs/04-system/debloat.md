# Debloat — what is disabled, how, and how to undo it

Ad, ACR/context, voice and "smart extras" components were disabled on
2026-10-22 with three vendor mechanisms (stop-markers, the app registry, vconf
preferences) plus one WAS cache edit. Since 2026-11-25 the `40-debloat` hook
keeps the markers, apps and preferences applied (§ Drift and the hook).
Method and app groups follow [jss8649/quiet-tizen](https://github.com/jss8649/quiet-tizen)
(UN75U8000FFXZA, same access route), restricted to IDs in its profile
`profiles/un75u8000ffxza-1300.9.json`.

Not disabled: `contents-recognition-service` (Samsung's ACR frame grabber) is
still running; it has no marker and no registry entry. See
`docs/04-system/acr-telemetry.md`.

## Drift and the hook (2026-11-25)

Found on 2026-11-25: Samsung had undone part of it. The `pisa-service` marker
was gone, `com.samsung.tv.pisa-service`, `com.samsung.tv.adagent-service` and
`org.tizen.pisa-control-service` were enabled again (`app_disable='false'`,
36 of 39 left), and `pisa-service`/`adagent-service` were running
(`pisa-service` since 2026-11-23 19:02). The same pattern as a vendor package
update on the CU7000 (`docs/04-system/debloat-cu7000.md` §B). Everything else
below still held.

`payload/debloat/qn85d.list` now lists the 9 markers, the 39 app ids (identical
to the ids in the two on-TV restore scripts) and the 3 preferences;
`payload/on-boot.d/40-debloat` re-applies them on boot, at uptime 300 s and on
every wake (`payload/README.md`). Its first run re-created the marker, stopped
both units and disabled the 3 apps (39 of 39). The hook disables with
`pkgcmd -D`, which sets `app_disable` only; the 3 re-disabled apps keep
`app_onboot`/`app_autorestart` as the update left them.

That first run also disabled `org.tizen.stt-client`, never part of this
debloat: the hook then matched each id as an app id *or* a package name, and
`org.tizen.voice-client` is both. It was re-enabled with `pkgcmd -A` (flags
back to the baseline `false|false|false`), and the list format now separates
`app ID` (exactly that app) from `pkg ID` (the whole package).

## Current state (verified 2026-10-24 unless marked)

### Stop-markers: 9 files in `/opt/usr/share/systemd/system/`

| Marker | Unit(s) blocked | State |
|---|---|---|
| `canalysis-daemon` | `canalysis-service` | inactive, `ConditionResult=no` |
| `pisa-service` | `pisa-service`, `adagent-service` | inactive, `no` |
| `com.samsung.tv.context-aware-agent` | same name `.service` | inactive, `no` |
| `com.samsung.tv.context-aware-service` | same name `.service` | inactive, `no` |
| `com.samsung.tv.ghdaemon` | same name `.service` | inactive, `no` |
| `com.samsung.tv.ondevice-voice` | same name `.service` | inactive, `no` |
| `org.tizen.voice-app` | same name `.service` | inactive, `no` |
| `samsunghealth-service-tv` | `samsunghealth-service-tv{,-dbus-activation,-path-activation}` | `samsunghealth-service-tv` inactive, `no` |
| `slive-provider-daemon` | same name `.service` | inactive, `no` |

`canalysis-service`, `pisa-service`, `adagent-service` and both
`context-aware` units have not started since boot (`ActiveEnterTimestamp`
empty); the voice/health/slive units were checked for state only.

### Registry: 39 apps with `app_disable='true', app_onboot='false', app_autorestart='false'`

The count (39) was last read 2026-10-22; the ID lists below come from the
on-TV restore scripts (read 2026-10-24).

| Batch | Restore script | Apps |
|---|---|---|
| voice (18) | `/opt/usr/share/tv-backups/voice-01/restore.sh` | `com.samsung.tv.alexa-client`, `-alexa-fullscreen-app`, `-bixby-context-client`, `-bixby-prov`, `-bixbycapsuleviewer`, `-ghdaemon`, `-mybixby`, `-ondevice-voice`, `-utterance-recommender-app`, `-utterance-recommender-core`, `-vif`, `-voiceassistant-music`, `-wakeup-engine-alexa`, `-wakeup-engine-bixby`; `org.tizen.voice-app`, `-voice-app-service`, `-voice-client`, `-voice-onboarding` |
| advertising (10) | `/opt/usr/share/tv-backups/debloat-02/restore.sh` | `com.samsung.tizen.samsung-analytics`, `com.samsung.tv.acr`, `com.samsung.tv.canalysis-service`, `com.samsung.tv.contents-recognition-dbmgr`, `com.samsung.tv.adagent-service`, `com.samsung.tv.pisa-service`, `org.tizen.pisa-control-service`, `com.samsung.tv.targeted-ad-service`, `yL49PNFmjW.PromotionApp`, `yL49PNFmjW.service` |
| context (6) | same | `com.samsung.tv.context-aware-agent`, `-agent-toast-view`, `-grab-control`, `-server-requester`, `-service`, `content-grab-service` |
| smart-extras (5) | same | `com.samsung.tizen.smartthings-client`, `com.samsung.tv.adplayer`, `com.samsung.tv.aov-dashboard`, `com.samsung.tv.ghservice`, `samsunghealth-service-tv` |

`acr` and `targeted-ad-service` processes are absent (`ps`, 2026-10-24).
A process named `smartthings-thi…` is running; it is not
`smartthings-client` (unverified which app).

### Preferences (vconf, `buxton2ctl get system <key>`)

| Key | Value now | Stock |
|---|---|---|
| `db/menu/autorun_tv_plus` | `False` | `True` |
| `db/si_service/voice_recog_enable` | `False` | `True` |
| `db/multi-assistant/prelaunch_mode` | `False` | `True` |

Three other quiet-tizen keys do not exist on this build:
`db/was/preload_by_country`, `db/menu/voice/voice_activated`,
`db/menu/voice/wakeupless_status`.

### WAS preload cache

`WASConfigTable` row `RESERVED_18`, JSON path `$.rsp.preloadByCountry`:
`"true"` → `"false"` (a JSON string, not a boolean);
`$.rsp.removableByCountry` left `"true"`. Set 2026-10-22, survived a reboot;
not re-checked since. Samsung catalog refreshes restore it (per quiet-tizen).

### Failed units

`systemctl --failed` on 2026-10-24: `sys-kernel-debug-tracing.mount`,
`com.samsung.tv.airplay-service-2`, `com.samsung.tv.aov-service` (exits 123
because `db/multi-assistant/aov_mode=0`), `com.samsung.tv.knox-browser`
(`/usr/bin/false`), `motion-sensor-service` (`/bin/true`),
`system-default-target-done`, `system-delayed-target-done`, `vdca-update`. All
eight failed before any debloat change. New since then:
`media-analytics-service` (bus-activated, ran from boot, exited 1 at 09:00:46;
cause unknown, not in the disabled set).

## Mechanisms

Systemd and the app framework are independent layers. Markers reach units;
registry flags reach apps launched by `launchpad`/app framework. Neither
reaches the other's objects.

### 1. Stop-marker (`ConditionPathExists=!`)

Many vendor units carry `ConditionPathExists=!/opt/usr/share/systemd/system/<name>`.
If the file exists, systemd skips the unit at boot and refuses manual starts.
The directory is on `/opt` (persistent). Units with such a guard on this TV
(`/usr/lib/systemd/system`, 2026-10-24):

`adagent-service` (shares `pisa-service`), `bms` (plus two other conditions),
`canalysis-service`, `com.samsung.tv.airplay-service`,
`com.samsung.tv.airplay-service-2`, `com.samsung.tv.context-aware-agent`,
`com.samsung.tv.context-aware-service`, `com.samsung.tv.farfieldvoice`,
`com.samsung.tv.ghdaemon`, `com.samsung.tv.ondevice-voice`,
`org.tizen.voice-app`, `pisa-service`, `samsunghealth-service-tv` (three
units), `slive-provider-daemon`, `vision-data-recognition-service`.
(`org.tizen.art-app` has the line commented out.)

Apply:

```sh
grep -Fqx 'ConditionPathExists=!/opt/usr/share/systemd/system/<marker>' /usr/lib/systemd/system/<unit>.service
touch /opt/usr/share/systemd/system/<marker>
systemctl stop <unit>.service        # the marker alone does not stop a running instance
```

Revert:

```sh
rm /opt/usr/share/systemd/system/<marker>
systemctl reset-failed <unit>.service
systemctl start <unit>.service
```

Observed behaviour (2026-10-22):
- `canalysis-service` ignored SIGTERM and sat in `stop-sigterm` for
  `TimeoutStopUSec=1min 30s` before systemd SIGKILLed it (`Result=timeout`).
- After reboot, guarded units never start (`ActiveEnterTimestamp` empty).
- `pisa-service.path` (`PathChanged=/opt/usr/share/systemd/system/pisa-service`)
  is active, so creating or deleting that marker triggers a start attempt; the
  condition still blocks it.

### 2. App registry (`/opt/dbspace/.pkgmgr_parser.db`)

Table `package_app_info`, fields `app_disable`, `app_onboot`,
`app_autorestart`. `app_disable='true'` overrides `app_preload='true'` (held
across two reboots). `app_onboot` alone is not enough: `acr` ran with
`app_onboot=false`.

The DB is 2,412,544 B, owner `app_fw`, mode 0664, with a `-journal` file
beside it (rollback-journal mode, unverified). The registry is live: snapshot
with `sqlite3 <db> ".backup '<path>'"`, not `cp`, then `PRAGMA quick_check`.

Plan changes with `tools/plan_cleanup.py` (port of quiet-tizen's planner):

```sh
python3 tools/plan_cleanup.py --snapshot <local copy of .pkgmgr_parser.db> \
    --apps <file with one app_id per line> --output <new dir>
```

It writes `apply.sql`/`apply.sh`, `restore.sql`/`restore.sh` and
`before.json`. Each transaction is `BEGIN IMMEDIATE` with a temp
`guard(ok CHECK(ok=1))` table holding one row per app that must match the
expected prior values; any drift aborts the whole transaction (tested on a
local copy: tampered `app_onboot` → `CHECK constraint failed`, nothing
changed).

Changing a row does not kill a running process; stop it or reboot.

### 3. vconf preferences

Set with `vconftool set -f -t bool <key> 0|1` (`/usr/bin/vconftool` present);
read with `buxton2ctl get system <key>`.

### 4. WAS preload cache

`.webappservice.db` → `WASConfigTable.RESERVED_18`. Edit guarded on
`json_type(...)='text'`. Not durable against vendor refreshes.

### 5. Runtime masks

For units without a marker: `/opt/masklist`, see
`docs/04-system/masking-daemons.md`. Currently empty.

## Rollback

Snapshots on the TV, `/opt/usr/share/tv-backups/`:

| Dir | Contents | Taken |
|---|---|---|
| `baseline-01/` | `.pkgmgr_parser.db` (2,416,640 B) | 2026-10-22 08:13, before any change |
| `voice-01/` | `restore.sh` (6,214 B) | 08:59, for the 18 voice apps |
| `debloat-02/` | `.pkgmgr_parser.db`, `.webappservice.db`, `restore.sh` (7,314 B) | 10:11–10:13, before the 21-app batch, prefs and WAS |

(`hello-01/`, `hello-02/` hold app DLL backups unrelated to debloat.)

```sh
# registry, newest batch first. A .sh file run as a script may be refused by
# UEP's bash hook, so pass the text instead (unverified which form works):
sh -c "$(cat /opt/usr/share/tv-backups/debloat-02/restore.sh)"
sh -c "$(cat /opt/usr/share/tv-backups/voice-01/restore.sh)"
# preferences
vconftool set -f -t bool db/menu/autorun_tv_plus 1
vconftool set -f -t bool db/si_service/voice_recog_enable 1
vconftool set -f -t bool db/multi-assistant/prelaunch_mode 1
# WAS: copy RESERVED_18 back from debloat-02/.webappservice.db
# markers: rm the files listed above, then start the units
```

The restore scripts check `quick_check`, guard every row, and only touch the
registry.

## Deliberately not done

| Item | Reason |
|---|---|
| quiet-tizen `light-shell` (`com.samsung.tv.home.sidebar`, `org.tizen.browser`), `tv-plus` (`HEPsqFNie0.tvplusstandalone`) | absent on this model |
| Quiet Home launcher, QuickLaunch bridge | replaces a shipped launcher; separate risk class. `XF86More` here routes to `org.tizen.function-osd` |
| `adplatform.ADPlayer`, `30IKH0OX8U.*` | marked do-not-touch infrastructure by [ardazeytin/samsung-tizen-tv-debloat](https://github.com/ardazeytin/samsung-tizen-tv-debloat) |
| `org.tizen.netflixlowmem` | low-memory fallback; removing it can stop Netflix launching (same source) |
| Uninstalling packages | can remove the launcher; recovery only by factory reset or USB reflash (same source). `sdb uninstall` returns 0 but does nothing (`docs/07-prior-art/tiz0wn.md`) |
| quiet-tizen runtime masks (`ipa`, `sp-kpi`, …) | `ipa` has no unit here; the others have no vendor marker (would need `/opt/masklist`) (2026-10-22) |
| `launchpad-process-pool` | **never touch:** it starts every app; its unit has `ConditionPathExists=!/proc/tztv-bootmode` (verified 2026-10-24) |
| `org.tizen.menu` disabled globally | the settings menu; quiet-tizen's own rule |
| restoring an old copy of a live registry DB | roll back individual fields instead; a whole-DB swap loses everything the TV changed since |

## Caveats

- Memory: `MemAvailable` readings were single `/proc/meminfo` samples at
  different uptimes (690 MB before, ~844–878 MB after, settled); no controlled
  benchmark. Stopping `canalysis-daemon` alone freed nothing measurable
  (4.6 MB RSS).
- ACR capture can also happen upstream of these apps; disabling `acr` and
  `canalysis` stops these uploaders, not necessarily every capture path
  (unverified).
- Vendor updates or catalog refreshes can restore preferences and WAS.
- Nothing here demonstrates zero telemetry.

## Data files

- `docs/04-system/tv-app-inventory.csv`: registry dump taken before any
  change (2026-10-22). No header; columns `app_id, app_exec, app_onboot,
  app_autorestart, app_preload, app_process_pool, app_package_system,
  app_removable`. Pre-debloat values; use it to see stock flags.
- `docs/04-system/process-list.txt`: `ps` (`PID COMMAND`) from before the
  debloat (shows `canalysis-daemon`, `pisa-service`, `targeted-ad-service`,
  `context-aware-*` running).
