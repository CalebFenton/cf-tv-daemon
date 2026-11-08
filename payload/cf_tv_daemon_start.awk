#!/usr/bin/awk -f
# cf_tv_daemon_start.awk - (re)start cf_tv_daemon as a transient unit.
# Run by parseinfo.sh at boot, and by `tv/root.py boot install` after a deploy.
#
# What does NOT work, each verified on this device:
#
#   cmd &                 dies when the caller exits
#   setsid cmd &          still dies. setsid leaves the *session*, but the
#                         process stays in AT-HUB's *cgroup*, and systemd kills
#                         the whole cgroup when the unit exits. Symptom: the log
#                         file is created by the redirect, then the daemon is
#                         gone and nothing else appears.
#   systemd-run ... "> f" the redirect is part of the argv, and systemd-run has
#                         no shell, so awk received a literal ">" argument and
#                         exited immediately. Unit went inactive instantly.
#
# What works: systemd-run with no shell metacharacters in its arguments, and the
# daemon opening its own log. A transient unit gets its own cgroup, so it
# survives AT-HUB exiting.
#
# NOT --collect: it removes the unit the moment the transient process ends, and
# in practice that also raced the daemon's startup -- the unit vanished and no
# process was left. Without it the unit persists, which is what we want; the
# unit name is fixed, so reset-failed clears any previous instance first.
#
# Restart=on-failure: a crash, a kill or an awk error restarts the daemon
# after 2 s (before 2026-10-25 nothing did, and root was gone until the next
# boot). A clean stop (this launcher, deploys) and the daemon's own exit 0
# ("already running") do not restart it. A restart is not a new boot, so the
# boot hooks do not rerun. Verified on systemd 244 with a test unit: killed
# twice, restarted twice.
BEGIN{
  system("systemctl stop cf_tv_daemon.service 2>/dev/null")
  system("systemctl reset-failed cf_tv_daemon.service 2>/dev/null")
  system("systemd-run --unit=cf_tv_daemon -p Restart=on-failure -p RestartSec=2 --no-block /usr/bin/awk -f /opt/cf_tv_daemon/cf_tv_daemon.awk")
}
