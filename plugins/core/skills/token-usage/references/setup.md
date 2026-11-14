# token-usage setup

## Sources

Each agent's logs, searched recursively; a missing directory is skipped.

| Agent | Default directories | What counts |
|---|---|---|
| Claude Code (`claude`) | `~/.claude/projects` (subagent transcripts included), and `stats-cache.json` next to it | every assistant line, as `/stats` counts; the history `/stats` kept for days whose transcripts are gone |
| omp (`omp`) | `~/.omp/agent/sessions` (subagent sessions included), `~/.omp/agent/archive/sessions` (`.jsonl.gz`, moved there by `omp gc`) | an assistant message or a side call (`model_usage`), once per entry id and time; a Claude Code session imported into omp (`claude-` ids) is left to Claude Code |
| Codex (`codex`) | `~/.codex/sessions`, `~/.codex/archived_sessions`, and the same two folders of each per-account Codex home Orca runs Codex in (`orca/codex-accounts/*/home` under `~/Library/Application Support` or `~/.config`); `.jsonl.zst` needs the `zstd` CLI, looked for on `PATH` and in `/opt/homebrew/bin`, `/usr/local/bin`, `/opt/local/bin`, `~/.local/bin` | the growth of the session's running token totals at each token event; a rollout found in several homes counts once |

Orca launches these same agents, so their calls are in these logs; the worktree an agent ran in is
its project. A file is read from where the previous pass stopped, and only up to its last complete
line; a file that shrank is read again from the start, and calls already indexed are not counted
twice. The first pass reads everything (about 25 s for 5 GB of logs); later passes take
milliseconds.

**Claude Code history.** `stats-cache.json` holds, up to `lastComputedDate`, per-model totals per
UTC day and all-time totals per kind. On every pass, for each day and model it covers, what it
counts beyond the transcript lines indexed for that day becomes one `(Claude Code history)` row at
noon UTC (totals older than its daily list go to the first session date). The rows are recomputed,
never added to, so the Claude Code total always equals `/stats` (cached days) plus the transcript
lines after them. Only the first `sources.claude` directory has its `stats-cache.json` read.

Codex reports cached input inside its input count; the index stores it as cache read, and input
as the uncached rest, the same split as the other agents.

## Fleet

`pull` (and the service, every `pull_every` seconds) runs on each host
`ssh <host> python3 - export --since <cursor>`, piping this script, so the host needs only
`python3` (3.9 or later) and non-interactive ssh. The host indexes its own logs into its own
`~/.local/share/token-usage/usage.sqlite` and answers with the calls that changed since the
cursor, its history rows and its prices. An answer that stops before its end is discarded; a host
that fails keeps everything already pulled, and the page's foot says since when.

## Registry `[tokens]` table

Optional, in the machines registry `~/.config/rigsync/machines.toml` (`RIGSYNC_REGISTRY` or
`--registry` overrides). Every key is optional.

```toml
[tokens]
port       = 40978            # loopback port the page listens on
bind       = "127.0.0.1"      # must be loopback; the hub is the only way in
every      = 60               # seconds between index passes (>= 10)
pull_every = 300              # seconds between fleet pulls (>= 60)
hosts      = ["macbook"]      # ssh aliases of the other machines
hosts_file = "~/path/hosts"   # more of them, one per line, # for comments
machine    = "rig-4090"       # this machine's name on the page; default: the short hostname
db         = "~/.local/share/token-usage/usage.sqlite"
title      = "Token usage"
backup_dir = "~/.local/share/token-usage/backup"  # where `backup` writes; a git checkout gets committed and pushed

[tokens.sources]              # replaces the default directories of an agent; * and ? wildcards match several
claude = ["~/.claude/projects"]
```

Deleting the database rebuilds it from the logs that still exist; what only the database held
(calls of logs deleted since, older pulls) is lost unless `backup_dir` is set (next section).

## Back it up

The index is the only copy of every call whose log is gone (Claude Code deletes transcripts after
`cleanupPeriodDays`, 30 by default). `tokens.py backup` writes every call and price under
`backup_dir` as JSON lines, one file per machine and UTC month (`calls/<machine>/<YYYY-MM>.jsonl`,
`prices.jsonl`), each with a header naming its columns; files are rewritten only when they
changed. When `backup_dir` is a git checkout it then commits and pushes, so make it a clone of a
**private** repository: the files hold working directories, model and machine names. About 50 MB
for 200k calls; a month's file changes only while that month is current.

`tokens.py restore` merges a backup into the index: a call already held keeps the larger count of
each kind, so it never lowers a total and is safe to repeat. `serve` restores by itself when its
index is new (deleted, or rebuilt after a schema change), before the first scan.

```bash
gh repo create <owner>/token-usage-backup --private
git clone https://github.com/<owner>/token-usage-backup ~/.local/share/token-usage/backup
```

## Install as a user service

```bash
mkdir -p ~/.config/systemd/user
for unit in token-usage.service token-usage-backup.service token-usage-backup.timer; do
  sed "s|__TOKENS_PY__|<absolute path to this skill's scripts/tokens.py>|" <skill>/assets/$unit > ~/.config/systemd/user/$unit
done
systemctl --user daemon-reload && systemctl --user enable --now token-usage.service token-usage-backup.timer
loginctl enable-linger "$USER"   # keep both running without a login session
python3 <skill>/scripts/tokens.py report --range all --group machine
```

The service restarts whenever it stops, without a retry limit. The timer backs up every 6 hours,
and on the next boot when a run was missed; `journalctl --user -u token-usage-backup` shows each
run. Skip the two backup units on a machine without `backup_dir`.

Point the unit at a repository checkout on the machine, or at a small launcher you keep outside
the install that starts the newest installed copy. Never point it straight at the installed skill
path: that path contains the plugin version and the host deletes it some time after an update, so
the unit would stop starting. The page assets are read from `assets/` next to `scripts/` on every
request.

## Put it behind service-hub

```toml
[hub.services.tokens]
name        = "Token usage"
description = "Every model call of the coding agents across the fleet, charted like a stock."
icon        = "🪙"
url         = "http://127.0.0.1:40978"
```

Restart the hub (`systemctl --user restart service-hub.service`), check `hub.py status`, and open
the URL `hub.py url tokens` prints.

## Troubleshooting

- **The page says it is indexing:** the first pass is running; `journalctl --user -u token-usage -n 20`.
- **A machine says failed:** the `[pull]` lines of the journal give ssh's last error; check
  `ssh -o BatchMode=yes <host> python3 --version`.
- **An agent is missing:** its logs are elsewhere; set `[tokens.sources]`. `scan --verbose` lists
  each file it indexes for the first time.
- **Cost is lower than expected:** the foot of the page counts the tokens of models omp never
  priced; one omp call of that model gives it a price.
