# How to actually get in: the working path, from the issue threads

Written 2026-11-26. Everything below is quoted or paraphrased from
`mbillow/localthings` issue #435, which has 220 comments and is where this work
actually happens. You were right: the work exists, is public, and I had not read
it.

## 1. The headline

**A refrigerator has been done.** `RF23DB9750QLAA` / `TP1X_REF_21K`, reported by
@Jason-Morcos:

> normal control uses an additive, least-privilege pairwise peer while Samsung
> remains the OCF owner. The peer UUID and 16-byte key are generated locally by
> our integration; they are not extracted from SmartThings, imported from a
> phone, or inferred from `devowneruuid`.

And a 2025 Frame TV was done with **no app, no cloud call, no BLE, no reset, no
OTM**, using an already-held owner credential.

So: no rooted phone is required for the peer-key route. What is required is
something else, and it is the actual blocker.

## 2. The thing that is actually required

From the same comment, on prerequisites:

> The appliance's **registration serial** from its onboarding label/QR
> information, and the same-account identifier expected by its provisioning
> firmware. The QR supplies registration information, not a PSK. On the tested
> WD53 stack the account input was the ten-character ASCII user ID belonging to
> the authenticated SmartThings location owner — not an email address, location
> UUID, cloud device ID, or OCF owner UUID.

And:

> that is not yet a portable public acquisition flow.

So the missing ingredient is **the appliance's registration serial** (on the QR
label) plus **the SmartThings account user ID**. Those are the private one-time
inputs. Everything else is public code.

## 3. The exact wire proofs (WD53 stack, version 5.0.47)

This is the part nobody should have to rediscover:

```text
S  = lowercase_hex(SHA512(registration_serial_ASCII)).encode(ASCII)
A  = SHA256(same_account_user_id_ASCII).digest()
n1 = decode_hex(first_current_nonce)       # 4 raw bytes
p1 = lowercase_hex(SHA256(S || n1))        # serial-only first

n2 = decode_hex(new_nonce_after_stage_1)   # read fresh; do not reuse n1
p2 = lowercase_hex(SHA256(S || A || n2))   # account proof second
```

Encoding traps that cost the original researchers time:

- `S` is **128 ASCII bytes**, not the 64-byte binary digest.
- `A` is **32 raw bytes**, not hex text.
- Concatenate with **no delimiters**.
- The nonce **rotates on every read** — even a diagnostic GET between preparing
  and sending invalidates it.
- Order matters: serial-only first, then serial+account. Their first attempts
  failed because the app helper's method naming suggested the opposite.

The confirmation POST goes to:

```text
/sec/provisioninginfo?if=oic.if.a&rt=x.com.samsung.provisioninginfo

{ "x.com.samsung.provisioning.hash": <fresh serial-only proof>,
  "x.com.samsung.provisioning.otmsupportfeature": 16384 }
```

`16384` is `0x4000`. No `reset` field, no account value. This replaces the
Android registration-confirmation callback — it is done locally over LAN.

## 4. OwnerPSK derivation, if you go that route

```text
label = b"oic.sec.doxm.mfgcert"       # selected OTM 2
# or b"x.org.iotivity.conmfgcert"    # selected OTM 0xFF02

K = P_SHA256(master_secret,
             b"key expansion" || server_random || client_random, 120)

OwnerPSK = P_SHA256(K, label || owner_uuid_raw16 || device_uuid_raw16, 16)
```

There is a **pure implementation already merged**:
`QuiteYellow/SmartThings-Local` `smartthings_local/protocol/owner_psk.py`. It
takes session material as input; it is not an onboarding tool.

The setup carrier is server-authenticated manufacturer DTLS with
`ECDHE-ECDSA-AES128-GCM-SHA256`, Samsung's manufacturer trust path, and **no
client identity leaf**. Not AC14K_M, not a copied app certificate, not disabled
server verification.

## 5. What the fridge actually looks like on the wire

Two measurements worth having before touching anything:

**@papirov's five-appliance table** (2026-09-27):

| appliance | `sct` | `oxms` | `oxmsel` | owned | our cert works |
|---|---|---|---|---|---|
| Garage fridge `TP1X_REF_21K` | 8 | `[65282]` | 65282 | true | **yes** |
| Family Hub `26K_REF_LCD_FHUB11.0` | 1 | `[2, 65282]` | 2 | true | `unknown_ca` |

**@DUC750 on the FHUB10**: it hosts **two co-located OCF stacks** answering with
distinct `di`, and `oxmsel: 2` with `oxms: [2, 65280, 65282]`.

And from the 24-hour run: 1438 probes, **1438 answered**, 15 state changes,
sizes only ever **421 B or 9356 B**. So state A/B is a clean binary, and the
sparse state still answers.

## 6. Why ours answers nothing, and what that means

Our panel: 30 probes, 0 answers. Every FHUB in those threads answers every time.
That is a real, measured difference, and it is not explained by the state A/B
behaviour — both states answer.

Possible readings, none established:

1. our unit's OCF is disabled by firmware or region;
2. it is on a different interface than the one we can reach;
3. we are probing the wrong address (the panel's CoAP may be on the main board's
   address, which is not on this LAN);
4. something is blocking UDP to it.

Note that in #435 the FHUB is reachable and *still* fails with `unknown_ca` — so
reachability alone is not the win. The garage fridge `TP1X_REF_21K` is the one
where a self-signed leaf was accepted.

## 7. What I would do next, given all this

1. **Find the registration serial.** It is on the label/QR of the appliance.
   Without it the provisioning proof cannot be computed. This is a physical
   action: look at the fridge.
2. **Get the SmartThings account user ID** — the ten-character ASCII user ID of
   the location owner, not an email.
3. **Re-test CoAP from a different vantage point.** Since every other FHUB
   answers and ours does not, the problem is more likely ours than the fridge's.
   Try from the phone on the same Wi-Fi, and confirm which interface the fridge
   is actually on.
4. **Read issue #435 in full.** 220 comments; I have read maybe a third. The
   rest contains more per-model results.
