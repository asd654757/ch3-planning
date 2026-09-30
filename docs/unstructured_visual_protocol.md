# Unstructured instruction and observation protocol

Status: front-end implementation and offline tests, not a completed benchmark.

## Information boundary

The VLM receives only the instruction, current image and registered skills and
arms. It receives no simulator object IDs, goal facts, true state or future
execution outcome. `SceneGrounder` uses an allowlisted input API. Entity IDs in
its output are local observations, NOT validated executor bindings.

Keep evaluation-only truth in a separate record. Final success must come from
independent simulator/task checks, never from the estimated goal and state
alone. Structural consistency and confidence thresholds do not certify visual
accuracy. Confidence is model-reported and requires calibration.

## Required next integration

1. Create a persistent multi-object scene with camera observations and stable
   physical handles. The current single-puck adapter is not sufficient to test
   distractor selection or unseen object instances.
2. Bind local entity IDs to physical handles using visual evidence; record
   ambiguous/rejected bindings. Do not supply evaluator identity mappings to
   the model or silently substitute ground truth at execution time.
3. Resolve uncertain observations through a bounded observe/clarify/reject
   path. Missing hand or object state must never default to empty/on-table.
4. Check parsed goals against hidden author-approved goals for scoring only.
   Predicted goals must drive planning; a wrong interpretation is a failure,
   even if the predicted goal is reached.
5. Execute and recover in the SAME episode. Save image, model request and
   response, estimated state, binding decisions and independent outcome.

## Paired pilot before formal experiments

Freeze tasks and seeds before model calls. Start with 12 scenes: object
displacement, grasp failure and referential ambiguity; include unperturbed
controls. Do not select only cases where one method wins.

For observation attribution compare stale state, image-estimated state and
true-state reference with the same instruction parser and recovery planner.
For interface attribution freeze the SAME estimated input and compare direct
planning against validation/repair. Keep model, call/step budgets, physical
initial conditions and perturbations matched. Failed parsing, ambiguous
grounding, safe rejection and unavailable recovery count in the denominator.

Report goal parsing, object binding, state estimation, unsafe acceptance,
recovery/final success, additional observation and model/action costs.
Use paired outcomes; account for task/seed clustering rather than treating
repeated seeds as independent tasks. This is open-instance, closed-skill
evaluation, not unrestricted open-world robot control.

Existing symbolic and frozen-plan benchmarks remain unchanged. The 18-point
visual pilot with fresh execution episodes is only interface feasibility and
does not demonstrate visual benefit or persistent-scene recovery.
