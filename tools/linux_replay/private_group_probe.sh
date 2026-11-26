#!/bin/bash
# Private-group rule, with expectations stated independently of the code under test.
# Runs as root in the replay image; every Nexus step runs as the unprivileged user dev.
set -u
L=/home/dev/nexus/integrations/project_scope/launcher.py
PY=/home/dev/nexus/.venv/bin/python
as_dev() { runuser -u dev -- env HOME=/home/dev PATH=/usr/bin:/bin bash -c "$1"; }
expect() {  # expect <label> <accept|refuse> <command>
  if as_dev "$3" >/tmp/out 2>&1; then got=accept; else got=refuse; fi
  printf '%-58s expected=%-6s got=%-6s %s\n' "$1" "$2" "$got" "$([ "$2" = "$got" ] && echo OK || echo MISMATCH)"
  [ "$2" = "$got" ] || { sed 's/^/    /' /tmp/out; FAIL=1; }
}
FAIL=0
mk() { as_dev "umask $1; mkdir -p $2 && cd $2 && git init -q && git -c user.name=f -c user.email=f@x.invalid commit -q --allow-empty -m f"; }

mk 002 /home/dev/w/private
echo "mode of .git under umask 002: $(stat -c '%A %G' /home/dev/w/private/.git)"
expect "umask 002, group dev has only dev"            accept "$PY -I $L enroll /home/dev/w/private"

mk 022 /home/dev/w/plain
expect "umask 022 (control: no group write)"         accept "$PY -I $L enroll /home/dev/w/plain"

# A shared group that dev belongs to, made the repo's group: group write is someone else's write.
groupadd team && usermod -aG team dev
mk 002 /home/dev/w/shared && chgrp -R team /home/dev/w/shared/.git
expect "umask 002, .git group is shared group team"   refuse "$PY -I $L enroll /home/dev/w/shared"

# Another account joins dev's own group: the formerly private group is no longer private.
useradd -m mallory && usermod -aG dev mallory
expect "same private repo after mallory joins group dev"  refuse "cd /home/dev/w/private && $PY -I $L claude /bin/echo"
expect "umask 022 repo still fine (control)"              accept "cd /home/dev/w/plain && $PY -I $L claude /bin/echo"
gpasswd -d mallory dev >/dev/null
expect "after mallory leaves group dev"                   accept "cd /home/dev/w/private && $PY -I $L claude /bin/echo"

# Another account uses dev's group as its primary group.
useradd -m -g dev eve
expect "eve has group dev as her primary group"           refuse "cd /home/dev/w/private && $PY -I $L claude /bin/echo"
echo "probe_result=$([ $FAIL = 0 ] && echo passed || echo FAILED)"
