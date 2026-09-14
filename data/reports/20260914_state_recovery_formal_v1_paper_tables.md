# State Recovery formal v1 paper tables

Source: `data/collections/state_recovery_formal_v1_20260914_101157.jsonl`; derived analysis: `data/reports/state_recovery_formal_v1_analysis_v2_20260914_112847.json`.

All rates are over 428 arm evaluation points (107 nominal_state + 321 non-nominal points), paired across five arms. For the non-nominal RSR column, `OPEN_LOOP` and `R2_NO_STATE` retain the perturbed prefix unchanged; their apparent 25.0% whole-set success comes only from the nominal_state subset and is 0.0% on non-nominal points.

## Main five-arm table

| Arm | Points | Valid from S_t | RSR | Non-nominal RSR | PBW | Prefix mutation | Avg calls | Total tokens | Tokens/call | SCER | Conflicting-action rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OPEN_LOOP | 428 | 25.0% | 25.0% | 0.0% | 41.6% | 0.0% | 0.000 | 0 | - | 66.6% | 33.4% |
| R2_NO_STATE | 428 | 25.0% | 25.0% | 0.0% | 41.6% | 0.0% | 1.000 | 369,683 | 863.745 | 66.6% | 33.4% |
| R2_STATE | 428 | 25.0% | 69.9% | 59.8% | 0.2% | 0.0% | 1.000 | 476,153 | 1112.507 | 70.1% | 34.8% |
| R1_FROM_STATE | 428 | 25.0% | 68.5% | 57.9% | 0.0% | 0.0% | 1.000 | 478,660 | 1118.364 | 68.5% | 38.8% |
| ROUTED | 428 | 25.0% | 72.0% | 62.6% | 0.2% | 0.0% | 1.297 | 480,233 | 865.285 | 72.2% | 39.0% |

Definitions: RSR = validator accepted and goal satisfied from the observed state; PBW = validator accepted but goal not satisfied; SCER = state-conditioned executable suffix (legacy-record derivation).

## Non-nominal recovery by perturbation

| Arm | Grasp failure | Object displacement | Wrong held object |
|---|---:|---:|---:|
| OPEN_LOOP | 0.0% | 0.0% | 0.0% |
| R2_NO_STATE | 0.0% | 0.0% | 0.0% |
| R2_STATE | 76.6% | 100.0% | 2.8% |
| R1_FROM_STATE | 69.2% | 99.1% | 5.6% |
| ROUTED | 79.4% | 100.0% | 8.4% |

## Recovery by task family (all perturbations, including nominal)

| Arm | pick/place | push | press |
|---|---:|---:|---:|
| OPEN_LOOP | 25.0% | 25.0% | 25.0% |
| R2_NO_STATE | 25.0% | 25.0% | 25.0% |
| R2_STATE | 57.6% | 77.1% | 75.0% |
| R1_FROM_STATE | 51.4% | 79.2% | 75.0% |
| ROUTED | 59.7% | 81.2% | 75.0% |

## Wrong-held-object failure signature

| Arm | RSR | Avg calls | Fallbacks | Fallback rate | Conflicting-action rate |
|---|---:|---:|---:|---:|---:|
| OPEN_LOOP | 0.0% | 0.000 | 0 | 0.0% | 100.0% |
| R2_NO_STATE | 0.0% | 1.000 | 0 | 0.0% | 100.0% |
| R2_STATE | 2.8% | 1.000 | 0 | 0.0% | 74.8% |
| R1_FROM_STATE | 5.6% | 1.000 | 0 | 0.0% | 87.9% |
| ROUTED | 8.4% | 1.963 | 103 | 96.3% | 88.8% |

