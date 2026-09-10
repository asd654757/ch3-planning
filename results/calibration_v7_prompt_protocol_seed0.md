# v7 prompt/infeasible protocol calibration (seed 0)

- Scope: all 10 easy tasks + all 5 infeasible tasks from frozen `formal_tasks_v6`.
- Model: `qwen3-vl-flash`; `max_tokens=3072`.
- Data: `data/collections/calibration_v7_prompt_protocol_easy_infeasible_seed0.jsonl`
- Records: 45; total tokens: 25,951.
- This is a calibration probe, not a formal multi-seed result.

## Fix 1: easy B1 complete pick-place output

| Difficulty | B1_n | B1_valid | B1_goal | B1_pbw | B0_n | B0_valid | B0_goal |
|---|---:|---:|---:|---:|---:|---:|---:|
| easy | 10 | 10 | 10 | 0 | 10 | 10 | 10 |

The v6 systematic easy failure was 0/50 because B1 returned a lone `pick`. In this
v7 probe, B1 returned the paired `pick` + `place` plan and reached 10/10 on seed 0.

## Fix 2: explicit infeasible refusal protocol

| Difficulty | Layer | n | explicit_refusal | valid_plan | goal_satisfied | pass_but_wrong |
|---|---|---:|---:|---:|---:|---:|
| infeasible | B1 initial | 5 | 5 | 0 | 0 | 0 |
| infeasible | R0 | 5 | 5 | 0 | 0 | 0 |
| infeasible | R1 | 5 | 5 | 0 | 0 | 0 |
| infeasible | R2 | 5 | 5 | 0 | 0 | 0 |
| infeasible | B0 direct | 5 | 5 | 0 | 0 | 0 |

All 25 infeasible-layer responses were explicit refusals. Pass-but-wrong was 0,
whereas formal_v6 had R1 5/25 and R2 4/25 pass-but-wrong. The deterministic
closed-world check now rejects any goal fact referencing an object absent from
the visible list, so a plausible substitution plan cannot bypass the refusal.

## Conclusion

The two targeted defects are fixed in the seed-0 calibration:

1. easy B1 no longer emits a partial plan;
2. infeasible tasks no longer produce executable pass-but-wrong plans.

Before formal claims, run the full 55-task × 5-seed protocol under the v7 prompt hash.
