# Core recovery study: execution audit and go/no-go

## Scope
LLM-generated complete remaining plans after actual robot execution feedback.
Fixed controller and current simulator; no new vision stack or learned controller.
Compare direct replanning with compact organization of the SAME raw evidence.
No promised performance advantage and no formal batch until the exposure is audited.

## Confirmed implementation issue and correction
`_request` constructs a rich prompt but execution-contract mode replaces it with
`planning_snapshot`. The snapshot omitted supported-delivery history, goal-update
records and raw execution deviations. Previous contract-mode results cannot be
called a complete shared-history study. Both methods now receive the same deep-copied
`recovery_evidence`, including current local rejection evidence. Predicted candidate
facts and free-form rejected model output are excluded from that evidence.
This changes the input protocol: do not pool new runs with historical v8/v10 results.
Old files/results are not rewritten. Derived compact diagnostics remain additional
organization, not privileged observations.

## Current backend boundary
Two movable cubes, two placement regions, adjacent pick/place, four total transfers.
Legal different orders exist when targets are clear (covered by validator tests).
This alone does NOT prove physically executable recovery alternatives or history
necessity. Historical supported deliveries do not continuously certify object state.
A goal change is a controlled user update, not a physical failure.

## Next gate, before paid pilot
1. Audit a same-episode trace with delivery, actual deviation and recovery. Verify
   no reset between them; actual observation supports state updates.
2. Separate physical attempts from model candidate rejections. A rejected plan is
   never counted as a failed robot action or appended as executed delivery.
3. Demonstrate alternative recovery sequences with the fixed backend, not merely
   a symbolic validator. If no genuine choice exists, label state-sufficient control;
   do not invent a history-dependent rule to obtain a gap.
4. Only then prescribe 5 paired layouts, same model/raw evidence/observations/gates/
   budget, alternating order. Keep all failures; do not choose seeds by outcomes.
5. Report success, false completion, physical waste, candidate rejections, calls,
   tokens and latency separately. Ties are neither superiority nor equivalence proof.

## Frozen auxiliary evidence
Occlusion/multiview acquisition pilots remain interface evidence, not a superior
recovery claim. Do not expand perception to rescue a null planning comparison.

## Status
Shared-evidence unit tests added and passed. No live-model batch started by this
change; physical-alternative gate is still pending. This is not formal-ready.
