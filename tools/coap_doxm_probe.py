#!/usr/bin/env python3
"""Read /oic/sec/doxm from a Samsung appliance over plaintext CoAP.

The cheapest non-destructive check on an OCF appliance: it says whether the
device speaks OCF at all, whether it is already owned, and which ownership
transfer methods it offers. `oxms` containing 2 (mfgcert) or 65282 (conmfgcert)
is the precondition for a no-root OTM; `owned: true` means the window is shut
until the device is factory-reset.

    tools/coap_doxm_probe.py 192.0.2.10            # one read
    tools/coap_doxm_probe.py 192.0.2.10 --watch 60 # sample for a minute
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

# CoAP option numbers used here.
URI_PATH = 11


def encode_options(path: str) -> bytes:
    out = b""
    last = 0
    for segment in path.strip("/").split("/"):
        delta = URI_PATH - last
        last = URI_PATH
        raw = segment.encode()
        if delta > 12 or len(raw) > 12:
            raise ValueError(f"option encoding not implemented for {segment}")
        out += bytes([(delta << 4) | len(raw)]) + raw
    return out


def coap_get(path: str, mid: int) -> bytes:
    header = bytes([0x40, 0x01, (mid >> 8) & 0xFF, mid & 0xFF])
    return header + encode_options(path)


def decode_cbor_strings(payload: bytes) -> list[str]:
    """Best-effort readable strings from a CBOR map.

    Enough to find `oxms`, `oxmsel`, `owned` and the method numbers without
    pulling a CBOR dependency into the repo.
    """
    found: list[str] = []
    i = 0
    while i < len(payload):
        b = payload[i]
        if 0x60 <= b <= 0x7F:  # text string, length in low 5 bits
            n = b & 0x1F
            chunk = payload[i + 1:i + 1 + n]
            if len(chunk) == n:
                try:
                    found.append(chunk.decode("utf-8"))
                except UnicodeDecodeError:
                    pass
            i += 1 + n
            continue
        i += 1
    return found


def probe(host: str, port: int, path: str, timeout: float) -> tuple[int, bytes] | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(coap_get(path, mid=0x1234), (host, port))
        payload, _ = sock.recvfrom(4096)
        return payload[0] >> 5 & 0x07, payload
    except (TimeoutError, OSError):
        return None
    finally:
        sock.close()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=5683)
    parser.add_argument("--path", default="oic/sec/doxm")
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--watch", type=int, default=0,
                        help="sample for N seconds instead of one read")
    args = parser.parse_args(argv)

    if not args.watch:
        result = probe(args.host, args.port, args.path, args.timeout)
        if result is None:
            print(f"no response from {args.host}:{args.port} /{args.path}")
            return 1
        code, payload = result
        print(f"response code class {code}, {len(payload)} bytes")
        strings = decode_cbor_strings(payload)
        if strings:
            print("readable fields:", ", ".join(strings)[:400])
        return 0

    deadline = time.time() + args.watch
    attempts = answers = 0
    sizes: list[int] = []
    while time.time() < deadline:
        attempts += 1
        result = probe(args.host, args.port, args.path, args.timeout)
        if result is not None:
            answers += 1
            sizes.append(len(result[1]))
        time.sleep(1.2)
    print(f"{attempts} attempts, {answers} answers")
    if sizes:
        print(f"payload sizes seen: min {min(sizes)} max {max(sizes)}")
    return 0 if answers else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
