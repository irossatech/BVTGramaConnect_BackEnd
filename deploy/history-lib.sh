#!/usr/bin/env bash
# Deploy history and rollback checks, shared by deploy.sh (server with root) and qa/BVT-deploy.sh
# (qa, or production on a hosting panel). Sourced, not run. BVTGC-74 (audit CROSS-REL-002).
#
# The history is one line per deploy or rollback, tab separated:
#   deployed|rolled-back   <time, UTC>   <backend commit>   <web commit>
# A rollback goes back to a recorded pair, so the website and console always match their API.

# history_record <file> <kind> <backend dir> <web dir>
history_record() {
  printf '%s\t%s\t%s\t%s\n' "$2" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(git -C "$3" rev-parse HEAD)" "$(git -C "$4" rev-parse HEAD)" >>"$1"
}

# history_target <file> <backend commit now> <web commit now>: prints "<backend> <web>" to roll back to.
# Only "deployed" lines are targets. The code on disk is the newest recorded pair after a good deploy,
# or not recorded at all after a deploy that failed its checks: then the newest recorded pair is the
# answer. Otherwise it is the newest pair recorded before the code on disk, so a second rollback goes
# one step further back. Fails (status 1) when there is no such pair.
history_target() {
  [[ -r "$1" ]] || return 1
  awk -F'\t' -v cb="$2" -v cw="$3" '
    $1 == "deployed" { n++; b[n] = $3; w[n] = $4 }
    END {
      live = 0
      for (i = n; i >= 1; i--) if (b[i] == cb && w[i] == cw) { live = i; break }
      if (live == 0) { if (n > 0) { print b[n], w[n]; exit 0 } exit 1 }
      for (i = live - 1; i >= 1; i--) if (b[i] != cb || w[i] != cw) { print b[i], w[i]; exit 0 }
      exit 1
    }' "$1"
}

# migrations_unknown_to <python> <backend dir> <backend commit>: the migrations applied to the database
# (apps/ only) that the commit does not have, one file per line. Running that commit on this database
# could fail or lose data, so a rollback stops when this prints anything. Uses the code on disk now.
migrations_unknown_to() {
  local applied known
  applied="$(cd "$2" && "$1" manage.py shell --no-imports -c "
from django.apps import apps
from django.db.migrations.recorder import MigrationRecorder
ours = {c.label for c in apps.get_app_configs() if c.name.startswith('apps.')}
for app, name in MigrationRecorder.Migration.objects.values_list('app', 'name'):
    if app in ours:
        print(f'apps/{app}/migrations/{name}.py')
")" || return 1
  applied="$(printf '%s\n' "$applied" | grep '^apps/' | sort -u || true)"
  known="$(git -C "$2" ls-tree -r --name-only "$3" -- apps | grep '/migrations/[^/]*\.py$' | sort -u || true)"
  comm -23 <(printf '%s\n' "$applied") <(printf '%s\n' "$known") | sed '/^$/d'
}

# resolve_commit <repo dir> <commit or name>: the full commit id, or status 1.
resolve_commit() {
  git -C "$1" rev-parse --verify -q "$2^{commit}"
}
