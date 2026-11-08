# cftv: put things on the TV

Updated 2026-11-06. Code: `apps/cftv.cs` (the agent, on the TV), `tv/cftv.py`
and `bin/cftv` (host). The root channel installs and starts the agent
(`bin/cftv` starts `tv/chan.py serve` itself when it is not running);
commands go to the agent over TCP. cftv replaces the one-off demo apps
`ball`, `say`, `overlay` and `video-probe`, which are deleted. It follows the
platform rules in `docs/03-video/transparent-overlay.md`.

Timings (2026-11-05, before commands moved to TCP; a new 600×300 PNG with
alpha, `{"ok":true}` = on screen):

| state | time |
|---|---|
| channel and agent running | 1.1 s (agent check 0.73, upload 0.17, show 0.22) |
| agent not running | 1.2 s (agent ready 0.3 s after launch) |
| neither running (`bin/cftv image …` from scratch) | 3.1 s |
| `move` of a shown item | 0.25 s (2026-11-06 over TCP: `bin/cftv ping` 0.25 s, most of it the `ensure()` check) |

## Usage

```sh
bin/cftv image logo.png --at 100,80                 # natural size from the file header
bin/cftv image cat.gif --at 1400,600 --size 400x400 --id cat
bin/cftv text "Dinner's ready" --at 560,60 --size 800x120 --color '#FFD000' --bg '#00000080' --font 64
bin/cftv rect --at 0,1040 --size 1920x40 --color '#00C8FF' --radius 8 --id bar
bin/cftv video clip.mp4 --at 1280,40 --size 600x340 --loop
bin/cftv video http://host/live.m3u8 --at 0,0 --size 960x540
bin/cftv sound chime.mp3 [--loop] [--volume 0.5] [--id NAME]   # any format ffmpeg reads, or a URL
bin/cftv say "the laundry is done"
bin/cftv notify "Ready in 5 minutes" --title Dinner --icon pot.png --sound [--duration 5] [--id NAME]
bin/cftv move cat [--at 200,200] [--size WxH] [--opacity 0.5]
bin/cftv remove cat | clear | ping | log
bin/hud live | off                                  # the recognition HUD draws through the `hud` op (docs/03-video/hud.md)
```

| option | commands | default (`move`: unchanged) |
|---|---|---|
| `--at X,Y` | image, text, rect, video, move | 0,0 |
| `--size WxH` | image, text, rect, video, move | image: natural size from the PNG/GIF/JPEG/WebP header; otherwise 400×300 |
| `--opacity F` | image, text, rect, video, move | 1.0 |
| `--id NAME` | image, text, rect, video, sound | file stem for images; otherwise `text`, `rect`, `video`, `sound` (`say` is always `say`) |
| `--color #RRGGBB[AA]` | text, rect | text `#FFFFFFFF`, rect `#FF0000FF` |
| `--bg #RRGGBB[AA]` | text | `#00000000` |
| `--font PX` | text | 48 |
| `--radius PX` | rect | 0 |
| `--loop` | video, sound | off |
| `--volume F` | sound, notify (with a sound file) | the file's own level; scales the samples on this computer |

`notify` takes the text as its optional positional argument (a title, the
text, or both) and these options:

| option | default |
|---|---|
| `--title TEXT` | none |
| `--icon FILE` | none; shown 72×72 left of the text |
| `--sound [FILE]` | silent; `--sound` alone plays the TV's chime `/usr/share/feedback/sound/operation/normal_noti.wav` |
| `--duration S` | 5; `0` keeps the card until `remove ID` |
| `--width PX` | 560; the height follows the wrapped text |
| `--id NAME` | new per card, so cards stack; reusing an id replaces that card |

From Python: `cftv.notify("text", title=..., icon=..., sound=cftv.SYSTEM_SOUND,
duration=...)` in `tv/cftv.py` takes the same options and returns the reply.

Coordinates are 1920×1080 screen pixels. Reusing an id replaces the item.
`move` keeps any value you leave out. `ping` lists the item ids. `clear`
removes every item; the agent keeps running (there is no `quit`, see below).
`log` prints the last 20 lines of the agent log. Each command prints the
agent's JSON reply, for example `{"ok":true}`, and exits 0 when `ok` is true.

## How it works

```
host                                         TV
tv/cftv.py ensure() ──channel (tv/chan.py)──> install/start the agent, read cftv/token
tv/cftv.py send()   ──TCP $CF_TV_IP:7741──> agent: token line, then one JSON line per command
                    <──────────────────────────── one JSON reply line per command
```

- **Agent:** one persistent NUI app in the borrowed slot
  `com.samsung.tv.social-chat-app`. It runs as uid 5001 (`owner`), label
  `User::Pkg::com.samsung.tv.social-chat-app` (verified 2026-10-24). Every
  command except `log` first calls `tv/cftv.py ensure()`:
  - It builds `build/cftv/Cftv.dll` when `apps/cftv.cs` or
    `apps/cftv.csproj` is newer (recipe: `docs/03-video/app-build.md`).
  - It installs the DLL when the slot DLL's md5 differs, first stopping the
    running app (`stop_agent`, below).
  - If the agent is not running, it starts it with
    `app_launcher -u 5001 -s` and waits up to 10 s for `cftv/ready` (the
    agent's pid). Either way it reads `cftv/token`.
- **The agent never exits by itself.** Any exit (its own `Exit()`,
  `app_launcher -t`, `-k`) hands focus to Smart Hub, and the Smart Hub bar
  slides up over the picture ~1 s later. Screenshots on 2026-11-05 showed it
  after `Exit()`, also with the window hidden first. `remove` and `clear` do
  not do this. So there is no `quit`, and the one exit left (replacing the
  DLL) goes through `stop_agent`: it notes the focused app, stops the agent,
  and if focus moves within 3 s, resumes that app. On 2026-11-05 it moved
  focus from Smart Hub back to `tv-viewer` before the bar showed (no frame
  had it).
- **Commands:** the agent listens on TCP port 7741. A client sends the
  token first, then one JSON command per line, and gets one JSON reply per
  line. The token is 32 random bytes, new at every agent start, in
  `cftv/token` (mode 0600, uid 5001); a wrong token closes the connection
  (logged as `rejected`). A reader thread per connection posts each command
  to the UI thread and waits for the reply, so one connection's commands run
  in order. `tv/cftv.py` keeps one connection per process; if it breaks (the
  agent was restarted), it runs `ensure()` and sends once more. `hud`
  commands are not logged (the log is on flash). Until 2026-11-06 commands
  went through files in `cftv/in/` and `cftv/out/`, written by a shell over
  the channel and polled by the agent every 50 ms: ~10 processes and
  100–230 ms per command. Over TCP a `hud` command takes 4.8 ms median
  (p90 16.5 ms, 50 commands, 2026-11-06).
- **Media:** a local file is uploaded once into `cftv/media/`, named by the
  first 16 hex digits of its sha256 plus its suffix. The upload goes over the
  channel's `put` (WRITEFILE) in 4 MiB chunks to the agent staging directory,
  then `cat` into place. Anything containing `://` (http, https, rtsp, HLS)
  goes straight to the player.
- **Items:** `.gif`/`.webp` play in an `AnimatedImageView` that loops
  forever. Other images use `ImageView`. Text is a `TextLabel`. Rect is a
  `View` with `CornerRadius`. Video is a `VideoView`. Each visual item gets its
  own window.
- **Sound:** `WavPlayer` with a `Notification` stream policy, which mixes
  the sound into whatever the TV is playing. It plays local WAV and OGG files
  only and has no volume, so `tv/cftv.py` (`sound_media`) converts every
  other file or URL with ffmpeg to 48 kHz stereo WAV, scaled by `--volume`,
  before the upload. A loop starts the file again until `remove`.
  Until 2026-11-07 sounds played through a media `Player`, which cut the
  programme's sound for good; see "A sound cut the programme's sound".
- **Notify:** a card (rounded `View`, optional `ImageView` icon, bold title
  and text `TextLabel`s sized with `GetHeightForWidth`) in the top-right
  corner, 32 px from the edges. Newest on top, 16 px apart. The card's window
  reaches from the card to the screen's right edge; the card slides in from
  outside the window (450 ms, `Animation` on `PositionX` and `Opacity`), so
  the window edge clips it as it appears. When the duration ends, or on
  `remove`, it slides out (350 ms) and is removed. NUI cannot animate a
  window, so the other cards move by stepping their window position with a
  16 ms timer (250 ms, ease-out). The sound starts before anything moves.
  `clear` removes cards at once, without sliding.
- **Frames (experiment, 2026-11-07):** TCP port 7743 takes MJPEG frames and
  PCM from the host and draws them without the TV's media player
  (`apps/cftv-frames.cs`), so the programme keeps its picture and sound.
  No `bin/cftv` command uses it yet; `tools/frames_bench.py` drives it.
  Measurements: `docs/90-log/2026-11-07-sound-and-video/`.
- **esvideo (experiment, 2026-11-07):** an H.264 Annex-B file through
  Samsung's ESPlusPlayer on the TV's sub decoder and sub scaler
  (`apps/cftv-esplayer.cs`): hardware decoding on its own plane while the
  programme keeps the main scaler and its sound. Video only so far. The
  wrapper's privilege check is bypassed by reflection. `tools/esvideo_probe.py`
  drives it; evidence in the same log.
- **Say:** `TTSPlayer` in `Default` mode. The reply carries the engine state.
  If the state is still `Unavailable` 1.2 s later, the text is played once
  more (cold engine, `docs/04-system/control.md` §3).
- **Files on the TV:** `cftv/cftv.log` (agent log), `cftv/ready` (pid), and
  `cftv/source` (saved input, see below).

## Video and the TV's own picture

A cftv video takes the TV's hardware video path. The HDMI/tuner picture goes
black while it plays and **does not come back on its own** after the video is
removed: `tv-viewer`'s `tvvideosink-*` windows disappear and stay gone. These
did not help: relaunching `tv-viewer`, quitting the agent, and re-selecting
the input that is already active (a no-op).

A real input change does work: switch to another source, then back. The calls
were captured with `busctl monitor` while the user switched by hand:

```
com.samsung.ISourceControl  /com/samsung/Object  MethodCall(function, size, ay)
  function 4, 26-byte query  -> reply's last int32 = current source
  function 0, 77-byte select -> int32 at byte 32 = source; buffer ends "org.tizen.tv-viewer"
  sources seen: 0 = TV tuner, 15 = HDMI (AVR S750H)
```

What `tv/cftv.py` does:

- **Before a video:** if `cftv/source` does not exist yet, it saves the
  current source and whether the auto-reconnect Bluetooth speaker is
  connected.
- **When a reply has `video_released`** (`remove`, `clear`; not a
  video replacing a video): it selects 0, waits 2.5 s, selects the saved
  source, and waits up to 10 s for the `tvvideosink` windows to come back. If
  the speaker was connected before, it disconnects and reconnects it, because
  the bounce moves audio to the AVR over HDMI (details in
  `docs/04-system/bluetooth.md`, "Audio routing"). If the saved source is the
  tuner, it does nothing.
- **Result:** `{"ok":true,"video_released":true,"source_restored":true}`. The
  picture follows a few seconds later. On 2026-10-24 the HDMI picture came
  back and the speaker reconnected, 9.3 s end to end.

The tuner shows briefly during the bounce. Playing on the TV's
secondary/PIP video path instead, so nothing is taken over, has not been
investigated.

## Status

| item | status |
|---|---|
| image (PNG with alpha), animated GIF, text, rect, move, remove, clear | render on screen; focus stays on `tv-viewer`; no launch screen or Smart Hub bar (screenshots 2026-11-05) |
| JPEG, animated WebP | no record of a cftv test (`cftv/media/` holds only PNG, GIF, MP3 and MP4 uploads); WebP in `AnimatedImageView` worked in the 2026-10-23 ball demo (unverified via cftv) |
| video | plays; the input is restored automatically afterwards (verified 2026-10-24) |
| sound | through `WavPlayer`: the user heard the chime over a show on HDMI and the show's sound kept playing (2026-11-07); the bus showed PulseAudio `SoundPlay` and no `avocd` player call, and `tv-viewer`'s player 124 kept the main output |
| say | audible: the user heard it (verified 2026-10-24) |
| notify | slide in, stacking, push down, slide out and closing the gap: frame captures at 18–22 fps over the NVIDIA Shield's ambient screen on HDMI (2026-11-05); stacked cards over a show, a card updated in place by id, and removal (screenshots 2026-11-07); its chime is the `sound` path below, heard 2026-11-07 |
| hud | lock-on, glide, fade-out, scan line and status panel: frame captures at ~20 fps and a 50 s live run over YouTube on HDMI (2026-11-05); `docs/03-video/hud.md` |
| video soundtrack | not checked by ear (unverified) |

## A sound cut the programme's sound (2026-11-07)

A `notify --sound` chime over a show on the NVIDIA Shield (HDMI, Bluetooth
speaker) played, and the show's sound stopped for good; reconnecting the
speaker did not bring it back. A bus capture of the media `Player` path
(`busctl --system monitor`) showed why:

1. `muse-server` registers the player with `avocd` (`PlayerRegisterM`) and
   asks for the main audio output (`PlayRequestM(player, source 7, out 0)`).
   `avocd` and `resource-center` give it the output; the HDMI source loses
   it.
2. On `PlayerUnregister` nothing asks for the output again: afterwards
   `rscmgr` `GetActiveAudioOut` was 0 for every player id from 0 to 140.

`avocd` has no call that lists players, so the HDMI source's player id, which
a direct `PlayRequest` would need, is unknown. Changing the input away and
back makes the TV set the HDMI audio up again (as after a video,
`restore_source`). `WavPlayer` goes through PulseAudio alone (`SoundPlay`,
`EOS`) and makes no `avocd` call. Video items still use a media player
(`VideoView`), and `restore_source` still bounces the input after them.

Screenshots of cftv items show each item twice; see "Double image" in
`docs/02-capture/screen-capture.md`.
