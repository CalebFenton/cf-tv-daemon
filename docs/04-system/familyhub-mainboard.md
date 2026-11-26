# The fridge main board: a second computer, and it is not the panel

Written 2026-11-26. This corrects a wrong turn and records a real finding.

## 1. Correction

I found `<apple-tv-address>` with 14 open ports and briefly treated it as the
fridge's second board. It is an **Apple TV**: port 5000/7000 answer
`Server: AirTunes/980.77.5`, 5060 answers SIP, 20020 answers `server: envoy`.
Unrelated to this device. The fridge at `<panel-address>` still has exactly one
open port.

## 2. The actual finding

`mbillow/localthings` issue #811, filed 2026-11-11, is a device dump from a
**Family Hub 10** refrigerator, model `RF32CG5900SR` — a 2023/24-era appliance,
two generations newer than the 2017 dump everything else in this field cites.

Its identity block is the important part:

```
"mnmo": "TP2X_REF_20K|00134041|00020232011511200100000030000000",
"mnhw": "MediaTek",
"mnos": "TizenRT 1.0 + IPv6",
"mnpv": "DAWIT 2.0",
"mnfv": "A-RFWW-TP2-21-COMMON_20220110"
```

**`TizenRT 1.0`, `MediaTek`, `DAWIT 2.0`.** That is not Tizen Linux. TizenRT is
Samsung's separate real-time OS for microcontrollers, and DAWIT is the appliance
framework on top of it.

Compare our panel: Tizen **7.2.1**, Chromium 105, `/opt` and `/opt/usr`
partitions, Mali-450. Those are two different computers that happen to live in
the same refrigerator:

| | main board | display panel |
|---|---|---|
| OS | TizenRT 1.0 + DAWIT 2.0 | Tizen 7.2.1 |
| SoC | MediaTek | (Mali-450 GPU, ARMv7) |
| role | cooling, doors, temps, ice maker | screen, apps, browser |
| seen on LAN | **not this device** | `.150`, port 17654 only |

So every "root the fridge" question has been two questions, and the interesting
part is that the main board serves a **documented CoAP/OCF resource tree** that
the panel does not.

## 3. What the main board exposes

From that dump, the `/oic/res` resource list on a Family Hub 10 unit:

| resource | type | `sec` |
|---|---|---|
| `/oic/d`, `/oic/p` | device + platform | false |
| `/oic/sec/doxm`, `/oic/sec/pstat` | ownership | true, 49154/49155 |
| **`/file/list/vs/0`** | `x.com.samsung.file.list` | **false** |
| **`/file/transfer/vs/0`** | `x.com.samsung.file.transfer` | **false** |
| **`/file/transfer/chunk/vs/0`** | `x.com.samsung.file.chunk` | **false** |
| `/file/information/vs/0` | file metadata | in dump |
| `/hass/state/vs/0`, `/hass/command/vs/0` | Home Assistant bridge | false |
| `/diagnosis/vs/0` | `diagnosisStart` | — |
| `/temperature/{current,desired}/{freezer,cooler}/0` | temps | — |
| `/information/vs/0` | model, serial, OTN DUID, MICOM fw | — |
| `/eeprom/...`, `/defrost/...`, `/icemaker/...`, `/sabbath/...` | appliance control | — |

The three `file` resources are marked `sec: false` — **no OCF security on the
file transfer surface**, in the resource directory itself.

That is the most interesting thing found in this entire investigation, and it is
not a memory-corruption bug: it is a file-transfer API the device advertises as
unauthenticated.

## 4. What is NOT established

- **Whether our panel's main board has the same tree.** Our panel is Tizen
  7.2.1 with `AFH-US-KTM-24`; the dump is a different model with `TP2X_REF_20K`.
  The resource naming is Samsung-wide `x.com.samsung.da.*`, so it is plausible,
  not established.
- **Whether the main board is reachable at all from this network.** The fridge's
  main board was not visible on a 254-address sweep of the `/24`. It may be on
  a separate internal interface (the dump's `ConnectedKitchenVTO2` /
  `172.16.42.126` pattern from the 2017 forensics writeup suggests the fridge
  creates its own network for the camera module), or it may be behind the panel.
- **Whether `sec: false` means unauthenticated in practice.** OCF marks many
  resources `sec: false` for discovery; reads of `/oic/d` are normally open.
  Whether `/file/transfer` actually accepts an unauthenticated write is a
  different question and would need to be tested against the device.

## 5. Why this matters more than the browser work

The file-transfer resource is a *documented, non-memory-corruption* interface on
the appliance's own control computer. If it accepts writes, it is the entry point
everyone has been looking for — no CVE, no heap grooming, no frozen browser.

It also explains why searching for "Family Hub root" keeps returning nothing:
the panel has been locked down hard across six years, while the main board runs a
different OS with a different, much more open API surface.

## 6. Next steps, and their falsifiers

1. **Find whether the main board is on the LAN.** Sweep for CoAP on 5683/49154
   across the network and the fridge's own Wi-Fi AP if it has one.
   *Falsified if:* nothing answers CoAP anywhere, in which case the main board
   is not directly addressable from here.
2. **If reachable, GET `/oic/res`.** That is a read and confirms whether this
   panel has the same tree as the Family Hub 10 dump.
   *Falsified if:* the tree differs or the resource list is secured.
3. **Only then** consider whether `/file/transfer/vs/0` accepts a write.

The first step costs a network sweep. It needs no interaction with the fridge at
all, which is why it is worth doing before anything else.
