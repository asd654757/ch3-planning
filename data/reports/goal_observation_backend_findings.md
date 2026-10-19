# Goal re-observation backend check (2026-10-19)

Two predetermined seeds 700 and 701, scripted fixture plans, zero API calls.
Both physical simulator episodes succeeded, each with one reset. Controlled first
placement offset was 0.045 m. These are interface checks, not LLM method results.

Seed 700 naturally returned `unknown` with reason `post_release_positions_not_stable`.
The single extra probe returned `not_satisfied` (x error approximately 0.050 m),
followed by a successful remaining-plan execution. Seed 701 needed no extra probe.
No evidence labels or goal tolerances were changed to trigger this branch.

Source: data/collections/goal_observation_backend_20261019_203516/check_summary.json.
Fresh observations may include physical settling and open-hand motion; this check
has NOT isolated viewpoint information from settling or proved zero object disturbance.
No claim of pure visual-information benefit or superiority over direct replanning.

Next pilot: fixed seeds 710–714, medium second-delivery deviation, direct versus
compact, same two noninitial model calls including observation decisions, one extra
probe per unresolved release. Include all API/parse/execution failures. Unknown
incidence is measured, not guaranteed; if absent, report insufficient exposure rather
than picking successful seeds. No legacy feedback. Ten episodes only, no formal run.
