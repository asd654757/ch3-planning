# v9 development findings

All 24 episodes retained. Both methods succeeded 12/12 across medium/hard.
Medium recovery tokens: direct 3498, diagnostic 4368.
Hard recovery calls: direct 6, diagnostic 12; tokens 3798 versus 9958.
All six diagnostic hard first proposals were rejected for occupied destination.
This is evidence AGAINST a cost/success superiority claim for v9, not a positive result.

The observed failed action references the old target while hard updates current goals.
Its salience appears to encourage failed-object-first planning despite current occupancy.
This causal interpretation is a hypothesis, not established by the small development run.
Next revision must type historical targets explicitly and isolate them from authoritative
current goals; evaluate on new seeds under a separately frozen protocol. Do not omit
v9 or pool repeated near-identical prompts as independent task generalization evidence.

420 tests passed with PYTHONPATH set to repository root. Without it, 4 existing CLI
subprocess tests fail to import ch3; that is an environment configuration issue.
