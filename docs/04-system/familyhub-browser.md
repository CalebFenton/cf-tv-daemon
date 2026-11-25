# Panel browser — Chromium 76.0.3809.146

Read from the CU7000's sibling device, the Family Hub panel, in Developer
Mode → `WebEngine Version`, 2026-11-25.

| Item | Value |
|---|---|
| Web engine | **Chromium `76.0.3809.146`** |
| Release date of that build | 2019-07 (M76 stable) |
| Panel | Tizen 7.2.1, One UI 7.0 Refrigerator, `AFH-US-KTM-24-T7XXMU` |
| Context it runs in | `org.tizen.browser`, `uid=5001`, SMACK `User::Pkg::org.tizen.browser` |

## Why this number matters

`76.0.3809.146` is **four years older than the Q60T Synacktiv rooted** (that TV
ran Chromium 79), and six years behind the 2024 TV baseline (M108). Every V8
bug patched between M76 and M108 is a candidate.

The known-adjacent chain, for shape rather than direct reuse:

- Synacktiv, Q60T: CVE-2020-6383 (`Array.prototype.sort` type confusion, V8,
  patched in M80) → code execution in `efl_webprocess` as `uid=5001`.
  https://github.com/synacktiv/samsung-q60t-exploit
- Calif, UN43T5300 (2026): from that browser shell, `/dev/ntksys` physmap →
  `struct cred` overwrite → root, no ROP, no shellcode, UEP not triggered.
  https://github.com/califio/publications/blob/main/MADBugs/samsung-tv/writeup.md

Both bugs are older than this engine, so neither is patched here by version.
The open question is not "is the browser old enough" — it plainly is — but
whether the panel's kernel exposes the same physmap driver (§4.2 of
`device-familyhub.md`).

## The probe page

`tools/fridge_probe.py` serves a page that reports the exact engine, the
features that pin a milestone, and whether the panel can reach its own loopback
and `file://` URLs. It logs the User-Agent and posts the full table back.

```sh
tools/fridge_probe.py 8088      # as a supervised service
# then, in the panel's Web View:
#   http://<this-host>:8088/
```

A Chromium 76 User-Agent is the confirmation; the feature table distinguishes
76.0.3809.146 from a spoofed string. The `file://` probe is the interesting one:
if local file reads work from the Web View, the panel's own `/proc/version`,
`/etc/os-release` and (if permitted) `/dev` listing answer the driver question
without any exploit at all.
