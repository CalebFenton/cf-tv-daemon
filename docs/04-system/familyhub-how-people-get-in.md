# How people actually get into these devices

Written 2026-11-26. This is the answer to "how has anyone hacked this device family",
after reading the repos rather than the search snippets.

## 1. The headline

**Nobody is exploiting the Family Hub panel.** The active work is on the appliance's
*other* computer — the OCF/CoAP/DTLS control plane — and the current state of the art
is not an exploit at all. It is **being the party that performs the OCF ownership
handshake**, which yields the `OwnerPSK`, which grants local control with no cloud and
no root.

Three projects, in order of relevance:

| project | what it does | target |
|---|---|---|
| `mbillow/localthings` | HA integration over CoAP-DTLS; drives OCF discovery | many appliances |
| `QuiteYellow/SmartThings-Local` | protocol library; OCF-PKI, PSK, discovery | many appliances |
| `neoKushan/Dumbthings` | **no-root** OTM app that derives the OwnerPSK | soundbars (verified) |

## 2. Why the OwnerPSK is the whole game

From Dumbthings' own framing, and it is the clearest statement of the model:

> Samsung appliances speak OCF over CoAP/DTLS on your LAN. To control one locally you
> need its `OwnerPSK` — a symmetric key established when the device is *owned* (OCF
> Ownership Transfer). The key is **never transmitted**; each side derives it
> independently from the ownership handshake.

So there are three ways to obtain it, and only one needs no root:

1. Extract it from the SmartThings app — needs a rooted phone + Frida, or an iOS backup.
2. Derive it from a manufacturer-DTLS handshake you drive — laundry only, not public.
3. **Perform the OTM yourself and derive it** — `Dumbthings`, no root, needs a Samsung
   account and a physical button press.

That is how this device family is actually being opened up. Not a memory bug.

## 3. The four conditions for route 3

Dumbthings is explicit that "my device advertises the right bit" is not enough. All four
must hold:

1. plaintext `/oic/sec/doxm` `oxms` includes a **manufacturer-certificate OTM** —
   method `2` (`oic.sec.doxm.mfgcert`) or `0xFF02` (`65282`, `conmfgcert`);
2. that mfg-cert DTLS handshake completes **with no client certificate**;
3. the ownership confirm is a **simple physical button**;
4. you are willing to **factory-reset** the device into setup mode.

Known **not** in scope: appliances that gate the transfer behind *account-bound proofs*
(laundry), devices needing a client certificate, and pairwise-peer setups where Samsung
stays the OCF owner.

Note condition 4: this is destructive. A factory reset of the refrigerator panel is a
real cost, and it is why this has not simply been tried.

## 4. What is measurable before any reset

The cheap, non-destructive check on any candidate is a plaintext
`GET /oic/sec/doxm` on **UDP 5683**. `oxms` and `oxmsel` say whether a mfg-cert OTM is
even offered. From `localthings` #213 on a `25K_REF_LCD_FHUB10.0`:

- `/oic/sec/doxm` returned `owned:true, oxms:[2, 65280, 65282], oxmsel:2, sct:1`
- `/oic/sec/pstat` returned `isop:true, cm:0, tm:0, om:4, sm:4`

So **`2` and `65282` are both advertised** — conditions 1 and 3 are plausible for that
model. What blocks it is that the device is already `owned`, and that the newer
Family Hubs sit in a *different credential class*: an additive pairwise peer key where
Samsung stays the OCF owner, which Dumbthings explicitly does not handle.

## 5. And on our panel specifically

30 probes over 60 s to UDP/5683: **no answer at all.** Not sparse, not state B — nothing.
Meanwhile other Family Hubs answer with 75 resources, and a `25K_REF_LCD_FHUB10.0` on
**Tizen 7** (the same OS generation as ours) has been driven locally with 36 entities and
working CoAP OBSERVE (#900).

So this panel either has OCF disabled, binds it internally, or is in a state that does
not advertise. The single most likely explanation is that it is **paired to SmartThings
in daily use**, and these appliances stop advertising once owned.

## 6. What that implies for the goal

If the aim is "stop the fridge phoning home / make it mine", the OCF route is the one
with a real community behind it, and it does not need a root exploit. If the aim is
"root the panel", note that **none of this gives a shell** — it gives appliance control
through a documented protocol, as the owner.

Those are different goals and I should not have been treating them as one. The panel has
no public root; the appliance has an ownership model that can be made to work in your
favour.

## 7. The one thing worth trying that is cheap

`GET /oic/sec/doxm` on UDP 5683, plaintext. It is a read, it changes nothing, and it
answers:

- whether this panel speaks OCF at all (currently: no response anywhere);
- if it does, whether `oxms` offers method `2` / `65282`;
- whether it is already owned.

That single request decides whether the OCF track is open for this unit. Everything else
in this document is downstream of it.

## 8. If the phone is rooted: the concrete procedure

`localthings/docs/credential-acquisition.md` documents several working routes.
Two matter here, and the second is far less invasive.

### Route A — recover the stored credential from SmartThings (rooted Android)

This is the documented, repeatable one. Tested on washers, an oven, an AC and a
TV. The credential is read out of SmartThings' persisted IoTivity security
store, not derived by a handshake.

**Prerequisites**, exactly as the doc lists them:

| where | what |
|---|---|
| phone | rooted Android with SmartThings installed and signed into **the account that owns the appliance** |
| phone | USB debugging on, host authorized |
| host | Android platform-tools (`adb`) |
| host | Frida host tools + a matching `frida-server` on the phone (tested: 17.17.0 / `frida-tools` 14.10.4) |
| host | Node.js/npm, to build the Frida Java agent |
| host | Python environment for the recovery helpers |
| host | a private place for exports and a secret store (the doc used Keychain) |

**The catch:** the recovery/verification helpers in that writeup are *local
research tools* and are **not published**. The doc says so plainly. So route A
is not a download; it is "build the equivalent."

An independent implementation does exist and is public for the oven case:
`KRZ303/locathings-oven-psk-retrieval` — scripts plus a recorded procedure with
tested versions and limits. That is the most directly reusable artifact.

### Route B — derive the OwnerPSK from OTM (no root needed, but destructive)

`neoKushan/Dumbthings` does the ownership transfer itself and derives the key.
No root and no SmartThings memory reading. Requirements: a mfg-cert OTM in
`oxms`, a handshake that completes with no client cert, a **physical button**
confirm, and a **factory reset** into setup mode.

### What you actually get

Either route yields an **OwnerPSK**: an identity UUID plus a 32- or 64-hex key.
That is what LocalThings imports. It authenticates a CoAP-DTLS session and lets
you read protected resources — 38 of them on one washer — and issue commands.

It does **not** yield a shell, and it does not run code on the appliance.

### The blocker for this fridge specifically

Our panel did not answer a single CoAP probe: 30 attempts over 60 s on UDP 5683,
zero responses. Every route above needs the appliance to answer OCF. So before
any phone or Frida work, the fridge has to be reachable at OCF level — and right
now it is not.

The most likely reason is that it is **paired and owned in daily use**, and these
appliances stop advertising once owned. The documented washers required
**removal and re-registration** in SmartThings before the credential could be
recovered, which is a change to a working setup.

### What to check on the phone first, cheaply

1. Does SmartThings on the rooted phone open and show this fridge under the same
   account? If the fridge is not in that account, no route works.
2. `adb shell su -c id` — confirm root.
3. Look for SmartThings' IoTivity stores before building any tooling. The iOS
   route documents the exact path (`Library/Security/oic_svr_db_client.dat`);
   the Android equivalent is what the Frida agent targets, and finding it is the
   first real step. On Android 13+ the AC recovery found PSK material in an
   encrypted `<UUID>.datenc` store while the plaintext SVR stores held none.

That third point is worth knowing before spending an evening on it: the storage
format has changed across SmartThings versions, and one writeup explicitly found
the plaintext files empty.

## 9. The concrete Android procedure (rooted phone)

`KRZ303/locathings-oven-psk-retrieval` (published 2026-10-03, sanitized helpers,
offline tests included) is the most reusable public implementation. It recovered
an oven's OwnerPSK from SmartThings on rooted Android 17 and is written as a
procedure with tested versions and limits.

### What it takes

| item | value |
|---|---|
| phone | rooted, ARM64, tested on Android 17 / KernelSU |
| app | `com.samsung.android.oneconnect` (SmartThings) **1.8.51.30** (code 185130010) |
| host | ADB, Python 3.14, `frida-server` **17.21.0** + `frida-tools` 14.10.4 |
| python | `cbor2==6.1.5`, `cryptography==50.0.2` |
| verify | Mbed TLS 3.6.7 for a standalone DTLS test |

The helper **rejects other app builds by version**, deliberately: the Java class
names are obfuscated per build. So the SmartThings version on the phone has to
match, or the scripts must be re-derived against that build.

### The part that surprised the author, and will surprise you

The obvious target file is wrong:

```
/data/user/0/com.samsung.android.oneconnect/
  shared_prefs/iotivityKey.xml
  files/<ACCOUNT_STORE_UUID>.datenc   <-- the credential is HERE (encrypted)
  files/<ACCOUNT_STORE_UUID>.dat      <-- certificate-only baseline
  files/<ACCOUNT_STORE_UUID>.db       companion provisioning database
```

On the first pass the credential was *not* in the main Core store
(`oic_svr_db_client.dat`) nor in the live native credential list — those held ten
certificates and no PSK. It only appeared in an **account-specific encrypted
`.datenc` store**, and only after the appliance was removed and re-registered on
that phone.

The store UUID is neither the appliance UUID nor the PSK identity.

### First steps, all cheap and non-destructive

```sh
adb devices -l
export ANDROID_SERIAL='<serial>'
adb -s "$ANDROID_SERIAL" shell su -c id          # must print uid=0
adb -s "$ANDROID_SERIAL" shell dumpsys package com.samsung.android.oneconnect
adb -s "$ANDROID_SERIAL" shell 'su -c "find /data/user/0/com.samsung.android.oneconnect/files -maxdepth 1 -type f -name \*.datenc"'
```

That last one lists candidate stores **without exporting contents**. Then:

```sh
python inspect_encrypted_store.py --serial "$ANDROID_SERIAL" --store "$STORE_UUID"
```

which reports credential **counts** by default — no keys, no identifiers.

### Boundaries worth respecting

- Removing an appliance can **reset its network setup and break routines/scenes**.
- Do not clear SmartThings data, uninstall the app, or delete its keys.
- The output JSON **is the secret**, and `0600` does not encrypt it.
- `read-storage-key.js` prints the storage key in its `RECOVERY_KEY` message —
  never run it in a logged terminal.

### For this fridge, the order still holds

None of the above helps until the appliance answers OCF. Our panel answered
nothing on 30 probes. So the sequence is:

1. confirm the fridge is visible in SmartThings **on the rooted phone's account**;
2. find whether this model ever answers `/oic/sec/doxm` on UDP 5683 (the cheap
   check that has, so far, failed on this unit);
3. only then consider the store-recovery work.

Step 2 is the gate. Everything in §8 and §9 is downstream of it.

## 10. The probe is now a repo tool

`tools/coap_doxm_probe.py` does the read described in §4 and §7:

```sh
tools/coap_doxm_probe.py <fridge-ip>              # one read of /oic/sec/doxm
tools/coap_doxm_probe.py <fridge-ip> --watch 60   # sample for a minute
```

It prints the response code class, the payload size and the readable CBOR
strings (which is enough to see `owned`, `oxms` and `oxmsel` without a CBOR
dependency). A `--watch` run reports how many probes were answered and the
payload size range, which is the state-A/state-B indicator from #213.

Against this panel it currently reports **no response**, consistent with every
other measurement so far.
