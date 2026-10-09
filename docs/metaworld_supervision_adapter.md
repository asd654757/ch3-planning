# MetaWorld Supervision Adapter V1

This adapter reuses the installed MetaWorld environment and fixed controller.
It does not install RoboDojo, download policy weights, or train a policy.

## Protocol

- One reset per session; recovery never creates a fresh episode.
- One physical arm (`right`), one puck (`red_cube_0`), one goal (`tray_1`).
- Only pick/place is physically mapped. Unsupported objects/targets are blocked
  by the backend before motion, even if the generic symbolic validator accepts them.
- Native goal height is adjusted to the table. This is a controlled tabletop
  protocol, not the unchanged native MetaWorld benchmark.
- Observations use privileged simulator positions and gripper measurements.
  Goal confirmation additionally requires an open-gripper stability window.
- Observer state does not use execution success or reward as completion evidence.
- Occupancy is a heuristic, not a contact sensor or learned visual estimator.
- Legacy `adflow_grasp`/`adflow_place` policy labels map to the fixed controller;
  no AD-Flow weights are loaded.
- The optional perturbation limits the first grasp to one actual simulation step.
  It is a controlled timeout, not a naturally occurring failure.

## Commands

Run from the repository root, with a new output path for every session:

```bash
/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python \
  scripts/run_metaworld_supervision.py \
  --output /tmp/metaworld_supervision_normal.jsonl

/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python \
  scripts/run_metaworld_supervision.py --interrupt-first-grasp \
  --output /tmp/metaworld_supervision_recovery.jsonl
```

Default planner is scripted. Its `model_calls` counts planner invocations;
`api_calls` is explicitly zero. These sessions are adapter smoke tests, not
evidence of language-model planning or recovery performance.

For an explicit real API run, add `--planner qwen --model qwen-vl-plus` and
configure `DASHSCOPE_API_KEY` in the environment (or `--env-file`). Credentials
are never journaled. This adapter does not supply camera images to the model:
even with Qwen-VL it remains structured-state feedback, not visual feedback.

The journal records protocol, observations, planner attempts, commands,
receipts and diagnostic primitive results. Diagnostic simulator quantities
are logged after the session and are not supplied as reward/score to the model.
Keep all smoke results separate from previous formal experiment datasets.

## Next Gate

Before a benchmark, run a real-model normal session and timeout recovery
session. Confirm API calls and state-dependent remaining-plan changes. Then
freeze matched tasks, seeds, controller and budgets for open-loop, direct
replanning and supervised recovery comparisons. This adapter alone does not
establish nonstructured-environment generalization or comparative advantage.

## Paired pilot

`bash scripts/start_supervision_paired_pilot.sh` starts 10 seeds × 3 methods
in the background, provided the calling shell has `DASHSCOPE_API_KEY` configured.
Paths are recorded in `/tmp/supervision_paired_pilot_{pid,log,output}`.

The pilot shares the first real-model output per seed. Every method uses the
same observed state, goal, fixed controller, physical initial state and hard
validation gate. Direct replanning receives the latest raw execution feedback,
but not immutable history or iterative validator diagnostics. Thus this is an
ablation of structured feedback/history, not an unsafe unvalidated baseline.
All methods have six planner attempts, eight commands and twelve observations.
Recovery remains within each method's single episode. Seed equality is checked
against actual initial puck, hand, gripper and target state before comparison.

Actual API calls are counted separately from logical planner calls because the
initial plan is replayed. Normal success requires post-command observation;
the timeout must be observed in diagnostics. Failure of initial plan generation
or an initial-state mismatch aborts rather than fabricating comparable cases.
This simple timeout pilot may saturate both recovery methods; it cannot establish
superiority on harder tasks or naturally occurring failures.

### V2 state-changing perturbations

`bash scripts/start_supervision_harder_pilot.sh` runs two perturbations × ten
seeds × three methods (60 sessions). First-place timeout uses ten controller
steps after a completed grasp. Post-grasp slip uses fifty actual open-gripper
simulation steps after a successful grasp; the original success receipt is
preserved so a fresh observation must detect disagreement. No-recovery stops
on an invalid remaining plan without requesting a new plan. Both recovery
methods retain the same fresh state and physical safety validation. Slip is
controlled release, not naturally occurring contact failure. Diagnostics record
whether each disturbance was actually realized; report these counts separately,
including unsuccessful injection cases in total task outcomes. Do not infer
method superiority from scenario design alone.

### V3 feedback correction (development regression)

Run `bash scripts/start_supervision_feedback_v3.sh` from the shell containing the
API key. It runs the same two perturbations and seeds 0–9, writing NEW journals
under `supervision_feedback_v3_*`. Previous V2 results are not overwritten.
These seeds are development cases, not held-out formal evaluation. Use
`--start-seed` on the comparison CLI to reserve new seeds after freezing code.

Both model-driven methods now use an identical strengthened JSON-object format
and explicit current-observation precedence over historical success receipts.
Only supervision includes a programmatic conflict diagnostic: last successful
pick versus fresh occupancy, invalidated holding precondition and blocked place
action. Unknown occupancy is not treated as loss. The physical cause remains
unknown; diagnostics do not prescribe repair actions or consult reward.

Two attempts per preparation, six total planner invocations and eight commands
remain unchanged for both methods. This is a combined feedback/prompt correction,
not an isolated causal test of the diagnostic alone. New case journals include
first repair acceptance, rejected candidates and rejection reasons; these should
be analyzed alongside task success and calls. Better direct-replan performance
after the shared prompt correction must be reported, not suppressed.
