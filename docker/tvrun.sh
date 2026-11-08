#!/bin/bash
# Stage the TV rootfs in Tizen layout and run something under qemu-arm inside chroot.
#   docker/tvrun.sh strace /usr/apps/.../acm-service
#   docker/tvrun.sh run    /usr/bin/foo
#   docker/tvrun.sh shell
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-run}"; shift || true
docker run --rm -i \
  -v "$ROOT:/work" \
  -v "$ROOT/livefs:/tvroot:ro" \
  --privileged --network host \
  tvhack bash -c '
    set -e
    MODE="$1"; shift
    mkdir -p /stage
    [ -d /stage/usr ] || cp -a /tvroot/usr /stage/usr
    mkdir -p /stage/etc && cp -a /tvroot/etc/. /stage/etc/ 2>/dev/null || true
    ln -sfn /usr/lib /stage/lib
    mkdir -p /stage/bin /stage/sbin /stage/tmp /stage/dev /stage/proc /stage/opt /stage/mnt /stage/sys
    ln -sfn /usr/bin /stage/bin; ln -sfn /usr/sbin /stage/sbin
    cp /usr/bin/qemu-arm-static /stage/usr/bin/ 2>/dev/null || true
    mount -t proc proc /stage/proc 2>/dev/null || true
    mount -t sysfs sys /stage/sys 2>/dev/null || true
    case "$MODE" in
      shell)  exec chroot /stage /bin/bash ;;
      strace) exec chroot /stage /usr/bin/qemu-arm-static -strace "$@" ;;
      *)      exec chroot /stage /usr/bin/qemu-arm-static "$@" ;;
    esac
  ' -- "$MODE" "$@"
