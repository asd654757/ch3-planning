# VLM Prompt / Collection Link

This link implements the frozen 2026-09-08 collection semantics.

## Call graph

```
InitialPlanner  -> shared ModelPlan P       -> B1 / B2a / B2b
DirectPlanner   -> independent B0 free text -> B0 parser
PlanRepairer    -> R0 / R1 / R2             -> one repair call
Validator       -> syntax/object/capability/state
GoalChecker     -> goal subset of final state
```

For every `(task, seed)` the collector writes one shared-plan record, one
independent B0 record, and one repair record per requested repair group when
the shared plan is invalid.

## Offline / real runs

Offline deterministic run:

```bash
.venv/bin/pytest -q
```

Real DashScope run (loads `DASHSCOPE_API_KEY` from the existing `.env`):

```bash
.venv/bin/python -m ch3.vlm.collector \
  --scenarios config/scenarios_pilot.jsonl \
  --output data/collections/vlm_smoke_20260909.jsonl \
  --task-ids easy_block_to_tray \
  --seeds 1 --seed-offset 0 \
  --model qwen-vl-plus
```

The collector refuses to overwrite an existing collection file unless
`--append` is explicitly passed.

## Log guarantees

Every JSONL record contains:

- rendered prompt, `prompt_id`, and SHA-256 `prompt_hash`;
- `raw_vlm_output` and the full `raw_api_response`;
- parsed `model_plan` and `parse_error`;
- four-layer validation result and first-invalid-step location;
- final-state facts and `goal_satisfied`;
- `pass_but_wrong` when validation passes but the goal does not;
- input/output/total tokens and latency;
- seed, task, difficulty, baseline, record type, and repair mode.

No API key is written to collection logs or error messages.

## 2026-09-09 smoke result

Two tasks were run against `qwen-vl-plus`:

- `easy_block_to_tray`: shared P valid and goal-satisfied; B0 also parsed and
  reached the goal.
- `infeasible_missing_target`: shared P invalid. R0 stayed invalid; R1 and R2
  produced validation-passing but goal-failing plans, correctly logged as
  `pass_but_wrong=true`.
