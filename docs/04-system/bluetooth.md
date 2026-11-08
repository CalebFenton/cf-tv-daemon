# Bluetooth audio: connect, disconnect, auto-reconnect

The single reference for the Bluetooth speaker. Updated 2026-10-24. Code:
`tv/bt.py` (request strings), `bin/bt-reconnect` (CLI),
`payload/on-boot.d/50-btconnect` (auto-reconnect hook), `tv/cftv.py`
(`restore_source`).

## Status

| capability | status |
|---|---|
| connect / disconnect from code (root) | works: the menu's sequence ending in `180` / `181`, verified many times 2026-10-23/24 |
| live "is audio connected?" | works: `bt_product` `4180`, bit `0x04` of the third byte |
| reconnect after a wake, or with the TV running | works: the `50-btconnect` hook / `bin/bt-reconnect connect`, audio within ~3–10 s of the request |
| route audio back to the speaker after an input change | works: `181` then the connect ([Audio routing](#audio-routing)) |
| reconnect after a power loss when the speaker was **not** connected before | works, but only once the speaker shows up by itself: audio at uptime 76–143 s in five soft-reboot tests (scan-then-connect) |
| reconnect after a power loss when the speaker **was** connected (the normal case) | root cause found and fix deployed (accept the pending authorization, [Power loss](#power-loss)); **not verified**: the speaker had gone to standby when the fix was ready |

| field | value |
|---|---|
| speaker | StealthTech Sound + Charge, `AA:BB:CC:DD:EE:FF` (bonded, dual mode) |
| connect target on the TV | `/opt/cf_tv_daemon/btconnect.addr`, deployed from `payload/btconnect.addr` (gitignored; copy `payload/btconnect.addr.example`) by `tv/root.py boot install` |
| saved output (`db/btapp/lastSoundRoute`) | persisted, **not** a connection signal: `docs/04-system/control.md` §4 |
| Bluetooth stack | Samsung "projectx" `bt-service` (`bluetooth-frwk-service.service`) on top of **Bluedroid** (Android's stack: `bt-btm`, `bt-btif`, `BT_OAL` log tags) |

## Commands

```sh
bin/bt-reconnect status       # live 4180 check, saved route, target address,
                              # last 5 50-btconnect lines of /opt/cf_tv_hooks.log
bin/bt-reconnect connect      # the menu's connect sequence (prints each 180's rc)
bin/bt-reconnect disconnect   # 181
bin/bt-reconnect connect --addr AA:BB:CC:DD:EE:FF
tv/root.py hooks wake         # run the reconnect hook (and every other wake hook) now
```

Runs through the root daemon (`tv/root.py` `daemon_exec`). Request strings come
from `tv/bt.py`; `tv/cftv.py` uses the same ones.

## The requests

All go to `bt-service` over D-Bus, `service_request(i service_type,
i function, i request_type, ay p1 … ay p5) → (i, v)` on
`org.projectx.bt /org/projectx/bt_service org.projectx.bt`, or
`service_request_product` on `org.projectx.bt_product
/org/projectx/bt_service_product`. Needs root; from uid 901 a connect returns
`-9` at once.

`busctl` prints `iv <code>`, then `Failed to parse bus message: Bad message`,
and exits 1 even on success (it cannot print the variant). Read the code from
`iv`, check the state with `4180`. Codes (Tizen `bluetooth-api.h`): `0`
started, `-9` INTERNAL, `-21` NOT_CONNECTED, `-29` IN_PROGRESS.

### Function numbers

From captures, and from `libbluetooth-api.so` on the TV: each
`bluetooth_*` API function loads its number before sending the request
(llvm-objdump, Thumb; checked against the captured `180`/`181`/`178`).
Samsung renumbered the public Tizen enum, so the open-source header does not
apply.

| number | service | meaning |
|---|---|---|
| 14 / 16 | bt | start / stop discovery (14 takes `[0,0] [30,0] [0,0,0,0]`) |
| 91 | bt | `bluetooth_authorize_device(addr, bool)`: `BT_SET_AUTHORIZATION` |
| 98 / 99 | bt | set / get profile trusted |
| 100 | bt | `bluetooth_disconnect_device` (whole device) |
| 176 / 177 | bt | `bluetooth_audio_connect` / `_disconnect` (all audio profiles) |
| 178 / 179 | bt | HFP (AG) connect / disconnect |
| **180 / 181** | bt | **A2DP connect / disconnect** (`bluetooth_av_connect` / `_disconnect`) |
| 243 / 244 | bt | AVRCP controller connect / disconnect |
| 4177 / 4178 | product | **accept** / reject a pending authorization (`addr`, `[service id, 0, 0, 0]`) |
| 4180 | product | connected-profile bitmask |
| 4247, 4248, 482, 484, 493, 4/180 | mixed | the menu's prelude; meaning unknown |

### Live connection check

```sh
gdbus call --system --dest org.projectx.bt_product --object-path /org/projectx/bt_service_product \
  --method org.projectx.bt_product.service_request_product 1 4180 0 \
  '[byte 0x20,0x64,0xde,0x76,0x54,0x4f]' '@ay []' '@ay []' '@ay []' '@ay []'
# (0, <[byte 0x00, 0x00, 0x00, 0x00]>)   not connected
# (0, <[byte 0x00, 0x00, 0x08, 0x00]>)   AVRCP only, NO audio (speaker's own reconnect after a power loss)
# (0, <[byte 0x00, 0x00, 0x0c, 0x00]>)   audio connected (0x04 = A2DP, 0x08 = AVRCP)
```

Audio is up when bit `0x04` of the third byte is set (`tv/bt.py`
`audio_connected`). "Any non-zero byte" was wrong: `0x08` alone is the partial
link, sound stays on the TV speakers.

### The connect: what the Sound Output menu sends

Captured with `busctl --system monitor` (2026-10-23, and 2026-10-24 after a
power loss, including bt-service's replies):

1. Queries `19`, `25`, `bt_product 4176(addr)`, `88(addr)`.
2. Discovery: `14`, ~1 s, `19`, `16`; the speaker appears as `DeviceFound`.
3. `25`, `4176`, `bt_product 4248`, `4247([2,0,0,0])`, `4247([16,0,0,0])`, `88`.
4. `4/180`, `482`, `484`, `493`, then `180(addr)` with request type 1.
5. If `180` returned `-9`: steps 4 again, once. After a power loss the menu's
   first `180` returned `-9` (the radio link came up, the stream setup closed:
   `bta_av_str_closed`, `BTA_AV_OPEN_EVT::FAILED status: 3`) and the second
   returned `0`. The same `-9`-then-`0` happens with our copy.

`tv/bt.py` `connect()` and the hook send this sequence. On a TV that has been
running, `180` alone also works; after a power loss it did not.

Signals after a successful connect: `DeviceConnected`,
`StereoHeadsetConnected`, `A2dpSourceAudioStarted`, `AvrcpConnected`; AVOC
switches the output.

### Who does what (captures and dlog)

| process | role |
|---|---|
| `btaudiolist` (Sound Output list) | sends the sequence above |
| `bt-service` (root) | performs requests; with Bluedroid; emits `org.projectx.bt_event` signals; on boot starts an A2DP connect to the saved speaker by itself |
| `bt.connectionmanager` (D-Bus-activated, starts ~28 s after boot) | after every connect calls `91(addr, [0,0,0,0])` = authorize **false** (so the speaker is never trusted), then HFP `178` attempts that fail harmlessly; handles authorization requests and accepts the saved speaker ("saved spk address is same with auth requested device address") |
| `btapp-lite` | forwards authorization requests to `bt.connectionmanager` |
| `btappui` | the "play TV sound through this device?" prompt; on timeout sends `4178` (reject) |
| `avocd` | switches the audio output when the A2DP sink appears |

## Auto-reconnect: the `50-btconnect` hook

`payload/on-boot.d/50-btconnect` is a boot/wake hook (how hooks run:
`payload/README.md`). `payload/on-wake.d/50-btconnect` is a git symlink to it;
on the TV both are regular files. Log lines go to `/opt/cf_tv_hooks.log`, each
with the uptime (`up=N`). Its header comment holds the evidence for every
rule.

1. **Phase 1, accept:** until uptime 30 s (boot) or for 5 s (wake), every
   0.5 s: if audio is up, stop; otherwise send `4177(addr, 3)` (accept). An
   accept with nothing pending is ignored (`BTM_DeviceAuthorized … State:0`).
2. **Phase 2, scan then connect**, up to 10 min (boot) / 3 min (wake):
   - audio up → stop;
   - AVRCP-only link (`0x08`) → wait for it to drop (a connect now hangs);
   - otherwise a ~10 s discovery scan; only if a `DeviceFound` signal names
     the speaker, the menu's connect sequence (at most 3 `180`s per event).

Scans are passive. Connects are not: a `180` to a speaker that is not
answering pages blocks `bt-service` for 30–60 s (every other connect gets
`-29`, the menu's too); 16 retries in 13 min (2026-10-23) wedged it until a
power cycle; failed connects make the TV show the prompt.

`/opt/cf_tv_daemon/btconnect.addr` absent or malformed: the hook logs `no speaker
configured` and stops. To change the speaker, edit `payload/btconnect.addr`
and run `boot install`; to stop the hook, `tv/root.py hooks disable
50-btconnect`. Both copies share one lock (same file name).

Why a wake trigger: "TV off" on the remote is kernel suspend
(`db/boot/instant_on = 1`); processes survive and boot hooks do not rerun. How
the daemon detects a wake: `docs/01-root/access-contexts.md` §3.

## Audio routing

A connected speaker does not keep the audio. An input change (cftv's tuner →
HDMI bounce after a video) moved sound to the AVR over HDMI while the speaker
stayed connected (2026-10-24), and nothing moved it back. `181`, 2 s, then the
connect routes it to the speaker again. `tv/cftv.py` `restore_source` does
this when the speaker had audio before the video (live `4180` check); 9.3 s
for the whole restore on 2026-10-24 (`docs/03-video/cftv.md`).

Output selection in general (AVOC, `db/btapp/lastSoundRoute`, PulseAudio):
`docs/04-system/control.md` §4.

## Power loss

### Root cause (bt-service's own log, 2026-10-24)

Read with dlog switched on (see [Tools](#tools)). Boot with the speaker
connected before the reboot:

| uptime | event (log tag) |
|---|---|
| 10.5 s | speaker reconnects; `BT App is not running, reject incoming authorization req` (bt-service) |
| 15.1–15.2 s | speaker retries: `ACL State: Connected`, `Start authorization`, `AuthorizationRequest` signal, service 3 |
| 15–26 s | `btapp-lite` forwards it to `bt.connectionmanager`, which is not running yet: D-Bus timeout |
| 18.6 s | AVRCP opens (mask `0x08`) |
| 20.6 s | bt-service starts its **own** A2DP connect: `btm_sec_l2cap_access_req - busy - PSM:25 delayed` (waits on the authorization) |
| 24.1 s | (our accept, test D) `access granted`, then `avdt_l2c_connect_ind_cback: outgoing conn in progress: Reject incoming conn`: the speaker's own A2DP channel is refused because of the TV's stuck outgoing one |
| 30–34 s | `bt.connectionmanager` gets the request, `btappui` shows the prompt |
| ~70 s | prompt times out: `btappui` → `4178` reject |
| ~137 s | the speaker drops its AVRCP-only link; afterwards it is not reachable for minutes |

So it is a TV-side race: the vendor component that would accept the known
speaker starts ~10 s too late, and the stack's own connect collides with the
speaker's. The fix (hook phase 1) accepts between ~15 s and ~20 s, before
bt-service's own connect starts. To fit that window the daemon fires the boot
event before its housekeeping (it took ~7 s from daemon start to hook start
before; the window is ~5 s wide).

### Measurements

Unplug tests by the user and software reboots (`reboot` as root, from a
`systemd-run` unit; a soft reboot reproduces the power-loss state exactly):

| speaker before the reboot | what the speaker does | result |
|---|---|---|
| connected (5 boots) | AVRCP-only link at 16–31 s, held ~2 min | any connect during it hangs; after it drops the speaker was seen at once (143 s) or not for 5+ min |
| not connected (5 boots) | nothing until it shows up by itself (BLE advertising, `INQUIRY_RESULT_BLE`): 70–131 s | scan-then-connect: audio at 76, 115, 141 s |
| after a `bt-service` restart | seen after ~70 s | connected (−9 then 0) |

Once the speaker is advertising, the TV pages it in ~100 ms
(`btm_sec_connected` 0.1 s after the request).

### 2026-10-23 history

| time | event |
|---|---|
| 20:11 | power loss #1; the unit of that time trusted `lastSoundRoute` and did nothing; the daemon died from the stale-lock bug (both fixed) |
| 20:19–20:32 | 16 root `180`s, 2–90 s apart: timeouts, `-29`, `-9`; `bt-service` wedged (log saved on the host: `.local/logs/cf_tv_btconnect-2026-10-24.log`) |
| ~20:33 | the user's menu connect stalled on "Connecting…" |
| 20:36 | power loss #2: menu connect worked again |

## Dead ends (do not retry)

| tried | result |
|---|---|
| any connect (`180`, with or without the menu's sequence) while the speaker holds its AVRCP-only link (`0x08`) | hangs 60 s, no reply (3 boots, 2026-10-24) |
| `181` to clear the AVRCP-only link | `-21` NOT_CONNECTED: it only covers A2DP |
| making the speaker trusted with `91(addr, [1,0,0,0])` | `rc 0`, but `bt.connectionmanager` sets it back to untrusted after every connect, and the next boot logged `trusted:0x00000000` |
| accepting the authorization after ~20.6 s (test D: 24 s) | the TV's own outgoing connect is already in progress; the speaker's A2DP is refused (above) |
| `bt_audio_connect(addr, mode)` from our own process (`apps/bt-connect.cs`) | `rc 0`, then `DeviceConnected` → `DeviceDisconnected`; no audio |
| emitting `bt_spk_info_changed` ourselves | AVOC ignores it without a live A2DP link |
| forcing the output through AVOC (`AudioSetSpeakerInformation`, …) | not implemented at runtime (`docs/04-system/control.md` §4) |
| `SPK_SetAcmMode` (`apps/acm-mode.cs`) | wrong subsystem (network speakers); most values segfault |
| vendor `bt.autoconnect` | Samsung's "Sero" (rotating TV) auto-connect for phones (strings `SeroAutoConnection`, `MobileList.cpp`); D-Bus-activated, normally not running; introspects `PrePareAndDoAutoConnect`, `UpdateList` (calls return `UnknownMethod`) |
| `db/btapp/last_btspk_addr` / `_name`, `bt-audiosink` addresses | not connection signals |
| `pactl` | PulseAudio is blind to Bluetooth |
| `dbus-monitor` | signals only on kdbus; use `busctl --system monitor` as root |
| `busctl monitor --match …` | ignored on this bus: the full stream comes through |
| retrying `180` (16 tries in 13 min) | `bt-service` wedged until a power cycle |

## Tools

- Bus watch and replay (`busctl --system monitor` as root, `busctl call`,
  `gdbus call` for variant replies): `docs/06-security/techniques.md`.
- **bt-service's own log (dlog):** Samsung ships it off (`plog=0`).
  `tv/root.py dlog arm`, `tv/root.py reboot --report`, `tv/root.py dlog fetch`
  logs one boot and switches itself off (method:
  `docs/06-security/techniques.md`). Tags: `BT_OAL`, `bt-btm`, `bt-btif`,
  `bt-l2cap`, `bt-avp`, `BLUETOOTH_FRWK_SERVICE`, `BT_CONNECTION_MANAGER`,
  `BT_APP_LITE`.
- Function numbers: `llvm-objdump -T` + `-d --triple=thumbv7-linux-gnueabi`
  on `/usr/lib/libbluetooth-api.so.1.0.0`; the number is an immediate moved
  into a register before the request call.
- `apps/bt-trace.cs`, ptrace breakpoint tracer:
  `dotnet BtTrace.dll PID SECONDS LIB:FILEOFF[:LABEL] ...`. For
  `libcapi-network-bluetooth.so`, FILEOFF = `st_value − 0x46320000`.

## Open

- Verify hook phase 1 on a real power loss with the speaker connected
  beforehand. Expected: audio at ~16–21 s, no prompt. Hook log line to look
  for: `boot/50-btconnect: up=N connected (mask 0x0c) after K accepts`.
- If phase 1 misses the window anyway, start the accept earlier (e.g. from
  `parseinfo.sh` itself) or start `bt.connectionmanager` early.
- Meaning of `4176/4242/4244/4247/4248/4274/4275/4278`, `482/484/493`.
