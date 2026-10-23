# Legacy run_id layouts

Read this only for an experiment whose `.py` pins a `RUN_ID_PATH_LAYOUT` other than `hashed-v2`:
`segments-v2`, `segments-v1`, `nested`, `collapsed-v1`, or `hashed-v1`. New experiments always use
`hashed-v2`; none of these is ever offered to a new one. `code/common/run_id.py` keeps rendering
each of them unchanged.

## Record

The authoritative record is constants in the experiment's `.py` that the path-building code
actually uses: the ordered `RUN_ID_PARAMS`, `RUN_ID_PATH_LAYOUT`, and, for the segments layouts,
`RUN_ID_SEGMENTS`. The EXPERIMENTS.md section header mirrors them (plus the map line for
`segments-v2`, `segments-v1`, and `hashed-v1`). An existing project with outputs is pinned to its
checked-in renderer even when it predates the explicit constant; if that renderer is ambiguous,
stop.

## Renderings

All run-scoped surfaces call `run_id_path(..., layout=RUN_ID_PATH_LAYOUT, segments=RUN_ID_SEGMENTS)`
(`segments` only for the segments layouts).

| layout | run directory | run folder in `scripts/`, `logs/`; EXPERIMENTS.md `run_id`; an older run's WandB name |
|---|---|---|
| `segments-v2` | one directory per segment, e.g. `model=mlp/hidden=64-128/optim_params=b4c2e09f6dbe6b4b,seed=0/` | the same path (`run_id_name`) |
| `segments-v1` | as `segments-v2`, pairs percent-encoded (`hidden=%5B64%2C128%5D`) | the same path |
| `nested` | `model=mlp/lr=1e-3/seed=0/` | `run_id_flat` |
| `collapsed-v1` | `model=mlp,lr=1e-3,seed=0/` | `run_id_flat` |
| `hashed-v1` | `rid-<16 hex>` of the collapsed string | `run_id_flat` |

- `RUN_ID_SEGMENTS` is an ordered list of segments, one directory each. An item is a param
  (rendered `key=value`) or a one-key dict naming a hash group (rendered `name=<16 hex>`: sha256
  over the layout name, the group name, and the group's ordered percent-encoded pairs).
- `segments-v2` renders only `A-Z a-z 0-9 . _ -` inside keys and values; a list joins with `-`.
  A value with no such rendering (a space, `/`, a dict, an empty or nested list, a list item
  containing `-`) is refused by the helper and belongs in a hash group.
- `run_id_flat` (`model=mlp,lr=1e-3,seed=0`) is the lossless identity with every param explicit;
  it is recorded in `.run_id.json` and `RUN_ID_MAP.json` under every legacy layout (and in the
  WandB config of a legacy run that logged to WandB, named `run_id_name(...)` there).
- EXPERIMENTS.md tables keep one column per identity param; segments layouts add a `run_id`
  column and one column per hash group, `hashed-v1` adds a `hash` column.
- `guard_run_config`, `check_run_id_plan`, and `write_run_id_map` work as under `hashed-v2`, and
  the map hard-fails when one group hash or `hashed-v1` hash maps to two param sets.

## What can change

- Before any checkpoint, evaluation, plot output, or wave README/script exists, the experiment is
  re-elected normally, and a fresh election uses `hashed-v2`.
- After the first such surface, `RUN_ID_PARAMS`, `RUN_ID_PATH_LAYOUT`, and `RUN_ID_SEGMENTS` are
  immutable: no in-place layout change, regrouping, or move to another layout. New identity needs
  go to a new numbered sub-experiment with its own `hashed-v2` identity, leaving every old
  artifact, wave record, WandB run, and EXPERIMENTS.md row untouched.
- A run_id migration (`runid migrate`) applies to `hashed-v2` experiments only and refuses a
  legacy layout.
- A path disagreement or a mixed tree blocks dispatch, recovery, and reconciliation; never infer a
  layout from slash depth or rewrite old paths.

## Historical: the segments rendering dialogue

Every election under a segments pin ran this dialogue with the user, which is what a
`RUN_ID_SEGMENTS` record means: (1) show the all-explicit paths with byte counts from
`preflight_run_id_path`; (2) the user chose which params stay explicit and which are hashed; (3)
hashed params were grouped along the Hydra config tree (e.g. `optim_params`); (4) the user placed
items into directories and approved a rendering that overflowed nothing. No hash exists outside an
approved group. It is never started for new work.
