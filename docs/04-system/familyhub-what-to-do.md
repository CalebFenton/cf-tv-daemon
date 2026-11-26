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

## 5. Why this host is not the problem — measured, not argued

I had hypothesised that this machine's Docker bridges might be dropping the
reply. **That is now disproved**, by three tests.

**Test 1 — a UDP round trip to the device works.** The fridge's own Spotify eSDK
on TCP 17654 answers, and ICMP to the host succeeds. The path is fine.

**Test 2 — closed ports generate ICMP port-unreachable.** If a firewall were
dropping UDP, every port would look silent. It does not:

| port | response |
|---|---|
| 21, 123, 1900, 5355, 9999, 17654 | `ICMP port-unreachable` → closed, host reachable |
| 49152, 49154, 49155, 49160 | `ICMP port-unreachable` → closed |
| **53, 5353, 5683, 5684** | **silent** |

So the kernel on the device is replying to unreachable ports normally. Only four
specific ports are silent.

**Test 3 — it reproduces.** Three trials, same answer every time:

```
5683 : ['SILENT','SILENT','SILENT']
5353 : ['SILENT','SILENT','SILENT']
49154: ['closed','closed','closed']
```

Not a rate-limit artifact — the #213 thread explicitly warned that `open|filtered`
results on these appliances are often an ICMP rate-limit artefact, which is why
this was repeated rather than accepted on one pass.

## 6. What that means

The four silent ports are **53 (DNS), 5353 (mDNS), 5683 and 5684 (CoAP)**. That
is not a firewall rule on this host — it is a coherent set of UDP services, and
it is exactly the set a device would drop *if those services are not running* but
something still swallows the packet instead of refusing it.

Two readings:

1. The device has a netfilter rule dropping those four ports specifically
   (plausible: mDNS and CoAP are disabled on this build).
2. The device is not the one answering on this address for those protocols —
   e.g. an internal relay.

Either way: **the silence is a property of the device, not of this machine.** My
earlier advice to re-probe from another host was wrong, and the measurements
above are why.

## 7. Why most people cannot get the answer

Not because of the rooted phone. The phone is one route among several, and it is
not the common blocker. Three separate reasons, in order of how often they bite:

**1. Most Family Hubs refuse the certificate.** `docs/appliance-compatibility.md`
is explicit that a reachable endpoint is not the same as an accepted credential.
Reported results on the same LAN, same tool:

| appliance | result |
|---|---|
| garage fridge `TP1X_REF_21K` | self-signed leaf **accepted**, 32 entities |
| kitchen Family Hub `26K_REF_LCD_FHUB11.0` | **`unknown_ca`** |

So half the Family Hub population gets a clean "rejected" and stops there. There
is no working client credential for that class.

**2. The PSK route needs inputs nobody has published how to get.** `doxm` reports
`oxms` and `oxmsel`, which say ownership *could* be transferred — but the key
derivation needs the appliance's **registration serial** from the QR label and
the **SmartThings account user ID**. Those are the private one-time inputs. The
researcher who built this says so in the thread: *"acquisition of that input is
still a prerequisite; the formulas below do not conjure it from public OCF data."*

**3. Acquisition is deliberately out of scope for the tool.** From #435's
decisions: *"Acquisition stays out of LocalThings, for now. We import an
already-provisioned credential. No OTM, no ownership transitions, no
`/oic/sec/*` writes."* That is a product decision, so the integration does not
even attempt it. The people who have done it did it with private tooling.

The README states the result plainly: **"An existing OCF credential is required;
most owners cannot acquire one yet."**

So the phone is not the barrier. The barrier is that the fridge either refuses
the public certificate class, or requires an ownership transfer whose inputs and
envelope are model-specific and unpublished.
