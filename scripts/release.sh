#!/usr/bin/env bash
# Release a plugin and install it on all three hosts (Codex, Claude Code, omp), in one step.
#
# Publishing and installing used to be separate: the tag went out, and refreshing the hosts was a
# prose reminder at the end of the README. That reminder got skipped, so the hosts silently ran
# stale versions. This script folds the install into the release and refuses to report success
# until every host reports the released version.
#
# Usage:
#   scripts/release.sh [--dry-run] [--plugin NAME] [-m MESSAGE]
#
# One plugin per run (default: research). Release core and research separately. On omp, core is a
# user-scope install; a domain plugin is upgraded in every project that installed it at project
# scope, and never installed at user scope.
#
# --dry-run validates and reports each host's installed version against the target without
# tagging, pushing, or touching any host. It doubles as a standalone drift check. It is not
# strictly read-only: it runs `git fetch origin main` and the test suite.

set -euo pipefail

PLUGIN="research"
MARKETPLACE="dansolombrino-skills"
# Where omp gets the marketplace when it has none configured: the same GitHub repo Claude uses.
OMP_SOURCE="dansolombrino/skills"
DRY_RUN=0
TAG_MESSAGE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --plugin) PLUGIN="${2:?--plugin needs a value}"; shift 2 ;;
    -m|--message) TAG_MESSAGE="${2:?--message needs a value}"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PLUGIN_DIR="plugins/$PLUGIN"
CODEX_MANIFEST="$PLUGIN_DIR/.codex-plugin/plugin.json"
PLUGIN_ID="$PLUGIN@$MARKETPLACE"

fail() { echo "✘ $*" >&2; exit 1; }
warn() { echo "! $*" >&2; }
step() { echo; echo "── $* ──"; }

# ─────────────────────────────── 1. preflight ───────────────────────────────
step "Preflight"

[ -f "$CODEX_MANIFEST" ] || fail "no such plugin: $PLUGIN_DIR"
command -v claude >/dev/null || fail "claude is not on PATH"
command -v codex  >/dev/null || fail "codex is not on PATH"
command -v omp    >/dev/null || fail "omp is not on PATH"

# Git state is fatal for a real release and advisory for --dry-run, so the drift check stays
# usable while there is work in progress.
git_check() { if [ "$DRY_RUN" -eq 1 ]; then warn "$1"; else fail "$1"; fi; }

branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" = "main" ] || git_check "on branch '$branch', expected 'main'"
[ -z "$(git status --porcelain)" ] || git_check "working tree is dirty; commit before releasing"

git fetch --quiet origin main 2>/dev/null || warn "could not fetch origin/main"
behind="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)"
[ "$behind" -eq 0 ] || git_check "local main is $behind commit(s) behind origin/main; pull first"

# The script's own target. validate_repo.py enforces that both manifests carry the same version, so
# reading one is enough.
TARGET="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$CODEX_MANIFEST")"
TAG="$PLUGIN--v$TARGET"

# A forgotten bump has to stop the release before anything is pushed. On --dry-run an existing tag
# is the normal state between releases, so report only whether the plugin moved past it.
if [ "$DRY_RUN" -eq 1 ] && git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  if bump_report="$(python3 scripts/check_version_bump.py --plugin "$PLUGIN" 2>&1)"; then
    echo "✔ $bump_report"
  else
    warn "$bump_report"
  fi
elif ! tag_report="$(claude plugin tag "$PLUGIN_DIR" --dry-run 2>&1)"; then
  git_check "$tag_report"
fi

# `codex plugin add` would keep a domain plugin on globally, and Verify would then fail after the
# tag is public. A real release stops here instead; --dry-run counts it as drift further down.
if ! global_on="$(python3 scripts/codex_plugin_state.py check-global 2>&1)"; then
  git_check "domain plugin on, or config unreadable, in the global Codex config: $global_on"$'\n'"  fix with: python3 scripts/codex_plugin_state.py set <plugin-id> false"
fi

# A domain plugin installed at omp user scope loads in every project, which per-project opt-in exists
# to prevent, and Verify fails on it. Stop before the tag is public; --dry-run counts it as drift.
if ! user_scoped="$(python3 scripts/omp_plugin_state.py check-user 2>&1)"; then
  git_check "domain plugin installed at omp user scope, or omp's plugin list unreadable: $user_scoped"$'\n'"  fix with: omp plugin uninstall --scope user <plugin-id>"
fi

echo "✔ preflight ok (branch=$branch, dry_run=$DRY_RUN)"

# ─────────────────────────────── 2. validate ────────────────────────────────
step "Validate"

run_quietly() {
  local label="$1"; shift
  local output
  if output="$("$@" 2>&1)"; then
    echo "✔ $label"
  else
    echo "$output" >&2
    fail "$label failed"
  fi
}

run_quietly "validate_repo.py" python3 scripts/validate_repo.py

# The custom validator covers every host's catalogs and metadata; this covers Claude's own schema as
# the CLI moves. Plain --strict: --profile directory would add submission rules that do not apply.
run_quietly "claude plugin validate --strict ." claude plugin validate --strict .
for plugin_dir in plugins/*/; do
  run_quietly "claude plugin validate --strict $plugin_dir" claude plugin validate --strict "$plugin_dir"
done

# The suite builds git fixtures in temp dirs. An ambient TMPDIR on a foreign-owned mount makes git
# refuse to work there ("dubious ownership"), which would fail every release for a reason that has
# nothing to do with the release. Pin a git-friendly scratch dir instead.
run_quietly "test suite" env TMPDIR="${RELEASE_TMPDIR:-/tmp}" python3 -m unittest discover tests

echo "✔ target version: $PLUGIN $TARGET"

# ──────────────────────── installed-version readers ─────────────────────────
claude_version() {
  claude plugin list --json 2>/dev/null | python3 -c '
import json, sys
try:
    entries = json.load(sys.stdin)
except Exception:
    print("unknown"); sys.exit()
for e in entries:
    if e.get("id") == sys.argv[1]:
        print(e.get("version", "unknown")); sys.exit()
print("not-installed")
' "$PLUGIN_ID"
}

codex_version() {
  codex plugin list --marketplace "$MARKETPLACE" --json 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("unknown"); sys.exit()
for e in data.get("installed", []):
    if e.get("pluginId") == sys.argv[1]:
        print(e.get("version", "unknown")); sys.exit()
print("not-installed")
' "$PLUGIN_ID"
}

# core: its user-scope version. A domain plugin: the version of its project-scope installs, one per
# opted-in project, comma-joined when they differ, or `none` when no project has it. See
# scripts/omp_plugin_state.py for how the projects are found.
omp_version() {
  python3 scripts/omp_plugin_state.py version "$PLUGIN_ID" 2>/dev/null || echo "unknown"
}

# A domain plugin with no project install has nothing to be stale: that is not drift.
omp_on_target() {
  [ "$1" = "$TARGET" ] || { [ "$PLUGIN" != "core" ] && [ "$1" = "none" ]; }
}

# The configured source of the marketplace in omp, empty when omp has none by that name.
# Reads the whole list (no early exit) so pipefail cannot trip on a SIGPIPE.
omp_source() {
  { omp plugin marketplace list 2>/dev/null || true; } | awk -v name="$MARKETPLACE" \
    '$1 == name && !found { sub(/^[ \t]*[^ \t]+[ \t]+/, ""); print; found = 1 }'
}

CLAUDE_BEFORE="$(claude_version)"
CODEX_BEFORE="$(codex_version)"
OMP_BEFORE="$(omp_version)"
OMP_SOURCE_BEFORE="$(omp_source)"
if [ -z "$OMP_SOURCE_BEFORE" ]; then
  warn "omp has no marketplace '$MARKETPLACE'; a release adds it from $OMP_SOURCE"
elif [ "$OMP_SOURCE_BEFORE" != "$OMP_SOURCE" ]; then
  warn "omp's marketplace '$MARKETPLACE' comes from '$OMP_SOURCE_BEFORE', not $OMP_SOURCE; a release installs from it as configured"
fi

# ───────────────────────────────── dry run ──────────────────────────────────
if [ "$DRY_RUN" -eq 1 ]; then
  step "Drift check (no changes made)"
  printf '  target      %s\n  claude      %s\n  codex       %s\n  omp         %s\n' \
    "$TARGET" "$CLAUDE_BEFORE" "$CODEX_BEFORE" "$OMP_BEFORE"
  dry_drift=0
  if [ "$CLAUDE_BEFORE" = "$TARGET" ] && [ "$CODEX_BEFORE" = "$TARGET" ] && omp_on_target "$OMP_BEFORE"; then
    echo "✔ every host is on $TARGET"
  else
    echo "✘ host(s) not on $TARGET — run scripts/release.sh (or the sync commands in README.md)" >&2
    dry_drift=1
  fi
  if ! python3 scripts/codex_plugin_state.py check-global >/dev/null 2>&1; then
    echo "✘ the global Codex config has a domain plugin on, or cannot be read (see the warning above)" >&2
    dry_drift=1
  fi
  if ! python3 scripts/omp_plugin_state.py check-user >/dev/null 2>&1; then
    echo "✘ omp has a domain plugin installed at user scope, or its plugin list cannot be read (see the warning above)" >&2
    dry_drift=1
  fi
  exit "$dry_drift"
fi

# ────────────────────────────── 3. tag + push ───────────────────────────────
step "Tag and push"

git push origin main

# Preflight already ran this as a dry run; it stays the real tag creation and the last word.
if [ -n "$TAG_MESSAGE" ]; then
  claude plugin tag "$PLUGIN_DIR" --push --remote origin -m "$TAG_MESSAGE"
else
  claude plugin tag "$PLUGIN_DIR" --push --remote origin -m "$(git log -1 --format=%s)"
fi

# ───────────────────────────── 4. sync Claude ───────────────────────────────
step "Sync Claude Code"

claude plugin marketplace update "$MARKETPLACE"
# `update` only refreshes an installed plugin, so a plugin's first release installs it instead.
if [ "$CLAUDE_BEFORE" = "not-installed" ]; then
  claude plugin install "$PLUGIN_ID" --scope user
else
  claude plugin update "$PLUGIN_ID" --scope user
fi

# ────────────────────────────── 5. sync Codex ───────────────────────────────
step "Sync Codex"

# Codex has no `update`; the documented path is remove-then-add against the refreshed snapshot.
# `remove` drops the plugin's config table and `add` always writes `enabled = true`, so record the
# state first and put it back. A plugin with no recorded state takes its default: core on, others off.
codex plugin marketplace upgrade "$MARKETPLACE"
CODEX_ENABLED_BEFORE="$(python3 scripts/codex_plugin_state.py get "$PLUGIN_ID")"
case "$CODEX_ENABLED_BEFORE" in
  true|false) CODEX_ENABLED_WANT="$CODEX_ENABLED_BEFORE" ;;
  *) if [ "$PLUGIN" = "core" ]; then CODEX_ENABLED_WANT=true; else CODEX_ENABLED_WANT=false; fi ;;
esac
codex plugin remove "$PLUGIN_ID" || warn "codex plugin remove failed; continuing to add"
codex plugin add "$PLUGIN_ID" || fail "codex plugin add failed; rerun it, then: python3 scripts/codex_plugin_state.py set $PLUGIN_ID $CODEX_ENABLED_WANT"
python3 scripts/codex_plugin_state.py set "$PLUGIN_ID" "$CODEX_ENABLED_WANT" || fail "could not restore enabled=$CODEX_ENABLED_WANT for $PLUGIN_ID in the global Codex config"

# ─────────────────────────────── 6. sync omp ────────────────────────────────
step "Sync omp"

# omp reads the Claude catalog (.claude-plugin/marketplace.json), so the marketplace is the same repo.
if [ -z "$OMP_SOURCE_BEFORE" ]; then
  omp plugin marketplace add "$OMP_SOURCE" || fail "omp plugin marketplace add $OMP_SOURCE failed"
fi
omp plugin marketplace update "$MARKETPLACE"
# `upgrade` naming a plugin reinstalls the catalog's current version and keeps a disabled install
# disabled; it needs an existing install, so core's first release installs it instead.
if [ "$PLUGIN" = "core" ]; then
  if [ "$OMP_BEFORE" = "not-installed" ]; then
    omp plugin install --scope user "$PLUGIN_ID"
  else
    omp plugin upgrade --scope user "$PLUGIN_ID"
  fi
else
  # A domain plugin lives only in the projects that opted in (project-init installs it there);
  # `upgrade` acts on the project of its working directory, so visit each one.
  omp_projects="$(python3 scripts/omp_plugin_state.py projects "$PLUGIN_ID")"
  [ -n "$omp_projects" ] || echo "no project has $PLUGIN_ID installed in omp; nothing to upgrade"
  while IFS= read -r project; do
    [ -n "$project" ] || continue
    (cd "$project" && omp plugin upgrade --scope project "$PLUGIN_ID" </dev/null) \
      || warn "omp plugin upgrade failed in $project"
  done <<< "$omp_projects"
fi

# ──────────────────────── 7. verify — the actual gate ───────────────────────
step "Verify"

CLAUDE_AFTER="$(claude_version)"
CODEX_AFTER="$(codex_version)"
OMP_AFTER="$(omp_version)"

printf '  %-8s %-12s %s\n' "host" "before" "after"
printf '  %-8s %-12s %s\n' "claude" "$CLAUDE_BEFORE" "$CLAUDE_AFTER"
printf '  %-8s %-12s %s\n' "codex"  "$CODEX_BEFORE"  "$CODEX_AFTER"
printf '  %-8s %-12s %s\n' "omp"    "$OMP_BEFORE"    "$OMP_AFTER"

drift=0
[ "$CLAUDE_AFTER" = "$TARGET" ] || { warn "Claude is on $CLAUDE_AFTER, expected $TARGET"; drift=1; }
[ "$CODEX_AFTER"  = "$TARGET" ] || { warn "Codex is on $CODEX_AFTER, expected $TARGET";  drift=1; }
if ! omp_on_target "$OMP_AFTER"; then
  warn "omp is on $OMP_AFTER, expected $TARGET"
  if [ "$PLUGIN" != "core" ]; then
    while IFS= read -r project; do
      [ -n "$project" ] || continue
      warn "  $project: $(python3 scripts/omp_plugin_state.py version "$PLUGIN_ID" --project "$project")"
    done <<< "$(python3 scripts/omp_plugin_state.py projects "$PLUGIN_ID")"
  fi
  drift=1
fi
if ! global_on="$(python3 scripts/codex_plugin_state.py check-global 2>&1)"; then
  warn "domain plugin on, or config unreadable, in the global Codex config: $global_on"
  drift=1
fi
if ! user_scoped="$(python3 scripts/omp_plugin_state.py check-user 2>&1)"; then
  warn "domain plugin installed at omp user scope, or omp's plugin list unreadable: $user_scoped"
  drift=1
fi
[ "$drift" -eq 0 ] || fail "released $PLUGIN $TARGET but a host is not right — fix before relying on it"

echo
echo "✔ $PLUGIN $TARGET released and installed on Codex, Claude Code, and omp."
echo "  Restart Codex, Claude Code, and omp sessions, or run /reload-plugins in a Claude Code or omp"
echo "  session (omp loads a changed extension only on restart); other running sessions keep the"
echo "  version they launched with."
