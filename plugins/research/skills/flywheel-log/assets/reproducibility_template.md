# Reproducibility

## Repository
- URL: <repo_url>
- Branch: <branch>
- Commit SHA: <sha>

## File Map
- `<script>`: <what it does>
- `<config>`: <what it controls>

## Command
```bash
<exact copy-pasteable command>
```

## Environment Setup
```bash
<exact environment creation/synchronization commands>
<exact activation command>
<required runtime or module-loading commands, or N/A — reason>
```

## Hardware
- Provider or host: <provider_or_host>
- Accelerator and count: <N>x <accelerator_model>, or N/A — reason
- Runtime / driver: <runtime_and_driver>, or N/A — reason
- Scheduler / partition: <scheduler_and_partition>, or N/A — reason
- Account / allocation: <account_or_allocation>, or N/A — reason

## Hyperparameters
- Seed: <seed>
- <key>: <value>   # all non-default hyperparameters

<!--
If a section genuinely does not apply to this run (e.g. a CPU-only experiment with no
SLURM configuration), write "N/A — <reason>" rather than deleting the section, so the
structure stays comparable across runs.
-->
