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
