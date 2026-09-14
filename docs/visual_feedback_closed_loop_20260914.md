# Online visual-state feedback closed loop: implementation note

## Scope

This change adds a bounded online visual feedback pilot to the existing
state-feedback closed loop. On a failed homogeneous execution segment, the
MetaWorld executor saves the current rendered camera frame. If visual feedback
is enabled, the repair VLM receives that frame in addition to the existing
structured prefix state, task objects, goal, and executed prefix.

This is therefore an **online visual-state feedback closed loop**, not a
vision-only state estimation benchmark. Deterministic validation and goal
checking remain authoritative; the image is advisory feedback for repair.

## Interface

1. VLM generates the initial task plan from the task image and textual task.
2. The deterministic validator accepts or rejects the structured plan.
3. MetaWorld executes one homogeneous primitive segment.
4. On execution failure, the executor saves a non-overwriting failure frame.
5. The repairer receives the live frame as `image_path`, while symbolic state
   and the validated executed prefix remain unchanged.
6. `R1_FROM_STATE` returns only the remaining-task suffix.

The image path and repair prompt are recorded for provenance. The repair prompt
explicitly says that the attached image is the latest execution observation and
must be used together with, not instead of, structured validation.

## Smoke validation

The following mock-VLM smoke tests used no paid API calls:

```bash
PYTHONPATH=/root/autodl-tmp/ch3-planning \
  /root/autodl-tmp/.venvs/metaworld-lerobot/bin/python \
  scripts/sim_closed_loop.py \
  --client mock --visual-feedback \
  --task-families pick_place --episodes-per-family 1 \
  --max-replan-rounds 1 \
  --output data/collections/sim_closed_loop_visual_mock_20260914_154357.json
```

Result: 1/1 success, no repair round triggered.

A deliberately truncated simulation run forced an execution failure and
verified that both the initial and repaired attempts produced failure frames:

```text
data/collections/sim_closed_loop_visual_failureframe_mock_20260914_154551.frames/multiskill_push_012_seed0_round_0_segment0_failure.png
data/collections/sim_closed_loop_visual_failureframe_mock_20260914_154551.frames/multiskill_push_012_seed0_round_1_segment0_failure.png
```

The saved frames contain the expected MetaWorld corner camera view. The
regression suite remains at 101 passed.

## Planned real-VLM pilot

The first real run should remain small:

```bash
PYTHONPATH=/root/autodl-tmp/ch3-planning \
  /root/autodl-tmp/.venvs/metaworld-lerobot/bin/python \
  scripts/sim_closed_loop.py \
  --client real --visual-feedback \
  --task-families pick_place push press \
  --episodes-per-family 3 \
  --max-replan-rounds 1 \
  --output data/collections/sim_closed_loop_visual_real_<timestamp>.json
```

At `9` episodes and at most one repair, the upper bound is `18` VLM calls.
If every initial plan succeeds without execution failure, the lower bound is
`9` calls. This pilot should be treated as mechanism validation, not as a
formal benchmark claim.

## Reporting red line

Do not describe this as a full visual closed loop or vision-only planning
system. Use the exact term **online visual-state feedback closed loop** unless
the simulator state is removed and a vision-based state estimator is validated.
