#!/usr/bin/env bash
# Release a plugin and install it on both hosts, in one step.
#
# Publishing and installing used to be separate: the tag went out, and refreshing the hosts was a
# prose reminder at the end of the README. That reminder got skipped, so the hosts silently ran
# stale versions. This script folds the install into the release and refuses to report success
# until both hosts report the released version.
#
# Usage:
#   scripts/release.sh [--dry-run] [--plugin NAME] [-m MESSAGE]
#
# --dry-run validates and reports each host's installed version against the target without
# tagging, pushing, or touching either host. It doubles as a standalone drift check.

set -euo pipefail

PLUGIN="research"
MARKETPLACE="dansolombrino-skills"
DRY_RUN=0
TAG_MESSAGE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --plugin) PLUGIN="${2:?--plugin needs a value}"; shift 2 ;;
    -m|--message) TAG_MESSAGE="${2:?--message needs a value}"; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

# Git state is fatal for a real release and advisory for --dry-run, so the drift check stays
# usable while there is work in progress.
git_check() { if [ "$DRY_RUN" -eq 1 ]; then warn "$1"; else fail "$1"; fi; }

branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" = "main" ] || git_check "on branch '$branch', expected 'main'"
[ -z "$(git status --porcelain)" ] || git_check "working tree is dirty; commit before releasing"

git fetch --quiet origin main 2>/dev/null || warn "could not fetch origin/main"
behind="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)"
[ "$behind" -eq 0 ] || git_check "local main is $behind commit(s) behind origin/main; pull first"

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

# validate_repo.py covers three of the four places a version lives. The fourth is the pin in
# tests/test_research_contracts.py, which is bumped in every release commit — catching a partial
# bump here beats letting it surface as a bare assertion diff halfway through the suite.
CONTRACT_TEST="tests/test_research_contracts.py"
if [ -f "$CONTRACT_TEST" ]; then
  pinned="$(sed -n 's/.*assertEqual(manifest\["version"\], "\([0-9.]*\)").*/\1/p' "$CONTRACT_TEST" | head -1)"
  manifest_version="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$CODEX_MANIFEST")"
  if [ -n "$pinned" ] && [ "$pinned" != "$manifest_version" ]; then
    fail "version bump is incomplete: manifests say $manifest_version but $CONTRACT_TEST still pins $pinned"
  fi
fi

# The suite builds git fixtures in temp dirs. An ambient TMPDIR on a foreign-owned mount makes git
# refuse to work there ("dubious ownership"), which would fail every release for a reason that has
# nothing to do with the release. Pin a git-friendly scratch dir instead.
run_quietly "test suite" env TMPDIR="${RELEASE_TMPDIR:-/tmp}" python3 -m unittest discover tests

# validate_repo.py already enforces that the Codex manifest, the Claude manifest, and the Claude
# catalog entry carry the same version, so reading any one of them is enough.
TARGET="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$CODEX_MANIFEST")"
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

CLAUDE_BEFORE="$(claude_version)"
CODEX_BEFORE="$(codex_version)"

# ───────────────────────────────── dry run ──────────────────────────────────
if [ "$DRY_RUN" -eq 1 ]; then
  step "Drift check (no changes made)"
  printf '  target      %s\n  claude      %s\n  codex       %s\n' \
    "$TARGET" "$CLAUDE_BEFORE" "$CODEX_BEFORE"
  if [ "$CLAUDE_BEFORE" = "$TARGET" ] && [ "$CODEX_BEFORE" = "$TARGET" ]; then
    echo "✔ both hosts are on $TARGET"
    exit 0
  fi
  echo "✘ host(s) not on $TARGET — run scripts/release.sh (or the sync commands in README.md)" >&2
  exit 1
fi

# ────────────────────────────── 3. tag + push ───────────────────────────────
step "Tag and push"

git push origin main

# `claude plugin tag` re-checks plugin.json against the enclosing marketplace entry and refuses to
# move an existing tag, so a forgotten version bump stops the release here.
if [ -n "$TAG_MESSAGE" ]; then
  claude plugin tag "$PLUGIN_DIR" --push --remote origin -m "$TAG_MESSAGE"
else
  claude plugin tag "$PLUGIN_DIR" --push --remote origin -m "$(git log -1 --format=%s)"
fi

# ───────────────────────────── 4. sync Claude ───────────────────────────────
step "Sync Claude Code"

claude plugin marketplace update "$MARKETPLACE"
claude plugin update "$PLUGIN_ID" --scope user

# ────────────────────────────── 5. sync Codex ───────────────────────────────
step "Sync Codex"

# Codex has no `update`; the documented path is remove-then-add against the refreshed snapshot.
codex plugin marketplace upgrade "$MARKETPLACE"
codex plugin remove "$PLUGIN_ID" || warn "codex plugin remove failed; continuing to add"
codex plugin add "$PLUGIN_ID"

# ──────────────────────── 6. verify — the actual gate ───────────────────────
step "Verify"

CLAUDE_AFTER="$(claude_version)"
CODEX_AFTER="$(codex_version)"

printf '  %-8s %-12s %s\n' "host" "before" "after"
printf '  %-8s %-12s %s\n' "claude" "$CLAUDE_BEFORE" "$CLAUDE_AFTER"
printf '  %-8s %-12s %s\n' "codex"  "$CODEX_BEFORE"  "$CODEX_AFTER"

drift=0
[ "$CLAUDE_AFTER" = "$TARGET" ] || { warn "Claude is on $CLAUDE_AFTER, expected $TARGET"; drift=1; }
[ "$CODEX_AFTER"  = "$TARGET" ] || { warn "Codex is on $CODEX_AFTER, expected $TARGET";  drift=1; }
[ "$drift" -eq 0 ] || fail "released $PLUGIN $TARGET but a host did not pick it up — fix before relying on it"

echo
echo "✔ $PLUGIN $TARGET released and installed on both hosts."
echo "  Restart Claude Code and Codex; running sessions keep the version they launched with."
