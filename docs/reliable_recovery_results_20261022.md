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

## Performance-evidence design informed by the thesis
The thesis does not infer a method advantage from one aggregate success number.
Its KALM simulation separates representation effects with oracle keypoints, then
compares multiple input/architecture baselines on five tasks, three random seeds,
and 100 test episodes per seed (printed pp. 89–91). Its real-world study isolates
the verification mechanism by comparing KALM with a version without verification
on the same ten setups per task (printed pp. 91–93). M0M separately reports five
repeats per task, iterations, estimation/planning/execution time, and the retained
failure case (printed pp. 42–47). The transferable lesson is controlled attribution
and complete outcome accounting, not copying their task count or claiming that
their results validate this framework.

For the next recovery comparison, preserve the already-frozen two-method scope
(direct replanning vs. the complete supervisor), and preregister:

1. **Primary outcome:** protocol-correct recovery success per paired seed. Success
   requires the independent terminal task check and no forbidden dispatch or
   unsupported completion. Report the paired 2x2 table and exact McNemar test;
   do not call a nonsignificant test equivalence.
2. **Secondary outcomes:** recovery calls, input/output tokens, physical control
   steps, candidate rejections, extra observations, and latency. Report per-seed
   compact-minus-direct deltas and distributions, not only pooled totals. Control
   steps are a simulator effort proxy, not wall-clock time.
3. **Mechanism reliability:** counts and opportunity denominators for stale-state
   dispatch, execution after candidate rejection, blind retry after uncertain
   receipt, and completion without positive evidence. Where the run contains no
   opportunity, label it “not exercised,” not zero failures.
4. **Attribution controls:** same initial plan when the initial request is
   identical; same simulator seed/layout, model and decoding parameters, raw
   observations/history, validator, controller, score and call/action budget;
   alternate method order and retain API/parse failures in the assigned method.
5. **Failure analysis:** classify every pair by failure stage (acquisition,
   generation/parse, validation, physical execution, terminal goal check), and
   retain trajectory/evidence references. Do not treat rejected candidates as
   physical attempts.

The 5-seed pilot already available is a feasibility result, not an effect estimate:
both methods succeeded in all 10 condition-method cells; recovery calls tied, and
compact used 200 more tokens in medium and 585 more in hard. A larger run can
estimate reliability and cost more precisely, but cannot be justified as a search
for a favorable gap. The formal batch should proceed only if the scientific aim is
to characterize this frozen framework against a strong direct-replanning baseline,
including a possible null result.

## Final interpretation
The current evidence supports: “The proposed supervisor enforces an auditable
observation–decision–execution–verification protocol in the restricted simulator
and records execution-recovery outcomes without treating rejected candidates or
receipts as goal evidence.” It does not support: “the supervisor is universally
more successful than direct replanning,” “compact feedback is cheaper,”
“open-world generalization,” or “visual perception is solved.”
