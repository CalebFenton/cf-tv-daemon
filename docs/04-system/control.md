# Controlling the TV: keys, volume, sound, TTS, audio routing

Device: QN75QN85DAFXZA, `T-NKM2AKUC-2301.1`, ARMv7. Updated 2026-10-24.
Every command here runs as root through the channel (`tv/chan.py serve` must
be up; `docs/01-root/access-contexts.md`). Bluetooth is in
`docs/04-system/bluetooth.md`.

## Quick reference

```sh
bin/control key XF86Back               # one press (down + up)
bin/control key Down 3                 # three presses, one round trip
bin/control keys                       # the 47 names bin/control knows, with descriptions
bin/control vol up | down | mute | get # pactl on sink 0: ±5 %, toggle mute, read back
bin/control vol set 40                 # pactl absolute: 40 %
bin/control ch up | down | list | guide | prev | favorite   # sends the channel key; `up` opens Accessibility Shortcuts (§1)
bin/control raw 'pactl list sinks short'                    # any shell command on the TV
bin/cftv say "the laundry is done"     # text to speech (§3)
bin/cftv sound chime.mp3               # play a sound (§2)
```

`bin/control key Return` and `key minus` print a note first: they commit
whatever has focus.

---

# 1. Keys

## The primitive

Enlightenment's test interface on the system bus:

```
org.enlightenment.wm  /org/enlightenment/wm
  org.enlightenment.wm.Test.EventKey(uint32 pressed, string keyname)   # 1 = down, 0 = up
```

`/usr/bin/input_keyevent NAME` is the TV's own bash wrapper: two `dbus-send`
calls, down then up (read 2026-10-24). `bin/control key NAME N` runs it N times
in one channel command.

Root is required. `/etc/dbus-1/system.d/org.enlightenment.wm.conf` (read
2026-10-24):

```xml
<policy user="root">      <allow send_path="/org/enlightenment/wm"/> </policy>
<policy context="default"> <deny  send_path="/org/enlightenment/wm"/> ... </policy>
```

A `boolean true` reply from `dbus-send` only proves delivery, not that
anything acted on the key.

## What each key does

| key | result | when |
|---|---|---|
| `Return` | works: moves focus, activates tiles, launches apps | 2026-10-23 |
| `Up` / `Down` / `Left` / `Right` | works: d-pad navigation | 2026-10-23 |
| `XF86Back` | works: dismissed the Smart Hub bar (2026-10-24). With `tv-viewer` focused on HDMI it reaches the HDMI device: the NVIDIA Shield left what was playing for Prime Video (2026-11-05) | 2026-10-24, 2026-11-05 |
| `XF86AudioMute`, `XF86AudioRaiseVolume` | delivered, no effect seen; not re-tested | 2026-10-23 |
| `XF86RaiseChannel` | opens Accessibility Shortcuts; does **not** change channel | 2026-10-23 |
| everything else | untested | |

**Keys go to the focused app, and from `tv-viewer` on to the HDMI device**
(HDMI-CEC). Check the focused app (`enlightenment_info -topvwins`, `O` in
the `Foc` column) before injecting anything.

## Key names

The device keymap `/usr/share/X11/xkb/tizen_key_layout.txt.tv` has 364
entries (counted 2026-10-24); all names are in `docs/04-system/key-names.txt`.
`bin/control` passes any name through, warning only if it is unknown and does
not start with `XF86`/`KEY_`. Useful ones:

| purpose | key |
|---|---|
| d-pad, ok | `Up`, `Down`, `Left`, `Right`, `Return` |
| back, home, menu | `XF86Back`, `XF86Home`, `XF86Menu` |
| volume, mute | `XF86AudioRaiseVolume`, `XF86AudioLowerVolume`, `XF86AudioMute` |
| channel | `XF86RaiseChannel`, `XF86LowerChannel`, `XF86ChannelList`, `XF86ChannelGuide`, `XF86PreviousChannel` |
| power | `XF86PowerOff`, `XF86Sleep`, `XF86ScreenMute` |
| digits | `0`–`9`, `minus` |

## Rules

- **Read the screen before a committing key.** `Return` acts on whatever has
  focus. A blind `Return` in a menu that `XF86RaiseChannel` had opened turned
  Voice Guide on (§5). Capture first: `bin/snap`
  (`docs/02-capture/screen-capture.md`).
- **Channels:** no working way to change channel is known (`XF86RaiseChannel`
  does not; the other channel keys and digits are untested). Switching the
  *input* (tuner, HDMI) works through `com.samsung.ISourceControl`; see
  `docs/03-video/cftv.md`.
- The TV's remote-control websocket (msf-server, ports 8001/8002) needs an
  on-screen pairing prompt; not used here (unverified).

## Which key launches which app

The remote's key-to-app bindings are a table: `/opt/dbspace/.app-context.db`,
table `key_filter`, 78 rows (verified read-only 2026-10-24):

```
appid | key | longpress | override | on_selector | request | target_selector | args
com.samsung.tv.broadcastingmenu|XF86Info|5000|||launch|package:com.samsung.tv.broadcastingmenu|ID:information-window
org.tizen.graphiczoom|XF86KeyGraphicZoom||||force-launch|package:org.tizen.graphiczoom|
```

```sh
sqlite3 'file:/opt/dbspace/.app-context.db?mode=ro' 'select * from key_filter'
```

Rebinding a key to our own app (the cftv slot) should be possible here.
Untested: whether the owning service caches the table, and whether edits
survive. Back it up with `sqlite3 … ".backup"` before any change
(`docs/04-system/debloat.md`).

---

# 2. Volume and sound

## Volume: PulseAudio

```sh
pactl set-sink-volume 0 40%      # absolute
pactl set-sink-volume 0 +5%      # relative
pactl set-sink-mute 0 toggle
pactl list sinks                 # read back
```

Set and read-back verified 2026-10-23 (`40%`, `Mute: yes`). Whether this
changes what you hear on the TV's speakers, the AVR, or the Bluetooth speaker
is **unverified**. Sinks: `0` = `tizenaudio-sink` (s16le, 2 ch, 48000 Hz),
`1` = `sink_null`. PulseAudio does not show the HDMI or Bluetooth outputs
(§4).

## Playing a sound

- `bin/cftv sound FILE|URL [--loop] [--volume 0.5]`: `WavPlayer` inside the
  cftv agent, mixed into the programme's sound. A media `Player` took the
  main audio output from the HDMI source and never gave it back
  (`docs/03-video/cftv.md`, "A sound cut the programme's sound").
- `paplay /usr/share/sounds/alsa/Front_Center.wav`: exits 0 and the sink goes
  `SUSPENDED` → `RUNNING` (2026-10-23); audibility not recorded.
- WAVs on the device (2026-10-24): `/usr/share/sounds/alsa/` `Front_Center`,
  `Front_Left`, `Front_Right`, `Noise`, `Rear_Center`, `Rear_Left`,
  `Rear_Right`, `Side_Left`, `Side_Right` (`.wav`).

---

# 3. Text to speech

**Works, audible:** `bin/cftv say "text"` (the user heard it, 2026-10-24).
The agent (`apps/cftv.cs` `Say`) uses `TTSPlayer` in Default mode. Notification
mode also spoke (2026-10-23).

## The API

Managed, in `Tizen.NUI`; no P/Invoke. Signatures from `Tizen.NUI.dll` metadata:

```csharp
public class TTSPlayer {                                   // namespace Tizen.NUI
  public static TTSPlayer Get();                           // Default mode
  public static TTSPlayer Get(TTSPlayer.TTSMode mode);
  public static TTSPlayer Instance { get; }
  public void Play(string text);                           // mode is fixed at Get()
  public void Stop(); public void Pause(); public void Resume();
  public TTSPlayer.TTSState GetState();
  public event EventHandler<StateChangedEventArgs> StateChanged;   // PrevState, NextState
  public enum TTSMode  { Default, Notification, ScreenReader, ModeNum }
  public enum TTSState { Unavailable, Ready, Playing, Paused }
}
```

The enums are **nested**: write `TTSPlayer.TTSMode`. Bare `TTSMode` gives
CS0246, `global::TTSMode` CS0400.

## Cold engine: replay only if still `Unavailable`

Observed 2026-10-23: the first `Play()` after idle returns with state
`Unavailable`, then `Unavailable → Ready → Playing`, and it speaks. An
unconditional retry spoke the text twice. `apps/cftv.cs` therefore replays
once, and only if the state is still `Unavailable` 1.2 s later (logged as
`say say replayed after cold engine`).

`GetState()` is not a completion signal: it stayed `Playing` long after the
speech ended (2026-10-23). The `say` reply carries the state right after
`Play()`. A new `say` stops the previous utterance (same item id `say`).

## Native side

| piece | notes |
|---|---|
| `libtts.so` | `tts_add_text`, `tts_play`, `tts_play_pcm`; config manager (`tts_config_mgr_set_speech_rate`, `_set_pitch`, `_get_bg_volume_ratio`) |
| `libcapi-tts-wrapper-tv.so` | TV wrapper: `tts_wrapper_add_pcm` / `play_pcm` / `stop_pcm` |
| `libdali2-adaptor.so` | links `libtts.so`; how NUI reaches it |
| `org.tizen.voice.ttsserver` | D-Bus activated as `ui_fw`; its `Exec` launches the engine app named in `db/voice/tts/engine/default` (`/usr/share/dbus-1/system-services/`, read 2026-10-24). Siblings: `ttsnotiserver`, `ttssrserver`, `ttsinterruptserver` |
| engine | `org.tizen.tts-engine-vd-ATSC` (the configured default), installed in `/opt/usr/apps/`; processes `tts-engine-vd` and `tts-engine-vd-sr` run as `owner` (2026-10-24). `/usr/apps/` also ships `com.samsung.tv.aria-engine` |

No CLI speaks text; `tts_getengine` only reads or writes the engine setting.

---

# 4. Audio output routing

## Outputs and the saved selection

`db/btapp/lastSoundRoute` holds the output chosen in the Sound Output menu:

| value | output |
|---|---|
| 0 | TV speakers (user-confirmed in the Sound Output panel) |
| 1 | seen once, meaning unknown |
| 2 | HDMI, here the AVR (user-confirmed) |
| 5 | Bluetooth speaker |

It is **persisted, not live**: it read 5 after a power loss while the TV
played on its own speakers (2026-10-23). It reads correctly after a menu
change, but never use it as "what is playing now". No live "current output"
query is known. For the Bluetooth speaker, the live check is `bt_product`
`4180` (`docs/04-system/bluetooth.md`).

## Who switches the output: AVOC

`avocd` (root) owns `org.tizen.tv.avoc` `/org/tizen/tv/avoc`, interface
`org.tizen.tv.avoc.AvOutputControl`: 413 methods, 30 signals (introspected
2026-10-24). It emits `DigitalAudioFormatChanged` when the output changes,
e.g. when the Bluetooth speaker connects.

| method | signature | runtime |
|---|---|---|
| `AudioSetSoundSelect` | `(i) → i` | not implemented (2026-10-23) |
| `AudioGetSoundSelect` | `() → (i, i)` | `UnknownMethod` |
| `AudioCheckSoundSelect` | `() → i` | not implemented (2026-10-23) |
| `AudioSetSpeakerInformation` | `(u) → i` | `UnknownMethod` |
| `SetDigitalAudioOutputInForce` | `(i onoff, i type) → i` | unknown |

An `InvalidArgs` reply does not prove a method is implemented: arguments are
checked against the introspection data before dispatch. That mistake once made
`AudioSetSpeakerInformation` look implemented.

## Other layers

- **PulseAudio** (`pulseaudio`, user `pulse`) is the internal path only.
  While the Bluetooth speaker played, `pactl` listed only `tizenaudio-sink`
  and `sink_null`, both `SUSPENDED` (2026-10-23). It says nothing about HDMI
  or Bluetooth output.
- **Input changes can move the audio** off a connected Bluetooth speaker:
  `docs/04-system/bluetooth.md`, "Audio routing".
- **`acm-service` is the network-speaker manager, not an HDMI-CEC bridge.**
  `/usr/apps/org.tizen.acm-service/bin/acm-service` (root; md5 identical to
  `livefs/`, 2026-10-24) serves `org.tizen.tv.networkspeaker.server`
  (`GetNetSpkNodeList`, `GetActiveNetSpkInfo`, …) and has `nam_connect_speaker`
  plus multiroom and soundbar code; its `bin/` holds SNAP mesh-network scripts
  (`snap_start.sh`, `startsnap.sh`). `libAcmEngine.so` is a PTP master/slave
  clock for multi-speaker sync. Neither contains `Anynet`, `CEC` or `HDMI`
  strings. It did not take part in the captured Bluetooth connect.

---

# 5. Accessibility: Voice Guide

On 2026-10-23 a blind `Return` in the Accessibility Shortcuts menu (opened by
`XF86RaiseChannel`) turned on Voice Guide, which then narrated every UI action.

| key | meaning |
|---|---|
| `db/setting/accessibility/tts` | the narration switch (read `True` while narrating) |
| `db/menu/accessibility/tts` | a different toggle; stayed `0` |
| `db/menu/system/accessibility/voice_guide_level` | Voice Guide level; does not stop narration |

Read 2026-10-24: narration off (`db/setting/accessibility/tts` = `False` in
the system and user layers; the other two `0`).
Turning narration off (worked 2026-10-23):

```sh
buxton2ctl set-bool system db/setting/accessibility/tts 0
buxton2ctl set-bool user   db/setting/accessibility/tts 0
```

`set-bool` takes `0`/`1`; `false` returns `Invalid number`.

## Settings storage

buxton2 keeps settings in SQLite: `/var/lib/buxton2/system.db` (also
`system_rtc.db`), table `buxton(key text PRIMARY KEY, data text)`; `data` is
base64 (`<priv>\0<priv>\0`, an int32, …, `if3\0(ssv)`). `buxton2ctl` has no
`list` command, so find keys with SQL:

```sh
sqlite3 /var/lib/buxton2/system.db "select key from buxton where key like '%voice%';"
```

Other keys under `db/menu/system/accessibility/` (2026-10-23):
`audio_description`, `highcontrast`, `focuszoom`, `grayscale`,
`nagative_colors` (sic), `signlanguage/*`, `slow_key_repeat`,
`voiceguide/{volume,pitch,speed,gender,tv_bg_volume}`.

---

# 6. Open questions

1. **Channel change:** no mechanism found.
2. **Volume:** does `pactl` change the audible level on any output? Is there a
   master-volume API (AVOC introspects `AudioMute`; untested)?
3. **Current audio output:** no live query known.
4. **`TTSPlayer.Get(ScreenReader)`:** untested.
5. **Screen dark after ~20 min idle** (user observation, unverified):
   `db/menu/auto_power_off = 6` (read 2026-10-24), meaning unknown.
