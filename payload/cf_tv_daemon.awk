#!/usr/bin/awk -f
# cf_tv_daemon.awk - the one long-running root process. Started at boot by
# parseinfo.sh through cf_tv_daemon_start.awk, as the transient unit
# cf_tv_daemon. Runs as uid 0, System::Privileged.
#
# 1. Command queue. awk has no socket API and we cannot exec our own ELF
#    (docs/01-root/native-code.md), so IPC is a directory queue:
#
#      req/<id>   one-line shell command; tv/root.py writes `eval "$(cat stage/<id>)"`
#      out/<id>   its stdout+stderr
#      rc/<id>    its exit status, written last
#
#    req/ stage/ out/ rc/ are sdk:sdk 0700: only uid 901 (the sdb shell, which
#    tv/root.py uses) and root can submit. Installed apps run as uid 5001 with
#    no capabilities and are locked out. Each command runs in a detached shell,
#    so the loop keeps accepting work while commands run.
#
# 2. Events. On the first start after a boot, and every time the screen comes
#    on, cf_tv_hooks.sh runs each file in /opt/cf_tv_daemon/on-boot.d/ or
#    on-wake.d/, detached. New behaviour belongs in a hook, not here: this file
#    is the root lifeline, and a broken daemon means falling back to the exploit.
#
#    Wake detection: the remote's power button does not reboot. A short "off"
#    only blanks the screen; a longer one also suspends the kernel. Either way
#    processes survive and boot hooks do not rerun. deviced's power state,
#    memory/pm/state (1 on, 3 screen off, 4 standby), goes 4 -> 1 in both cases
#    (2026-11-25, CU7000: an off/on with no kernel suspend). Earlier versions
#    looked for a jump in wall clock minus uptime, which needs a suspend, and
#    /proc/uptime counts suspended time on kernel 5.4: no wake was ever seen there.

BEGIN{
  RUN="/opt/cf_tv_daemon/state"; REQ=RUN "/req"; OUT=RUN "/out"; RC=RUN "/rc"
  STAGE=RUN "/stage"; LOCKS=RUN "/locks"
  DAEMONLOG="/opt/cf_tv_daemon.log"
  LOCK=RUN "/.lock"; PIDF=RUN "/daemon.pid"; BOOTF=RUN "/boot_id"
  HOOKS="/opt/cf_tv_daemon/cf_tv_hooks.sh"
  PM_EVERY=10    # loops (of 0.2 s) between power-state reads; one buxton2ctl read is ~26 ms

  system("mkdir -p " REQ " " OUT " " RC " " STAGE " " LOCKS)
  # Under systemd-run there is no shell to redirect output, so open the log here.
  # Appended, not truncated: after a crash and restart (the unit restarts on
  # failure), the lines before it are the evidence. Capped below.
  print strftime("%Y-%m-%d %H:%M:%S") " start pid " PROCINFO["pid"] >> DAEMONLOG
  close(DAEMONLOG)

  # Boot event once per boot, not on every daemon restart (deploys restart it).
  # Fired first, before the lock check and the housekeeping: under boot load
  # every system() call costs ~0.5 s, and the Bluetooth hook has a deadline
  # ~20 s after boot (payload/on-boot.d/50-btconnect). Firing before the lock
  # is safe: boot_id is written here, so a second instance (only ever started
  # later, e.g. by a deploy or an AT-HUB restart) sees it and fires nothing.
  bid = ""; getline bid < "/proc/sys/kernel/random/boot_id"; close("/proc/sys/kernel/random/boot_id")
  old = ""; getline old < BOOTF; close(BOOTF)
  if (bid != old) {
    print bid > BOOTF; close(BOOTF)
    event("boot")
  }

  # The lock and pid file live on /opt and survive reboots. A bare `kill -0`
  # on the old pid is wrong after a reboot: the number is often reused by an
  # unrelated process (observed: storaged held pid 1519), and the daemon then
  # exits believing it is already running. Only an alive process whose command
  # line is this script counts as the owner.
  if (system("mkdir " LOCK " 2>/dev/null") != 0) {
    pid=""
    getline pid < PIDF
    close(PIDF)
    if (pid != "" && pid != PROCINFO["pid"] &&
        system("grep -q cf_tv_daemon.awk /proc/" pid "/cmdline 2>/dev/null") == 0) {
      note("exit: already running as pid " pid)
      exit 0
    }
    system("rmdir " LOCK " 2>/dev/null")
    if (system("mkdir " LOCK " 2>/dev/null") != 0) exit 0
  }
  print PROCINFO["pid"] > PIDF
  close(PIDF)

  # Modes are set on every start: mkdir -p does not fix existing directories,
  # and deployments before 2026-10-24 left them 0777.
  # Numeric ids: the same payload may be run from a namespace whose /etc/passwd
  # lacks `root`/`sdk` (the archive exploit's does). system() discards the
  # error, so a name lookup that fails would leave the queue unrunnable.
  system("chown 0:0 " RUN " " LOCKS "; chmod 755 " RUN "; chmod 700 " LOCKS)
  system("chown 901:901 " REQ " " OUT " " RC " " STAGE "; chmod 700 " REQ " " OUT " " RC " " STAGE)
  system("rm -f " OUT "/* " RC "/* " REQ "/* " STAGE "/* 2>/dev/null")
  # Capped at 256 KiB (cut to the last 128 KiB): ~40 bytes a line, a few lines
  # a boot, so years of boots. Plain text on purpose: /opt has ~500 MB free.
  system("[ \"$(wc -c < " DAEMONLOG ")\" -gt 262144 ] && tail -c 131072 " DAEMONLOG " > " DAEMONLOG ".tmp && mv " DAEMONLOG ".tmp " DAEMONLOG)

  pm = power_state(); loops = 0
  while (1) {
    system("ls " REQ " > " RUN "/.list 2>/dev/null")
    while ((getline name < (RUN "/.list")) > 0) {
      if (name == "") continue
      cmd=""
      if ((getline cmd < (REQ "/" name)) <= 0) { close(REQ "/" name); continue }
      close(REQ "/" name)
      if (length(cmd) == 0) { system("rm -f " REQ "/" name); continue }

      # Status must be captured inside the group: `{ cmd; }` exits the
      # subshell for `exit`/`exec`, so an external `echo $?` never runs.
      # The outer ( … ) & backgrounds command and status together. Before
      # 2026-10-25 the `&` applied only to the final `echo`, so every command
      # ran in the foreground: a slow one froze the queue, the heartbeat and
      # wake detection (a recursive grep stalled it for minutes).
      shell = "( { " cmd " ; } > " OUT "/" name " 2>&1 ) ; echo $? > " RC "/" name
      system("( " shell " ) &")
      system("rm -f " REQ "/" name)
    }
    close(RUN "/.list")
    system("rm -f " RUN "/.list")

    if (++loops % PM_EVERY == 0) {
      now = power_state()
      if (now == 1 && (pm == 3 || pm == 4)) {
        note("wake, power state " pm " -> 1")
        event("wake")
      }
      if (now != "") pm = now
    }

    # Heartbeat. Clients cannot rely on the pid file or the directory existing:
    # both survive a reboot, so their presence does NOT mean a daemon is alive.
    # A recent value here is the only honest liveness signal.
    system("cut -d\" \" -f1 /proc/uptime > " RUN "/heartbeat.tmp 2>/dev/null; mv " RUN "/heartbeat.tmp " RUN "/heartbeat 2>/dev/null")

    system("/bin/sleep 0.2")
  }
}

# Hooks run detached; the runner logs to /opt/cf_tv_hooks.log itself.
function event(name) {
  note("event " name)
  system("( CF_EVENT=" name "; eval \"$(< " HOOKS ")\" ) >/dev/null 2>&1 &")
}

# deviced's power state: 1 on, 3 screen off, 4 standby; "" if unreadable.
function power_state(   cmd, line, n, f) {
  cmd = "buxton2ctl get memory memory/pm/state 2>/dev/null"
  line = ""; cmd | getline line; close(cmd)
  n = split(line, f, " ")
  return (n && f[n] ~ /^[0-9]+$/) ? f[n] + 0 : ""
}

function note(msg) {
  print strftime("%Y-%m-%d %H:%M:%S") " " msg >> DAEMONLOG
  close(DAEMONLOG)
}
