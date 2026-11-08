# ACR / screen-content capture and telemetry services

Status: evidence collected; not a current work item. What is disabled and how:
`docs/04-system/debloat.md`.

## Still running: `contents-recognition-service` (verified 2026-10-24)

Unit `contents-recognition-service.service`: `ExecStart=/usr/bin/contents-recognition-service &`,
`User=owner`, `Restart=always`, `RestartSec=0`, `WantedBy=multi-user.target.wants`
and `vdtizen-bootseq-delayed.target.wants`. Active, pid 1741 on 2026-10-24.
No stop-marker guard; not in the registry batches; not masked.

The binary references `libvideo-capture.so.0` and `libavoc.so` and contains
`captureBufferIndex`, `CaptureCropImpl`, `AnalyzeBlackScreen`,
`bBlackScreen_NOOSD`, `BlackScreenEngine` (`grep -a` on the TV). That is a
frame-grab + analysis pipeline. Whether it uploads anything on this build is
unverified.

To stop it: no vendor marker exists, so use `/opt/masklist`
(`docs/04-system/masking-daemons.md`). `Restart=always` means a plain
`systemctl stop` is not enough without the mask (unverified).

## Disabled: `canalysis-daemon` (screen-content recogniser)

`/opt/usr/apps/com.samsung.tv.canalysis-service/bin/canalysis-daemon`
(890,978 B, md5 `2dcc41f6b2e705deb0cac6465e8f8e95`, verified 2026-10-24).
Strings:

```
engine_ld/src/VideoComp/LogoDetector/LogoDetector.cpp
engine_ld/src/VideoComp/LogoDetector/CaffeCNN.cpp
engine_ld/src/VideoComp/LogoDetector/tiny-dnn-master/...
engine_ld/src/VideoComp/MaskDetector/pHashMaskDetector.cpp
engine_ld/src/VideoComp/UIChangeDetector.cpp
[ChannelRecognitionAPI] Input Image Type is not valid
Capture Start / Success Video Capture / Frame #%d Capture Size : %dx%d
Data dir: /opt/usr/home/owner/apps_rw/com.samsung.tv.canalysis-service/data/UIdata/HDMI
```

It captures video frames (including HDMI input), runs a CNN logo detector,
perceptual-hashes UI changes, and exposes a channel-recognition API. Blocked by
the `canalysis-daemon` marker since 2026-10-22.

## Other telemetry / ad services (state 2026-10-24)

| Unit | State |
|---|---|
| `adagent-service` (pisa-service package; binary contains `rtb-config.samsungads.com/tvplus`) | blocked by `pisa-service` marker |
| `pisa-service`, `canalysis-service`, `context-aware-{agent,service}` | blocked by markers |
| `media-analytics-service` (binary contains `osb.samsungqbe.com`, `osbstg.samsungqbe.com`) | failed, exit 1 at 09:00:46 (bus-activated) |
| `contents-recognition-service` | active |
| `contents-ui-detector-service` | active |
| `mirroring-recognition-manager` | active |
| `kpi-logging` | active |
| `devicetracker` | active |
| `dta-agent` | active (`enabled`) |
| `emergency-log-transmitter` | active |
| `issue_report_agent` | active |
| `abnormal_user_behavior` | active |
| `connman-logger` | active |

`contents-ott-detector-service` (listed in older notes) has no unit and no
process on this build.

Registry-disabled apps (`com.samsung.tv.acr`, `targeted-ad-service`, …) are
listed in `docs/04-system/debloat.md`.

## Literature

- *Watching TV with the Second-Party: A First Look at ACR Tracking in Smart
  TVs*, IMC 2024 (arXiv 2409.06203): ACR stays active when the TV is used as an
  HDMI display; capture ~every 500 ms; fingerprints sent ~every minute; opt-out
  stops ACR server traffic. Samsung domains: `acr0.samsungcloudsolution.com`,
  `log-config.samsungacr.com`, `log-ingestion-eu.samsungacr.com`,
  `acr-eu-prd.samsungcloud.tv`.
- UC Davis / UCL / UC3M, 2024: snapshots as often as every 10 ms, matched
  server-side.

## Open

- Which of the active services above originate uploads on this build.
- Whether masking `contents-recognition-service` breaks any wanted feature
  (black-screen detection, picture modes).
