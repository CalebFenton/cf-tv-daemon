# What you actually need to do

Written 2026-11-26 after reading localthings #435 in full (220 comments),
`docs/credential-acquisition.md`, `docs/appliance-compatibility.md`, the
Localthings README, and running `smartthings-local`'s own probe against the
fridge.

## 1. The blunt answer

**There is no published recipe for a Family Hub that you can follow today.** Not
because someone is hiding it — because the people with the hardware have not
solved it, and they say so repeatedly:

- `docs/credential-acquisition.md`: *"An existing OCF credential is required;
  most owners cannot acquire one yet."*
- Mainline Family Hubs (`26K_REF_LCD_FHUB11.0`) return **`unknown_ca`** even
  when they *are* reachable. One reporter's family: garage fridge
  (`TP1X_REF_21K`) works with a self-signed leaf; kitchen Family Hub does not.
- The one refrigerator that was done (`RF23DB9750QLAA`) needed a
  **model-specific OCF-over-BLE bootstrap** plus *"private one-time
  registration/account inputs that are not yet a public setup flow."*

So the honest position is: this is unsolved for your model, by the community that
solves these.

## 2. What is genuinely unknown vs. blocked

Two separate problems, and only one of them is ours to fix.

**Problem A (maybe ours): the fridge never answers.** I ran, in order:

| probe | result |
|---|---|
| 30 × hand-rolled CoAP GET /oic/res, 60 s | 0 answers |
| `probe_dtls_ports` on 5683, 5684, 49152–49160 | `outcome='unreachable'`, `no_dtls_response` on every port, 3 attempts each |
| `discover_ocf_secure_ports` | `found=False`, `no_ocf_response` |

That is the reference library, not my code. But every Family Hub in those
threads answers 100% of the time (1438/1438 over 24 h on one unit), so a silent
device is anomalous.

Per `appliance-compatibility.md`, there is a specific documented cause for this:

> A silent 5683 deserves a second look before you call the port closed: **the
> answer comes from another source port**, so a stateful firewall, an inter-VLAN
> policy or a Docker bridge NAT drops it while the device is answering normally.

This host has nine Docker bridges. I checked the route (`ens18`, direct, no
policy routing) but I have **not** ruled out a stateful firewall dropping the
*reply* because it arrives from a different source port than the one queried.
That is testable and it is the single most likely explanation for our silence.

**Problem B (not ours): even if it answers, this model may refuse.** `unknown_ca`
is the reported result for `FHUB11.0`. Ours is `AFH-US-KTM-24`; nobody has
reported either way. If it is in the `unknown_ca` class, the certificate route is
closed and only PSK works — and PSK needs the serial + account flow.

## 3. What to do, in order

### Step 1 — settle whether the fridge answers at all (do this first)

The one thing that must be ruled out before anything else, because it makes every
other result unreliable. On a **different host** from this one — your phone on
the same Wi-Fi is ideal, and phones have no Docker NAT:

- Install a CoAP client, or simply use any app that can send a UDP packet.
- Send a CoAP GET for `/oic/res` to `<fridge-ip>:5683`.
- Or, easier: run `tools/coap_doxm_probe.py` from a laptop on the same VLAN.

**If it answers from another host but not this one**, the problem is this
host's networking, and everything I have measured about the fridge is suspect.

**If it does not answer from anywhere**, the device really is silent, and that is
itself the finding.

### Step 2 — if it answers, read `/oic/sec/doxm`

That single read gives `owned`, `oxms`, `oxmsel`. From the published per-model
table:

| `oxmsel` | meaning |
|---|---|
| `2` | manufacturer-certificate OTM — the `unknown_ca` class |
| `65282` | the `0xFF02` variant |

If `oxmsel` is 2 and the cert is rejected, the certificate route is closed.

### Step 3 — decide which of two goals you actually want

These are different, and only one is achievable today.

**Goal "make it mine / local control / stop the cloud dependency":**
needs the credential. Requires the **registration serial from the fridge's QR
label** plus the **SmartThings account user ID**. The serial is physically on the
appliance. Nothing downstream can be computed without it. Even then, the full
flow is not public for a fridge.

**Goal "root the panel":** there is no public path. Not for a Family Hub, at any
Tizen version. The panel work I did earlier (Chromium 105, browser route) is dead
on measured evidence.

### Step 4 — the phone is only useful for one route

Your rooted phone helps **only** if you go the "read the stored credential out of
SmartThings" route, and that route:
- needs SmartThings **1.8.51.30** exactly (the Frida agent is version-specific);
- on the reference unit needed the appliance **removed and re-registered**;
- found the key in an account-specific `.datenc`, *not* the main Core store;
- gives local control, **not** a shell.

It cannot help if the fridge never answers OCF, because the credential is useless
without a reachable endpoint.

## 4. What I need from you

Only two things, and only one is physical:

1. **The registration serial** — on the label or QR of the fridge. Without it,
   nothing in the provisioning flow can be computed, by anyone.
2. **A CoAP probe from a host that is not this one.** Your phone, a laptop, same
   Wi-Fi. `tools/coap_doxm_probe.py <fridge-ip>` — one command.

Item 2 decides whether the last three hours of "no response" is a property of the
fridge or of this machine. Until it is settled, every conclusion about the
fridge's silence is provisional, and I should stop building on it.
