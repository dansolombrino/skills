# CINECA Leonardo — Slurm dispatch target

Read this file only when a wave assigns runs to `leonardo`. Everything that is not Slurm-specific
(gates, wave ids, script layout, three-signal monitoring, tracking rows, ten-minute updates) is
unchanged and lives in `../SKILL.md`. This file states only what differs on a batch scheduler.

Every CINECA fact below carries its source. Where the docs are silent the text says
"not documented"; do not fill such gaps from memory. Re-fetch the cited page when a value looks
stale — partitions, QOS limits, and cleanup policies change without notice.

## Vocabulary

- **run** — one experiment execution, exactly as elsewhere in this plugin.
- **job** — one Slurm allocation. On Leonardo a job hosts **exactly one run** by default. Never
  pack several runs into one job and never request more than one GPU unless the run itself needs
  several and the user says so for that wave.
- A **slice** on `leonardo` is the set of jobs a wave ends up submitting there. There are no lanes
  and no tmux sessions: Slurm is the queue, and every job is independent. In the wave's pool the
  cluster is one entry with a **job cap** and a **core-hour cap**; `sweep-supervisor` keeps at
  most that many of this wave's jobs submitted, taking runs from the **tail** of the wave's queue
  while the rig lanes take from its head, and never commits more core-hours than the cap.

## Driver host and access

Dispatch to Leonardo happens **from rig-4090**, like every other target. The hub reaches the
login node with the ssh alias `leonardo` and the data movers with `leonardo-dm`; both use the
key `~/.ssh/id_leonardo` plus a certificate `~/.ssh/id_leonardo-cert.pub` issued by CINECA's
smallstep CA (`https://sshproxy.hpc.cineca.it`) through the `cineca-hpc` OIDC provisioner. The
certificate is valid for 12 hours and is obtained interactively in a browser, so **the agent can
never issue it**. It is issued on the user's laptop and copied to the hub.
(access: https://docs.hpc.cineca.it/general/access.html)

**Precondition, before any Leonardo command in a session** — check the certificate on the hub:

```bash
ssh-keygen -L -f ~/.ssh/id_leonardo-cert.pub | sed -n 's/^ *Valid: //p'
ssh -o BatchMode=yes -o ConnectTimeout=20 leonardo true
```

If the file is missing, the `Valid:` window does not contain the current time, or the probe fails,
stop and print exactly this block for the user to paste on the laptop, then wait. Nothing else
in the wave may proceed until the probe succeeds:

```bash
cineca-login   # = step ssh certificate <user> ~/.ssh/id_leonardo --provisioner cineca-hpc --no-password --insecure -f
rsync -avhz -e ssh ~/.ssh/id_leonardo-cert.pub ~/.ssh/id_leonardo.pub ~/.ssh/id_leonardo rig-4090:~/.ssh/
```

The private key is stored **without a passphrase** on purpose: `--no-password --insecure` only
control local encryption of the key file and change nothing the CA or the login node sees
(https://smallstep.com/docs/step-cli/reference/ssh/certificate/), while a passphrase-protected
key cannot be used by the non-interactive sessions that drive dispatch from the hub. The 12-hour
certificate and the `0600` file mode are the protection. Never print, cat, or copy the private key
anywhere but the hub's `~/.ssh/`.

Because the certificate expires mid-wave, **a monitoring failure on Leonardo is first an
authentication question**: rerun the precondition before declaring the site unreachable. Jobs
already in the queue keep running; only observation is lost until a new certificate arrives.

## What the site is (docs.hpc.cineca.it)

- Login: `login.leonardo.cineca.it` round-robins over `login01/02/05/07-ext`. Login nodes allow
  editing, compiling, data movement, and tests **under 10 minutes of CPU time per task**; anything
  heavier is killed. GPUs exist only on compute nodes, reachable only through Slurm.
  (https://docs.hpc.cineca.it/hpc/leonardo.html, https://docs.hpc.cineca.it/hpc/hpc_scheduler.html)
- Data movers `data.leonardo.cineca.it` (`dmover1..4`): no shell, no CPU limit, only `rsync`,
  `scp`, `sftp`, `wget`, `curl`, `rclone`; `$HOME`/`$WORK` are undefined there, use absolute paths.
  Use them for anything larger than small files. (https://docs.hpc.cineca.it/hpc/hpc_data_storage.html)
- Booster node: 4× NVIDIA A100 64 GB, 32 cores, 512 GB RAM, diskless, `/tmp` is 10 GB of RAM.
  Driver 535 / CUDA 12.2 on the nodes; a wheel built for a newer CUDA runs only if the environment
  smoke says so. (https://docs.hpc.cineca.it/hpc/leonardo.html, https://docs.hpc.cineca.it/services/singularity.html)
- **Compute nodes have no internet.** Wandb runs with `WANDB_MODE=offline` and is synced from a
  login node afterwards; datasets and model weights are downloaded beforehand on a login node or
  a data mover. (https://docs.hpc.cineca.it/faq.html "local-only restricted")
- Partitions and QOS for GPU work (https://docs.hpc.cineca.it/hpc/leonardo.html):

| partition / QOS | walltime | per-job | per-user | priority | use here |
|---|---|---|---|---|---|
| `boost_usr_prod` / normal | 24 h | ≤ 64 nodes | — | 40 | **default for every run** |
| `boost_usr_prod` / `boost_qos_dbg` | 30 min | ≤ 8 nodes | ≤ 2 jobs running or pending | 80 | smoke and template checks only |
| `boost_usr_prod` / `boost_qos_lprod` | 4 days | ≤ 8 nodes | 32 GPUs per project | 40 | only when a run's ETA exceeds 24 h and the user asks |
| `lrd_all_serial` (default partition) | 4 h | 4 cores, no GPU | — | 40 | budget-free CPU work: `wandb sync`, packing |

- Billing: a job costs `hours × nodes × R × 32` core-hours, `R` the largest fraction of the node
  it holds. One GPU is a quarter node, so **one GPU-hour costs 8 core-hours**. Interactive
  allocations are billed for their full requested walltime. Budget spent by a hung job is not
  refunded. (https://docs.hpc.cineca.it/hpc/hpc_intro.html, https://docs.hpc.cineca.it/faq.html)
- `saldo -b` lists every project account with total, consumed, and end date. As a monthly share is
  used up, jobs lose priority but still run. An exhausted account can only be revived by CINECA
  support. (https://docs.hpc.cineca.it/hpc/hpc_intro.html, https://docs.hpc.cineca.it/faq.html)
- Storage (https://docs.hpc.cineca.it/hpc/hpc_data_storage.html):

| area | quota | cleanup | role in this plugin |
|---|---|---|---|
| `$HOME` | 50 GB | none, no backup on Leonardo | dotfiles, `uv`, `tmux`, the ssh certificate |
| `$WORK` = `/leonardo_work/<account>` | 1 TB per project | kept 6 months after the project | **storage root**: checkout, wave worktrees (`.waves/`), keyed environments (`.envs/`), checkpoints, evaluations |
| `$FAST` = `/leonardo_scratch/fast/<account>` | 1 TB per project, fixed | 6 months after the project | datasets and hub caches (`HF_HOME` and friends) |
| `$CINECA_SCRATCH` | none | **files idle for 40 days are deleted** | never for anything a run must find again |
| `$TMPDIR` on a compute node | 10 GB RAM | per job | scratch inside a job only |

`$WORK` and `$FAST` follow the account selected with `chprj`; the registry entry must name the
account's absolute path, not the variable.

- Modules: only `profile/base` loads by default; `profile/deeplrn` holds `cineca-ai`; `cuda/12.2`
  is the default CUDA module. This plugin does not use them for Python: the project environment is
  a `uv` environment whose wheels bundle their CUDA runtime, and the in-job smoke decides whether
  they run on driver 535. (https://docs.hpc.cineca.it/hpc/hpc_environment.html)

## Registry and project declaration

`~/.config/rigsync/machines.toml` on the hub gains one entry per **account** the user dispatches
under, because `$WORK` and `$FAST` are per account:

```toml
[machines.leonardo]
ssh = "leonardo"
# no hostname: the alias lands on whichever login node is free, and their names differ
gpus = "job"                  # the login node has no GPU: environment-sync defers the smoke to the job
transfer_ssh = "leonardo-dm"  # data mover: rig-sync pull/push rsync through it, nothing else does
storage_root = "/leonardo_work/<account>"
# no quota_fs: `quota -w` prints nothing on Lustre, while `df -P $WORK` reports the project quota
min_free_gb = 100

[machines.leonardo.caches]
HF_HOME = "/leonardo_scratch/fast/<account>/cache/huggingface"
HF_DATASETS_CACHE = "/leonardo_scratch/fast/<account>/cache/huggingface/datasets"
UV_CACHE_DIR = "/leonardo_work/<account>/cache/uv"
TORCH_HOME = "/leonardo_scratch/fast/<account>/cache/torch"
WANDB_DIR = "/leonardo_work/<account>/wandb"
```

`gpus = "job"` and `transfer_ssh` are the two cluster fields of `rig-sync`'s registry
(`rig-sync/references/configuration.md`, "Cluster fields"); both must be present for the login-node
verify and the data-mover transfers below to work as described.

`sync.toml` declares `[machines.leonardo] repo_path = "/leonardo_work/<account>/<project>"`.
`rig-sync check-paths`, `doctor`, `repo-path`, and `storage-env` then work unchanged; the login
shell is bash, so `provision-env` prepends its block to `~/.bashrc` where every `ssh <cmd>` and
every job sees it. Run `rig-sync doctor --machines leonardo` before every Leonardo wave, like any
rig.

Speed weight for assignment math and cost normalization: **1.0 per A100 GPU**, the same as
`rig-4090`, a nominal prior declared in `../../research-project-init/references/conventions.md`.

## Environment on the login node

`environment-sync provision` and `verify` run on the login node over ssh, exactly as on a rig: the
login node has internet, `git`, and the user-installed `uv` at `~/.local/bin/uv`. Two differences:

- The login node has no GPU, so **the lane smoke cannot run at provision time**. The registry's
  `gpus = "job"` tells `environment-sync` so: `doctor` and `verify --machines leonardo` skip the
  `nvidia-smi` probe, still compare OS/arch/libc and the wave environment's fingerprint with the
  hub, and report
  `gpus=deferred to job`; `--lane leonardo:<n>` is refused with a message saying why. The GPU
  smoke runs as the first step inside every job, where the existing environment guard already
  performs it. State this in the gate-3 preview: "Leonardo fingerprint verified on login node;
  GPU smoke deferred to the job".
- A large `uv sync` can exceed the 10-minute CPU limit and be killed. Re-running it resumes from
  the cache under `UV_CACHE_DIR`; if it is killed twice, provision from a `lrd_all_serial` job
  (budget-free) with the same command and verify again from the login node.

## The job script

The job script **is** the run's wave script — one file per (run, wave),
`scripts/<NNN_exp>/<run_id folder>/wave_<wave_id>/wave.sh`, submitted from the
wave worktree as `.waves/<wave_id>/scripts/...`. A rig lane runs it with `bash` (the header is
comments there); the cluster runs it through `sbatch`. The body is the
[standard wave script](templates.md) verbatim — artifact guard, Git guard, storage guard,
environment guard and smoke, `tee` log, failed-status fallback — with this header in place of
the bare shebang; inside a job the script leaves `CUDA_VISIBLE_DEVICES` to Slurm:

```bash
#!/usr/bin/env bash
#SBATCH --job-name=<wave_id>__<run_id_name>
#SBATCH --account=<account chosen for this wave>
#SBATCH --partition=boost_usr_prod
#SBATCH --qos=normal
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=128G
#SBATCH --time=<HH:MM:SS: ETA × 1.5, rounded up to 15 min, capped at 24:00:00>
#SBATCH --output=logs/<NNN_exp>/<run_id folder>/wave_<wave_id>/slurm-%j.out
#SBATCH --error=logs/<NNN_exp>/<run_id folder>/wave_<wave_id>/slurm-%j.out
#SBATCH --signal=B:USR1@600
#SBATCH --no-requeue
# run: <flat run_id>   experiment: <NNN_exp>
# wave: <wave_id>   rig: leonardo   gpu: 1 (Slurm-assigned)
set -uo pipefail
export WANDB_MODE=offline
export OMP_NUM_THREADS="$SLURM_CPUS_PER_TASK"
# ...then the standard body from `export WAVE_ID=...` on
```

- `sbatch` copies the script into Slurm's spool, so `$0` is meaningless inside the job. The
  standard body therefore takes the project root from `SLURM_SUBMIT_DIR`, which is why every
  submission `cd`s to `<repo_path on leonardo>` first and never into the worktree. A queued job
  reads its code from `.waves/<wave_id>/` whenever it starts, so later waves, commits, or
  deployments on the same account can never change what it runs.

- Resources are fixed: **one GPU, 8 cores, 128 GB** — a quarter of a Booster node, matching how
  the site bills a GPU. Change them only for a run that needs several GPUs, and then only with the
  user's explicit statement recorded in the wave README the way a behemoth grant is.
- `--time` in the header is only the fallback. The supervisor passes the real value on the
  `sbatch` command line, which overrides the header: the `experiments-tracking` ETA hierarchy with
  a 50% margin — **measured in this wave as soon as any run has finished**, the prior before that
  — rounded up to 15 minutes. With no basis at all it uses the user-approved `default_walltime_s`
  from the gate-3 preview; never submit with a guessed
  24 h "to be safe", because walltime shapes priority and the per-GPU budget estimate. A per-step
  cost that differs by dataset (a tiny dataset that validates every few steps) is exactly what a
  flat guess gets wrong. A run whose
  estimate exceeds 24 h routes to the user with `boost_qos_lprod` as the proposal.
- `--signal=B:USR1@600` delivers `SIGUSR1` to the batch shell ten minutes before the walltime.
  The script does not trap it; it exists so a project that checkpoints on that signal can do so
  without changing the header. Whether a resubmitted run resumes or restarts was fixed at
  experiment design time, never here.
- `--no-requeue`: Slurm must not resubmit on its own. Recovery is the supervisor's explicit action
  (below) so that it is recorded in the wave's ledger and reported.
- The `tee` log path is unchanged; the extra `slurm-%j.out` beside it captures what Slurm itself
  writes (prolog, OOM kill, walltime message) and is the first thing to read on a failure.
- The wave README records, in addition to the standard fields: account, partition, QOS, the
  resource line, the walltime and its basis, and the budget estimate `runs × walltime_h × 8`
  core-hours.

## Submit

Per job, by the supervisor service from the hub, after the all-rig Git deployment gate
(`deploy-revision` created `.waves/<wave_id>` on `leonardo`) and `verify-revision` on `leonardo`:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=20 leonardo "cd <repo_path on leonardo> && squeue --me --noheader --format=%j | grep -Fxq '<wave_id>__<run_id_name>' && echo ALREADY-QUEUED || sbatch --parsable --time=<HH:MM:SS> --export=ALL,LANE_RIG=leonardo,LANE_GPUS=1 .waves/<wave_id>/scripts/<NNN_exp>/<run_id folder>/wave_<wave_id>/wave.sh"
```

- The exact job-name check is the never-double-submit rule: the job name is unique per (run,
  wave), so a queued or running job with that name means the run is already placed. Match names
  with `grep -F`, never `squeue --name`/`sacct --name`: those split their argument on commas, and
  run names contain commas.
- Append the printed job id to `scripts/<NNN_exp>/<run_id folder>/wave_<wave_id>/leonardo.jobs`
  on the hub as `<ISO timestamp> <jobid> submitted` (one line per submission, history kept, never
  rewritten), commit nothing: this file is state, like EXPERIMENTS.md, and is gitignored by
  `research-project-init`. The EXPERIMENTS.md row for the run carries `rig=leonardo`, `gpu=1`, and
  the job id in `notes`.
- Before the first submission of a wave, run `saldo -b` and refuse any account whose consumed
  hours are at or above its total or whose end date has passed; show the remaining hours and the
  wave's estimate in the gate-3 preview.
- The supervisor keeps up to the pool's job cap submitted and tops it up as jobs end; it stops at
  the core-hour cap and asks the user without blocking. A job still `PENDING` costs nothing, so
  when a rig lane goes idle with nothing left in the queue the supervisor `scancel`s one pending
  job **by id** and gives that run to the lane.

## Monitor

The three signals still decide a run's state, read over ssh from the login node's filesystem with
the standard probe in [templates.md](templates.md) (`leonardo` is a peer, never local). Slurm adds
a fourth, cheap signal that batches the whole slice in one call:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=20 leonardo "squeue --me --noheader --format='%i|%j|%T|%M|%L|%S|%R' | grep -F '|<wave_id>__'; sacct -X --noheader --parsable2 --format=JobID,JobName,State,Elapsed,ExitCode,End --starttime=<wave dispatch time> | grep -F '|<wave_id>__'"
```

State mapping (`squeue` while queued or running, `sacct` once gone):

| Slurm state | run state in reports | action |
|---|---|---|
| `PENDING` | `queued`; reason column explains (`Priority`, `Resources`, `QOSMaxJobsPerUserLimit`, `AssocGrpBillingMinutes`) | none; an accounting reason that persists for an hour is reported as a budget problem |
| `RUNNING` | `running`; the heartbeat and `.status.json` take over | standard monitoring |
| `COMPLETED` + artifact present | `done` | none |
| `COMPLETED` + artifact absent | `failed` with reason "exit 0 without artifact" | report; never resubmit blindly |
| `FAILED`, `OUT_OF_MEMORY` | `failed` (code) | report with the tail of `slurm-%j.out`; no automatic resubmit |
| `TIMEOUT` | `interrupted` (walltime) | resubmit with a walltime resized from the run's measured progress; a restart-from-scratch run that cannot fit the 24 h cap goes to the user |
| `NODE_FAIL`, `PREEMPTED`, `CANCELLED` not by the user, `BOOT_FAIL` | `interrupted` (machine fault) | resubmit the same script, same wave id |
| `CANCELLED` by the user for a supersede | `superseded` | none; the row names the superseding wave (SKILL.md § Supersede) |
| exit code `86`/`87`/`88` in `sacct` | drift or storage, never a run failure | stop the slice, report, do not resubmit |

- ETA for a queued job = `squeue --start` estimate (labeled `scheduler estimate`; often absent,
  then `ETA unavailable: no scheduler estimate`) plus the run estimate. ETA for a running job comes
  from structured progress as usual, bounded by the job's remaining walltime (`%L`), and a run
  whose progress-based ETA exceeds its remaining walltime is reported as "will time out" before it
  does.
- Freshness: one bounded ssh per supervisor cycle for the whole slice (queue, accounting, and the
  status files of its live runs). Never poll from inside a job.
- A stale heartbeat with `RUNNING` in `squeue` is a hang, not a machine fault: report it, and
  never `scancel` a **running** job without the user's approval (the cost is already spent and is
  not refunded). The other `scancel`s are a user-approved supersede and the supervisor's
  pending-job move above; both name exact job ids, never a user, partition, or pattern.
- The certificate check from the precondition section is rerun on every ssh failure before the
  site is called unreachable.

## Recover

Resubmission is the entire recovery, after `rig-sync deploy-revision` for the wave (it verifies
the worktree, or recreates it if it was pruned): the same script, same wave id, same job name,
guarded by
the exact job-name check above, so a job that Slurm already requeued or that is still queued is
never doubled. Append the new job id to `leonardo.jobs` as `<timestamp> <jobid> resubmitted after
<state>`. Recovery never edits the sbatch header: a different QOS or resource line is a
new wave with its own authorization. Walltime is the exception, because it is a command-line
value sized from measurement: after a `TIMEOUT` the supervisor resubmits with the larger of twice
the old walltime and `measured seconds per step × total steps × 1.5`, capped at 24 h. A run that
restarts from scratch and cannot fit 24 h goes to the user.

Autonomous, not silent: resubmitting an interrupted job needs no permission, but the ledger, the
table, and the agent's decisions say which jobs were resubmitted, why, with what walltime, and
whether each resumes or restarts.

## Fetch results and sync wandb

- Artifact movement stays `rig-sync`'s job (`pull evaluations/<NNN_exp> --from leonardo`). For
  anything beyond small files, point the transfer at the data-mover alias `leonardo-dm` with
  absolute paths, because `$WORK` is undefined there and login nodes kill transfers at 10 CPU
  minutes. rig-sync uses `transfer_ssh` when declared: with `transfer_ssh = "leonardo-dm"` in the
  registry, `pull`/`push --from/--to leonardo` already rsync through the data mover (the output
  says `via leonardo-dm` and prints the rsync command line), and retry up to three times if the
  connection still drops (rsync exit 12/255), resuming where the previous attempt stopped. Never
  hand-write the rsync.
- After the slice finishes, sync offline wandb runs from a login node:
  `ssh leonardo "cd <repo_path> && wandb sync --sync-all <WANDB_DIR>"`; if it is killed for CPU
  time, run the same command inside a `lrd_all_serial` job.
- `logs/.../slurm-<jobid>.out` is pulled with the run logs.

## Smoke job (once per hub setup, and after any change to this header)

Validates access, account, partition, resource line, and the GPU from inside an allocation. It
spends about 0.3 core-hours. Submit only with the user's explicit approval of the account:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=20 leonardo 'cat > $WORK/smoke_leonardo.sh <<"EOS"
#!/usr/bin/env bash
#SBATCH --job-name=smoke_leonardo
#SBATCH --account=<account>
#SBATCH --partition=boost_usr_prod
#SBATCH --qos=boost_qos_dbg
#SBATCH --nodes=1 --ntasks=1 --gres=gpu:1 --cpus-per-task=8 --mem=128G
#SBATCH --time=00:02:00
#SBATCH --output=%x-%j.out
hostname; nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv; echo CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES; nproc; free -g | head -2
EOS
cd $WORK && sbatch --parsable smoke_leonardo.sh'
```

Then poll `sacct -X -j <jobid> --format=State,Elapsed,ExitCode` and read `$WORK/smoke_leonardo-<jobid>.out`.
