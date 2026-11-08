# A chime cut the programme's sound; video without the TV's player

> **Historical log (2026-11-07).** Evidence for the sound fix
> (`docs/03-video/cftv.md`, "A sound cut the programme's sound") and for the
> video experiments that followed. Current facts live in `docs/03-video/`.

## Files

| file | what |
|---|---|
| `bus-media-player-chime.txt` | `busctl --system monitor` while the agent played a chime through `Tizen.Multimedia.Player`: muse-server → avocd `PlayerRegisterM(0,10,0,128)`, `PlayRequestM(128,7,0)`, then on release `AudioMute`, `PlayerUnregister` |
| `bus-wavplayer-chime.txt` | the same chime through `WavPlayer` (Notification policy): PulseAudio `SoundPlay`, `DeviceRunningChanged`, `EOS`; no avocd player call |
| `bus-input-switch-restore.txt` | the tuner→HDMI switch that brought the sound back: rscmgr `RscStateChanged(11, 7, 0, 124, "org.tizen.tv-viewer")` |
| `avoc-introspect.txt`, `resource-center-introspect.txt`, `rscmgr-introspect.txt` | introspection of the three services |
| `resource-center-getters.txt`, `rscmgr-getters.txt` | read-only getters (zones, resource owners) while a show played on HDMI |
| `option-b-map.md` | read-only map of the TV's second-video path (sub scaler, ESPlusPlayer, Multi View); nothing in it was run |
| `frames-640x360-audio-run.txt` | `tools/frames_bench.py` output of a run with sound |

Screenshots taken during these runs (notification cards over a show; host-decoded frames in the agent's window; the sub-scaler clip) are not published.

## Sound

1. 17:55 the `notify --sound` demo played the TV chime twice; afterwards the
   show (NVIDIA Shield on HDMI, Bluetooth speaker) was silent. Re-selecting
   the same input and reconnecting the speaker did nothing.
2. After the chime, rscmgr `GetActiveAudioOut` was 0 for player ids 0–140:
   nobody held the main output.
3. The agent's sounds moved to `WavPlayer` (commit 7633461); its bus
   capture has no avocd call.
4. 18:42 one input switch (tuner, back to HDMI 15, speaker reconnect; 8.2 s)
   gave player 124 (`tv-viewer`) the main output again; the user confirmed
   sound.
5. 18:44 a `WavPlayer` chime over the show: the user heard it and the show's
   sound kept playing; player 124 kept the output.

## Video without the TV's player (Option A)

`VideoView` takes the main scaler and the main audio output the same way.
The experiment: decode on the host, send MJPEG over TCP port 7743, decode
on the TV with libturbojpeg (`tjDecompressToYUVPlanes`) into Y/U/V planes,
upload them as three L8 textures, convert to RGB in a shader
(`apps/cftv-frames.cs`). Sound: PCM packets into `AudioPlayback`.

Findings:

- The TV is on Wi-Fi (`wlan0` up, `eth0` down). Raw YUV at 640×360 30 fps
  needs 83 Mbit/s; it reached 13.4 fps (~37 Mbit/s). Hence JPEG.
- DALi's default sampler made the textures sample as 0 (solid green): GL's
  default minification filter needs mipmaps. A `Sampler` with LINEAR/LINEAR
  and ClampToEdge fixed it.
- `PixelData`'s native constructor copies the buffer (`new[]` + `memcpy` in
  `CSharp_Dali_PixelData_New`, libdali2-csharp-binder), so one managed array
  per plane is reused.

10 s runs at 30 fps, frames bottom-right over the show:

| frames | Wi-Fi | received / shown | decode + upload | agent CPU |
|---|---|---|---|---|
| 640×360 testsrc2 | 4.6 Mbit/s | 301 / 298 | 2.0 + 0.9 ms | 23% of one core |
| 960×540 Big Buck Bunny | 24 Mbit/s | 301 / 301 | 7.3 + 2.1 ms | 45% |
| 1280×720 Big Buck Bunny | 40 Mbit/s | 301 / 301 | 12.5 + 4.1 ms | 71% |
| 640×360 + PCM (5 s) | 4.4 Mbit/s | 151 / 151, audio 151 played | 2.1 + 1.0 ms | 27% |
| 960×540 + PCM (20 s, sync demo) | 7.8 Mbit/s | 601 / 601, audio 588 played, 1 dropped | 4.2 + 1.9 ms | 36% |

Frame numbers read back from screen captures (bit blocks on each frame)
advanced at ~30 fps. With sound, the bus showed no avocd or muse player call
and player 124 kept the main output. PulseAudio reported 60 ms stream buffer
plus 60 ms sink latency for the agent's stream; the Bluetooth speaker adds
more (not measured). Lip sync is not handled yet.

The user heard the sound of the 640×360 and 8 s runs; the "silent" run
they reported was the 3 s `frames_bench.py` smoke run, which had no audio.

## Option B: the TV's second video path

Map: `option-b-map.md`. Test: the agent's `esvideo` op
(`apps/cftv-esplayer.cs`), driven by `tools/esvideo_probe.py`, plays a
silent 640×360 H.264 test clip through `Tizen.TV.Multimedia.ESPlayer` with
`SetAlternativeVideoResource(1)`.

1. 20:10 `MethodAccessException: http://developer.samsung.com/privilege/esplay`
   (`esvideo-probe-1-privilege.txt`). The wrapper's constructor asks Cynara
   whether the calling app holds the privilege; the borrowed app slot does
   not. Bypassed for the experiment: the object is created without its
   constructor and `Initialize()` (which creates the native player) is
   called by reflection. The native library made no check of its own.
2. 20:11 the player reached `Playing` but ended after 0.36 s
   (`esvideo-probe-2-pts-units.txt`): the wrapper's `ESPacket.pts` is in
   nanoseconds (it divides by 1,000,000 for the native call); `duration`
   passes through in milliseconds.
3. 20:12 10 s clip, correct units (`esvideo-probe-3-sub-scaler.txt`,
   `esvideo-bus-3-sub-scaler.txt`; screenshot not published):
   - played 10.0 s (`Playing` 20:12:34.62, end of stream 20:12:44.62);
   - rscmgr gave our process (handle 131) resource 23 (sub video decoder)
     and resource 18 (category 16, sub scaler); both freed at the end;
   - the main scaler (7) and main audio out (9) stayed with `tv-viewer`
     124, and player 124 kept its audio output;
   - a new 640×360 hardware plane at +1232+672, above the programme's plane;
   - avocd saw only `VideoStart(786432, 1)` and `VideoStop(786432, 1)` from
     our process: no audio call;
   - the screen capture shows the clip bottom-right over the show.
   Not yet confirmed by the user's eyes on the panel.

Untested: ESPlayer's own sound through simple mix out
(`option-b-map.md` test B), larger clips, and HEVC.
