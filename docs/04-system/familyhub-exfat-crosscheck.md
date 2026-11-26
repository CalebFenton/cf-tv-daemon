# Family Hub: cross-check of the USB exFAT route

Written 2026-11-26 after reading the two exploit projects properly instead of
skimming their READMEs. This corrects the previous note, which over-claimed.

## 1. What the projects actually establish

`Luminger/samsung-tv-usb-root` is not a 2018 project. It was published 2026-09-08
and its compatibility document is the most useful artifact found in this whole
investigation, because it classifies Samsung platforms by whether the exFAT
defect is present **and** whether a working chain exists for it.

The distinction matters and I had it wrong:

| class | meaning |
|---|---|
| Hardware verified | full USB-to-root chain exercised on that exact TV |
| Zero-delta static profile | vulnerable modules and every chain address independently recovered and matching |
| **Vulnerable, chain incomplete** | **the defect is present, but the kernel/module differs enough that a new payload chain is required** |
| Architecturally incompatible | cannot transfer |

## 2. The correction to my last message

I implied the exFAT route was probably dead because the fridge runs a newer
kernel. The document says the opposite for the relevant platforms:

> Samsung retained the out-of-tree split exFAT driver through the
> representatives below. Each contains the unchecked `num_ext` copy and the same
> saved-slot geometry, but none has a complete port of the cave/UEP/cache
> payload.

| platform | year | kernel | verdict |
|---|---|---|---|
| `T-NKLAKUC` NikeL | 2020 | 4.1.10 | vulnerable, chain incomplete |
| `T-NKM2AKUC` NikeM2 | 2021 | **4.1.10** | vulnerable, chain incomplete |
| `T-PTMAKUC` PontusM | 2022 | **5.4.77** | vulnerable, chain incomplete |

So: **the defect survives into 5.4.x kernels on Samsung's out-of-tree driver.**
A 5.4 kernel is not by itself a reason to dismiss the route. My "probably dead"
was wrong on the reasoning, even if the conclusion may still turn out right.

Note also `T-NKM2AKUC` 2301.1 — that is the QN85D this repo already documents.
It is classified *vulnerable*, and the constant set for it is published in that
document.

## 3. What is genuinely unknown for this panel

Two things, and they are different questions:

1. **Does the fridge's firmware still ship the out-of-tree split exFAT driver**
   (`exfat_core.ko` + `exfat_fs.ko`), or an in-tree one? The document's newest
   classified platform is 2022/PontusM. This panel is a 2024 appliance on Tizen
   7.2.1. Nothing in the document covers it.
2. **If it does, what are the chain constants** — cave, UEP gate,
   `call_usermodehelper`, `memcpy`, the `ldm ip` gadget. These are per-kernel and
   must come from the shipped image.

Both are answerable **statically** from the firmware archive. Neither requires
touching the fridge.

## 4. The real blocker, and it is not technical

The fridge firmware archive is not publicly obtainable by guessing a URL
(checked: the download-center paths 404). The TV archives are because the TV
community has mapped those URLs; the appliance equivalent has not been mapped,
and Samsung's appliance download centre gates it behind model registration.

That means the static route needs one of:

- **the OTA download URL from the panel** — `Settings > About Family Hub >
  Software update` may display one, or the update mechanism may fetch from a URL
  visible in a log;
- a firmware archive from Samsung support for this exact model;
- a partition image obtained from the device.

Until one of those exists, the exFAT route cannot be evaluated further, and no
amount of searching will change that.

## 5. What is settled, and what is not

**Settled:**

- Both public USB exploits trigger the same defect: the exFAT `num_ext`
  name-fragment overflow in `exfat_get_uniname_from_ext_entry`, which is
  CVE-2023-4273 in the mainline driver.
- They are effective because Samsung's driver is an out-of-tree fork
  (`exFAT: 1.1.11p1 (13-FEB-2013)` on Muse.M) that kept the unchecked copy.
- Samsung carried that fork forward through at least 5.4.x on TVs.
- The exploit runs the **signed** `/bin/bash` via `call_usermodehelper`, so UEP
  is never defeated and nothing is written to the device.

**Not settled, and honestly unknowable right now:**

- Whether a 2024 Family Hub appliance ships that driver at all.
- Whether its kernel has a usable cave and gadget set.
- Anything about the 2024 board's UART or eMMC layout.

## 6. If the firmware were in hand

The check is mechanical and short:

1. Extract `kernel.bin` and the module set.
2. Look for `exfat_core.ko` / `exfat_fs.ko` and, if present, disassemble
   `ffsReadDir` for the `uniname = fp - 0x230` frame and the unchecked copy.
3. If the frame matches, recover the per-kernel constants the way the Kant-M2
   writeup does (`research/findings/kant-m2-chain.md` documents the method
   instruction-by-instruction).
4. Only then consider a stick, knowing a wrong address hangs the device.

This repo already has the tooling for step 1's sibling problem:
`vendor/samsung-tv-root-source/swu/` decrypts `.msd` regions, and
`tools/vdfs-unpack/` unpacks the TV filesystem format.

## 7. What I am not claiming

I am not claiming this route works on the fridge. The evidence supports exactly
one statement: **the defect is a Samsung-wide fork that persisted into kernels
newer than this panel's era on TVs**, which makes the appliance worth checking
when its firmware becomes available — not before.
