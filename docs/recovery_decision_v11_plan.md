# Recovery decision study v11 — implementation gate (2026-10-19)

## Question, not a promised outcome

Is direct model replanning sufficient with accurate state? Does compact evidence
organization help with ambiguous feedback, history-dependent failures and genuine
recovery alternatives? Legacy detailed feedback is retired from future comparisons.

## Current audit

- Existing fixture supports continuous same-episode execution and RGB release/goal
  checks. Direct and compact receive current state, goals and executed history.
- Existing successful results already supply an accurate-state control (40/40 each).
  Do not spend more calls replicating the saturated control now.
- `persistent_reference_sequence.run` currently terminates on unknown goal evidence
  (`observed_goal_unknown_no_completion`); there is no planner-selectable observe action.
- Flexible backend accepts adjacent pick/place only, two movable cubes and two regions.
  It lacks a registered alternate waypoint/strategy and evidence-backed persistent
  failure context. It cannot currently establish the proposed history benefit.
- Previous supervision history scripts use symbolic disturbances; they cannot stand
  in for physical persistent-scene execution evidence.

## Minimal next implementation, before model experiments

1. Introduce a shared observation decision interface: complete / act / observe / stop.
   UNKNOWN must remain UNKNOWN, not converted into a factual negative or positive.
   Both methods receive identical timestamped observation/action results and available
   observation requests; compact gets a derived summary, not extra facts.
2. Allow one bounded stationary re-observation using existing probe code. Record raw
   evidence before/after, observation cost and unsupported action commitments. Test
   that unknown evidence never authorizes completion or fabricates object location.
3. Add physical recovery alternatives ONLY after the fixed backend can execute them
   and their prerequisites can be validated. Do not encode 'two failures => switch'
   as a synthetic hidden rule. Do not claim history dependence if current state alone
   suffices. If backend support is absent, mark this condition unavailable.
4. Hand-check paired trajectories and repeatable physical/evidence behavior before
   paying for a small live-model pilot. No formal-size batch is authorized by this doc.

## Fairness and analysis

Same model, decoding, controller, gate, action/observation/model budgets and raw
history. Same perturbation schedule independent of method. No evaluator truth in
planner input. Hold initial model output shared where inputs are identical.

Report all outcomes: task success, attempted failed physical transfers, repeated
unchanged recovery proposals, validator rejections, model calls/input-output tokens,
extra observations, unsupported commitments and termination reasons. Never equate
validator rejection with physically failed action. Include API/parse failures.

Pilot: at most 5 prespecified paired layouts per physically supported condition,
then freeze independently held-out formal layouts if the interface audit passes.
Retain all conditions and failures; no selection of seeds by winning gap. Success ties
are not noninferiority proof. Null or negative results remain valid conclusions.

## Executable change

`flexible_fixture_pilot.py --execution-contract --compare-compact-direct` selects
ONLY direct and compact, alternating order. Existing legacy comparison remains for
historical reproducibility but must not be used for new studies. This flag alone does
NOT implement partial-observation or history-dependent tasks.
