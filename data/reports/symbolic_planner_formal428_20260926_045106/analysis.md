# Symbolic planner baseline - formal428

- Points: **428**
- Search action cap: **8**
- VLM calls: **0**, tokens: **0**

| Mode | Points | Search ok | No plan | Empty suffix (goal holds) | Symbolic valid | Goal ok | Executable shape | Final success | Rate | Mean suffix actions | Max suffix actions |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BFS_SHORT | 428 | 428 | 0 | 107 | 321 | 321 | 321 | 321 | 0.7500 | 1.002 | 2 |
| BFS_VALID | 428 | 428 | 0 | 107 | 428 | 428 | 214 | 428 | 1.0000 | 1.252 | 3 |
| BFS_EXEC | 428 | 321 | 107 | 0 | 428 | 321 | 321 | 321 | 0.7500 | 1.002 | 2 |

## Paired vs LLM arms

### BFS_SHORT

| Reference | Points | SYMBOLIC | REFERENCE | only SYMBOLIC | only REF | p (exact) |
|---|---:|---:|---:|---:|---:|---:|
| R1_FROM_STATE | 428 | 321 | 293 | 34 | 6 | 8.36458e-06 |
| R2_STATE | 428 | 321 | 299 | 25 | 3 | 2.74405e-05 |
| ROUTED | 428 | 321 | 308 | 22 | 9 | 0.0294494 |

### BFS_VALID

| Reference | Points | SYMBOLIC | REFERENCE | only SYMBOLIC | only REF | p (exact) |
|---|---:|---:|---:|---:|---:|---:|
| R1_FROM_STATE | 428 | 428 | 293 | 135 | 0 | 4.59177e-41 |
| R2_STATE | 428 | 428 | 299 | 129 | 0 | 2.93874e-39 |
| ROUTED | 428 | 428 | 308 | 120 | 0 | 1.50463e-36 |

### BFS_EXEC

| Reference | Points | SYMBOLIC | REFERENCE | only SYMBOLIC | only REF | p (exact) |
|---|---:|---:|---:|---:|---:|---:|
| R1_FROM_STATE | 428 | 321 | 293 | 34 | 6 | 8.36458e-06 |
| R2_STATE | 428 | 321 | 299 | 25 | 3 | 2.74405e-05 |
| ROUTED | 428 | 321 | 308 | 22 | 9 | 0.0294494 |


## By perturbation

| By perturbation | Mode | Points | Final success | Rate | Executable shape | Mean suffix actions |
|---|---|---:|---:|---:|---:|---:|
| grasp_failure | BFS_SHORT | 107 | 107 | 1.0000 | 107 | 1.336 |
| grasp_failure | BFS_VALID | 107 | 107 | 1.0000 | 107 | 1.336 |
| grasp_failure | BFS_EXEC | 107 | 107 | 1.0000 | 107 | 1.336 |
| nominal_state | BFS_SHORT | 107 | 107 | 1.0000 | 0 | 0.000 |
| nominal_state | BFS_VALID | 107 | 107 | 1.0000 | 0 | 0.000 |
| nominal_state | BFS_EXEC | 107 | 107 | 1.0000 | 107 | 1.336 |
| object_displacement | BFS_SHORT | 107 | 107 | 1.0000 | 107 | 1.336 |
| object_displacement | BFS_VALID | 107 | 107 | 1.0000 | 107 | 1.336 |
| object_displacement | BFS_EXEC | 107 | 107 | 1.0000 | 107 | 1.336 |
| wrong_held_object | BFS_SHORT | 107 | 0 | 0.0000 | 107 | 1.336 |
| wrong_held_object | BFS_VALID | 107 | 107 | 1.0000 | 0 | 2.336 |
| wrong_held_object | BFS_EXEC | 107 | 0 | 0.0000 | 0 | 0.000 |

## By task family

| By task family | Mode | Points | Final success | Rate | Executable shape | Mean suffix actions |
|---|---|---:|---:|---:|---:|---:|
| pick_place | BFS_SHORT | 144 | 108 | 0.7500 | 108 | 1.500 |
| pick_place | BFS_VALID | 144 | 144 | 1.0000 | 72 | 1.750 |
| pick_place | BFS_EXEC | 144 | 108 | 0.7500 | 108 | 1.500 |
| press | BFS_SHORT | 140 | 105 | 0.7500 | 105 | 0.750 |
| press | BFS_VALID | 140 | 140 | 1.0000 | 70 | 1.000 |
| press | BFS_EXEC | 140 | 105 | 0.7500 | 105 | 0.750 |
| push | BFS_SHORT | 144 | 108 | 0.7500 | 108 | 0.750 |
| push | BFS_VALID | 144 | 144 | 1.0000 | 72 | 1.000 |
| push | BFS_EXEC | 144 | 108 | 0.7500 | 108 | 0.750 |

## Reading notes

- The planner is handed the frozen closed-world symbolic state and the goal facts, so no perception and no natural-language grounding is needed to instantiate the search. These are upper bounds for the deterministic route, not deployment numbers.
- `BFS_SHORT` accepts on the goal test alone, mirroring the frozen feasibility certificate definition.
- `BFS_VALID` additionally rejects a suffix that ends with an object still held, i.e. it searches for a suffix that passes this paper's own symbolic evaluator.
- `BFS_EXEC` additionally requires a suffix shape the frozen execution protocol can run as one fresh episode (pick/place pairs, or a single push, or a single press).
- A returned empty suffix is scored as valid with zero repair actions under the frozen evaluator, which bypasses the held-object completeness rule.
- Search cost is reported in milliseconds; model calls and tokens are zero by construction.
- Headline arm per protocol: `formal428` -> `BFS_VALID` (symbolic-layer scoring); `fair100` -> `BFS_EXEC` (end-to-end scoring with MetaWorld execution).
