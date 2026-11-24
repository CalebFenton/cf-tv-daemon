# Screen capture

Updated 2026-11-07. Code: `bin/snap`, `bin/stream`, `apps/capture-probe.cs`
(on the TV as `/opt/cap/CaptureProbe.dll`). Both tools need the root channel,
`tv/chan.py serve` (`docs/01-root/access-contexts.md` §2).

## Use

```sh
bin/snap [out.jpg]                         # one frame; default /tmp/snap.jpg; prints "<path> <bytes> bytes"
bin/stream --frames 30 --outdir /tmp/s     # one burst (defaults: 30 frames, /tmp/stream) -> frame_00000.jpg …
bin/stream --seconds 10                    # repeat capture+pull bursts for 10 s
bin/stream --frames 60 --batch 8           # --batch: frames per READFILE round trip (default 8)
```

| tool | measured 2026-10-24 |
|---|---|
| `bin/snap` | 0.32 s per frame |
| `bin/stream` | 12.7 fps end to end (capture + pull), 40/40 frames distinct |

Every frame is a 720×404 baseline JPEG (~220–250 KB). The requested size is
ignored. `bin/snap` and `bin/stream` frames show every plane: app GL, our own
windows, the hardware video plane, subtitles and OSD. This was verified on
2026-10-22 over Netflix, with an overlay composited above the video. DRM
content such as Netflix captures normally. The probe's `--serve` mode (the
HUD's frame source) captures the hardware video plane alone, so the HUD does
not appear in its own input (verified 2026-11-07, below).

The probe alone, in `--loop N 0` mode on the TV, did 100 frames in 4.40 s
(22.7 fps, all distinct; 2026-10-22). The JPEG is already compressed: gzip
saves 0.6%.

## How the tools work

**`bin/snap`** sends one channel command:
`/usr/bin/dotnet /opt/cap/CaptureProbe.dll /opt/cap/.snap.jpg 2>&1; base64 -w0 /opt/cap/.snap.jpg`.
It decodes the last stdout line into the output file. If the channel reports
`channel_down`, `no_daemon` or a refused connection, it starts the channel
with `tv/root.py`'s `channel_start()` (which waits up to 70 s for it) and
retries once. `bin/snap --tv NAME` selects the TV.

**`bin/stream`** runs one channel command per burst. The command deletes old
frames, captures a 16-frame warmup with `--loop 16 0` and throws it away, then
captures `--frames` frames with `--loop N 0` into `<staging>/frames/f_NNNN.jpg`.
It pulls them with the channel's raw READFILE op (`{"op":"file"}`), `--batch`
files per round trip, and deletes them at the end. Frames go into the agent's
staging directory because READFILE only accepts paths under it
(`docs/01-root/access-contexts.md` §2). That directory is on `/opt`.

**The probe** (`apps/capture-probe.cs`) runs under plain `/usr/bin/dotnet` as
uid 0, label `User`. dotnet's SMACK64EXEC sets that label, and capture works
with it.

```
CaptureProbe <out.jpg>                one frame; prints "OK <path> <bytes> bytes"
CaptureProbe <out.jpg> --loop N MS    N frames <stem>_0000<ext>…, MS ms apart; prints "done ok/N"
CaptureProbe --serve PORT [IDLE_S]    stay running: video-plane frames over TCP (token from CF_CAPTURE_TOKEN, then one byte per frame; reply 4-byte big-endian length + JPEG); pid in /tmp/cf-capture.pid; exits after IDLE_S s (30) without a request. Used by bin/hud live (port 7742): 69 ms median per frame on the host (p90 95 ms, 60 frames, 2026-11-07)
CaptureProbe --probe                  one frame to /tmp/cap/_probe.jpg; prints rc, size, dimensions
```

It exits 0 on success. Every frame is checked for the JPEG magic `FF D8`.

## The capture call

Samsung's `libep-common-screencapture.so` does the capture. Its
`EPScreenCapture::CaptureScreen(ImageProperties*, bool)` (disassembled
2026-11-07, ARM Thumb):

1. prefixes the path with `/home/owner/share/magicinfo/` if the `bool` is
   true, and does nothing else with it;
2. constructs an `EPScreenCapture::ScreenCapture` (width capped at 720,
   height at 404);
3. calls the first virtual method of `IVideoCapture::getInstance()` with the
   object's input parameters (offset 0) and output parameters (offset 0x28);
4. JPEG-encodes the result to the path with `ScreenCapture::saveTo`.

The probe makes these calls itself (exported C++ symbols, P/Invoke) so it can
pick the `IVideoCapture` method by vtable slot:

| slot | used by | captures |
|---|---|---|
| 0 | `CaptureScreen`; `libvideo-capture`'s `secvideo_api_capture_screen` | every plane, composited |
| 1 | `libvideo-capture`'s `secvideo_api_capture_screen_video_only` | the hardware video plane alone |

On 2026-11-07, with the HUD drawn over video from the NVIDIA Shield on HDMI,
slot 0 frames showed the status panel, a label and the reticle; slot 1 frames
showed none of them. In the probe, one slot 1 capture took 60–74 ms and one
slot 0 capture 39–59 ms (5 each). `bin/snap` and `bin/stream` use slot 0,
`--serve` slot 1.

`saveTo` takes a `std::string`, which C# cannot marshal, so the probe builds
the C++11 layout by hand (`char* data; size_t len; union { char buf[16];
size_t cap; }`). It uses the long representation: data on the heap,
`cap = len + 1`. That way no libstdc++ allocator is needed.

Call chain: `libep-common-screencapture.so` → `IVideoCapture::getInstance()`
→ `libvideo-capture.so.0` → `dlopen("/usr/lib/libvideo-capture-impl-sec.so")`.
That library opens `/dev/dri/card1` and uses Samsung scaler capture
(`scaler_drm_open`, `scaler_capture_buffer_create`, `scaler_capture`,
`scaler_map_buff`, `scaler_capture_execute` → custom DRM ioctls
`sdp_drm_gem_capture` / `sdp_drm_hw_capture`). Protected content goes through
`TZCAPTURE_Capture_screen` in `libtzcapturec.so` (TEE) instead. All four
libraries are present, and `/dev/dri/card0..2` are `crw-rw-rw- root:display`
(verified 2026-10-24).

## Deploying the probe

`tv/root.py boot install` builds `apps/capture-probe.csproj` into
`build/capture/` (when the source is newer than the build) and deploys `CaptureProbe.dll` and its
`runtimeconfig.json` to `/opt/cap/` (`root:root` 0755; `doctor` and
`boot status` check them). Keep it on `/opt`: `/tmp` is tmpfs and is wiped at
reboot. A probe in `/tmp/cap` once vanished on a power cycle and capture
"stopped working".

The first deploy of the rebuilt probe (2026-10-25) replaced a hand-deployed
binary (md5 `2aa1dc715babe5f73cf119e62b10108f`, 2026-10-22) of which no copy
exists. Its first run returned `TZCAPTURE_ACCESS_DENIED` while Prime Video was
playing; whether the rebuild captures normal content is **not yet verified**.

`/opt` is nearly full (541 MB free after the 2026-10-24 cleanup). Check
`df -h /opt` before long `bin/stream` runs. Frames are ~250 KB each and are
deleted after each run.

## Traps

- **`enlightenment_info -dump_screen` cannot see video.** It grabs the GL
  composition only, so a hardware video plane comes out black or white.
- **`TZCAPTURE_ACCESS_DENIED` / `checkProcessWhitelist` does not mean the screen
  is protected.** It comes from the protected-content branch of
  `libtzcapturec.so`. On 2026-10-22 it appeared in two cases: when the probe
  was missing (it had been in `/tmp/cap`), and when capture ran through the
  exploit path (`tv/root.py exec --via exploit`) instead of the channel. The
  exploit context was not re-tested after that path was fixed on 2026-10-24
  (missing PYTHONPATH). Capture is verified only through the channel. Ruled
  out as causes on 2026-10-22: DRM content, SMACK label, argv[0], `masklist`,
  `/dev/dri` permissions, stale shm, `ld_capture`.
- **`apps/capture-diagnose.cs`** (assembly `CapWhy`, not deployed) prints
  `scaler_init` and `scaler_capture_is_protect` results. A non-zero value
  means the library would take the TZCAPTURE path. Normal capture never needed
  it.
- **Black warmup frames.** In `--loop` bursts after a pause, the first ~14
  frames came back solid black: a 6,221-byte JPEG instead of ~222 KB
  (2026-10-22). `bin/stream` discards a 16-frame warmup for this. A single
  `bin/snap` frame was real content (250,483 bytes on 2026-10-24).
- **Double image.** Screenshots show each cftv item twice, the second copy
  960 px to the right. This is probably a capture artifact. Not checked
  against the panel (unverified).
- **OCR does not work at 720×404:** `docs/02-capture/reading-frames.md`.

## Inspecting windows and planes

`enlightenment_info` is mode 0550 root:root, so it needs root (verified
2026-10-24).

| command | use | caveat |
|---|---|---|
| `enlightenment_info -topvwins` | window list: PID, geometry, layer, `HWC_ST` (`CL` GL, `VD` video plane) | focused window = `O` in the `Foc` column (verified 2026-10-24) |
| `enlightenment_info -hwc_wins` | hardware planes: zpos, format, geometry | lists planes, not pixels: no proof of content |
| `enlightenment_info -prop -pid N` | read window properties | **setting `Layer` (`-prop … Layer <value>`) crashes the compositor**; never do it |
| `enlightenment_info -dump_screen` | GL composition as `/tmp/dump_screen.png` (1920×1080 RGBA) | no video planes |
| `enlightenment_info -dump_video` | — | refuses DRM: `-1073741822` (2026-10-22) |
| `enlightenment_info -keygrab_status <file>` | key grabs | needs the path argument; its first `cmd:` line is always `csfs`, so it does not show the foreground app |
| `enlightenment_info -wininfo -pid N -shape` | — | prints 0 input rectangles for every window, which means "no custom region", not "empty" |

When a capture disagrees with the owner's eyes, the owner is right.
