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
