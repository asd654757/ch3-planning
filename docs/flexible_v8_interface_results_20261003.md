# v8: authoritative snapshot and isolated candidate review

Scope: LLM remaining-task planning and execution recovery. No controller training,
automatic plan ordering, scoring changes or simulator-truth feedback.

The v7 prompt repeated the same task in goals, obligations, remaining facts,
history, updates, prerequisites and diagnostic prose. Failures persisted despite
more instructions. V8 sends one object/location/current-goal table, protection,
placement rules and remaining budget. Historical requests remain local audit.
When a candidate fails, the feedback method receives that unexecuted candidate
and its concrete violation in an isolated review field. Predicted final facts
are excluded. The direct method retries without that review. Both methods use
identical state, observations, gates, budgets and fixed controller.

This is an interface redesign, not evidence that a particular sentence caused
the earlier failures. Combined changes do not identify individual contributions.

Saved-input development replay: seeds 202/211, two methods, 4/4 accepted plans.
Input images verified by SHA-256 against archived recovery requests. No physics
executed by replay; acceptance is not task success.

Independent hard smoke: seeds 239/240, two methods, 4 physical executions;
direct 2/2, feedback 2/2, six actual calls (two shared initial + four recovery).
All recovery candidates accepted first try. Thus the smoke tests usability,
not incremental benefit from rejection feedback. Same-episode execution and
old strict terminal scoring retained.

Files: data/collections/flexible_v8_replay_20261003.jsonl and
data/collections/flexible_v8_independent_20261003/.
The smoke protocol.json inherited the old goal_feedback description because
metadata was corrected after launch; actual model_audit requests and v8 protocol
identify the interface. Original artifact is not rewritten.

Limitations: known colors and fixture, latest supported-release state rather
than continuous full-scene state estimation, single-cube planar destinations.
No broad nonstructured-world claim, no proof feedback beats direct replanning.
Freeze this interface before additional paired trials; do not keep changing
prompts on formal seeds. Prior v6/v7 negative results remain available.
