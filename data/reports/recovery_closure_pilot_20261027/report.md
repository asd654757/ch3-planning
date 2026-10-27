# Reliable recovery frozen assessment

Labels: direct = MODEL_DIRECT_REPLAN; full implementation = MODEL_COMPACT_REPAIR. This comparison retains shared execution guards and does not isolate their contribution.

Complete data gate: True. Information boundary audit: True.

| Condition | Method | Success/episodes | Recovery API calls | Recovery tokens | Candidate rejects |
|---|---|---:|---:|---:|---:|
| medium | MODEL_DIRECT_REPLAN | 5/5 | 5 | 5691 | 0 |
| medium | MODEL_COMPACT_REPAIR | 5/5 | 5 | 5891 | 0 |

medium: paired n=5; McNemar p=1.0. No significance is not equivalence; repeated seeds are layouts within a single task family.

Failure records (all assigned failures retained):
```json
[]
```
| hard | MODEL_DIRECT_REPLAN | 5/5 | 10 | 13467 | 5 |
| hard | MODEL_COMPACT_REPAIR | 5/5 | 10 | 14052 | 5 |

hard: paired n=5; McNemar p=1.0. No significance is not equivalence; repeated seeds are layouts within a single task family.

Failure records (all assigned failures retained):
```json
[]
```

## Mechanism attribution
Test-only ablation changes only the positive-evidence completion gate. Same observed state and evidence, no model calls or physical actions. Missing/unknown evidence must not authorize completion. Ablation accepting these cases is protocol failure, not measured robot failure. See mechanism.json.

## Information separation
Checked 50 recorded model requests. Terminal scoring occurs after execution; no planner callback in that terminal block. Scoring canary leaves snapshot unchanged. See isolation.json.

## Applicability and failures
Restricted known-color two-cube pick/place fixture; controlled 45 mm placement deviation; hard adds a predeclared goal update. One episode per run, fixed controller and score thresholds. Not a visual open-world, unseen-object, learned-policy or cross-model result. No uncertain receipt coverage is inferred from absent events. API/parse failures, rejected candidates, physical failures and independent terminal failures must remain distinct; raw error/stage are retained above. Zero failures do not imply zero risk. Costs include rejected noninitial attempts; shared initial responses are excluded from recovery cost. Physical steps are a proxy, not latency.

## Decision
This bundle closes the bounded system evaluation, interface-level mechanism attribution, bounded information-separation audit and failure/boundary reporting only when the complete-data gate passes. It does not establish universal performance improvement. If incomplete, the bundle is interim and no completion claim is authorized.
