# Contract recovery V1: readiness and attribution

Model generates all candidate actions. No BFS, search, sorting, or executable
answer injection. Existing evaluate_flexible performs common safety checking,
including destination capacity and remaining transfers. New module wraps this
checker, projects errors without predicted final facts, and identifies occupied
transfer's action index. Frozen supervision and existing fixture untouched.

Three methods share facts, goals, rules, budget, model, decode and safe checker:
DIRECT receives generic rejection and same rejected candidate; DIAGNOSTIC gets
specific violation; FULL also gets derived occupancy and obligation summaries.
Thus FULL input differs in representation, not access to hidden state or rules.
Budget two calls, 2048 output tokens, temperature .1; no service-error retries
counted as method performance. All raw outputs, finish reasons and request
latencies retained. Parse failures separate from semantic rejection.

Pilot cases: occupied destination ordering; omitted goal coverage; redundant
transfer exceeding remaining execution budget. Five repeated model seeds each.
Two separate tracks: natural candidates with at most one correction; shared
controlled invalid candidate with one correction. 90 cases total. Controlled
candidate contains an error, never an answer, and is not an executed history.
This validates planning/correction only with symbolic states. No physical success
or open-world/visual claim. Model seeds are repeated trials of three fixtures,
not ninety independent tasks. All methods may saturate; retain that outcome.

References inspected: local PragmaBot vlm_task_planner.py (executed action feedback
and memory separated); official REFLECT LLM/prompt.py (request/log wrapper, not a
complete recovery implementation); official ProgPrompt README (situated task
planning/VirtualHome scope). We have not reproduced these papers or imported
free-form generated code execution. Source links:
https://github.com/leggedrobotics/pragmabot
https://github.com/real-stanford/reflect/blob/main/LLM/prompt.py
https://github.com/NVlabs/progprompt-vh

Before formal expansion:
- all unit tests and feasible witnesses pass;
- pilot completes without infrastructure errors;
- report natural first acceptance vs second-candidate improvement separately;
- quantify semantic vs parse errors and costs by method/track;
- validate execution contract against actual continuous physical fixture;
- freeze protocol, unique new tasks, seeds, provenance and primary metric;
- do not choose only cases where FULL wins. Null findings do not authorize
  repeated post-hoc task selection. Offline correction evidence is not enough
  to mark physical benchmark ready.
