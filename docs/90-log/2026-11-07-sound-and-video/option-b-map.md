# Option B: a second video that leaves tv-viewer's picture and sound alone

Date: 2026-11-07. Sources: static analysis of `livefs` (`LF` = the repo's `livefs/`, the extracted root filesystem), decompiles in Binary Ninja (bintools) and dnSpy-style C# (dotnet tools), and **read-only getters** run on the TV:
- `busctl introspect` on rscmgr
- resource-center `Get*`/`Is*` methods
- rscmgr `Get*` methods
- `od` of `/proc/device-tree/resource-manager/rm_tbl_idx`

Raw getter output is in `resource-center-getters.txt` and `rscmgr-getters.txt` (this folder). Nothing on the TV was set, registered, played or switched. `[INFERENCE]` marks a conclusion that is not directly observed.

## 0. Bottom line

1. **Why the picture dies.** It is a resource reclaim, not an avoc problem.
   - The TV's resource table (`LF/etc/config/resource_information_dual.xml`, the file the TV loads) has two scalers. `sec_scaler0` is category `scaler`, rm id 7, and is "main". `sec_scaler1` is category `scaler-sub`, rm id 16.
   - Live right now: tv-viewer holds scaler 7 (device 11). Scaler 16 (device 18) is **free**. Both video decoders (`/dev/video10`, `/dev/video11`) are **free**, because HDMI uses no decoder.
   - The media player (muse-server, then libmmfplayer) asks for category 7 unless told otherwise.
   - rscmgr's `CScalerStrategy::findCandidate` returns a free device in the requested category. Only if none is free does `GetRetirableConsumers` reclaim one by priority. With 7 busy, it reclaims tv-viewer's scaler, so the picture goes and stays gone. [INFERENCE: consistent with the code and with `tvvideosink` windows vanishing.]
2. **Why the sound dies.**
   - libmmfplayer always calls `avoc_player_register_m` and then `avoc_play_request_m(player, sec_ctl_source, …)`. The source is 7 for a PCM or pulsesink sink and 10 for the HW decoder sink.
   - In single-view mode, avocd turns that into `main out source select = <source>`, which takes the main output away from HDMI.
   - A software audio codec does not avoid this in libmmfplayer. It only swaps the sink to `pulsesink`; registration and play request still happen.
3. **The way out.** It exists on this TV and needs no Multi View mode. The **ESPlusPlayer** stack (`libesplusplayer.so` → `libplusplayer-core.so` → `libtrackrenderer.so`, run in-process, not via muse) gives you both halves:
   - **Picture:** `SetAlternativeVideoResource(1)` uses the sub decoder (`/dev/video11`) and the **sub scaler**.
   - **Sound:** either have no audio track, in which case trackrenderer skips avoc entirely, or select the "simple mix out" audio resource with a software audio codec. Then trackrenderer allocates a fake `Pulse.Audio.Out` resource, uses `pulsesink`, and logs `"sw decoder + pulsesink, don't need avoc register"`.
   - A .NET wrapper `Tizen.TV.Multimedia.ESPlayer` is on the TV, and Samsung's own CameraLauncher uses it the same way.
4. **Multi View itself** (resource-center zones) is the OS-level layout mode. It needs `SetAppToZone`, the multiview vconf and the multiscreen app. That is a visible TV-state change and the wrong tool here.

## 1. Multi View: who implements it, what a zone is

| Piece | Evidence |
|---|---|
| resource-center (`LF/usr/bin/resource-center`, service `org.tizen.tv.system.resource` `/Org/Tizen/TV/System/Resource`) | Embedded introspection XML; `resource-center-introspect.txt`. Owns zones, audio focus and Multi View speaker/mixing. Strings include `GetMultiViewStrategy`, `PMRscStrategy2ViewZB`, `main scaler free(%d)/zone(%d)/consumer(%s)/sub zone(%d)`, `focused zone(%d), scaler id(%d)`, `get list of RM_CATEGORY_AUDIO_SUB_OUT`, `SwitchBestQaulityZone`. |
| rscmgr-service (`LF/usr/bin/rscmgr-service`, `rscmgr.resource` `/rscmgr/resource`) | Device-level allocator. `CScalerStrategy::{findCandidate, GetRetirableConsumers, SelectConsumersByZoneId, SelectConsumersNotInAnyZone, SelectConsumersBySwap}`, `NotifyAppZoneInfo(si)`, `SwapResources(ii)`. |
| com.samsung.tv.multiscreen (`MultiScreen.dll`, CoBA host for `com.samsung.tv.coba.mv*`) | The Multi View app. Launched by `XF86MultiView` → `multiview_key` (`LF/usr/share/packages/com.samsung.tv.csfs.xml:56-57`) and by app-control op `http://samsung.com/tv/appcontrol/operation/multiview`. |
| com.samsung.tv.multiviewsetupapp | Setup UI and whitelist (`/home/owner/share/mvwhitelist`). |
| `Tizen.TV.Service.ScreenOnScreen.dll` (`LF/usr/share/dotnet.tizen/tvappapi/`) | Launch API: `LaunchMultiView`, `ForceToLaunchMultiView`, `MVLaunchZonePosition` (`Zone1..5`, layout positions). |
| tv-viewer | PIP in-app (`m_CreatePipLayout`, `XF86PIP*`), plus `tv-viewer-sub1..3` instances (`LF/usr/share/packages/org.tizen.tv-viewer.xml`). |
| PulseAudio `module-multiview-manager.so` (`LF/usr/lib/pulse-13.0/modules/`, loaded by `LF/etc/pulse/system.pa`) | Routes a stream to `tizenaudio-sink` / `tizenaudio-sink-sub` / `combined-sink` by its pid's zone, listening to resource-center signals. |
| Multi View mode flag | vconf `memory/multiscreen/info`, a JSON whose `"mode"` is `"on"`. Read by libmmfplayer (`_mm_player_config_multiview_from_vconf`), avocd (`on_vconf_multiview_mode_cb`) and the WFD sink. |
| App metadata | `multiview_support`, `multiview_min_resource`, `multiview_priority`, `multiview_fullosd` in `LF/usr/share/packages/*.xml`. This only affects which apps the Multi View UI offers. |

**`SetAppToZone`**
- Signature: `SetAppToZone(i commandid, s appid, i zoneid) -> i`. Also `SetPreAssignedAppToZone` with the same arguments. Client wrapper: `rc_set_app_zone_info(const char*, int)` in `libresource-center-api.so.0`.
- `module-multiview-manager.so` contains the string `SetAppToZone`.
- tv-viewer only *reads* zones (`rc_get_focused_zone`, `rc_zone_get_app_id`).
- The caller that sets zones during a Multi View launch was not pinned down. It is presumably the multiscreen app or its CoBA core [INFERENCE].

**Live zone state** (read-only getters, `resource-center-getters.txt`):
- `GetMultiviewZoneIds` → `[(1,""),(2,"")]`: zones exist and are empty.
- `GetFocusedZone` → `(0, -1, "")`: not in Multi View.
- `GetMultiviewMixingMode` → `-3`.
- `GetPlayerAudioOut(124)` → `1`. resource-center numbers are 1 = main, 2 = sub (libmmfplayer string `audio_out value [ %d ],1 is main,2 is sub`).
- rscmgr `GetActiveAudioOut(124)` → `1`. tv-viewer's player 124 has the main out.

**What a zone means.**
- A zone is a resource-center slot that pairs with one scaler. Strings: `zone id(%d) - scaler id(%d)`, `bq_zone_id(%d)-scaler_id(%d)`, `Zone %d : Audio out : %d`.
- Zone 1 is main scaler and main audio out; zone 2 is sub scaler and sub audio out [INFERENCE from those strings].
- rscmgr uses the requester's zone when choosing a victim to reclaim (`SelectConsumersByZoneId` → `SelectConsumersNotInAnyZone` → `SelectConsumersBySwap` → `SelectConsumersByAllocationTime`).
- When a free device exists in the requested category, zones play no part: `findCandidate` takes the first allocatable one (decompile at `rscmgr-service` 0x280b0).
- **Different "zones"**: the LXC `zone-manager`/`libzone` containers (`docs/04-system/lxc-zones.md`) and the compositor's `E_Zone` are unrelated to these.

### rm category ids on this TV

Built from rscmgr `GetResourceList(i)`, run live, plus `resource_information_dual.xml`. `/proc/device-tree/resource-manager/rm_tbl_idx` = `"32"`. The TV's `/etc/config/resource_information.xml` → `resource_information_dual.xml`, md5 `f48f3be2…`, which is identical to the livefs copy.

| rm id | XML type | device id : node | owner now |
|---|---|---|---|
| 1 | audio-decoder | 19 `/dev/dvb/adapter1/audio0` | tv-viewer (124) |
| 3 | video-decoder | 22 `/dev/video10` (mfc0, main), 23 `/dev/video11` (mfc1) | free |
| 7 | scaler | 11 `/dev/dri/card0` (`sec_scaler0`, overlay=0, main) | tv-viewer (124) |
| 9 | audio-main-out | 1 `alsa_control_main_out` | tv-viewer (124) |
| 12 | mm-pcm-playback | 4 `/dev/snd/pcmC0D0p` | free |
| 14 | audio-decoder-sub [INFERENCE: node matches `sec_aud_dec1`] | 20 `/dev/dvb/adapter1/audio1` | free |
| 16 | **scaler-sub** | 18 `/dev/dri/card0` (`sec_scaler1`, overlay=1) | **free** |
| 19 | ext-hdmi-src | 14 N/A | tv-viewer (124) |
| 20 | **video-decoder-sub** | 23 `/dev/video11` | free |
| 51 | **audio-sub-out** | 63 `alsa_control_sub_out` | free |
| 53 | display-out (`BackgroundOut`) | 11, 18, 65 | – |

How the sub ids are confirmed:
- 16, 20 and 51: libmmfplayer `__mmplayer_get_resource` (0x9764c) maps scaler 7 → 0x10 when `fixed_video_scaler` is set, video decoder 3 → 0x14 when `fixed_video_decoder` is 4, and main-out 9 → 0x33 for mixing with the sub audio decoder.
- `GetScalerState` → `[(7,11,state 3,124,"org.tizen.tv-viewer"), (16,18,state 0,…)]`.

## 2. Player-level knobs that select secondary resources

### 2a. ESPlusPlayer (in-process; the important one)

`LF/usr/lib/libesplusplayer.so` exports, from `readelf --dyn-syms`:
- `esplusplayer_set_alternative_video_resource`
- `esplusplayer_set_alternative_audio_resource`
- `esplusplayer_set_simple_mix_out_buffer_level`
- `esplusplayer_set_audio_codec_type`
- `esplusplayer_set_video_codec_type`
- `esplusplayer_set_resource_allocate_policy`
- `esplusplayer_set_media_packet_video_decoded_cb`
- `esplusplayer_set_video_frame_buffer_type`
- `esplusplayer_set_video_frame_buffer_scale_resolution`
- `esplusplayer_set_display`, `esplusplayer_set_ecore_display`, `esplusplayer_set_display_roi`, `esplusplayer_set_display_mode`, `esplusplayer_set_display_visible`
- `esplusplayer_set_audio_mute`
- `esplusplayer_set_resource_conflicted_cb`

It links `libplusplayer-core.so`, which links `libtrackrenderer.so` and `libavoc.so`. No muse is involved: GStreamer runs in the caller's process.

Value mapping, all decompiled:

| Call | Value | Effect |
|---|---|---|
| `esplusplayer_set_alternative_video_resource(h, n)` → `EsPlayer::SetAlternativeVideoResource` → `TrackRenderer::SetAlternativeVideoResource_` (trackrenderer 0x4e46c) | 0 | decoder cat 0 (main), renderer cat 2 (main scaler) |
| | **1** | **decoder cat 1 (sub), renderer cat 3 (sub scaler)** |
| | 2 | decoder sub, scaler unchanged (main) |
| | **3** | **decoder unchanged (main), sub scaler** |
| `esplusplayer_set_alternative_audio_resource(h, n)`: the C wrapper passes `n` straight through (0x5c4d0); `ConvertToTrackRendererAlternativeAudioResource` (plusplayer-core 0x246e4) maps 0→0, 1→1, 2→3; `TrackRenderer::SetAlternativeAudioResource` (0x50ac4) | 0 | audio out cat 6 (main out), audio dec cat 4 |
| | 1 | audio out cat 7 (sub out), audio dec cat 5 (sub) |
| | **2** | **trackrenderer value 3, so audio out cat 8 = "simple mix out"** |
| `esplusplayer_set_audio_codec_type(h, n)` (`EsPlayer::SetAudioCodecType` 0x42434) | 0 = hardware, non-zero = software | must be set before the audio stream exists |
| `esplusplayer_set_video_codec_type(h, n)` (0x42634) | 0 = HW, 1 = SW, 2 = "HW N-decoding" | SW is refused with frame-buffer type *scale*; refused when a mixer owns resources |

Guards:
- Alternative video is refused (`"player can't control resource type, mixer will do it"`) when display type 3 (mixer) is used.
- `"has set resource allocate policy, the alternative resource setting will be wrong"` means: do **not** call `set_resource_allocate_policy`.

Trackrenderer `ResourceManager::Alloc_` (0x49064) chooses in this order:
1. `NeedSwDecoderResource` (decoder category plus the use-SW flag) → `AllocSwDecoderResource_`. This makes a synthetic `"FFMPEG.SW.Decoder"` resource with no `rm_allocate_resources`.
2. `NeedPulseResource` (0x48124) is true for **cat 8**. It is also true for cat 6 with the SW flag when the main out (rm 9) is already held by the *same* app id. It leads to `AllocPulseResource_` (0x47edc), which is a synthetic `"Pulse.Audio.Out"` with no rm call.
3. Otherwise `AllocHWResource_` → `rm_allocate_resources`.

Then:
- `GetAudioSinkPluginName_` (0x5d3dc) returns `pulsesink` when the audio-out component is `Pulse.Audio.Out`.
- `NeedAvocPlayerRegister_` (0x5d0d0) returns 0 in two cases:
  - **No audio track** (`"Not found audio track"`).
  - `NeedPulseResource` is true (`"sw decoder + pulsesink, don't need avoc register"`).
- `AvocPlayRequest_` likewise skips with `"sw decoder + pulsesink, don't need avoc request"`.

**.NET:** `LF/usr/apps/csapi-tv-tizenfx/shared/lib/Tizen.TV.Multimedia.ESPlayer.dll` (csapi-tv-multimedia 1.1.17). Class `Tizen.TV.Multimedia.ESPlayer`:
- `Open`, `SetAlternativeVideoResource(uint)`, `SetDisplay(Tizen.NUI.Window)`, `SetDisplay(ElmSharp.Window)`
- `GetVideoWindowControl().SetDisplayArea(x,y,w,h)`, which sets DstRoi plus `SetDisplayRoi`
- `SetDisplayMode`, `SetDisplayVisible`, `SetAudioMute`
- `SetStream(VideoStreamInfo|AudioStreamInfo)`, `PrepareAsync(Action<StreamType>)`, `Start`
- `SubmitPacket(ESPacket)`, `SubmitEosPacket`
- `GetVirtualResourceId(ResourceType.VideoRenderer)`
- Events: `ResourceConflicted`, `EOSEmitted`

The native handle is the private field `player` (`IntPtr`). There is **no** managed wrapper for alternative audio, codec type or decoded-frame callbacks, so P/Invoke those against `/usr/lib/libesplusplayer.so` using that handle.

**Samsung precedent:** `LF/usr/apps/com.samsung.tv.camera-launcher/bin/CameraLauncher.dll`, `CameraPlayer.CreateCameraPlayer` (decompiled) calls:

```
new ESPlayer(); Open(); SetAlternativeVideoResource(0u); SetDisplay(playerWindow); SetDisplayVisible(true); SetAudioMute(true)
```

It then sends a video-only stream (`SetStream(VideoStreamInfo{mimeType=11,…})`, `SetLowLatencyMode(256)`) and places it with `VideoCanvasView` / `IVideoWindowControl`.

### 2b. capi-media-player (muse-server → libmmfplayer; the `VideoView` / `Tizen.Multimedia.Player` path)

TV exports in `LF/usr/lib/libcapi-media-player.so.0` (`readelf --dyn-syms`):
- Resource and mixing: `player_set_fixed_video_scaler`, `player_set_fixed_video_decoder`, `player_set_fixed_max_resolution`, `player_set_audio_mixing`, `player_set_multiview_position`, `player_get_virtual_rsc_id`, `player_set_app_info`
- Codec and buffers: `player_set_audio_codec_type`, `player_set_video_codec_type_ex`, `player_set_video_frame_buffer_type`
- Other: `player_set_audio_only`, `player_set_ini_param`

Implementations in libmmfplayer (decompiled):
- `_mm_player_set_fixed_video_scaler(p, n)` (0x7d8cc): 0 = main, 1 = sub. Values above 1 are forced to 0 (`"it is not oscarp … force use main scaler"`). With 1, the scaler request becomes rm **16**.
- `_mm_player_set_fixed_video_decoder(p, n)` (0x7d834): 0/1 → field `0x3eec` (rm id 0x3e9 when non-zero); 2..5 → field `0x3ef0` = 0..3. **4 → rm 20 (video-decoder-sub)**.
- `mm_player_set_audio_mixing(p, on)` (0xea8c4) sets the ASM mixing type to 1. With the sub audio decoder that requests rm 51 (audio-sub-out) (`__mmplayer_get_resource` case 9).
- `mm_player_set_multiview_position` is a 4-byte no-op stub.
- `mm_player_set_app_info` calls `rm_set_app_id`, which ties the player to the app's zone.
- `_mmplayer_set_codec_type` (0x7eb40): audio SW sets flag `0x6b10`, and the sink becomes `pulsesink` (`"audio codec SW case, use pulsesink"`).
- **But** `__mmplayer_gst_create_audio_pipeline` (0xd0848) still calls `__mm_player_register_avoc_functions` (0xd89bc), which calls `avoc_player_register_m(win_id, sec_ctl_source, source==11, rm_handle)`. `__mmplayer_avoc_play_request` (0xd8658) then calls `avoc_play_request_m(id, sec_ctl_source, source==11)`.
  - `sec_ctl_source` is 7 for the pulsesink/PCM sink, 0xa for `mmaudiosink`, and 0xb for the sub audio decoder.
  - This exactly matches the capture `PlayerRegisterM(0,10,0,128)` then `PlayRequestM(128,7,0)`.
- Registration also happens in `__mmplayer_scsa_get_resource` (0x4ff28) and in `_mm_player_activate_stream_internal` (0x9db00, audio-not-permit branch). All three call sites are audio-related [INFERENCE: a file with no audio track builds no audio pipeline and so makes no avoc call].

.NET access:
- `Tizen.Multimedia.Player.Handle` is **public**; `OnPreparing()` is protected virtual. So a subclass, or a plain caller before `PrepareAsync`, can P/Invoke the knobs above.
- `Tizen.TV.Multimedia.Player` (TV DLL) adds `player_set_audio_mixing`, `player_set_app_id`, `player_set_ini_param` and others, but no fixed scaler.
- NUI `VideoView` hides its handle (`GetNativePlayerHandle` is internal) and prepares on its own.

### 2c. Other

- `libcapi-media-epplayer` has no resource selectors.
- `libmmfwfdsinkvd` (mirroring) has `mm_wfd_sink_vd_set_sub_mirroring_mode` and `…_set_multiview_mode`. These are internal to the mirroring sink.
- `libtv-resource-manager.so.1` provides `rm_*`, including `rm_notify_app_zone_info`, `rm_get_resource_list` and `rm_get_scaler_state`.
- `libtv-resource-information.so.1` provides `ri_*`, including `ri_mv_get_resource_strategy` and `ri_get_zone_id_based_reclaim_policy`.

## 3. Audio

1. **A sub output exists in hardware but is not usable outside Multi View.**
   - rm 51 `alsa_control_sub_out` and the sub audio decoder `/dev/dvb/adapter1/audio1` are both free.
   - avocd stores `audio_out` as 0 = main, 1 = sub. `avocd_get_audio_out_by_rscID` forces values above 1 to MAIN.
   - In `_avocd_play_request`, the `audio_out` value comes from resource-center (`avocd_rc_player_request_play`), not from the caller.
   - avocd uses the sub mixer set (`sub out source select`) only when dual-sound or Multi View mode is on. Otherwise it does `main out source select = source`. (AudioOutMap decompile of avocd; mixer-control names are in `libaudio-control-impl-sec`.)
   - So `PlayerRegisterM(…, audio_out=1, …)`, `AvPlayerRegisterM(i resource_id, i source_id, i audio_out, out i player_id)` and `player_set_audio_mixing` all still end in "main out source select" in single view [INFERENCE from the decompile; not run].
2. **PulseAudio mixes alongside the HDMI programme.**
   - Already verified: the WavPlayer chime over HDMI kept the show's sound (`docs/03-video/cftv.md`, 2026-11-07).
   - `libaudio-control-impl-sec` has `pcm main pulse gain`. Pulse PCM is mixed into the main out with its own gain, separately from the source select [INFERENCE from the control name].
   - `LF/etc/pulse/stream-map.json` role `media` covers frameworks player, wav-player, tone-player and audio-io.
   - Pulse runs as a system instance with an anonymous socket (`LF/etc/pulse/system.pa`, `client.conf`).
3. **Media-player audio through Pulse without avoc.**
   - ESPlusPlayer simple mix out works as described: alternative audio resource 2 plus SW audio codec gives `Pulse.Audio.Out`, `pulsesink`, and no avoc register or request.
   - libmmfplayer's SW codec gives pulsesink **but still registers and play-requests avoc** with source 7, so it still steals main out.
   - `player_set_sound_stream_info` / `ApplyAudioStreamPolicy` only change the Pulse stream role; they do not stop the avoc calls.
   - `IsAudioOnly` is the opposite of what we want.
4. **avocd configuration.**
   - No source or out table exists as a config file. Only migration scripts were found: `LF/usr/share/upgrade/vd_scripts/essential_scripts/003_avoc_mig.sh` and `LF/usr/apps/migration/jobs/avoc.migration.json`.
   - Source ids 0..11 are bounded in `_avocd_play_request`. Seen values:
     - 7: PCM/MM, pulsesink or alsasink-tv.
     - 10: HW decoder via `mmaudiosink`.
     - 11: sub audio decoder, registered with `audio_out=1`.
     - trackrenderer `GetAvocSourceType` returns 7, 10 or 11 on the same lines.

## 4. Video without the hardware plane

| Path | Frames to app? | Takes HW decoder? | Takes a scaler? | avoc? |
|---|---|---|---|---|
| `Tizen.Multimedia.Player.EnableExportingVideoFrame()` (`Display` must be null; `player_set_media_packet_video_frame_decoded_cb`; feature `tizen.org/feature/multimedia.raw_video`) → libmmfplayer `fakesink`/tbm | yes, `MediaPacket` (tbm) | yes (OMX via rm) unless SW codec | not established. HW frames come as scaler/V4L2 buffers (`"scaler buffer don't have v4l2_info structure"`), so a scaler may still be needed [INFERENCE] | yes, if the file has audio |
| NUI `VideoView(bool swCodec)` (hidden ctor) → dali plugin texture mode (`InitializeTextureStreamMode`, `PushPacket(media_packet_s*)`, `player_set_video_codec_type_ex`) | yes, into a `NativeImageSource` | no if SW codec | unknown | yes, if the file has audio (same libmmfplayer path) |
| ESPlusPlayer `esplusplayer_set_media_packet_video_decoded_cb` + `set_video_frame_buffer_type` (1 copy, 2 reference, 3 raw, 4 scale) + `set_video_codec_type(SW)` | yes | **no**: `FFMPEG.SW.Decoder` is synthetic, no rm | no: `fakesink`; `device-scaler` is only set in the `directvideosink` branch (`CreateVideoSink_` 0x5f05c) | none without an audio track or with simple mix out |
| Own decoder: P/Invoke `libavformat.so.57` / `libavcodec.so.57` (NEON build; decoders h264, hevc, vp8, vp9, mpeg4…) / `libswscale.so.4` | yes | no | no | no |

- SW decoders: `/etc/multimedia/mmfw_media_codec.ini` lists `avdec_h264` and others. `LF/usr/lib/gstreamer-1.0/libgstffmpeg.so` and `libgstpulseaudio.so` are present.
- The CPU is ARMv7. SW decode is realistic for SD/720p30, not for 4K [INFERENCE].

## 5. Candidate paths, ranked

Shared observation kit for every test (read-only, on the TV). The baseline values are today's:

```
R="busctl --system call rscmgr.resource /rscmgr/resource rscmgr.resource"
$R GetResourceList i 7     # main scaler: must stay (11,3,…,"org.tizen.tv-viewer",124)
$R GetResourceList i 16    # sub scaler: expect our app/pid as owner during the test
$R GetResourceList i 9     # main audio out: must stay tv-viewer 124
$R GetActiveAudioOut i 124 # must stay 1
busctl --system monitor org.tizen.tv.avoc org.tizen.tv.system.resource   # must show no PlayerRegisterM/PlayRequestM from us
enlightenment_info -hwc_wins   # which plane the clip lands on (tv-viewer's tvvideosink must keep its plane)
```

Plus the user's eyes and ears. If anything goes wrong, the fallback is `restore_source`.

### A. ESPlayer, video-only, sub scaler; sound through Pulse separately (most likely)

**Calls.**
1. `var es = new Tizen.TV.Multimedia.ESPlayer(); es.Open();`
2. `es.SetAlternativeVideoResource(1)`. Use 3 to keep the main decoder; both decoders are free.
3. `es.SetDisplay(window)`, `es.GetVideoWindowControl().SetDisplayArea(x,y,w,h)`, `es.SetDisplayVisible(true)`.
4. `es.SetStream(new VideoStreamInfo{mimeType=H264, width, height, maxWidth, maxHeight, num, den})`. Look up the `VideoMimeType` value for H.264 in the DLL.
5. `await es.PrepareAsync(_ => feed())`, `es.Start()`, then `SubmitPacket(ESPacket{…})` per access unit with pts, and finally `SubmitEosPacket`.
6. Demuxing:
   - either on the host (H.264 Annex-B access units plus pts, uploaded),
   - or in the agent via P/Invoke `avformat_open_input` / `av_read_frame` from `/usr/lib/libavformat.so.57`.
7. The clip's sound goes out as WAV through the existing `WavPlayer` / `AudioPlayback` path, which is Pulse and already proven harmless.

**Resources.**
- Video: rm 20 (or 3) for the decoder and **rm 16 (sub scaler)**. Both are free, so `findCandidate` takes them with no reclaim.
- Audio: none. With no audio track, `NeedAvocPlayerRegister_` returns 0.

**Risk to tv-viewer.** Low to medium.

Unknowns:
- Whether the sub scaler's overlay plane is composited on screen outside Multi View/PIP.
- Whether resource-center reacts to a zone-less app holding the sub scaler (`NeedScalerSwapNonMobileZone`, best-quality-zone logic) [INFERENCE].
- Whether `directvideosink` calls avoc `VideoStart` for the sub source (`avoc-sub-source` property, `gst_wayland_sink_avoc_start`).
- Whether the agent's SMACK label may open `/dev/video11` and `/dev/dri/card0` in-process.

**Cheapest test.**
- Play 5 s of a 640×360 H.264 elementary stream with no audio, uploaded as Annex-B plus a pts list, through steps 1–5.
- Run the observation kit before, during and after.
- Pass if: rm 16 is owned by us and rm 7 / rm 9 stay with tv-viewer; there is no avoc `PlayerRegisterM`; the programme picture and sound are uninterrupted; the clip is visible.
- If the clip is invisible but nothing broke: the sub plane is not shown in single view. Retry with value 3, then fall back to D.

### B. ESPlayer with its own audio via simple mix out

**Calls.**
- Everything in A, plus an audio stream (`SetStream(AudioStreamInfo{AAC…})`).
- Before `SetStream(audio)` and `PrepareAsync`, add P/Invoke on `h = (IntPtr)typeof(ESPlayer).GetField("player", NonPublic|Instance).GetValue(es)`:
  - `esplusplayer_set_alternative_audio_resource(h, 2)`
  - `esplusplayer_set_audio_codec_type(h, 1)`
- Optionally `esplusplayer_set_simple_mix_out_buffer_level`.

**Resources.**
- Audio: synthetic `FFMPEG.SW.Decoder` and `Pulse.Audio.Out`, so no rm and no avoc.
- Video: as A.

**Risk.**
- Low for the programme's sound if the decompiled logic holds.
- The upstream doc remark "simple out is no sync support" [from memory, unverified] means A/V sync may be loose.
- The value 2 → simple-mix mapping is read from the code path above (C wrapper passes the value through to Convert 2→3, then cat 8). It has not been run.

**Cheapest test.**
- Audio-only ESPlayer: no video stream, an AAC elementary stream, the two P/Invokes above.
- Watch `busctl monitor` for the absence of `PlayerRegisterM` while the clip is audible over the programme's sound.
- This tests the audio half without touching any video resource.

### C. `Tizen.Multimedia.Player` with `player_set_fixed_video_scaler(Handle, 1)`, video-only file; sound through Pulse separately

**Calls.**
1. `var p = new Player(); p.SetSource(new MediaUriSource(file)); p.Display = new Display(window);`
2. `[DllImport("libcapi-media-player.so.0")] int player_set_fixed_video_scaler(IntPtr p, int type)`, called with `(p.Handle, 1)`. Optionally `player_set_fixed_video_decoder(p.Handle, 4)`.
3. `await p.PrepareAsync(); p.Start();`
4. Use a clip with **no audio track**; sound as in A.

The capi C signatures are inferred from the libmmfplayer side, `(player_h, int)`.

**Advantage.** URL, HLS and MP4 playback with no demuxer of our own; it is the same engine as today's `VideoView`.

**Resources.** rm 16 (sub scaler) and rm 3 or 20. No avoc call is expected without audio [INFERENCE: every libmmfplayer avoc register site is on the audio side].

**Risk.** Medium.
- muse-server's dispatch of `fixed_video_scaler` might be gated by privilege or app id (`player_disp_set_fixed_video_scaler`, not traced).
- libmmfplayer may still have a non-audio avoc or `VideoStart` path through `directvideosink`.
- If the knob is ignored, this repeats today's failure: the picture is lost and `restore_source` is needed.

**Cheapest test.**
- Same observation kit, with a 5 s video-only MP4 (`ffmpeg -an`).
- First check the return value of `player_set_fixed_video_scaler`, then confirm the rm 16 owner before `Start()`.
- If rm 7 changes hands, stop immediately and run `restore_source`.

### D. Pure software decode into a NUI texture (certain to be safe, costs CPU)

**Calls.**
- Either the ESPlayer decoded-frame path (`set_video_codec_type(h,1)`, `set_display(h, 0 /*NONE*/, null)`, `set_video_frame_buffer_type(h, 1 /*copy*/)`, `set_media_packet_video_decoded_cb`) feeding a NUI `NativeImageSource` / `PixelBuffer`,
- or P/Invoke libavcodec/libswscale directly.

**Resources.** None (synthetic SW decoder, `fakesink`). Audio as A or B.

**Risk.** None to tv-viewer [INFERENCE: no rm, no avoc, no plane]. Costs ARMv7 CPU; frame rate is unknown.

**Cheapest test.** In the agent, decode 5 s of 480p H.264 with libavcodec to RGBA with no display, and log fps and CPU. This has zero interaction with the media stack.

### E. Hardware decode with frame export (`EnableExportingVideoFrame`)

Low. It still allocates a HW decoder and possibly a scaler through libmmfplayer, and avoc is involved if the file has audio. It adds nothing over A, C or D.

### F. Real Multi View / zones

Lowest for this goal.
- **Calls:** `SetAppToZone(cmd, appid, 2)` / `rc_set_app_zone_info`, multiview vconf `memory/multiscreen/info` mode `"on"`, or launching `com.samsung.tv.multiscreen` (`ScreenOnScreen.LaunchMultiView`).
- **Risk:** it re-lays out the whole screen, changes audio focus and speaker routing (`MultiviewSpeakerChanged`), and may terminate apps (`all apps in multiview will be terminated`). It is a forbidden-class state change while the user watches.
- **Test:** none proposed during viewing. Only with the user's consent, launch Multi View by remote (`XF86MultiView`) while capturing `busctl monitor` to learn who calls `SetAppToZone`.

## 6. Corrections to earlier sub-reports

- Some scout notes said `libesplusplayer.so`, `libplusplayer*.so`, `libtrackrenderer.so`, `libmixer.so` and `usr/lib/pulse-13.0/modules/*` are absent from livefs. They are present; the scouts' globs honoured `.gitignore`. Checked with `ls LF/usr/lib` and `find`.
- `resource_information.xml` on the TV → `resource_information_dual.xml`, verified with `readlink` and `md5sum` on the TV.

## 7. Unknowns that the tests above resolve

1. Whether the sub scaler (`sec_scaler1`, `overlay="1"`) is shown on screen in single view (tests A and C).
2. Whether muse-server honours `fixed_video_scaler` for a root/borrowed-slot client (test C).
3. Whether an in-process `libesplusplayer` works from the agent's SMACK domain (tests A and B).
4. The exact `VideoMimeType` / `AudioMimeType` / `ESPacket` field units for the .NET `ESPlayer` (read from the DLL before coding).
