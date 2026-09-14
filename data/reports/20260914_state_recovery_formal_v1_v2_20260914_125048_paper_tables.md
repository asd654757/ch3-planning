# State Recovery formal v1 paper tables

Source: `data/collections/state_recovery_formal_v1_20260914_101157.jsonl`.
Authoritative derived analysis: `data/reports/state_recovery_formal_v1_analysis_v2_20260914_125048.json`.

Each of 428 paired evaluation points is scored for all five arms; therefore the JSONL contains 2,140 arm-level records. The four evaluation subsets are nominal_state (107), grasp_failure (107), object_displacement (107), and wrong_held_object (107).

RSR counts a repaired suffix that is executable from the observed state, satisfies the task goal, and does not mutate the executed prefix. SCER counts only suffix executability. Non-nominal RSR excludes nominal_state. PBW means executable from state but goal-unsatisfied. Conflicting-action rate uses a suffix-local state simulation.

## Main five-arm table

| Arm | Points | RSR | Non-nominal RSR | PBW | Prefix mutation | Avg calls | Total tokens | Tokens/call | SCER | Conflicting action |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OPEN_LOOP | 428 | 25.0% | 0.0% | 41.6% | 0.0% | 0.000 | 0 | - | 66.6% | 33.4% |
| R2_NO_STATE | 428 | 25.0% | 0.0% | 41.6% | 0.0% | 1.000 | 369,683 | 863.745 | 66.6% | 33.4% |
| R2_STATE | 428 | 69.9% | 59.8% | 0.2% | 0.0% | 1.000 | 476,153 | 1112.507 | 70.1% | 23.1% |
| R1_FROM_STATE | 428 | 68.5% | 57.9% | 0.0% | 0.0% | 1.000 | 478,660 | 1118.364 | 68.5% | 28.5% |
| ROUTED | 428 | 72.0% | 62.6% | 0.2% | 0.0% | 1.297 | 480,233 | 865.285 | 72.2% | 25.2% |

## Non-nominal recovery by perturbation

| Arm | Grasp failure | Object displacement | Wrong held object |
|---|---:|---:|---:|
| OPEN_LOOP | 0.0% | 0.0% | 0.0% |
| R2_NO_STATE | 0.0% | 0.0% | 0.0% |
| R2_STATE | 76.6% | 100.0% | 2.8% |
| R1_FROM_STATE | 69.2% | 99.1% | 5.6% |
| ROUTED | 79.4% | 100.0% | 8.4% |

## Recovery by task family

| Arm | pick/place | push | press |
|---|---:|---:|---:|
| OPEN_LOOP | 25.0% | 25.0% | 25.0% |
| R2_NO_STATE | 25.0% | 25.0% | 25.0% |
| R2_STATE | 57.6% | 77.1% | 75.0% |
| R1_FROM_STATE | 51.4% | 79.2% | 75.0% |
| ROUTED | 59.7% | 81.2% | 75.0% |

## Wrong-held-object failure signature

| Arm | RSR | Avg calls | Fallbacks | Fallback rate | Conflicting action |
|---|---:|---:|---:|---:|---:|
| OPEN_LOOP | 0.0% | 0.000 | 0 | 0.0% | 100.0% |
| R2_NO_STATE | 0.0% | 1.000 | 0 | 0.0% | 100.0% |
| R2_STATE | 2.8% | 1.000 | 0 | 0.0% | 72.0% |
| R1_FROM_STATE | 5.6% | 1.000 | 0 | 0.0% | 82.2% |
| ROUTED | 8.4% | 1.963 | 103 | 96.3% | 80.4% |

Notes: The formal v1 records predate the direct `repair_suffix_executable` field, so analyzer v2 derives SCER from goal satisfaction or pass-but-wrong status for this frozen collection. Newer collections use the direct field. The wrong-held prompt/fallback fix is commit `bafa93e`; formal v1 remains the pre-fix frozen benchmark and is not mixed with any post-fix pilot.
