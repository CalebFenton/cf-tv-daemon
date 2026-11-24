# Devices — naming several TVs and finding them again

Three TVs are planned; two are rooted (`device.md`, `device-cu7000.md`). Their
LAN addresses move: DHCP hands out a new lease after a reboot, a router
restart, or simply when the lease expires. Their identity does not move.

## Identity that survives DHCP

Every Samsung TV answers `GET http://<ip>:8001/api/v2/` with no login, and that
JSON carries a fixed `id`:

```
"id": "uuid:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

The same value appears as `device.duid` and `device.udn`. It is set at
manufacture and is not the address, the MAC (`wifiMac` is the Wi-Fi MAC even on
a wired TV), or the model name.

Scanning a /24 for it takes about 2 s (254 parallel 1.2 s probes), so a lookup
is cheap enough to do on demand rather than keeping a discovery service
running.

## devices.toml

Written by `tv/devices.py scan`, not in git:

```toml
default = "un55cu7000dxza"

[tv.un55cu7000dxza]
id = "uuid:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
address = "192.0.2.185"
model = "UN55CU7000DXZA"
note = "living room"      # optional, free text

[tv.qn85d]
id = "uuid:..."
address = "192.0.2.175"
model = "QN75QN85DAFXZA"
```

`address` is a cache, not a key. `id` is the key.

## Commands

```sh
tv/devices.py scan                  # add/update from the LAN, sets `default` if there is one TV
tv/devices.py list                  # name, address, up/down, sdb, model
tv/devices.py address qn85d         # the address to use now (scans if the stored one is stale)
tv/devices.py current               # which TV this shell selects
tv/devices.py default qn85d         # the TV used when --tv is absent
```

Resolution order for a name: `--tv NAME`, `$CF_TV_NAME`, `devices.toml`
`default`, then `CF_TV_IP` in `.env` when no name is selected at all (the
single-TV setup this repo had before). `--tv` may appear before or after the
subcommand: it is read from `argv` at import time (`tv/project_env.py`) because
module constants resolve the TV before argparse runs.

When a name is used: the stored address is checked against the stored `id`
(one HTTP request). If it does not match, the /24 is scanned for that `id`, the
address is written back, and the command continues. A TV that is off fails with
a message naming the id and the subnet, not a timeout.

## Per-TV isolation

Two TVs on one host must not share state that belongs to one of them. These are
keyed by address:

| thing | path |
|---|---|
| channel socket | `/tmp/tvchan-<ip>.sock` |
| channel log | `/tmp/tvchan-<ip>.log` |
| channel state | `/tmp/tvchan-<ip>.state` |
| root probe cache | `/tmp/rootctl.probe-<ip>` |

`tv/chan.py serve` serves one TV. Two TVs need two `serve` processes, started
with different `--tv`.

## What is still single-TV

- `tv/hud.py` talks to `cf-vision`, a separate service, and to the cftv agent;
  it follows the selected TV but the recognition service does not.
- `payload/btconnect.addr` is one speaker per TV on the TV itself, but the repo
  keeps one copy for `boot install`, so a second TV with a different speaker
  needs its own file. Currently the same hook and no speaker on the CU7000.
- `bin/control` forwards `--tv` to `tv/chan.py`; a key press goes to one TV.
