# Repair-pressure calibration (2026-09-11)

## Purpose

The v8 task benchmark showed that `qwen3-vl-flash` remained saturated on
feasible attribute-grouped and exclusion-constraint tasks.  Therefore we moved
to a controlled repair-pressure benchmark: start from a valid VLM baseline
plan, inject a deterministic invalid prefix/suffix, and compare R0/R1/R2 with
the same error distribution.

## Pressure types

| Name | First error | Purpose |
|---|---|---|
| `duplicate_pick_after_prefix` | E05 ARM_NOT_EMPTY | arm-state repair after a valid prefix |
| `unknown_object_after_prefix` | E02 UNKNOWN_OBJECT | closed-world object repair |
| `invalid_target_after_prefix` | E02 UNKNOWN_OBJECT | closed-world target repair |
| `place_before_pick` | E06 OBJECT_NOT_HELD | state-only repair without validated prefix |
| `repeat_pick_after_valid_plan` | E08 STATE_TRANSITION_ERROR | deletion-only repair after a complete plan |

## Runner

```bash
set -a; source /root/autodl-tmp/metaworld-smolvla/qwen-dialogue-control/.env; set +a
.venv/bin/python -m scripts.repair_pressure \
  --scenarios data/scenarios/stress_tasks_v8_pilot_v2.jsonl \
  --output data/collections/repair_pressure_<timestamp>.jsonl \
  --model qwen3-vl-flash --seeds 1 --repair-groups R0 R1 R2
```

Metrics:

```bash
.venv/bin/python -m ch3.metrics.repair_pressure_metrics <collection.jsonl>
```

## Calibration result

Six feasible tasks (3 `attribute_grouped`, 3 `exclusion_constraint`) × one seed
were used.  All 6 baseline plans were valid.

## Formal-run caveat: do not use the first formal run for arm comparison

The first formal run
(`data/collections/repair_pressure_formal_v8_20260911_064919.jsonl`) completed
48/48 slices, but its runner passed the valid source plan as the repair input.
Consequently, the R1/R2 prompts contained the correct answer.  Exact-match
audit found:

| Mode | n | Repairs exactly equal to the valid source plan |
|---|---:|---:|
| R0 | 240 | 46 |
| R1 | 240 | 186 |
| R2 | 240 | 167 |

Thus its reported R1 `100%` and R2 `87.1%` CRR are oracle-contaminated and must
not be used for the paper.  R0 remains useful only as a diagnostic because R0
does not receive the original plan.  The runner has been corrected so R1/R2
receive the corrupted stress plan as `original_plan`, and a regression test now
checks that the repair prompt contains the injected unknown object rather than
leaking the valid source plan.  A corrected formal rerun is required.

### First calibration (R0/R1/R2)

The runner initially used all 90 repair calls after the scene-object fix.  The
raw result exposed two R2 protocol gaps rather than hiding them:

| Mode | n | CRR | GSR_after_repair | FRR |
|---|---:|---:|---:|---:|
| R0 | 30 | 50.0% | 50.0% | 0.0% |
| R1 | 30 | 100.0% | 100.0% | 0.0% |
| R2 | 30 | 53.3% | 53.3% | 0.0% |

### R2 protocol fix

R2 now explicitly handles two valid repair actions:

1. no validated prefix → return the complete corrected plan;
2. valid prefix plus illegal tail → return `{"actions": []}` and merge back the
   validated prefix.

The same six tasks and seed were rerun with R2 only:

| Mode | n | CRR | GSR_after_repair | FRR | pass-but-wrong |
|---|---:|---:|---:|---:|---:|
| R2 | 30 | 90.0% | 83.3% | 6.7% | 2 |

Remaining R2 failures were: one `prefix_overlap`, two incomplete full plans for
the no-prefix case, and two valid-but-wrong plans.  This is acceptable for a
small pilot, but formal v8 should keep all five pressure types and not report
only the repairable subset.

## Formal-v7 refusal-source recomputation

The new metrics code recomputes `infeasible_source` from `raw_vlm_output` when
older JSONL rows lack the field.  It did not mutate the frozen v7 file.

| Arm | n | Refusals | Model-only | Deterministic guard |
|---|---:|---:|---:|---:|
| B0 | 275 | 25 | 25 | 0 |
| B1 | 275 | 25 | 10 | 15 |
| R0 | 25 | 25 | 25 | 0 |
| R1 | 25 | 25 | 25 | 0 |
| R2 | 25 | 25 | 14 | 11 |
