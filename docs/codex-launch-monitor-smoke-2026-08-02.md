# Codex experiment launch and monitoring smoke test — 2026-08-02

## Outcome

The Codex-native research workflow passed an end-to-end smoke test on the three approved
targets: local `rig-4090` GPU 0, `rig-3090-ti` GPU 0, and `behemoth` GPU 0. The test used a
16-parameter, Python-standard-library toy model. It did not import PyTorch or another GPU
framework, so it reserved no model VRAM and opened no CUDA context.

The repository implementation is staged as research plugin version `1.1.0`. It adds the
`rig-sync` skill and makes project initialization and sweep dispatch aware of a local hub, so
Codex does not attempt to SSH into the machine it is already running on.

## Validation performed

- Repository unit and contract tests: 18 passed after the hostname-drift follow-up.
- Repository structure validator: passed for 1 plugin, 7 distributed skills, and 2 repo-local
  maintenance skills. The workspace's `.agents` mount was empty/read-only, so the validator's
  supported `--agents-source` option was pointed at an archive of the tracked `.agents` tree.
- New-skill quick validation: passed.
- `git diff --check`: passed.
- Independent marketplace skill review: no remaining blockers, should-fix findings, or nits.
- Temporary marketplace installation: a fresh ephemeral Codex process discovered all seven
  research skills, including `research:rig-sync`, and selected the direct local launch path for
  `rig-4090` instead of self-SSH.
- The temporary development plugin and marketplace were removed after the smoke test; the
  original installed research plugin was preserved.

## Live wave

- Disposable project: `/tmp/codex-research-smoke-20260802-093008`
- Wave: `20260802-093008`
- Tracking source of truth: `EXPERIMENTS.md`
- Hub inventory after additive pulls: 7 terminal `.status.json` markers and 6 `result.json`
  artifacts. The intentional code failure is the sole run without a result artifact.

| Rig | Scenario | Result | Evidence exercised |
|---|---|---|---|
| `rig-4090`, GPU 0 | success | done, artifact present | local direct dispatch, heartbeat, completion |
| `rig-4090`, GPU 0 | code failure | failed as intended | traceback and failed marker |
| `rig-3090-ti`, GPU 0 | interruption | done after relaunch | missing session + running marker classified as interrupted; resumed at checkpoint step 1 |
| `behemoth`, GPU 0 | wrong-GPU attempt | rejected with no writes | authorization guard ran before status, checkpoint, log, or artifact creation |
| `behemoth`, GPU 0 | idempotency | done | completed run self-skipped on recovery; no duplicate result |
| `behemoth`, GPU 0 | missing artifact | done after same-wave recovery | done marker without golden artifact triggered warning and step-4 resume |
| `behemoth`, GPU 0 | two normal successes | done, artifacts present | sequential assigned-GPU dispatch and completion |

All seven recorded runs are terminal: six `done` and one intentionally `failed`. The resumed
3090 result records `resumed=true` and `start_step=1`; the recovered behemoth result records
`resumed=true` and `start_step=4`.

## Synchronization and safety checks

- `prepare` was previewed before confirming each new disposable project root.
- `doctor` passed on the local hub and both SSH peers before dispatch.
- Source staging used the Git tracked/non-ignored manifest and additive, itemized rsync.
- Artifact pulls were previewed before confirmation and never used `--delete`.
- SSH calls are bounded, batch-mode calls; failed inventory commands are errors rather than an
  empty inventory.
- The smoke used an isolated machine registry inside the disposable project. It did not mutate
  the user's existing global rig registry.

## Resource observations

Before the wave, GPU 0 on both 24 GB rigs reported 1 MiB used and 0% utilization. They returned
to the same values afterward. Behemoth GPU 0 had an unrelated workload before and after the
test (2,273 MiB reported in both snapshots, with utilization fluctuating). The toy process did
not create a CUDA context, and the behemoth guard prevented any use of GPUs outside the approved
GPU 0 assignment.

## Environment findings

- Self-SSH to `rig-4090` is unavailable; the new local-hub path is therefore required and was
  exercised successfully.
- During the smoke, `rig-3090-ti` was the working SSH alias and the machine hostname was
  `rig-3090`. The machine was renamed to `rig-3090-ti` afterward on 2026-08-02; targeted hostname
  checks supersede this historical observation.
- Behemoth lacks the `python3-venv`/`ensurepip` path used by the other rigs, but its existing
  `uv` installation created the isolated test environment successfully without sudo.
- `rig-3080-ti` was not touched because it was outside the user's approved live-test target set.
- Optional Codex MCP authentication warnings observed in the ephemeral process were unrelated
  to skill discovery or experiment execution.

## Post-smoke hostname follow-up

After `rig-3090-ti` was renamed at the operating-system level, its hub registry, rig-local
registry, and isolated smoke registry were updated to the canonical hostname. The hardened
`rig-sync doctor` observed and accepted `rig-3090-ti`, and the read-only evaluation inventory
still found the completed interruption/resume run. No experiment or GPU process was launched.

## Post-smoke behemoth registry follow-up

The hub registry gained canonical `[machines.behemoth]` identity while retaining the legacy
`[machines.rig-6000-pro-blackwell]` compatibility entry. The hub's stale `rig-2023` hostname
metadata was also corrected to its observed `rig-4090` name. Full-fleet `doctor` then passed for
`rig-4090`, `rig-3090-ti`, and `behemoth` using the normal global registry, and the read-only
evaluation inventory found all seven expected run directories in their original placements.
The legacy Blackwell SSH alias remained reachable. Behemoth's filesystem was not modified, and
no experiment or GPU process was launched.

## Durable artifacts

- Timeline plot: `/tmp/codex-research-smoke-20260802-093008/plots/000_codex_smoke/plot_timeline/status_timeline.svg`
- Reconciled tracker: `/tmp/codex-research-smoke-20260802-093008/EXPERIMENTS.md`
- Research journal: `/tmp/codex-research-smoke-20260802-093008/JOURNAL.md`
- Disposable project commits: `6bc9578` (scaffold) and `db9d171` (outcomes)

The plugin repository changes remain uncommitted and untagged for user review.
