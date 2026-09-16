# State Recovery Offline P0/P1 Analysis

## 1. Recovery Feasibility Certificate

- Formal points: **428**
- Recoverable: **428**
- Infeasible: **0**
- Recoverable rate: **1.0000**
- Search depth limit: **8**

| Perturbation | Points | Recoverable | Infeasible | Rate | Mean oracle actions |
|---|---:|---:|---:|---:|---:|
| grasp_failure | 107 | 107 | 0 | 1.0000 | 1.000 |
| nominal_state | 107 | 107 | 0 | 1.0000 | 0.000 |
| object_displacement | 107 | 107 | 0 | 1.0000 | 1.000 |
| wrong_held_object | 107 | 107 | 0 | 1.0000 | 1.000 |

## 2. Goal Checker Ablation

| Method | Points | Success | Success rate | PBW | Fallback |
|---|---:|---:|---:|---:|---:|
| ROUTED_without_GC | 428 | 301 | 0.7033 | 1 | 0 |
| ROUTED_full | 428 | 308 | 0.7196 | 1 | 127 |

Paired success counts:

```text
both_success              = 301
full_only_success         = 7
without_GC_only_success   = 0
both_fail                 = 120

McNemar exact p-value     = 0.015625
```

## 3. Action Overhead / Oracle Efficiency

| Method | Points | RSR | Mean repair actions | Mean overhead | Mean NAO | Mean calls |
|---|---:|---:|---:|---:|---:|---:|
| R2_STATE | 428 | 0.6986 | 0.951 | 0.201 | 0.201 | 1.000 |
| R1_FROM_STATE | 428 | 0.6846 | 0.893 | 0.143 | 0.143 | 1.000 |
| ROUTED | 428 | 0.7196 | 0.916 | 0.166 | 0.166 | 1.297 |

Notes:

- RSR is Recovery Success Rate on the original 428-point denominator.
- Oracle length is the shortest legal symbolic recovery plan.
- A negative overhead means the method produced a shorter suffix than the program-side minimal-action approximation recorded in the formal runner.
- This report uses zero VLM calls and does not modify the frozen formal dataset.
