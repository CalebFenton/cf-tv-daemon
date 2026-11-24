"""esvideo_probe - play an H.264 clip through the agent's `esvideo` op and watch what it takes from the TV.

usage: tools/esvideo_probe.py CLIP.h264 FW FH FPS SECONDS [--resource N]

CLIP is Annex-B H.264 with an access unit delimiter before every frame
(ffmpeg ... -x264-params aud=1:repeat-headers=1 -f h264). It plays in the
bottom-right corner. Before, during and after, the probe records:
  - rscmgr owners of the main scaler (7), sub scaler (16), main audio out (9);
  - tv-viewer's player 124 active audio out (1 = it has the main output);
  - the compositor's hardware planes (enlightenment_info -hwc_wins);
plus a screen capture while the clip plays, the agent log and every avocd /
resource-center call on the system bus. If the programme lost the main scaler
or its audio output, the input is switched once (cftv.restore_source).
Output goes to stdout and .local/esvideo/probe-<time>.txt.
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".local" / "esvideo"
sys.path.insert(0, str(ROOT / "tv"))
import cftv

RSC = "busctl --system call rscmgr.resource /rscmgr/resource rscmgr.resource"
STATE = (
    f'for r in 7 16 9; do echo "resource $r: $({RSC} GetResourceList i $r 2>&1)"; done; '
    f'echo "player 124 audio out: $({RSC} GetActiveAudioOut i 124 2>&1)"; '
    "enlightenment_info -hwc_wins 2>/dev/null | grep -E 'zpos|VD|DV|tvvideo' ; true"
)
BUS_CALLS = (
    "grep -E 'Member=(PlayerRegisterM|AvPlayerRegisterM|PlayRequestM|PlayRequest|AudioMute|PlayerUnregister"
    "|PlayerRequestPlay|SetAppToZone|VideoStart|VideoStop|ScalerSwap|RscStateChanged)' /tmp/cf-bus.txt "
    "| sed 's/.*Member=//' | sort | uniq -c; true"
)


def main() -> None:
    clip, fw, fh, fps, seconds = sys.argv[1], *(int(a) for a in sys.argv[2:6])
    resource = int(sys.argv[sys.argv.index("--resource") + 1]) if "--resource" in sys.argv else 1
    OUT.mkdir(parents=True, exist_ok=True)
    log = OUT / f"probe-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    lines: list[str] = []

    def note(text: str) -> None:
        print(text)
        lines.append(text)

    cftv.ensure()
    src = cftv.media(clip)
    note(f"clip {clip} -> {src}, {fw}x{fh} @ {fps}, resource {resource}")
    note("== before\n" + cftv.run(STATE).strip())
    # What restore_source re-selects and reconnects if the programme loses out.
    cftv.run(f"echo '{cftv.source_get()} {cftv.speaker_in_use() or '-'}' > {cftv.SAVED_SOURCE}")
    cftv.run(
        "rm -f /tmp/cf-bus.txt; ( busctl --system monitor > /tmp/cf-bus.txt 2>&1 & p=$!; "
        f"sleep {seconds + 8}; kill $p ) >/dev/null 2>&1 &"
    )
    time.sleep(1)
    x, y = 1920 - fw - 48, 1080 - fh - 48
    reply = cftv.send(
        {
            "op": "esvideo",
            "id": "esvideo",
            "src": src,
            "fw": fw,
            "fh": fh,
            "fps": fps,
            "x": x,
            "y": y,
            "resource": resource,
        },
        timeout=15,
    )
    note(f"reply {reply}")
    time.sleep(1.5)
    note("== during\n" + cftv.run(STATE).strip())
    shots = OUT / "capture"
    subprocess.run(["rm", "-rf", str(shots)])
    subprocess.run(
        [sys.executable, str(ROOT / "tv" / "stream.py"), "--frames", "3", "--outdir", str(shots)], check=False
    )
    time.sleep(max(0.0, seconds - 1.5) + 3)
    note("== after\n" + cftv.run(STATE).strip())
    note("== agent log\n" + cftv.run(f"grep esvideo {cftv.slot().q}cftv.log | tail -15").strip())
    time.sleep(3)
    note("== avocd / resource calls on the bus\n" + cftv.run(BUS_CALLS).strip())
    bus = log.with_name(log.stem.replace("probe", "bus") + ".txt")
    bus.write_text(cftv.run("cat /tmp/cf-bus.txt; rm -f /tmp/cf-bus.txt"))

    after = cftv.run(STATE)
    scaler_ok = "resource 7:" in after and "tv-viewer" in after.split("resource 7:")[1].split("\n")[0]
    audio_ok = "player 124 audio out: i 1" in after
    if not (scaler_ok and audio_ok):
        note(
            f"programme lost {'the main scaler' if not scaler_ok else ''} {'its audio out' if not audio_ok else ''}; switching input"
        )
        note(f"restore_source -> {cftv.restore_source()}")
        note("== restored\n" + cftv.run(STATE).strip())
    else:
        cftv.run(f"rm -f {cftv.SAVED_SOURCE}")
    log.write_text("\n".join(lines) + "\n")
    print(f"-> {log}, {bus}; captures in {shots}")


main()
