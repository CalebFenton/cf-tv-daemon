# cf_tv_hooks.sh - run every hook for one event. Shell text that callers
# `eval`; never executed as a file (UEP refuses executing scripts that live
# on writable storage, but eval of their text works).
#
#   ( CF_EVENT=boot; eval "$(< /opt/cf_tv_daemon/cf_tv_hooks.sh)" )
#
# Callers: cf_tv_daemon.awk on boot and on wake; `tv/root.py hooks EVENT`.
#
# Each regular file in /opt/cf_tv_daemon/on-$CF_EVENT.d/ runs in parallel, as
# `eval` of its text in its own subshell and process group, as root. Hooks
# with the same file name in on-boot.d and on-wake.d share one lock.
#
# A run still going when a new event arrives is replaced: killed with its
# whole process group, then the hook starts again. That happens when the TV
# slept during the run: suspend freezes processes, and at wake the old run
# would carry on with stale state while the new event was skipped (2026-11-02:
# a frozen speaker scan blocked two wakes). A hook must therefore tolerate
# being killed at any point.
# Hooks log with `say TEXT`; all output goes to /opt/cf_tv_hooks.log.
# Hooks that start long-lived processes must use systemd-run: anything else
# they start is killed with them, and dies when cf_tv_daemon restarts.

log=/opt/cf_tv_hooks.log
exec >> $log 2>&1
# Hooks are started before anything else: under boot load every program start
# costs ~0.5 s, and 50-btconnect has a deadline ~20 s after boot. So no `cat`
# (bash reads the file itself) and no "start" line (it cost a `date`).
for hook in /opt/cf_tv_daemon/on-$CF_EVENT.d/*; do
  [ -f "$hook" ] || continue
  (
    name=${hook##*/}
    lock=/opt/cf_tv_daemon/state/locks/$name
    say() { echo "$(date '+%F %T') $CF_EVENT/$name: $*"; }
    exec 9> $lock
    if ! flock -n 9; then
      kill -TERM -- -"$(< $lock.pid)" 2>/dev/null
      flock -w 10 9 || { say "skipped: the run from an earlier event did not stop"; exit 0; }
      say "replaced a run from an earlier event"
    fi
    # set -m: the hook gets its own process group (verified on the TV,
    # 2026-11-05), so one kill stops it and everything it started. set +m
    # inside keeps the hook's own background jobs in that group.
    set -m
    ( set +m; eval "$(< "$hook")" ) &
    echo $! > $lock.pid
    wait $!; rc=$?
    if [ $rc = 143 ]; then say "stopped: replaced by a newer event"; else say "exit $rc"; fi
  ) &
done
# Capped at 1 MiB (cut to the last 512 KiB): a normal boot logs ~1 KB, a day of
# heavy testing ~90 KB (2026-10-25). Plain text on purpose: /opt has ~500 MB free.
if [ "$(wc -c < $log)" -gt 1048576 ]; then
  tail -c 524288 $log > $log.tmp && mv $log.tmp $log
fi
