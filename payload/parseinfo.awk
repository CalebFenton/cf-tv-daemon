#!/usr/bin/awk -f
# parseinfo.sh - vendor behaviour plus our root boot hook.
#
# Installed as /opt/etc/parseinfo.sh (root 0755, immutable: /opt/etc is
# world-writable, so without the flag any app could rename a file over this
# one and run as root at the next boot). Runs as root at boot via
# AT-HUB.service (System::Privileged). gawk is not UEP-hooked, which is why
# this executes at all.
#
# What it adds over the vendor script, which only writes /proc/target_version
# (archive/originals/parseinfo.ORIGINAL.sh):
#   /opt/masklist  units masked and stopped at every boot; `boot restore`
#                  drops every mask with this file
#   /opt/cmdq      queued commands, run detached, output to /opt/cmdout
#   then cf_tv_daemon_start.awk starts cf_tv_daemon (queue + boot/wake hooks)

BEGIN{
  repo=""; model=""
  while ((getline l < "/etc/prd_info.ini") > 0) {
    if (l ~ /^Build=/) { split(l,a,"="); split(a[2],b,";"); repo=b[1] }
    if (l ~ /^Model=/) { split(l,a,"="); split(a[2],b,";"); model=b[1] }
  }
  close("/etc/prd_info.ini")
  v="__" repo "__" model
  print v > "/proc/target_version"
  close("/proc/target_version")

  # --- masklist (docs/04-system/masking-daemons.md) ---
  system("mkdir -p /run/systemd/system")
  n=0
  while ((getline m < "/opt/masklist") > 0) {
    if (length(m)==0) continue
    system("ln -sf /dev/null /run/systemd/system/" m ".service"); n++
  }
  close("/opt/masklist")
  if (n>0) system("systemctl daemon-reload")
  while ((getline m < "/opt/masklist") > 0) {
    if (length(m)==0) continue
    system("systemctl stop " m ".service 2>/dev/null")
  }
  close("/opt/masklist")

  # --- queued commands ---
  q=0
  while ((getline c < "/opt/cmdq") > 0) {
    if (length(c)==0) continue
    if (c ~ /^[[:space:]]*sleep/) continue
    system(c " >> /opt/cmdout 2>&1 &"); q++; if (q>20) break
  }
  close("/opt/cmdq"); system(": > /opt/cmdq")

  # --- persistent root daemon ---
  # The launcher is a separate file because tv/root.py reuses it to restart the
  # daemon after a deploy; it is awk because .sh files are UEP-refused.
  system("/usr/bin/awk -f /opt/cf_tv_daemon/cf_tv_daemon_start.awk")
}
