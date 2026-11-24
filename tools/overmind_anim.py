#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["playwright>=1.50", "pillow>=12"]
# ///
"""overmind_anim - render the living Overmind mark to short animated WebP files.

The mark is the brand's own web component (`brand/web/overmind-mark.js` in the
overmind_hub repository), not a redrawing. Headless Chromium runs it on a
virtual clock with a seeded Math.random, so every frame is exact and a render
is reproducible; each frame is screenshotted with a transparent background.

Each variation is a short script of states (`wake`, `idle`, `done`, ...) plus
a fade in/out applied to the whole frame, ending fully transparent: an
AnimatedImageView loops, so the last frames must be invisible in case the
item is removed a moment late.

    tools/overmind_anim.py                # every variation into build/overmind/
    tools/overmind_anim.py wake-160 done  # some of them
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "overmind"
BRAND = Path(os.environ.get("OVERMIND_BRAND", "/mnt/cache/repos/overmind_hub/brand"))
FPS = 30
# Chromium already in the Playwright cache; no browser download.
CHROMIUM = next(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell"), None)

# Virtual time: requestAnimationFrame, performance.now and setTimeout advance
# only when __advance(ms) is called; Math.random is seeded.
CLOCK = """
(() => {
  let now = 0, seq = 0, seed = 20261124;
  const frames = new Map(), timers = [];
  performance.now = () => now;
  window.requestAnimationFrame = (cb) => { frames.set(++seq, cb); return seq; };
  window.cancelAnimationFrame = (id) => frames.delete(id);
  window.setTimeout = (cb, ms = 0) => { timers.push({ id: ++seq, at: now + ms, cb }); return seq; };
  window.clearTimeout = (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); };
  Math.random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
  window.__advance = (ms) => {
    now += ms;
    for (const t of timers.filter((t) => t.at <= now)) { timers.splice(timers.indexOf(t), 1); t.cb(); }
    const due = [...frames.values()]; frames.clear();
    for (const cb of due) cb(now);
  };
})();
"""

PAGE = """<!doctype html><html><head><style>
html, body {{ margin: 0; background: transparent; }}
#stage {{ display: inline-flex; align-items: center; gap: {gap}px; padding: {pad}px; }}
#stage > * {{ filter: {halo}; }}
overmind-mark {{ display: block; width: {size}px; height: {size}px; }}
#word {{ height: {word_h}px; }}
</style></head><body><div id="stage">{word}<overmind-mark id="mark" state="{start}"></overmind-mark></div>
<script type="module" src="overmind-mark.js"></script></body></html>"""

# Dark halo from the brand rules: the mark on a busy background needs it.
HALO = "drop-shadow(0 0 4px rgba(3,6,11,.85)) drop-shadow(0 0 10px rgba(3,6,11,.55))"


@dataclass
class Variation:
    size: int  # mark width in TV pixels (the screen is 1920x1080)
    seconds: float
    start: str  # the state when the clock starts
    states: list[tuple[float, str]] = field(default_factory=list)  # (time, state)
    fade_in: float = 0.0  # seconds of fade-in from t=0
    fade_out: tuple[float, float] = (0.0, 0.0)  # (start, end): opacity 1 -> 0
    wordmark: bool = False  # OVERMIND beside the mark, fading in after it
    halo: bool = True


VARIATIONS = {
    # Brackets fly in from loose and overshoot (the brand's `wake`), settle, fade.
    "wake-160": Variation(160, 3.0, "dormant", [(0.15, "wake"), (1.2, "idle")], 0.2, (2.2, 2.8)),
    "wake-112": Variation(112, 3.0, "dormant", [(0.15, "wake"), (1.2, "idle")], 0.2, (2.2, 2.8)),
    "wake-96": Variation(96, 3.0, "dormant", [(0.15, "wake"), (1.2, "idle")], 0.2, (2.2, 2.8)),
    # Fades in already idle, then the brand's `done`: two ripples and a bump.
    "done-128": Variation(128, 3.0, "idle", [(0.35, "done")], fade_in=0.25, fade_out=(2.2, 2.8)),
    # Wake, then the wordmark appears to the left of the mark.
    "lockup-96": Variation(96, 3.4, "dormant", [(0.15, "wake"), (1.2, "idle")], 0.2, (2.6, 3.2), wordmark=True),
}


def envelope(v: Variation, t: float) -> float:
    a = min(1.0, t / v.fade_in) if v.fade_in else 1.0
    start, end = v.fade_out
    if end and t >= start:
        a *= max(0.0, 1 - (t - start) / (end - start))
    return a


def render(name: str, v: Variation, browser) -> Path:
    word = '<img id="word" src="overmind-wordmark.svg" style="opacity:0">' if v.wordmark else ""
    html = PAGE.format(
        size=v.size,
        start=v.start,
        word=word,
        word_h=round(v.size * 0.22),
        gap=round(v.size * 0.18),
        pad=round(v.size * 0.4),  # the wake ripple grows past the mark; the halo too
        halo=HALO if v.halo else "none",
    )
    page = browser.new_page(viewport={"width": 1200, "height": 400})
    page.add_init_script(CLOCK)
    # Serve the page and the brand files from one origin (module scripts need it).
    files = {
        "index.html": (html.encode(), "text/html"),
        "overmind-mark.js": ((BRAND / "web" / "overmind-mark.js").read_bytes(), "text/javascript"),
        "overmind-wordmark.svg": ((BRAND / "svg" / "overmind-wordmark.svg").read_bytes(), "image/svg+xml"),
    }

    def serve(route) -> None:
        body, kind = files[route.request.url.rsplit("/", 1)[1]]
        route.fulfill(body=body, content_type=kind)

    page.route("http://brand.local/**", serve)
    page.goto("http://brand.local/index.html")
    page.wait_for_function("customElements.get('overmind-mark') !== undefined")
    page.evaluate("__advance(0)")
    stage = page.locator("#stage")

    frames, pending = [], sorted(v.states)
    step = 1000 / FPS
    for i in range(round(v.seconds * FPS)):
        t = i / FPS
        while pending and pending[0][0] <= t:
            page.evaluate("s => document.getElementById('mark').setAttribute('state', s)", pending.pop(0)[1])
        if v.wordmark:  # appears as the brackets settle
            page.evaluate("o => document.getElementById('word').style.opacity = o", min(1.0, max(0.0, (t - 0.6) / 0.4)))
        page.evaluate("o => document.getElementById('stage').style.opacity = o", envelope(v, t))
        page.evaluate(f"__advance({step})")
        frames.append(Image.open(io.BytesIO(stage.screenshot(omit_background=True))).convert("RGBA"))
    page.close()

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{name}.webp"
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=round(step), loop=0, quality=80, method=4)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", metavar="NAME", help=f"default: all of {', '.join(VARIATIONS)}")
    args = parser.parse_args()
    if unknown := set(args.names) - set(VARIATIONS):
        parser.error(f"unknown variation(s): {', '.join(sorted(unknown))}")
    if CHROMIUM is None:
        sys.exit(
            "no chrome-headless-shell in ~/.cache/ms-playwright; run `uv run --with playwright playwright install chromium-headless-shell`"
        )
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROMIUM))
        for name in args.names or VARIATIONS:
            v = VARIATIONS[name]
            out = render(name, v, browser)
            w, h = Image.open(out).size
            print(f"{out.relative_to(ROOT)}  {w}x{h}  {v.seconds}s  {out.stat().st_size // 1024} KiB")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
