"""frames_bench - measure the agent's frames path (apps/cftv-frames.cs) on the TV.

Streams MJPEG frames (ffmpeg testsrc2, or --src CLIP looped) to the agent's
frames port, paced at FPS, into the bottom-right corner. Each frame carries its
number as 10 bit blocks along the top; without --src, a white box flashes in
the middle for the first 3 frames of every second. --audio adds a PCM packet
before every frame: the clip's sound, or a 1 kHz beep in time with the flash.

usage: tools/frames_bench.py FW FH FPS SECONDS [--capture N] [--src CLIP] [--q Q] [--audio]

Prints the agent's counters, the agent's TV CPU use, the avocd/muse calls seen
on the system bus (with --audio), and with --capture which frame numbers
reached the screen in N screen captures (tv/stream.py; needs tv/chan.py serve).
"""

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CAPTURES = ROOT / ".local" / "frames-capture"
sys.path.insert(0, str(ROOT / "tv"))
import cftv

BITS = 10


def source(fw: int, fh: int, fps: int, clip: str | None, q: int) -> subprocess.Popen:
    bar = fh // 8
    boxes = [
        f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill",
        f"drawbox=x=iw/4:y=ih/4:w=iw/2:h=ih/2:color=white:t=fill:enable='lt(mod(n\\,{fps})\\,3)'",
    ]
    for k in range(BITS):
        boxes.append(
            f"drawbox=x={k * fw // BITS + 4}:y=4:w={fw // BITS - 8}:h={bar - 8}:color=white:t=fill"
            f":enable='gte(mod(n\\,{2 ** (k + 1)})\\,{2**k})'"
        )
    inp = ["-stream_loop", "-1", "-i", clip] if clip else ["-f", "lavfi", "-i", f"testsrc2=size={fw}x{fh}:rate={fps}"]
    scale = [f"scale={fw}:{fh},fps={fps}"] if clip else []
    return subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            *inp,
            "-vf",
            ",".join(scale + boxes),
            "-c:v",
            "mjpeg",
            "-q:v",
            str(q),
            "-pix_fmt",
            "yuvj420p",
            "-f",
            "image2pipe",
            "-",
        ],
        stdout=subprocess.PIPE,
    )


def pcm(fps: int, clip: str | None) -> subprocess.Popen:
    inp = (
        ["-stream_loop", "-1", "-i", clip, "-vn"]
        if clip
        else ["-f", "lavfi", "-i", "aevalsrc='if(lt(mod(t\\,1)\\,0.1)\\,0.4*sin(2*PI*1000*t)\\,0)':s=48000:c=stereo"]
    )
    return subprocess.Popen(
        ["ffmpeg", "-v", "error", *inp, "-f", "s16le", "-ar", "48000", "-ac", "2", "-"], stdout=subprocess.PIPE
    )


def jpegs(pipe):
    """Split ffmpeg's MJPEG output at end-of-image markers (never inside entropy data)."""
    buf = b""
    while True:
        end = buf.find(b"\xff\xd9")
        if end >= 0:
            yield buf[: end + 2]
            buf = buf[end + 2 :]
            continue
        chunk = pipe.read1(1 << 16)
        if not chunk:
            return
        buf += chunk


def cpu_sample(pid: str) -> tuple[float, float]:
    out = cftv.run(f"cat /proc/{pid}/stat; head -1 /proc/stat").splitlines()
    f = out[0].rsplit(")", 1)[1].split()
    proc = int(f[11]) + int(f[12])
    total = sum(int(x) for x in out[1].split()[1:])
    return proc, total


def decode(path: str, x: int, y: int, fw: int, fh: int) -> int:
    img = Image.open(path).convert("L")
    sx, sy = img.width / 1920, img.height / 1080
    bar = fh // 8
    n = 0
    for k in range(BITS):
        cx = (x + k * fw // BITS + fw // BITS / 2) * sx
        cy = (y + bar / 2) * sy
        if img.getpixel((int(cx), int(cy))) > 128:
            n |= 1 << k
    return n


def main() -> None:
    fw, fh, fps, seconds = (int(a) for a in sys.argv[1:5])

    def opt(name, default):
        return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

    capture, clip, q = int(opt("--capture", 0)), opt("--src", None), int(opt("--q", 4))
    x, y = 1920 - fw - 48, 1080 - fh - 48
    cftv.ensure()
    pid = cftv.run(f"cat {cftv.Q}ready").strip()
    src = source(fw, fh, fps, clip, q)
    frames = jpegs(src.stdout)
    sound = "--audio" in sys.argv
    abytes = 48000 // fps * 4 if sound else 0
    asrc = pcm(fps, clip) if sound else None
    if sound:
        cftv.run(
            f"rm -f /tmp/cf-bus.txt; ( busctl --system monitor > /tmp/cf-bus.txt 2>&1 & p=$!; "
            f"sleep {seconds + 6}; kill $p ) >/dev/null 2>&1 &"
        )
    total = 0
    sock = socket.create_connection((cftv.TV, 7743), timeout=10)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
    sock.sendall(cftv._token.encode() + b"\n")
    sock.sendall(
        json.dumps(
            {"id": "frames", "x": x, "y": y, "w": fw, "h": fh, "fw": fw, "fh": fh, "audio_bytes": abytes}
        ).encode()
        + b"\n"
    )

    caps = []
    if capture:

        def cap():
            time.sleep(2)
            r = subprocess.run(
                [sys.executable, str(ROOT / "tv" / "stream.py"), "--frames", str(capture), "--outdir", str(CAPTURES)],
                capture_output=True,
                text=True,
            )
            caps.append(r.stdout.strip() + r.stderr.strip())

        subprocess.run(["rm", "-rf", str(CAPTURES)])
        t = threading.Thread(target=cap)
        t.start()

    p0, t0 = cpu_sample(pid)
    start = time.monotonic()
    sent = late = 0
    while time.monotonic() - start < seconds:
        frame = next(frames)
        total += len(frame)
        due = start + sent / fps
        now = time.monotonic()
        if now < due:
            time.sleep(due - now)
        elif now - due > 1 / fps:
            late += 1
        if sound:
            sock.sendall(b"A" + abytes.to_bytes(4, "little") + asrc.stdout.read(abytes))
        sock.sendall(b"V" + len(frame).to_bytes(4, "little") + frame)
        sent += 1
    wall = time.monotonic() - start
    p1, t1 = cpu_sample(pid)
    sock.shutdown(socket.SHUT_WR)
    reply = sock.makefile().readline().strip()
    src.kill()
    if sound:
        asrc.kill()
        time.sleep(3)
        print(
            "avocd/muse calls during the run:",
            cftv.run(
                "grep -o -E 'Member=(PlayerRegisterM|AvPlayerRegisterM|PlayRequestM|PlayRequest|AudioMute|PlayerUnregister|PlayerRequestPlay)' /tmp/cf-bus.txt | sort | uniq -c; "
                "grep -o -E 'Member=[A-Za-z]+' /tmp/cf-bus.txt | sort | uniq -c | sort -rn | head -12; rm -f /tmp/cf-bus.txt"
            ).strip(),
        )
    print(
        f"{fw}x{fh} @ {fps}: sent {sent} in {wall:.1f}s ({sent / wall:.1f}/s, {total / wall / 1e6 * 8:.1f} Mbit/s, {total / sent / 1024:.0f} KB/frame), late {late}"
    )
    print("agent:", reply)
    ncpu = int(cftv.run("grep -c ^processor /proc/cpuinfo").strip())
    print(f"agent CPU: {(p1 - p0) / (t1 - t0) * ncpu * 100:.0f}% of one core ({ncpu} cores)")
    if capture:
        t.join()
        print("capture:", caps[0])
        files = sorted(str(p) for p in CAPTURES.glob("frame_*.jpg"))
        nums = [decode(f, x, y, fw, fh) for f in files]
        print("frame numbers on screen:", nums)


main()
