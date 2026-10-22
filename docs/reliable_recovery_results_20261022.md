# Reliable execution-feedback recovery: audit results (2026-10-22)

## Revised claim
The primary contribution is an auditable execution-feedback supervision and
remaining-plan recovery framework, not a claim that compact textual feedback
outperforms direct replanning. The framework separates current observation,
positive goal evidence, past execution receipts and rejected model candidates;
revalidates a plan after each fresh observation; and refuses completion without
positive evidence.

## Audit invariants
The read-only analyzer `scripts/analyze_supervision_reliability.py` checks:

1. every dispatched command is associated with a recorded current observation
   and a ready decision;
2. every model attempt is associated with a recorded observation sequence;
3. command identifiers are unique within an episode;
4. confirmed completion has an explicit positive goal-evidence record and a
   complete decision;
5. the journal contains no duplicate observation sequence inside a case;
6. dispatch uses the latest recorded observation and cannot reuse an already
   consumed observation sequence;
7. after an uncertain receipt, another command requires a strictly newer
   observation recorded before dispatch.

The analyzer does not use hidden simulator truth to repair a journal and does
not establish superiority, equivalence or open-world capability.

## Existing formal journals
| journal | episodes | protocol violations | confirmed complete with positive evidence |
|---|---:|---:|---:|
| `supervision_final_20261011_202726/cases.jsonl` | 150 | 0/150 | 150/150 |
| `supervision_stress_20261011_212731/repeat_slip_place_timeout.jsonl` | 150 | 0/150 | 148/148 |

The stress journal contains three methods and repeated physical perturbations.
Neither audited journal contains uncertain-receipt events. Their absence of
blind retries therefore provides no empirical recovery coverage for that case;
the analyzer's detection is checked with synthetic journal tests only.
Its outcome comparison remains separate: direct/state-first variants reached
50/50 while supervised recovery reached 48/50. The audit result means that the
48 completed episodes were not falsely declared complete; it does not turn the
2 failures into a benefit.

## Important boundary
`supervision_history_hard_20261012_102111.jsonl` is a symbolic exploratory
journal whose construction contains repeated observation sequence values when
its prefix and recovery records are combined. The analyzer correctly flags
30/30 cases. It must not be presented as evidence of the current continuous
execution protocol. Its existing success numbers are retained historically,
but excluded from this reliability claim.

## Recommended paper table
Report two separate tables:

**Table A — outcome and cost:** final/recovery success, model calls, tokens,
commands, candidate rejections and failure stage for each method.

**Table B — reliability audit:** command-without-fresh-observation,
completion-without-positive-evidence, duplicate command id, model request
without observation, and unknown-receipt blind retry. Report zero counts only
for journals passing the protocol audit; do not infer untested safety properties.

## Final interpretation
The current evidence supports: “The proposed supervisor enforces an auditable
observation–decision–execution–verification protocol in the restricted simulator
and records execution-recovery outcomes without treating rejected candidates or
receipts as goal evidence.” It does not support: “the supervisor is universally
more successful than direct replanning,” “compact feedback is cheaper,”
“open-world generalization,” or “visual perception is solved.”
