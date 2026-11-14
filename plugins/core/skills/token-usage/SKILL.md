---
name: token-usage
description: Run the token-usage page - an indexer that reads the session logs the coding agents (Claude Code, omp, Codex, including the ones Orca launches) already write on this machine and, over ssh, on the rest of the fleet, and a read-only page behind service-hub that charts every model call like a stock - the running total as a price line against the previous period, or the burn rate per hour as candles with a moving average, with stacked volume bars, split by agent, model, project or machine, in tokens or estimated cost, over 1 day to all time, counted the way Claude Code's `/stats` and omp's own stats count. Use when the user asks how many tokens or how much money the agents used, which project, model or machine used the most, wants that chart, or to install, troubleshoot or reconfigure it. Do not use for a provider's billing dashboard.
---

# token-usage

`scripts/tokens.py` is a stdlib service. Every `every` seconds it indexes what each agent's session
logs gained since the last pass into SQLite; every `pull_every` seconds it runs itself over ssh on
each fleet machine (`export`, which needs only `python3` there) and stores the calls that changed
since the last answer. It serves a read-only page on loopback that service-hub proxies (`/tokens/`).

Sources, fleet, configuration and install: [references/setup.md](references/setup.md).

## What it counts

The page's default (Claude Code + omp, every machine, every token kind) equals, machine by machine,
Claude Code's `/stats` total plus omp's own all-time count, the sum a status line's all-time row
usually shows:

- **Claude Code** like its `/stats`: every assistant line of the transcripts, so a reply logged
  over several lines counts on each (`/stats` does). For the days `stats-cache.json` has computed,
  whatever `/stats` counted beyond the transcript lines still indexed is added as
  `(Claude Code history)` rows, one per day and model at noon UTC: transcripts Claude Code deleted
  stay counted. Per model, the kinds of those rows add up to the all-time kinds of `/stats`.
- **omp**: assistant messages and side calls, once per entry id and time (forks and archived
  copies count once); Claude Code sessions imported into omp are left to Claude Code.
- **Codex** (off by default, being outside that sum): the growth of each session's running totals.
- Rows are never deleted: a log removed later, or a machine that is unreachable, keeps what was
  counted.
- **Cost is an estimate**: per model and token kind, the latest price per token omp recorded,
  applied to every agent's calls of that model; tokens of models omp never priced are left out
  and reported.
- The price line is the running total since the range start, with the previous period's total
  dotted. A candle is the burn rate per hour over the trailing candle-length window: it opens
  where the previous candle closed, green when the rate rose.
- The range and the candle interval are separate choices, as on TradingView: 1m, 5m, 15m, 30m,
  1h, 4h, 6h, 12h, 1D, 1W or Auto (1h for a day, 6h for a week, 1D up to 120 days, 1W beyond). An
  interval fits a range when it gives 1 to 1600 candles; the page greys out the others. Candles
  follow the local clock: a day starts at local midnight, so it has 23 or 25 hours across a daylight
  saving change, and a week starts on Monday. Every range starts at a local hour, so changing the
  interval never changes the totals; a candle the range start cuts is drawn partial. The
  `(Claude Code history)` rows are daily totals placed at noon UTC, so candles under a day show
  them as a spike at that hour.
- Projects are the basename of the working directory, so an agent run in a worktree counts under
  the worktree's name; a directory named by a hash counts under its parent.

## Commands

```bash
python3 scripts/tokens.py scan [--verbose]      # index this machine's new calls now
python3 scripts/tokens.py pull                  # fetch every fleet machine now
python3 scripts/tokens.py report [--range all] [--interval auto|1m|…|1W] [--group tool|model|project|machine] [--metric tokens|cost] [--tools claude,omp] [--json]
python3 scripts/tokens.py serve [--no-scan]     # what the service runs
python3 scripts/tokens.py backup [--dir D] [--no-git]  # dump the index to tokens.backup_dir, commit and push
python3 scripts/tokens.py restore [--dir D]     # merge a backup into the index
```

Answer usage questions from `report` (or `/api/series`), not by reading the logs by hand. Report
the page's hub URL from `hub.py url tokens` (service-hub), never the backend port.

## Safety contract

- Prompts, answers and tool output are never parsed into the database; only the fields above.
  Project names (directory basenames), model and machine names do appear on the page.
- `export` on a fleet machine only reads its logs and writes its own index under
  `~/.local/share/token-usage/`; it changes nothing else there.
- The service binds loopback only (a non-loopback `bind` is refused), answers GET/HEAD only, and
  sends a strict Content-Security-Policy. The page loads no external scripts and builds its DOM
  and SVG without parsing markup.
- Share it only through a service-hub guest token, and only with people who may see the project
  names.
- `backup` pushes working directories, model and machine names to the backup repository's
  remote: only ever a private repository.
