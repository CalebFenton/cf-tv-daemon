# Wide search: what actually exists for this fridge

2026-11-26. Written after the user pointed out I keep declaring things impossible
and then being wrong. This is a survey, not a conclusion. Nothing here says
"impossible".

## 1. The honest correction first

Over this session I claimed, wrongly: that the engine was Chromium 76; that a
newer kernel killed the USB route; that no guide existed for the onboarding
serial; that the root cause of one CVE was absent (it may be, but I tested the
wrong thing first). Each time the user asked "are you sure" and each time the
correction was cheap to find.

So this document lists what exists without ranking it by my guesses.

## 2. What people are actually doing with these fridges

There is a **large, active community**, and what they do is not rooting. They put
custom web pages on the panel and drive their homes from it.

| project | what it is |
|---|---|
| `rohankapoorcom/splash` | standalone HA dashboard explicitly built for "low-power kiosks such as Samsung Family Hub fridges" |
| `Tjindarr/family-hub-display` | HomeDash, same idea |
| `santoshgs/familyhub-pub` | fork of OpenFamily, self-hosted family dashboard |
| HA community thread #344678 | 74+ pages of people doing exactly this |

Techniques they document, which I had not found:

- **The browser stays on the screen if you leave it open.** From the HackerNoon
  piece: *"This fridge cannot run Tailscale (at least not yet), but it does have
  a web browser, which will generally stay on the screen if you leave it open."*
- **Screensaver workaround**: autoplay a hidden YouTube video to stop the screen
  sleeping.
- **`browser_mod`** in HA to wake/control the display remotely.
- **Fullscreen** exists after a recent update, but exits on lock screen.
- **Text-only sites run best** — *"about the only thing that doesn't stress the
  fridge tablet's memory."* Everything else "runs like molasses".

That is the realistic, working, widely-used answer to "how do I make this fridge
do what I want": **you don't install apps, you serve a web page and leave the
browser on it.** No root, no exploit, and it works today.

## 3. The other path: the fridge's own setup mode

Documented by Samsung and independently reverse-engineered:

- Hold **Fridge** (or the setup button) ~5 s until **AP** appears on the panel.
- The fridge broadcasts a Wi-Fi network whose SSID starts with `refrigerator`.
- **Password is `1111122222`** — same on every device.
- The fridge serves HTTP on `192.168.4.1:8888` with `/deviceinfo`, `/keyinfo`,
  `/confirminfo`.
- The SSID itself leaks `mnId`, onboarding id and parts of the serial.
- The full protocol, including the QR format
  `https://qr.samsungiots.com/?m={mnId}&s={onboardingId}&r={serial}`, is written
  up at `6xq.net/localthings`, and there is a working fake-server implementation
  (`localthings` on Codeberg) proven on Samsung dishwashers.

**Nobody has published this for a Family Hub.** That is a gap in the record, not
a demonstration that it fails. It is also the most promising unexplored door,
because it is the fridge's own provisioning interface and it is documented.

## 4. What the local-control community has and hasn't done

- `QuiteYellow/SmartThings-Local`, `mbillow/localthings`, a Homey port
  (`moKorean/com.lomohome.localthings`) — all read the same OCF resources.
- Family Hubs consistently fail: `#416` is a `24K_REF_LCD_FHUB9.0` with *"no
  ICMP unreachable, effectively complete silence"* on 49152–49160 and only 17654
  open — **the same signature as ours**.
- Their own maintainer's answer was "closing as duplicate, will start working
  once support for SmartThings-Local#16 is finished."
- `#213` has a `25K_REF_LCD_FHUB10.0` that **does** answer on 5683 with 75
  resources.

So there are at least two populations of Family Hub, and ours matches the silent
one. That is a real distinction in the record, and it is the first time our
device's behaviour has matched a published report exactly rather than being
unique.

## 5. What this changes about the plan

I had been treating "get local OCF control" as the goal. The community's actual
answer to "I want my fridge to do something else" is the browser-kiosk approach,
and it has years of users.

Three routes, none of which I should call impossible:

1. **Browser kiosk** — works today, no root, widely used. Serves a page, leaves
   the browser open. Gets you a custom dashboard, not control of the fridge's
   hardware.
2. **Provisioning/AP mode** — documented protocol, working tooling for other
   appliances, unproven on a fridge. Would need the fridge removed from
   SmartThings and put into AP mode.
3. **OCF local control** — blocked by the silent-device signature that #416 also
   reports. Waiting on upstream work, per the maintainer.

## 6. What I still do not know

- Whether the fridge even has an AP mode reachable by the documented button
  sequence. Samsung's docs say hold Fridge for 5 s; this panel may differ.
- Whether its `192.168.4.1:8888` speaks the same EasySetup protocol.
- What the QR sticker contains, or whether this model has one.

All three are answerable by looking at the fridge and holding a button. None
require code, and none are blocked on anything I've been unable to do.
