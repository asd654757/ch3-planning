# V8 Frozen Paired Execution Protocol

Medium seeds 241-270; hard seeds 271-300. Each condition has 30 paired
samples comparing MODEL_DIRECT_REPLAN and MODEL_CONSTRAINT_REPAIR, for
120 executions total. Development and smoke seeds are excluded.

Use the v8 authoritative snapshot and isolated unexecuted-candidate review.
Both methods share initial candidates per seed, current observation, goals,
occupancy gate, goal checker, fixed controller and budgets. Execution order
alternates by seed parity. Maximum four attempted transfers per episode and
two noninitial model requests. No score thresholds are changed.

Medium: controlled second placement offset, preserve completed current goals.
Hard: same offset followed by the predeclared goal swap. This is controlled
execution recovery, not natural failure distribution or general open-world
evaluation. Online inputs exclude simulator scoring truth.

Keep all planned trials, unknown observations, rejections and failures.
Report paired final success, first-candidate acceptance, rejection reasons,
correction after rejection, calls, tokens and attempted transfers. Shared
initial-call cost must be separated from method-specific recovery cost.
Compare the two conditions separately. Different-seed old versions are not
strict ablations. If no rejection occurs, do not claim feedback-correction
benefit merely from high final success.

Run medium then hard, with no continuous monitoring. A failed batch command
stops the runner. Completion requires both summaries and runner completion
marker, not only a process exit. Never overwrite or silently replace trials.
