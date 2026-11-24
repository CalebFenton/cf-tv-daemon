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

Finding it takes about 3 s: an SSDP M-SEARCH for the DIAL service
(`urn:dial-multiscreen-org:service:dial:1`, sent on the LAN interface
explicitly), then a REST identity check of each device that answered, since
non-TV casting devices answer the same search. A Samsung TV's SSDP `USN`
carries the same `uuid:` as its REST `id`. That is cheap enough to do on
demand rather than keeping a discovery service running.

## .local/devices.toml

Written by `tv/devices.py scan`, in the git-ignored `.local/` with everything
else that identifies a TV or its owner (`tools/check_private.py` blocks such
text from commits):

```toml
default = "un55cu7000dxza"

[tv.un55cu7000dxza]
id = "uuid:..."
address = "192.0.2.10"
model = "UN55CU7000DXZA"
note = "living room"      # optional, free text

[tv.qn85d]
id = "uuid:..."
address = "192.0.2.11"
model = "QN75QN85DAFXZA"
speaker = "AA:BB:CC:DD:EE:FF"  # optional: the Bluetooth speaker the 50-btconnect hook reconnects
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
subcommand. The TV is resolved the first time a command needs it
(`project_env.tv()`), after argument parsing, so `--help` and argument errors
never touch the network. `--tv` is exported as `CF_TV_NAME`, so child
processes (`tv/control.py` → `tv/chan.py`, `bin/snap`) use the same TV.
`$CF_TV_DEVICES` moves the file.

When a name is used: the stored address is checked against the stored `id`
(one HTTP request). If it does not match, the LAN is searched over SSDP for that
`id`, the address is written back, and the command continues. A TV that is off
fails with a message naming the id, not a timeout. While the address
answers on sdb but not yet on the REST API (a booting TV: sdbd starts ~10 s
first), the API is polled for up to 30 s. An open sdb port is never taken as
proof of identity, because DHCP may have handed the address to another TV.

`tv/devices.py` rewrites the file after a scan or a move. Keys added by hand
are kept; comments are not.

## Per-TV state on this computer

Under `$XDG_RUNTIME_DIR/cf-tv/` (`/run/user/<uid>`, mode 0700, tmpfs), not
`/tmp`, where another local user could create the channel socket first and
receive the root commands. One directory per TV address
(`project_env.tv_dir()`):

| file | what |
|---|---|
| `<ip>/chan.sock`, `chan.log`, `chan.state` | the root channel (`tv/chan.py serve`) |
| `<ip>/probe` | `tv/root.py` probe cache (30 s) |
| `<ip>/btconnect.addr` | the speaker file `boot install` deploys, written from `speaker` |
| `sdb.lock` | one host sdb server serves every TV, so this lock is shared |

`tv/chan.py serve` serves one TV. Two TVs need two `serve` processes, started
with different `--tv`.

## Per-TV settings

- `speaker`: the Bluetooth speaker the `50-btconnect` boot/wake hook
  reconnects. `tv/root.py boot install` deploys it to
  `/opt/cf_tv_daemon/btconnect.addr`, and removes that file from a TV with no
  `speaker`. `bin/bt-reconnect` uses it as its default `--addr`.
- `debloat`: names `payload/debloat/<name>.list`, the stop-markers, app
  disables and preference values the `40-debloat` hook keeps applied on this
  TV. `boot install` deploys it as `/opt/cf_tv_daemon/debloat.list`, or
  removes that file from a TV with no `debloat`.
- `hello`: names a render in `build/overmind/` (`tools/overmind_anim.py`,
  e.g. `wake-96`) that the `30-hello` hook plays top right for 3 s when the TV
  comes on. `boot install` deploys it as `/opt/cf_tv_daemon/hello.webp`, or
  removes that file from a TV with no `hello`. The hook's position and size
  fit `wake-96`.

## What is still single-TV

- `tv/hud.py` talks to `cf-vision`, a separate service, and to the cftv agent;
  it follows the selected TV but the recognition service does not.
- `tv/control.py` sends a key to one TV, the selected one.
