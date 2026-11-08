# Drawing over the TV picture: platform rules

Updated 2026-10-24. QN75QN85DAFXZA, `T-NKM2AKUC-2301.1`, .NET Core 3.1.3,
ARMv7.

This file holds the rules for any NUI window drawn over whatever the TV is
showing. `apps/cftv.cs` follows every one of them. To put things on screen,
use `bin/cftv` (`docs/03-video/cftv.md`). Building and deploying an app:
`docs/03-video/app-build.md`. Screenshots and window inspection:
`docs/02-capture/screen-capture.md`.

## Rules at a glance

| # | rule | in `apps/cftv.cs` | verified |
|---|---|---|---|
| 1 | set an explicit alpha-0 `BackgroundColor` on every window | `MakeOverlay` | 2026-10-22, 2026-10-24 |
| 2 | a full-screen transparent window over HDMI video keeps the picture (the 2026-10-22 blackout did not reproduce) | `Show` (items), `BuildHud` (HUD) | 2026-11-06 |
| 3 | `SetAcceptFocus(false)` + empty input region | `MakeOverlay` | 2026-10-22, 2026-10-24 |
| 4 | the main window must commit a frame at launch | `Main`, `OnCreate` | 2026-10-24 |
| 5 | never exit: any exit hands focus to Smart Hub (its bar slides up) | no `quit` op; host `stop_agent` | 2026-11-05 |
| 6 | re-raise item windows once a second | `raise` timer | 2026-10-22 |
| 7 | keep every NUI `Timer` referenced | `_timers` | code comment only |
| 8 | a `VideoView` is opaque hardware and takes over the TV's video path | `VideoItem` | 2026-10-22, 2026-10-24 |

## 1. Transparency: explicit alpha-0 background

```csharp
window.BackgroundColor = new Color(0f, 0f, 0f, 0f);   // explicit alpha 0
```

If `BackgroundColor` is left unset, the surface clears to opaque black.
Nothing else fixed that on its own: `WindowMode.Transparent`,
`ImageView.PreMultipliedAlpha`, `ecore_wl2_window_transparent_set`,
`alpha_set`, `opaque_region_set` (2026-10-22).

With that line, per-pixel alpha works. Transparent PNG regions, SVG
cut-outs (2026-10-22) and alpha GIF/WebP (2026-10-23/24) all composite over
whatever is behind. cftv sets `PreMultipliedAlpha = true` on images because
the files carry straight alpha. For shapes, use `View.CornerRadius`, an alpha
PNG, or an SVG through `ImageView`. Gradients work in SVG or with `ColorVisual`.

## 2. Geometry over live video

| window | result over live video |
|---|---|
| 1920×1080 fullscreen, transparent (2026-10-22) | picture went black |
| 1920×1080 fullscreen, transparent, cftv's `MakeOverlay` (2026-11-06, Netflix on the HDMI box) | **picture normal**, by eye; a red marker item on top showed |
| 1920×360 at (0,720), opaque or transparent (2026-10-22) | video visible |

The 2026-10-22 blackout is not explained, and the setup of that test is not
recorded (`research/OVERLAY.md` in commit `c91cd61` gives no code). It may
predate rule 1: an unset `BackgroundColor` clears to opaque black, which
looks the same. On 2026-11-06 the compositor list (`enlightenment_info
-topvwins`) was identical with windows of 1920×1080, 1920×1079 and 1919×1080:
`tv-viewer` stayed `Vsbt 0` (visible) and `tvvideosink-video` stayed on the
`VD` hardware plane.

cftv items still get one window each, sized to the item: each item is placed,
raised and removed on its own. The HUD (`apps/cftv-hud.cs`) is one
full-screen window, so its views can be animated by NUI `Animation` (§9).
Windows come from `new Window(name, new Rectangle(x, y, w, h), true)`; the
main window from `NUIApplication(style, WindowMode.Transparent, Size2D,
Position2D)`.

## 3. Focus and input pass-through

A normal NUI window takes the remote away from the app underneath. Both calls
below are needed:

```csharp
window.SetAcceptFocus(false);                       // no keyboard focus
window.SetInputRegion(new Rectangle(0, 0, 0, 0));   // no pointer/touch area
```

Evidence:

- 2026-10-22: `input_keyevent Down` ×5 while the overlay ran. The overlay's
  `KeyEvent` handler logged zero events.
- 2026-10-24: with cftv items up, `tv-viewer` keeps focus (`O` in the `Foc`
  column of `enlightenment_info -topvwins`).

Shape and keygrab output are not valid checks. See the inspection table in
`docs/02-capture/screen-capture.md`.

## 4. Launch screen: commit a frame

At launch, Tizen shows a "Launchscreen" window. It holds focus, and so the
remote, until the app's main window commits a frame. NUI renders on demand, so
an empty transparent window never commits one and the launch screen stays.
Fix, in `apps/cftv.cs` (verified 2026-10-24):

1. Construct the app with `WindowMode.Transparent` and an explicit
   `Size2D`/`Position2D`. With the default constructor no window reached the
   compositor.
2. Add a 1×1 view with alpha 0.01 to the main window.
3. After 500 ms, shrink the main window to 1×1 at (0,0).

## 5. Never exit

Any exit of the overlay app hands focus to Smart Hub, and the Smart Hub bar
slides up over the picture ~1 s later. Screenshots on 2026-11-05: after the
app's own `Exit()`, also with its window hidden first, and after
`app_launcher -t`. (2026-10-24 recorded that `Exit()` and `-t` returned focus
to `tv-viewer` with no bar; the screenshots disprove that.) So the app stays
resident and removes its windows instead. To replace it, note the focused
app, stop it, and resume the noted app (`app_launcher -s <appid>`) as soon as
focus moves: `stop_agent` in `tv/cftv.py`. The focused app is the process of
the window marked `O` in `enlightenment_info -topvwins`, and its SMACK label
`User::Pkg::<appid>`.

## 6. Staying on top: re-raise

The compositor stacks by focus order, so a newly launched app goes above us.
`wm.policy.win.above.lock` sent through `ecore_wl2_window_aux_hint_add` returns
`rc=0` but is not honoured. Only NUI's own `wm.policy.win.user.geometry`
registers. What works is re-raising once a second:

```csharp
[DllImport("libecore_wl2.so.1")] static extern void ecore_wl2_window_raise(IntPtr win);
var mi = window.GetType().GetMethod("GetNativeWindowHandler",
    BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
ecore_wl2_window_raise((IntPtr)mi.Invoke(window, null));   // from a Timer(1000)
```

On 2026-10-22 this held `zpos=3`, the topmost plane, above Settings. The
window survives app switches. Do not try to change the layer with
`enlightenment_info -prop … Layer`: it crashes the compositor.

## 7. Keep timers referenced

A NUI `Timer` that nothing references gets garbage-collected and stops
without an error. cftv keeps every timer in the `_timers` list. Source: code
comments in `apps/cftv.cs` and the deleted ball/overlay apps. No test log
exists.

## 8. Video: `VideoView`

- **Plays files and URLs.** HLS is fetched by the TV: on 2026-10-22 it pulled
  `stream.m3u8` and 8 `.ts` segments from a host HTTP server and rendered them
  at 960×540 at (480,270). cftv video was verified 2026-10-24.
- **Has its own hardware plane, not a GL texture** (`-hwc_wins`, 2026-10-22):

  ```
  1  ... zpos 1    CL  AR24  1920x1080          <- our GL window
  2  ... zpos 0    VD  XR24   960x540 +480+270  <- our VideoView
  3  ... zpos -999 DV  AR24  1920x1080          <- video plane below
  ```

  Treat the video rectangle as opaque. GL drawn inside it does not composite
  over the video. The window around it stays transparent. `Opacity` does blend
  the video itself: at 0.5 over an opaque red backdrop, the red showed through
  (2026-10-22).
- **Takes the TV's video path.** The HDMI/tuner picture goes black and does
  not come back on its own after the video is removed. cftv restores it; see
  "Video and the TV's own picture" in `docs/03-video/cftv.md`.
- **Members** (`Tizen.NUI.dll` metadata): `ResourceUrl`,
  `Play/Pause/Stop`, `Forward/Backward`, `Looping`, `Muted`, `Volume`,
  `Underlay`, `Finished`, and `GetNativePlayerHandle()` (internal). It has no
  alpha or pixel-format member.
- **No transparent video.** `VideoView` has no alpha control. There is also no
  Matroska/WebM demuxer: no `libgstmatroska`, no vpx plugin, and no
  libvpx/libdav1d/libaom in `/usr/lib`. GStreamer decoding goes through
  `libgstomx`, `libgsttzdecoder` and `libgsttheora` (verified 2026-10-24).
  Whether tzdecoder handles VP8 is unverified. For a transparent moving
  object, use an animated GIF/WebP (§9).
- **Untested:** video audio by ear, decode cost.

## 9. Animation

- **NUI `Animation` works.** Tested properties: `Position`, `Opacity`, `Scale`;
  the HUD also animates `Size`, `SizeWidth`, `PositionX` and, with
  `AnimateBetween` and `KeyFrames`, `PositionY` and `Opacity` (2026-11-06).
  `RotationAngle` is not a valid name (`ArgumentException`). `"Rotation"` and
  `"Orientation"` exist but were not confirmed as animation targets.

  ```csharp
  var a = new Animation(1600);
  a.AnimateTo(view, "Position", new Position(320, 300, 0));
  a.Looping = true;
  a.Play();
  ```
- **A window can slide.** `Animation` only targets views, but setting
  `Window.WindowPosition` from a 16 ms `Timer` moves a window smoothly: cftv
  notification cards (250 ms moves) showed 3–5 in-between positions in frame
  captures at 18–22 fps (2026-11-05). A view sliding inside its own window is
  clipped at the window edge.
- **Anything that blocks the UI thread freezes timer-driven motion.**
  Creating a `Tizen.Multimedia.Player` takes 94 ms; timer-driven moves
  started before it jumped to their end position (2026-11-05). `Animation`
  is not driven by the UI thread: DALi steps it on its update/render thread.
  The HUD moved from timer-stepped windows to `Animation` on 2026-11-06; a
  1.5 s glide captured at ~22 fps advanced 8–12 px (of 720) every frame with
  no repeated position.
- **Pinning to a corner:** a child with the same `ParentOrigin` and
  `PivotPoint` (for example `TopRight`) stays on that corner while the
  parent's `Size` animates. NUI needs `PositionUsesPivotPoint = true` for the
  pivot to count; the HUD sets it on every pinned view.
- **`AnimatedImageView` with one animated GIF or WebP file works**, with
  per-pixel alpha. Verified with a 240×240, 60-frame ball in both formats on
  2026-10-23, and with a GIF through cftv on 2026-10-24. cftv uses it for
  `.gif`/`.webp`. Decoders found by static analysis on 2026-10-23: GIF
  (`DGif*` in `libdali2-adaptor.so.2`), animated WebP (`WebPAnimDecoder*` via
  `libwebpdemux`), PNG (libpng16 linked into `libdali2-adaptor`).
- **When making assets,** draw every frame from scratch (GIF `disposal=2`).
  An ffmpeg `geq` ball accumulated across frames and smeared into a ring.
- **APNG is not animated.** An `acTL`/`fcTL` APNG shows frame 1 forever.
- **`AnimatedImageVisual` with a URL list hangs the app.** Measured 2026-10-22
  with 12 PNG URLs, 8 s per run, main-thread CPU sampled over 3 s:

  | mode | timer ticks | main-thread CPU / 3 s | verdict |
  |---|---|---|---|
  | off (no sprite) | flowing, 100 ms cadence | 32 jiffies | baseline |
  | `AnimatedImageVisual` | 1, then frozen at 1001 ms | 590 jiffies (99%) | hangs |
  | same + `CacheSize=12 BatchSize=12` | 0 | 310 jiffies | still hangs |
  | `ImageView` + 80 ms timer URL swap | flowing for 8 s | 31 jiffies | works |

  It pins a core and starves the NUI event loop. Do not use it.
- **`ImageView` + a `Timer` swapping PNG URLs works.** 80 ms measured as
  above. A 33 ms swap of 60 PNGs animated (frame diff, 2026-10-23), but the
  rate actually achieved was not measured.

## 10. Native window handle and compositor reference

```
Window.GetNativeHandle()        -> Tizen.NUI.Any   (private Dali container; unusable)
Window.GetNativeWindowHandler() -> System.IntPtr   (the Ecore_Wl2_Window*)
```

Both are `internal`, so call them through reflection.
`ecore_wl2_window_surface_get(h)` returns a valid `wl_surface*`. Calls that
worked on our own window: `raise`, `stack_mode_set`, `transparent_set`,
`alpha_set`, `opaque_region_set`, and `aux_hint_add` (accepted).

Protocol facts from static analysis on 2026-10-22, not re-checked:

- `tizen_policy_interface` v8 has 42 methods and 10 events, exported from
  `/usr/bin/enlightenment`.
- `libecore_wl2` opcodes: `aux_hint_add`=0, `raise`=4, `lower`=5,
  `type_set`=10, `conformant_set`=11, `aux_hint_change`=25, `aux_hint_del`=26,
  `aux_hints_supported_get`=27, `floating_mode_set`=30, `stack_mode_set`=32,
  `video_has`=38.
- Window types 1..9 map to protocol values 1,5,6,7,8,9,10,11,12. Tizen.NUI
  declares a 4-value `WindowType` enum and never references it.
- Layer values (`e_comp_canvas_layer_map`): `0,10,100,150,200(NORMAL),
  250(ABOVE),450,500,550,600,650,750,850,950,990(CURSOR),999,1999,3000,3100,
  3200,5000,9999,32767`.
- Aux hints in the compositor allowlist: `above.lock`,
  `transient_for.always_on_top`, `deiconify.update`, `effect.disable`,
  `fixed.resize`, `gesture.disable`, `iconify`, `iconify.buffer.flush`,
  `msg.use`, `rot.dependent`, `rot.render.nopending`, `user.geometry`. Only
  `user.geometry` is honoured for us.

## 11. Open questions

- **Video under an item:** does live video show through the transparent
  pixels inside an item window, or does that region go black?
- **Selective input:** can a window take input only inside a widget,
  using `SetInputRegion` with a real rectangle? Untested.
- **Frame-rate ceiling:** neither `Animation` nor timer-driven sprites have a
  frame-interval measurement. NUI exposes no public per-frame callback
  (`EventProcessingFinished` and `FrameUpdateCallback` are internal).
- **`above.lock`:** why it is accepted but ignored. Probably sender trust.
- **`-prop Layer` crash:** cause unknown. Do not retry.
