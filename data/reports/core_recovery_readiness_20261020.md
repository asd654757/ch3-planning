# Core recovery readiness review

## Completed gates
- Zero-API physical gate: seeds 780–781, both initial transfer orders, 4/4 success,
  one runtime reset each, accepted recovery after observed placement deviation.
  Scripted outputs check backend feasibility only, not LLM competence.
- Live DeepSeek pilot: seeds 790–794, medium/hard, two methods, 20/20 success.
  All expected records present, no duplicate/unexpected records or reset violations.
  Shared raw recovery evidence present in every planning request.
- Tests: 444 passed.

## Pilot results (noninitial requests only)
| Condition | Method | Success | Calls | Tokens | Candidate rejects |
|---|---|---:|---:|---:|---:|
| medium | direct | 5/5 | 5 | 5691 | 0 |
| medium | compact | 5/5 | 5 | 5891 | 0 |
| hard | direct | 5/5 | 10 | 13467 | 5 |
| hard | compact | 5/5 | 10 | 14052 | 5 |

Costs include all noninitial requests, including rejected candidates. Shared initial
requests are excluded; raw actual API totals are not comparative method costs.
Compact tokens are higher. No compact advantage is demonstrated.

## Trace review
Seed790 medium: one supported blue delivery, one observed deviation, one accepted
recovery suffix. Seed790 hard: same supported-delivery count, an explicit user goal
update and deviation, first suffix rejected DESTINATION_OCCUPIED, second accepted.
The rejected candidate does not advance supported history. Terminal positions meet
unchanged independent scoring. This is automated log/trace review, not manual video
inspection. Runtime reset counts remain one throughout the reports.

## Decision
TECHNICALLY READY for a frozen, failure-inclusive replication study of this restricted
state-informed recovery interface. NOT evidence-ready for compact superiority, genuine
history necessity, nonstructured/open-world operation or broad task generalization.
The analyzer deliberately does not automatically authorize a formal run.

Formal scope: independently held-out layout seeds 800–849, medium and hard, two methods
= 200 episodes. Same DeepSeek configuration, shared raw evidence, validator, controller,
scoring and two noninitial-request budget; alternate method order. Freeze source hashes
before launch; no prompt edits, winning-seed exclusion or score changes during the run.
Report success, calls, tokens, candidate rejections and all failures. Hard is a controlled
user-goal-update condition, not proof of a fundamentally non-Markov/history-only task.

A larger sample may characterize costs and reliability but is NOT expected to manufacture
a success gap. Do not spend it if the only acceptable outcome is a winning compact result.
No formal-size model batch was started in this readiness review.
