#!/bin/bash
# Run the TV analysis image with the TV's own rootfs as sysroot.
#   docker/run.sh                    -> interactive shell
#   docker/run.sh <cmd...>           -> run one command
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec docker run --rm -it \
  -v "$ROOT:/work" \
  -v "$ROOT/livefs/usr/lib:/sysroot/usr/lib:ro" \
  -v "$ROOT/livefs/usr/bin:/sysroot/usr/bin:ro" \
  -v "$ROOT/livefs/etc:/sysroot/etc:ro" \
  -e TVROOT=/sysroot \
  -w /work \
  --network host \
  --cap-add=NET_RAW --cap-add=NET_ADMIN \
  --name tvhack-$$ \
  tvhack "$@"
