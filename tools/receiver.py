#!/usr/bin/env python3
"""Accept one tar stream from the TV and write it to a file.

The TV side is `tar cf - ... | cat > /dev/tcp/<host>/<port>`. Python is used
instead of `nc -l -k` because the TV closes the connection at EOF and a
shell-piped listener exits on the first disconnect.

Usage: receiver.py <port> <outfile>
"""
import socket
import sys

port = int(sys.argv[1])
outfile = sys.argv[2]

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("0.0.0.0", port))
srv.listen(1)
print(f"listening on {port}, writing {outfile}", flush=True)

conn = None
while conn is None:
    c, addr = srv.accept()
    if addr[0].startswith("127.") or addr[0] in ("::1", "local"):
        c.close()          # health probe, not the TV
        continue
    conn = c
    print(f"connection from {addr}", flush=True)
total = 0
with open(outfile, "wb") as f:
    while True:
        chunk = conn.recv(1 << 20)
        if not chunk:
            break
        f.write(chunk)
        total += len(chunk)
conn.close()
srv.close()
print(f"received {total} bytes", flush=True)
