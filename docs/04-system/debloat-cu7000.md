# CU7000 debloat survey (`UN55CU7000DXZA`, `T-KSU2ECAKUC-0090-2230.0`, 192.0.2.185)

Research only, 2026-11-24, uptime ~35–60 min. Nothing was disabled, killed, masked or
uninstalled. All probes ran as root through `tv/root.py exec`.

Tags: **[O]** observed on this TV today. **[Q]** observed on the QN85D (repo docs), not
re-tested here. **[I]** inferred, not tested.

Disclosure: two probe writes landed outside the staging dir. A `touch /etc/.wtest` failed
with EROFS. A `touch` + `rm` of `/opt/usr/share/systemd/system/.wt` left the directory empty
again. Throwaway awk scripts went to `/home/owner/share/tmp/sdk_tools/` and were removed.
Two `tcpdump` runs wrote `/tmp/*.cap` on the TV (tmpfs), which were also removed.

## 0. Baseline facts

| Fact | Value | Tag |
|---|---|---|
| RAM | 1578 MB total, **~495 MB available**. zram swap 1025 MB, **~720 MB used** | [O] |
| Sum of PSS over all processes | 637 MB PSS + 728 MB swapped | [O] |
| CPU | 4 cores, loadavg ~30, ~55 % busy over 5 s | [O] |
| Packages | 438 packages, 508 app records. 285 preload, 208 removable, 234 `package_system` | [O] |
| App locations | 244 apps in `/usr/apps` (read-only rootfs, `removable=false`). 128 preload apps in `/opt/usr/apps` (`removable=true`). 136 non-preload apps in `/opt/usr/apps` (store/"builtin" downloads) | [O] |
| Registry disables in effect | **none**: 0 rows `app_disable='true'`; `package_app_info_for_uid` empty | [O] |
| Stop-markers / masks in effect | none: `/opt/usr/share/systemd/system/` is empty, `/opt/masklist` is 0 bytes, `/run/systemd/system/` holds only `*.target.done` | [O] |
| Vendor privacy consent | `db/homedatacontrol/disclaimeragrees`: `viewing:false`, `visionAI:false`, `syncplus:false`, `rm:false`, `pp:true`, `tnc:true`. `db/setting/ad_id_opt_out=1` | [O] |
| Firmware auto-update | **off** (`db/menu/support/softwareupdate/autoupdate=0`). Last update 2026-08-08, 2211.5 → 2230.0 | [O] |
| App auto-update | **on** (`db/apps/auto_update=True`, `App_Game_Auto_Update=ON`). The store updated packages on 11-13, 11-18 and 11-24 (`pisa-service` 11-13, `samsung-analytics` 11-18, Tubi 11-24) | [O] |
| Current source | `memory/eden/source/current_source` = `tv-service`/`SOURCE_TYPE_TV`, **not HDMI** | [O] |

So the vendor menu has already declined Viewing Information (ACR) and limited the ad ID.
The ACR, ad and context daemons run anyway; whether they upload anything with consent off
was not measured.

## 1. What costs the most right now

| What | Cost | Tag |
|---|---|---|
| **`bms-service`** (TV Plus / AOC HLS player, `com.samsung.tv.slive-daemon`) | **~110 % CPU** (1101 ticks in 10 s, more than any other process). Threads `HLSEngine`, `HLS:Curl`, `cmaf_demux`, `tz_video_appsrc`, `omxaudiodec`. eth0 RX ~5 Mbit/s. Connected to Akamai `23.47.51.212:443`. 32 MB | [O] |
| WAS "backgroundAtStartup" preloads launched by `org.tizen.litewebappservice` | Prime Video (`aria-engine`) 85 MB, YouTube (`cobalt`) 82 MB, Netflix 62 MB, all `launch_mode=backgroundAtStartup`. Samsung TV Plus web app (`HEPsqFNie0`, via `wrt`) 80 MB. **~310 MB PSS+swap together** | [O] |
| `slive-provider-daemon` | 57 MB (46 MB swapped) | [O] |
| ad stack: `adagent-service` 27 MB, `pisa-service` 3 MB, `targeted-ad-service` 4 MB | idle | [O] |
| voice stack, 11 processes | ~50 MB | [O] |

Most other Samsung daemons are 1–4 MB and mostly swapped out. Each is cheap; they matter
for privacy more than for RAM.

Open: the TV is on the TV source and `bms` is decoding a live HLS stream. Is someone
watching Samsung TV Plus, or is it auto-playing (for example the Home preview, or
`autorun_tv_plus=True`)? I did not capture the screen.

## 2. Inventory: top candidates

PSS+swap in MB [O]. "launched by" comes from `/proc/<pid>/cgroup` [O]: `system.slice/<unit>`
means systemd, `init.scope` means amd/launchpad. "on/ar" are the registry flags
`app_onboot`/`app_autorestart` [O].

| # | id (app / unit) | what it is | running? (launched by) | MB | rec | mechanism | conf |
|---|---|---|---|---|---|---|---|
| 1 | `bms.service` (`org.tizen.bms-service`, pkg `com.samsung.tv.slive-daemon`) | TV Plus / AOC virtual-channel streaming engine (strings `TCBmsStrategyTVPlus`, `PopAdData`) | yes, systemd, 110 % CPU | 32 | disable if TV Plus is unwanted | marker `bms` | med |
| 2 | `slive-provider-daemon.service` (same pkg) | TV Plus provider, D-Bus `com.samsung.FCCallListener` | yes, systemd | 57 | disable with #1 | marker `slive-provider-daemon` | med |
| 3 | `HEPsqFNie0.tvplusstandalone` | Samsung TV Plus web app (store app, `removable=1`) | yes, wrt | 80 | disable autorun; remove if unused | vconf `autorun_tv_plus=0`; Smart Hub delete or registry | high |
| 4 | `org.tizen.primevideo` (runs `aria-engine`) | Prime Video, preloaded in background. DNS shows `*.minerva.devices.a2z.com`, `telemetry.insights.video.a2z.com` | yes, amd (bg) | 85 | stop preload; remove if unused | WAS preload off; Smart Hub delete | high |
| 5 | `com.samsung.tv.cobalt-yt` | YouTube, preloaded in background (`use_count` 26, the most used app) | yes, amd (bg) | 82 | keep app, stop preload | WAS preload off | med |
| 6 | `org.tizen.netflix-app` | Netflix, preloaded (`nrdp.logs.netflix.com`) | yes, amd (bg) | 62 | keep app, stop preload | WAS preload off | med |
| 7 | `adagent-service.service` (pkg `com.samsung.tv.pisa-service`) | ad agent. Listens on 127.0.0.1:19201-19203. QN85D binary contains `rtb-config.samsungads.com` [Q] | yes, systemd | 27 | **disable** | marker `pisa-service` + registry | high |
| 8 | `pisa-service.service` / `org.tizen.pisa-control-service` | PISA ad/personalisation service; 127.0.0.1:19152/19154 | yes, systemd | 3 | **disable** | marker `pisa-service` + registry | high |
| 9 | `com.samsung.tv.targeted-ad-service` | targeted ads (on=true, ar=true) | yes, amd | 4 | **disable** | registry | high |
| 10 | `com.samsung.tv.acr` (pkg `acr-service-app`) | ACR uploader (`ACRService`) | yes, amd (ar=true) | 4 | **disable** | registry | high |
| 11 | `canalysis-service.service` | screen-content CNN logo detector, captures HDMI too [Q] | yes, systemd | 2 | **disable** | marker `canalysis-daemon` + registry | high |
| 12 | `contents-recognition-service.service` | ACR frame grabber (`libvideo-capture`); `Restart=always`, no marker | yes, systemd | 12 | disable | `/opt/masklist` | med |
| 13 | `mirroring-recognition-manager.service` | content recognition for mirroring; `Restart=always`, no marker | yes, systemd | 2 | disable (test mirroring afterwards) | masklist | low-med |
| 14 | `com.samsung.tv.context-aware-service` / `-agent` (+ `-toast-view`, `-grab-control`, `-server-requester`) | AI/context suggestions, content grab | yes, systemd | 6 | **disable** | markers (same names) + registry | high |
| 15 | `content-grab-service`, `com.samsung.tv.iacr`, `com.samsung.tv.contents-recognition-dbmgr` | capture / ACR helpers. `content-grab-service` is in `/usr/apps` (sys, ro) | no | 0 | disable | registry | high |
| 16 | `com.samsung.tizen.samsung-analytics` | "SES (Samsung Analytics)"; store-updated 11-18 | no | 0 | disable | registry | high |
| 17 | `org.tizen.tv.sp-kpi.service` | KPI performance reporting | yes, systemd, ~1.5 % CPU | 6 | disable | masklist | med |
| 18 | `devicetracker` + `abnormal_user_behavior` + `issue_report_agent` | usage / issue reporting. The first two `Requires=issue_report_agent` | yes, systemd | 3+2+4 | disable all three together | masklist | med |
| 19 | `emergency-log-transmitter.service` | log uploader; binary has `fc.samsungcloud.tv`, `www.samsungrm.net` | yes, systemd | 2 | disable | masklist | med |
| 20 | `org.tizen.tv.tagpro-service-tv` | system profiling/diagnostics collector (CPU iowait, temperature, USB dumps) | yes, systemd | 12 (10 PSS) | disable | masklist | med |
| 21 | `scs-agent.service` (+ `/usr/apps/scs-agent/SCSClient`) | Samsung cloud agent; DNS `scs.samsungqbe.com` | yes, systemd | 3 | probably disable | masklist | low |
| 22 | `data-service.service` (`org.tizen.tv.logger` bus) | logging bus service. Connected to GCP `34.160.212.185:443`. Reverse dependency: `context-aware-service` | yes, systemd | 7 | maybe, after #14 | masklist | low (other callers of the logger bus are unknown) |
| 23 | `org.tizen.voice-app` (+ `-service`) | voice UI, kept warm (`Preloading true`) | yes, amd | 12 | disable if voice unused | vconf voice + registry + marker `org.tizen.voice-app` | high |
| 24 | `org.tizen.voice-client`, `org.tizen.stt-client` | STT client (ar=true) | yes, amd | 6 | same | registry + marker `org.tizen.voice-client` | high |
| 25 | `com.samsung.tv.vif` | voice interaction framework (on, ar) | yes, amd | 4 | same | registry | high |
| 26 | `com.samsung.tv.bixby-prov`, `com.samsung.tv.bixbycapsuleviewer`, `com.samsung.tv.mybixby` | Bixby | yes, amd | 4+7 | same | registry + marker `com.samsung.tv.bixby-prov` | high |
| 27 | `com.samsung.tv.ondevice-voice` | on-device voice (on, ar). Its unit is `failed` but the app runs from amd | yes, amd | 2 | same | registry + marker | high |
| 28 | `com.samsung.tv.ghdaemon` / `ghservice` | voice-adjacent Google/game helper. QN85D batch put it under voice | yes, amd | 5 | same | registry + marker `com.samsung.tv.ghdaemon` | med |
| 29 | `com.samsung.tv.utterance-recommender-core` / `-app` | voice suggestions (on=true) | yes, amd | 6 | same | registry | high |
| 30 | `org.tizen.multi-assistant-service`, `com.samsung.tv.alexa-*`, `wakeup-engine-{alexa,bixby}`, `farfieldvoice`, `voiceassistant-music`, `voice-instant-app` | assistant arbitration, Alexa, wake words | multi-assistant yes (3); the rest no | 3 | same | registry. Keep `org.tizen.tts-engine-neural` if Voice Guide (accessibility) is used | med |
| 31 | `com.samsung.tv.aov-dashboard` | in quiet-tizen's smart-extras group. `aov-service` exits because `aov_mode=0` [Q] | yes, amd | 18 | disable | registry | med |
| 32 | `com.samsung.tizen.smartthings-client`, `-thing`, `com.samsung.tv.smartthingsfind-daemon`, `iot-hub-control`, `tvsensor-iot-service` | SmartThings device, Find, IoT hub, sensors | yes, amd | 3+3+2+3+4 | disable if SmartThings is unused | registry | med (breaks control from the SmartThings phone app) |
| 33 | `com.samsung.tv.health-mde-service`, `samsunghealth-service-tv`, `com.samsung.tv.samsung-health`, `coba.healthview` | Samsung Health. `samsunghealth-service-tv.service` is in an auto-restart loop | health-mde yes | 2 | disable | registry + marker `samsunghealth-service-tv` | high |
| 34 | `com.samsung.tv.buds-scanner` | Galaxy Buds auto-pair scanner (on, ar) | yes, amd | 2 | disable if no Buds | registry | med (keep if a Bluetooth headset pairs this way) |
| 35 | `com.samsung.tizen.samsung-cloud`, `com.samsung.tizen.mcf-agent` | "SES (Samsung Cloud)", "SES (Multi Connectivity Framework)" | yes, amd | 3+3 | probably disable | registry | low |
| 36 | `com.samsung.tv.tap-service-agent` | unknown "tap" agent (on, ar) | yes, amd | 3 | investigate | — | low |
| 37 | `com.samsung.tv.lightweight-node-service` (runs `spotifyd`) | Spotify Connect daemon, port 35707 (on, ar) | yes, amd | 4 | disable if Spotify is unused | registry, or delete Spotify | med |
| 38 | `org.tizen.browser` | Internet app, preloaded at boot (`Preloading`, `FirstLaunch`) | yes, amd | 18 | leave (disabling removes the browser) | — | low |
| 39 | `com.samsung.tv.mde-framework`, `com.samsung.tv.MultiControl-Service` | multi-device experience, Multi Control (keyboard/mouse sharing with Galaxy devices) | yes, amd | 10+9 | disable if not used with a Samsung phone/PC | registry | low-med |
| 40 | unused store apps: Hulu, Tubi, Sling, Disney+, Peacock, QVC+, Angel Studios, ESPN, AMC+, Instagram, Glance, klyq, Amazon Luna, Blacknut, PBS Kids, GameBreaks, YouTube TV, Xbox, Spotify, Apple TV (`aria-video`), Prime Video | `use_count=0` in `.webappservice.db` except where noted | no (Prime Video yes) | disk only | **delete via Smart Hub** if not wanted | vendor UI | high |

Ad/ACR packages present but not running: `adplatform.ADPlayer` (keep: do-not-touch
infrastructure per ardazeytin [Q]), `com.samsung.tv.ad-web-framework`, `com.samsung.tv.coba.adagent`,
`com.samsung.tv.adplayer` (QN85D smart-extras batch). `yL49PNFmjW.*` (PromotionApp on the QN85D)
is **not installed** here [O].

Already failed/inactive here [O]: `media-analytics-service`, `fw-update-service`,
`reporterCA`, `security-emdaemon`, `buds-conn-manager`, `vdca-update`,
`org.tizen.webappservice-api`, `com.samsung.tv.ondevice-voice` (unit).
`vdsystem-update-manager` is in an auto-restart loop.

## 3. Mechanisms

| # | Mechanism | How it works here | Survives reboot | Firmware update | App (store) update | Undo | Risk |
|---|---|---|---|---|---|---|---|
| M0 | **Vendor menus** (no root): Terms & Privacy, "Autorun Samsung TV Plus", voice settings, app auto-update, Smart Hub "Delete" | Settings write vconf/buxton on `/opt`. Viewing info and ad ID are already off [O] | yes [I] | kept [I] | n/a | flip back | none |
| M1 | **vconf preferences** `vconftool set -f -t <type> <key> <val>` | All six quiet-tizen keys exist here [O], unlike the QN85D (which had 3 of 6): `db/menu/autorun_tv_plus` (bool, True), `db/si_service/voice_recog_enable` (True), `db/multi-assistant/prelaunch_mode` (True), `db/was/preload_by_country` (string "true"), `db/menu/voice/voice_activated` (True), `db/menu/voice/wakeupless_status` (int 1) | yes (buxton DB on `/opt`) [Q] | may be reset [I] | Samsung catalog refreshes restored preload flags (quiet-tizen) | set back to the values above | low |
| M2 | **WAS preload cache** `.webappservice.db` → `WASConfigTable.RESERVED_18` `$.rsp.preloadByCountry` | Currently JSON text `"true"` [O]. This drives the `backgroundAtStartup` launches of YouTube/Netflix/Prime/TV Plus [I: link to the launches inferred from the bundle `caller_process org.tizen.litewebappservice` plus the QN85D result] | yes [Q] | ? | vendor refresh restores it [Q] | guarded `json_set` back to "true" | low |
| M3 | **Stop-marker** `touch /opt/usr/share/systemd/system/<marker>` + `systemctl stop` | 19 unit files here carry `ConditionPathExists=!/opt/usr/share/systemd/system/<m>` [O]: `adagent-service`+`pisa-service` (`pisa-service`), `bms` (`bms`), `canalysis-service` (`canalysis-daemon`), `com.samsung.tv.airplay-service-2`, `com.samsung.tv.bixby-prov`, `com.samsung.tv.context-aware-{agent,service}`, `com.samsung.tv.ghdaemon`, `com.samsung.tv.googlecast-service`, `com.samsung.tv.ondevice-voice`, `com.samsung.tv.passthru-service`, `org.tizen.voice-app`, `org.tizen.voice-client`, `samsunghealth-service-tv{,-eventd,-path-activation}`, `slive-provider-daemon`, `tts-download-service` (`org.tizen.tts-engine-neural`). The directory exists, is empty and is root-writable [O] | yes [Q] | unit files live on rootfs and could change [I] | unaffected (units in `/usr/lib/systemd/system`) [I] | `rm` the marker, `systemctl start` | **low**: vendor-designed switch. Reaches systemd units only, not amd-launched apps |
| M4 | **Registry flags** in `/opt/dbspace/.pkgmgr_parser.db` (`app_disable`, `app_onboot`, `app_autorestart`) via `tools/plan_cleanup.py` guarded SQL | Same schema as the QN85D [O]. DB is `app_fw` 0664 with a live `-journal` [O]. `sqlite3` present [O] | yes, held across 2 reboots [Q] | DB migration may reset [I] | **a store update re-registers the package, likely resetting the row** [I]; updates happen weekly here [O] | `restore.sh` from the planner | medium: live DB, `pkgmgr-server`/amd cache it, needs reboot. QN85D: 39 apps, no breakage [Q] |
| M5 | `pkgcmd -D/-A` (deactivate/activate app or pkg, `--global`, `--uid`) | Present per `--help` [O]. Untested. `package_support_disable`/`app_support_disable` are `false` for every app [O]; pkgmgr may refuse or ignore those apps [I]. quiet-tizen: "did not reliably report or retain the intended state" | ? | ? | ? | `pkgcmd -A` | unknown; test on one harmless app first |
| M6 | **`/opt/masklist`** (our boot payload) | `parseinfo.sh` symlinks `/run/systemd/system/<line>.service` → `/dev/null`, runs `daemon-reload`, then `systemctl stop` each line (`payload/parseinfo.awk`). Only `.service` units; bare names; no comments. Same as `systemctl mask --runtime` + stop | yes: re-applied every boot from `/opt` | **lost if `parseinfo.sh` is replaced or `boot restore` runs: the vendor `parseinfo.ORIGINAL.sh` has no masklist code** [O, archive copy] | unaffected | remove the line, `rm` the `/run` symlink, `daemon-reload`, `start` | medium-high: a wrong line (`sdbd`, `msf-server`, `ac`, …) is re-broken on **every** boot. Recovery needs root via exploit or daemon |
| M7 | `systemctl mask` / `disable` (persistent) | `/` and `/etc` are read-only VDFS [O] (`touch /etc/x` → EROFS); WantedBy symlinks live in `/usr/lib` (ro) | — | — | — | — | **not available**. Only the `--runtime` form, in tmpfs `/run` |
| M8 | `pkgcmd -u` uninstall | Present [O]. Untested by design. 244 `/usr/apps` apps are `removable=false`. For updated preloads with a copy in both `/usr/share/packages` and `/opt` (e.g. `csfs`), uninstall would remove the update only [I]. `/opt`-only preloads (e.g. `pisa-service`, `slive-daemon`, `acr-service-app`) have **no rootfs copy**: `/usr/apps/.preload-*` are empty and `/opt/usr/apps/.recovery` is empty [O], so they are gone until factory reset or reflash [I] | yes | an OTN may re-push [I] | the store may re-push "builtin" apps (`was-builtinupgrade`, `preview-downloader`) [I] | reinstall from store if offered; else factory reset | **high**: ardazeytin reports launcher loss needing factory reset; `sdb uninstall` returned 0 and did nothing on a Q60T [Q]. Not recommended for Samsung system packages |
| M9 | Smart Hub UI delete (store apps) | Vendor flow for `removable=1` WAS apps [O: flag] | yes | yes [I] | n/a | reinstall from store | **low** |
| M10 | **iptables** | `iptables` v1.6.1 and `ip6tables` present [O]. Filter table has vendor `STC_*` and `DPM-*` chains (owner `device-policy-manager`; `stc-manager` inactive) [O]. Matches: `cgroup` (help shows `--path`, matched against the cgroup2 root, which is mounted), `iprange`, `tcp`/`udp`, `nfacct`; targets `REJECT`, `LOG` [O]. **No `owner`, no `string`** [O]. Systemd services each sit in `/system.slice/<unit>`, so per-service blocking works for them (`-m cgroup --path system.slice/adagent-service.service -j REJECT`) [I, untested]. **All amd-launched apps share `/init.scope`** [O], so they cannot be blocked per app | **no**, kernel state; re-add from an `on-boot.d` hook | hook survives | n/a | delete rules or reboot | low-medium: `device-policy-manager` may rebuild chains [I]; IP blocking of CDNs is brittle |
| M11 | Hosts / DNS blocking | `/etc/hosts` is on ro rootfs [O]; `nsswitch: hosts: files dns` [O]. `/etc/resolv.conf` → `/opt/etc/resolv.conf`, written by connman [O]: `192.0.2.1`, **`8.8.8.8` hardcoded as second**, plus an IPv6 resolver. A boot-hook `mount --bind /opt/…/hosts /etc/hosts` is possible as root [I, untested] | bind mount: no; hook: yes | hook survives | n/a | `umount` | low. **Best: filter at the router** (Pi-hole/NextDNS) and also intercept 8.8.8.8:53 and IPv6 DNS. No TV change |
| M12 | Boot/wake hook (`payload/on-boot.d/`) | Shell text eval'd as root at boot and wake (`payload/README.md`) | yes, with the payload | lost with the payload | n/a | `tv/root.py hooks disable` | low as a carrier for M1/M10/M11. **Poor for killing**: `Restart=always` units and `app_autorestart` apps respawn |

## 4. Never touch

| Component | Breaking it looks like |
|---|---|
| `sdbd` (+ systemd socket on :26101), `cf_tv_daemon`, `cf_tv_agent`, `AT-HUB`, `/opt/etc/parseinfo.sh` | loss of root/remote access, permanently if the change is applied at every boot |
| `com.samsung.tv.samsungdex` | the cftv agent slot (listens :7741/:7743) |
| `msf-server` (:8001/:8002) | `tv/devices.py` resolve/scan and the REST/remote websocket stop working |
| `ac.service` (`amd`), `launchpad-process-pool`, `launchpad-loader`, `wrt-loader`, `dotnet-loader`, `pkgmgr-info`/`pkginfo-server`, `security-manager`, `cynara`, `buxton2` | no app launches at all; boot hangs or stays on a black screen |
| `enlightenment`, `org.tizen.tv-viewer`, `com.samsung.tv.csfs` ("FirstScreen" home, 97 MB, the biggest process), `com.samsung.tv.home.media`, `com.samsung.tv.home.sidebar` | no picture, no Home or Smart Hub. quiet-tizen only disabled the sidebar after building a replacement launcher |
| `org.tizen.menu`, `settings-service`, `source-service`, `tvs-daemon`/`tvs-lite`, `avocd`, `muse-server`, `pulseaudio`, `hdmicec-service`, `screend`, `deviced` | no settings, no input switching or HDMI, no sound, no CEC, no power handling |
| `org.tizen.function-osd` / `com.samsung.tv.virtual-remote` (pid shows `DN_function-osd`), `org.tizen.volume-app`, `remote-server`, `bt-service`/bluetooth stack, `scim`/`isf-panel-efl` | the More key and the on-screen remote, the volume OSD, the remote/Bluetooth speaker, the on-screen keyboard |
| `connman`, `net-config`, `vd-net-config`, `wpa_supplicant`, `connman-logger` (`sysinit.target` and ~20 units order on it [O]) | no network, so no sdb |
| `webappservice`, `litewebappservice`, `wrt-service` | all web apps (YouTube, Netflix, TV Plus) fail |
| `Ddrm`/`FKPServer`, `key-manager`, `license-manager-agent`, `org.tizen.netflixlowmem` | DRM playback fails; Netflix may not launch [Q] |
| `adplatform.ADPlayer`, `30IKH0OX8U.*` (AriaVideoBGService) | ardazeytin marks these do-not-touch [Q] |

## 5. Network endpoints seen (2.5 min DNS + 2.5 min TLS ClientHello capture, plus `netstat -tnp`)

[O] DNS: `pagead2.googlesyndication.com` ×48 (A+AAAA) in 2 min, source app unknown, plausibly
TV Plus or YouTube ad SDKs [I]. Also `nrdp.logs.netflix.com`, `scs.samsungqbe.com`,
`dls.di.atlas.samsung.com`. TLS SNI: Amazon `minerva.devices.a2z.com`,
`telemetry.insights.video.a2z.com` (Prime Video).

[O] Established connections: `bms-service` → 23.47.51.212:443 (Akamai, TV Plus HLS);
`aria-engine` (Prime Video) → AWS; `netflix-app` → AWS; `data-service` → 34.160.212.185:443;
`pushd` → 34.254.217.197:5229 (Samsung push, `gld.push.samsungosp.com` in binary); `sso-service`
→ CloudFront/AWS (CLOSE_WAIT). Binary strings: `emergencyLogTransmitter` → `fc.samsungcloud.tv`,
`samsungrm.net`, `rmdev.samsungcloudsolution.net`. From the QN85D/IMC paper [Q]: ACR hosts
`acr0.samsungcloudsolution.com`, `log-config.samsungacr.com`, `log-ingestion-*.samsungacr.com`,
ads `rtb-config.samsungads.com`, `osb.samsungqbe.com`.

LAN listeners [O]: 7000 AirPlay, 8001/8002 msf, 8080 webserver, 4000 mvpd_native,
35381 health-mde, 35707 spotifyd, 7678 remote-server, 8187 allshare, 9197 dmr.

## 6. Recommended plan (safest first)

1. **Vendor menus (no root).** Turn off "Autorun Samsung TV Plus" (`autorun_tv_plus`).
   Turn off voice wake-up and Bixby/Alexa if unwanted. Delete the unused store apps in row 40
   (Prime Video is the big one). Decide on app auto-update: leaving it on means store updates
   can undo M4 and can overwrite `com.samsung.tv.samsungdex`, though root.py reinstalls the
   agent on md5 change. Expected effect: TV Plus stops starting with the TV and frees
   ~80 MB plus `bms` CPU [I].
2. **Stop-markers (M3)** for units that have one, one at a time with `systemctl stop`:
   `pisa-service` (ad agent + pisa), `canalysis-daemon`, `com.samsung.tv.context-aware-agent`,
   `com.samsung.tv.context-aware-service`, then voice (`org.tizen.voice-app`,
   `org.tizen.voice-client`, `com.samsung.tv.bixby-prov`, `com.samsung.tv.ondevice-voice`,
   `com.samsung.tv.ghdaemon`), `samsunghealth-service-tv`. Add `bms` + `slive-provider-daemon`
   only once TV Plus is confirmed unwanted. After `bms`, check the TV/antenna source still
   tunes [I: risk unknown].
3. **vconf (M1)**: `voice_recog_enable 0`, `prelaunch_mode 0`, `voice_activated 0`,
   `wakeupless_status 0` (voice only if dropped). Set `preload_by_country false` plus the
   **WAS RESERVED_18 edit (M2)** to stop the YouTube/Netflix/Prime background preloads,
   ~230 MB [I]. Re-check after each reboot and catalog refresh.
4. **Registry (M4)** via `tools/plan_cleanup.py` with a fresh snapshot in
   `/opt/usr/share/tv-backups/cu7000-*/`. This covers amd-launched apps that markers cannot
   reach: rows 9, 10, 15, 16, 23–33, then optionally 34–37 and 39. Reboot, verify, and
   re-verify after any store update.
5. **Masklist (M6)** for unit-only telemetry, **one per boot**, verifying `sdb`,
   `tv/root.py probe` and `devices.py resolve` each time: `contents-recognition-service`,
   `org.tizen.tv.sp-kpi`, `emergency-log-transmitter`, `org.tizen.tv.tagpro-service-tv`, then
   `devicetracker`, `abnormal_user_behavior`, `issue_report_agent` together; optionally
   `mirroring-recognition-manager`, `scs-agent`.
6. **Network (M11 at the router)**, optionally M10 cgroup rules from a boot hook for
   systemd-unit uploaders. Last, because it only hides traffic.
7. **Not recommended:** `pkgcmd -u` of Samsung packages (M8). It is irreversible for
   `/opt`-only preloads.

## 7. Open questions

- Is someone watching Samsung TV Plus (current source = TV, `bms` streaming), or is it
  auto-play? This decides #1–3 and step 2's `bms` marker.
- Does disabling `bms` break the TV source when no antenna is connected (TV Plus channels
  may be the only TV-source content)? Untested.
- Does `pkgcmd -D -t app -n <id> --global` work on preload apps here, given
  `support_disable=false`? Test on one inert app (e.g. `com.samsung.tv.iacr`, not running).
- Do store app updates reset `app_disable` rows? Check one updated package after the next
  update.
- Does `preload_by_country=false` alone stop the `backgroundAtStartup` launches here, or is
  RESERVED_18 also needed?
- Unknown roles: `com.samsung.tv.tap-service-agent`, `aoc-enabler`, `passthru-service`
  (has a marker), `cis` (comm `CheckProgramCha…`), `vd-sc-client-service`, `qse-daemon`,
  `sfpmd` (~10 % CPU), `boot-helper-dyboost-tv` (~16 % CPU 50 min after boot).
- Which process issues the `pagead2.googlesyndication.com` lookups? Needs a per-process
  DNS trace, which toybox lacks.
- Resolved: the masklist is our payload's, not the vendor's (`masking-daemons.md`).
  `boot restore` drops every mask.
