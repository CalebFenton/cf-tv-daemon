# HUD: boxes on what the TV picture shows

Updated 2026-11-07. Code: `tv/hud.py`, `bin/hud`, `apps/cftv-hud.cs`. A tech
demo: recognise faces, hand gestures, objects and people in whatever the TV
is showing, and draw outlines and labels over the picture, several times a
second. The recognition is cf-vision, a separate recognition service (not
part of this repo); this repo grabs the frames and draws.

## Decisions (with the user, 2026-11-05)

| question | decision |
|---|---|
| what to look at | the TV picture (not a camera in the room); gestures only show when someone on screen makes them |
| libraries | MediaPipe for hand gestures, YOLO for objects and people (body pose) |
| faces | box and name them: InsightFace, names from photos the user supplies |
| where recognition runs (2026-11-07) | a service of its own, cf-vision, so other clients (home security cameras) can use it; the HUD is one client |

InsightFace finds the faces itself (its SCRFD detector), so MediaPipe is not
used for faces.

## Layers

Each layer works on its own before the next starts.

| # | layer | state |
|---|---|---|
| 1 | recognition on this computer: grab a frame, find things, save an annotated preview (now `bin/snap`, then cf-vision) | done |
| 2 | a `hud` command in the cftv agent that draws and animates targets (`apps/cftv-hud.cs`) | done |
| 3 | live loop at ~10 Hz with target tracking, on and off (`bin/hud live`, `bin/hud off`) | done |
| 4 | names for faces: people from photos, and faces it cannot name learned as persistent subjects (`SUBJECT 0042`, `SEEN 3×`) for the user to name (in cf-vision) | done 2026-11-06 |
| 5 | scene caption and on-screen text in the status panel (from cf-vision) | done 2026-11-06 |
| 6 | recognition, tracking and names moved into the cf-vision service; `bin/hud` is its client | done 2026-11-07 |

## Usage

```sh
bin/hud live                          # HUD on the TV until Ctrl-C
bin/hud live --seconds 60 --interval 0.5   # default interval 0: as fast as frames come
bin/hud off                           # also: bin/cftv clear
```

`live` needs cf-vision running and reachable. It
deploys the cftv agent and font if needed, opens a cf-vision session (every
kind, the scene caption, faces matched against the galleries `family` and
`subjects:tv`, new subjects learned into `subjects:tv`; `SESSION` in
`tv/hud.py`), then updates as fast as frames come. Ctrl-C or the end of
`--seconds` removes the HUD and ends the session.

## The look

| element | what it does |
|---|---|
| target | four glowing corner brackets (3 px, 75 % opaque, `View.BoxShadow` glow in the target's colour) and a label: title in the target colour, detail line `TGT-007  M 34  CONF 90%` (sex and age only for faces; a named face shows `MATCH 90%`, a subject `SEEN 3×`) |
| colours | face red `#FF3B30`, human cyan `#00E5FF`, hand amber `#FFC400`, object mint `#7CFFB2` |
| titles | `FACE`, `IDENTIFIED: JOE CONSORTI`, `SUBJECT 0042`, `HUMAN`, the object class (`POTTED PLANT`), `GESTURE: THUMBS UP` |
| lock-on | a new target's brackets start 60 % wider and close in over 420 ms while fading up; the title is wiped in left to right behind a block cursor (360 ms) |
| tracking | a known target glides at constant speed toward where it is predicted to be at the next update (one update period); consecutive glides join without stopping |
| loss | a target not detected shows `SIGNAL LOST` on its detail line; 0.6 s after it was last seen it fades out (280 ms) |
| face | a dot for each of the 106 InsightFace landmarks (3 px, 55 % opaque), moving with the brackets; estimated sex and age in the detail line, averaged over updates (weight 0.2 on the newest) because single-frame estimates jump |
| person | the 16 bones of the COCO skeleton as 2 px lines in the target colour; each bone glides (position, length, angle) with the box |
| primary target | a named face first, then a subject, then the largest face seen in this update: a red SVG reticle (dashed ring, ticks, crosshair) spins once every 6 s over it, and its centre leaves a trail of fading dots (600 ms each) |
| ENHANCE inset | top right: the primary face cropped square with a 40 % margin, magnified to 284×284 (Lanczos), titled `ENHANCE // TGT-007` (or the name or subject); a new image at most every 0.25 s, swapped in only once loaded; hidden when there is no face |
| status panel | top left: `CF-TV // OPTICAL ANALYSIS`, blinking `● LIVE`, up to four lines (target counts; latency, update rate, clock; then scene caption and on-screen text); the panel is as tall as its lines |
| frame | thin brackets (1 px, 40 % opaque) in the four screen corners |
| scan line | a faint full-width 1 px line (peak 40 % opaque) sweeps down at constant speed (1 s) after an update, unless one is still sweeping |

Not yet checked on the panel (2026-11-06): the skeleton, mesh, reticle,
trail and inset. A synthetic `hud` command with all of them returned
`ok` and the agent logged no exception, but the TV's own Netflix app was in
front, which refuses capture, so no screenshot exists yet.

Font: Share Tech Mono (`apps/assets/fonts/`, OFL). The TV only has Samsung's
UI font; the agent registers ours with `FontClient.AddCustomFontDirectory`,
and `tv/cftv.py ensure()` deploys it next to the agent.

## How it is drawn

Each target is a cf-vision track: a stable id, so
the TV glides the brackets instead of locking on again, and a velocity per
box edge. A new target needs 55 % confidence and survives 0.6 s without a
detection (`SIGNAL LOST` meanwhile). The box sent to the TV is
where the target should be when the TV's glide ends: grab time + time spent
so far + one update period, at most 1 s ahead. The TV glides at constant
speed for one update period (`glide`, the measured period, averaged the same
way), so a moving target keeps moving between updates instead of darting and
stopping. A target missed in this update is not extrapolated. Boxes are
scaled from the 720×404 capture to 1920×1080, and one `hud` command carries
every target and the status lines.

Frames come from the capture probe kept running in `--serve` mode, over TCP
port 7742 (`Camera` in `tv/hud.py`; protocol in
`docs/02-capture/screen-capture.md`): 69 ms median per frame. The probe
captures the hardware video plane alone, so the HUD never recognises its own
labels, dots or lines, and the scene caption never reads them; anything not
on the video plane (TV apps' graphics, the OSD) is not seen either. It exits 30 s
after the last request. The loop asks for the next frame as soon as it has
one, so the TV captures it while this one is recognised; a frame's time is
when it was asked for (the probe captures on arrival). When the TV refuses
the capture, the panel says `SIGNAL BLOCKED` and the loop retries once a
second. Commands go to the agent over its TCP port 7741
(`docs/03-video/cftv.md`): 4.8 ms median.

The agent (`apps/cftv-hud.cs`, a partial of `apps/cftv.cs`) draws the HUD in
one full-screen transparent window (`transparent-overlay.md` §2). A target is
a view the size of its box with an L-shaped bracket pinned to each corner and
the label pinned above (below near the top of the screen, right-aligned near
the right edge), so animating the box's `Position` and `Size` carries
everything. All motion is NUI `Animation`: lock-on, glide, fade-out, the title
wipe, the scan line and the blinking `LIVE`. DALi runs animations on its
update/render thread, so text layout and command handling on the UI thread
do not stall them.

History (2026-11-05): the first version gave every bracket and label its own
small window, because a full-screen window was believed to black out the
picture, and moved the windows from a 16 ms timer on the UI thread. Every
window create (15–100 ms), destroy (~65 ms) and text change (10–60 ms) froze
all motion; a window pool and queued text brought the worst freeze from
2.2 s to 49 ms, but the scan line still visibly stuttered.

## Recognisers

cf-vision uses InsightFace for faces (with age, sex and 106
landmarks, named from photo galleries), YOLO26-l and YOLO26-l-pose as TensorRT
engines for objects and people, MediaPipe for hand gestures, Florence-2 for
the scene. Each frame goes to the service as the JPEG the probe sent; the
result's tracks become targets (`hud_target`).

## Measurements

Grabbing the TV picture (720×404 JPEG): 437–658 ms with a new probe process
per frame (as `bin/snap`); 130–200 ms with the first `--serve` version (a
shell, a request file and base64 per frame); 58 ms median, p90 110 ms, over
TCP (2026-11-06). YouTube (2026-11-05) and Netflix (2026-11-06) through the
NVIDIA Shield on HDMI captured normally, so neither is blacked out by copy
protection. The TV's own Netflix app refuses capture: with it focused, the
probe's `CaptureScreen` failed every time, and `bin/snap` printed
`TZCAPTURE_Initialize: ACCESS_DENIED Process` (2026-11-06).

Live loop (2026-11-05 runs: YouTube on HDMI, 30 s; agent timer stalls =
gaps over 34 ms in its 16 ms tick; 2026-11-06: Netflix on HDMI, 40 s,
`--interval 0`, host load average 540–680):

| | first version (11-05) | window pool (11-05) | one window, TCP, GPU faces (11-06) |
|---|---|---|---|
| update cycle | 0.6–2.3 s | ~0.45 s (grab 0.13–0.20, recognition 0.13–0.26, send 0.10–0.23) | 0.098 s median, 374 updates (grab 54 ms, recognition 35 ms, send 5 ms; medians) |
| `hud` command on the TV | 200–1046 ms when targets appeared | 0–6 ms | not measured |
| timer stalls | after every update, 250–2189 ms | 11 in 30 s, at most 49 ms | no timer: motion is `Animation` |
| targets created | 31 in 20 s with 5–8 on screen | 24 in 40 frames, mostly real (cuts, new objects) | not measured |

TensorRT and a background send (2026-11-07): `hud` commands go to the TV
from a thread while the loop recognises the next frame. Two 60 s runs of
each version back to back, the same video on the NVIDIA Shield, host load
average 10–13 (medians; frame age = time from asking for the frame to the
end of recognition):

| | before (PyTorch YOLO, send in the loop) | after |
|---|---|---|
| first pair: updates per second | 12.3 | 13.3 |
| second pair: updates per second | 10.2 | 15.7 |
| second pair: time between updates | 92 ms (p90 135) | 62 ms (p90 81) |
| second pair: recognition (`detect`) | 62.6 ms | 19.4 ms |
| second pair: waiting for the frame | 2.9 ms | 39.3 ms |
| second pair: frame age | 159 ms (p90 208) | 82 ms (p90 104) |
| second pair: `hud` command round trip | 18.4 ms, in the loop | 3.5 ms, in a thread |

After the change the loop waits for the TV's frame grab most of the time,
so the grab sets the update rate. A `hud` command with one person and three
faces took 4.8–6.1 ms round trip without face meshes and 8.0–9.9 ms with
them (2026-11-07, 60 commands each): about 1.2 ms per 106-dot mesh on the
TV. Setting each dot's opacity only when it changes made no difference
beyond run-to-run noise and was not kept.

With the video-plane capture (2026-11-07, one 60 s run, other scenes of the
same programme, host load 15): 12.7 updates per second, 76 ms between
updates (p90 104), frame age 109 ms (p90 139), waiting for the frame 40.9 ms,
recognition 32.2 ms. The faces it stored had no HUD drawing in them; the
earlier runs' faces had labels, skeleton lines and landmark dots across them.
With the ENHANCE inset showing a face, SCRFD found that face in an
every-plane capture (score 0.90) and nothing there in a video-plane capture
(2026-11-07): before the change, the HUD could track and learn its own inset.

Through cf-vision (2026-11-07), with the TV stubbed out (16 saved frames, a
60 ms wait per grab), 30 s, host load average 79: 378 updates, every target
kind, skeleton bones, face meshes, the reticle, ENHANCE insets, subject
labels and scene lines in the commands; a recognition round trip (send the
JPEG, get the result) took 50.5 ms median, of which the service 45.5 ms.

## Open

- **Lag:** boxes trail moving video by the grab + recognition time (frame
  age ~82 ms median, Measurements); the TV extrapolates each box to the end
  of its glide, which hides the lag for steady motion but overshoots when a
  target stops or turns. The TV's frame grab now sets the update rate.
- **Display freezes of ~0.4 s (first version):** frame bursts showed the scan
  line stopping and then jumping in some sweeps. The user also saw uneven
  sweeps on the panel (2026-11-06). The HUD now animates on DALi's
  render thread; whether the panel shows it smooth is not yet checked by eye.
- **Overlapping labels:** a face inside a person box puts its label next to
  the person's; nothing moves them apart.
- **Not yet run on the TV through cf-vision:** the TV was unreachable
  (2026-11-07, 12:54) after the change. The run before it, a 3-minute test
  of the old in-process HUD, ended after ~65 s when the GPU fell off the bus
  (`NVRM: Xid 79`).
- **Long names:** the label's title area is 356 px; `IDENTIFIED: ` plus a long
  name is cut off at its right edge.
- **Offline check only (2026-11-06):** a 40 s run of the full host loop on two
  saved TV frames, with the TV stubbed out, produced targets with skeleton
  bones (7 of 16 visible), a 106-point face mesh, `M 44` on the face, the
  inset, a scene caption and text lines; ~20 KB per command (median, mostly
  the inset JPEG). The host load average was 500–6000 during it.
