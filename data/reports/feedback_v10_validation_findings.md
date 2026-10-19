# Frozen feedback v10: held-out layout validation

## Scope and completeness

Source: `data/collections/feedback_v10_validation_20261019_153658/validation_analysis.json`.
Seeds 600–619, medium and hard, three methods: 120/120 completed episodes; no missing pairs.
These are held-out layouts within the existing task families, not unseen task semantics.
All methods use the same model/inference protocol, validation and execution backend.

## Results

Costs below are recovery-only; shared initial planning and simulator/processing costs are excluded.

| Condition | Method | Success | Recovery calls | Recovery tokens | Candidate rejections |
|---|---|---:|---:|---:|---:|
| Medium | Direct replanning | 20/20 | 20 | 11600 | 0 |
| Medium | Legacy detailed feedback | 20/20 | 20 | 14578 | 0 |
| Medium | Compact feedback | 20/20 | 20 | 12400 | 0 |
| Hard | Direct replanning | 20/20 | 20 | 12660 | 0 |
| Hard | Legacy detailed feedback | 20/20 | 40 | 32960 | 20 |
| Hard | Compact feedback | 20/20 | 20 | 13460 | 0 |

Combined compact versus legacy: recovery tokens decrease 45.60% (47538 to 25860),
recovery calls decrease 33.33% (60 to 40). Hard-condition reductions are 59.16% tokens
and 50% calls, with candidate rejections dropping from 20 to zero.

Combined compact versus direct: successes and recovery calls tie, but compact uses
6.60% more recovery tokens (25860 versus 24260). Direct remains the cheapest method.
All success comparisons have no discordant pairs (exact McNemar p=1); this is not
proof of statistical equivalence or noninferiority.

## Interpretation and limits

This independently reproduces the benefit of replacing verbose historical diagnostics
with compact, current-state-first feedback relative to the old feedback design.
It does NOT establish superiority over strong direct replanning, higher final success,
or an inherent need for explicit diagnostics on these tasks. The interpretation that
historical failed targets anchor planning is consistent with development traces; the
aggregate validation alone does not isolate each prompt component causally.

The task families are narrow, involve controlled execution perturbations, and share
near-identical semantic/prompt structures across layout seeds. Do not treat these as
40 independent semantic task types, general open-world evidence, or a continuous
physical safety certificate. Candidate rejection is a pre-execution validator event,
not necessarily a physically attempted failed action. Token savings are not measured
end-to-end monetary or wall-clock savings.

## Decision

Keep compact feedback as an improvement over the legacy implementation. Do not
expand this same saturated family merely to seek a success-rate gap. Any follow-up
must define a distinct hypothesis and retain the equally informed direct baseline.
