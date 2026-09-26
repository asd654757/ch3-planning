# Symbolic planner baseline - fair100

**SIMULATION DISABLED: ``final_success`` degenerates to 0 because the frozen
Fair-100 protocol requires a MetaWorld success. Do not read the paired
comparisons below as method results.**

- Points: **100**
- Search action cap: **8**
- VLM calls: **0**, tokens: **0**

| Mode | Points | Search ok | No plan | Empty suffix (goal holds) | Symbolic valid | Goal ok | Executable shape | Final success | Rate | Mean suffix actions | Max suffix actions |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BFS_SHORT | 100 | 100 | 0 | 0 | 100 | 100 | 100 | 0 | 0.0000 | 1.340 | 2 |
| BFS_VALID | 100 | 100 | 0 | 0 | 100 | 100 | 100 | 0 | 0.0000 | 1.340 | 2 |
| BFS_EXEC | 100 | 100 | 0 | 0 | 100 | 100 | 100 | 0 | 0.0000 | 1.340 | 2 |

## Paired vs LLM arms

### BFS_SHORT

| Reference | Points | SYMBOLIC | REFERENCE | only SYMBOLIC | only REF | p (exact) |
|---|---:|---:|---:|---:|---:|---:|
| CHECKER_LOOP_STATE_V2 | 100 | 0 | 71 | 0 | 71 | 8.47033e-22 |
| R1_FROM_STATE | 100 | 0 | 98 | 0 | 98 | 6.31089e-30 |
| ROUTED | 100 | 0 | 98 | 0 | 98 | 6.31089e-30 |
| SELF_REFINE_STATE_V2 | 100 | 0 | 95 | 0 | 95 | 5.04871e-29 |

### BFS_VALID

| Reference | Points | SYMBOLIC | REFERENCE | only SYMBOLIC | only REF | p (exact) |
|---|---:|---:|---:|---:|---:|---:|
| CHECKER_LOOP_STATE_V2 | 100 | 0 | 71 | 0 | 71 | 8.47033e-22 |
| R1_FROM_STATE | 100 | 0 | 98 | 0 | 98 | 6.31089e-30 |
| ROUTED | 100 | 0 | 98 | 0 | 98 | 6.31089e-30 |
| SELF_REFINE_STATE_V2 | 100 | 0 | 95 | 0 | 95 | 5.04871e-29 |

### BFS_EXEC

| Reference | Points | SYMBOLIC | REFERENCE | only SYMBOLIC | only REF | p (exact) |
|---|---:|---:|---:|---:|---:|---:|
| CHECKER_LOOP_STATE_V2 | 100 | 0 | 71 | 0 | 71 | 8.47033e-22 |
| R1_FROM_STATE | 100 | 0 | 98 | 0 | 98 | 6.31089e-30 |
| ROUTED | 100 | 0 | 98 | 0 | 98 | 6.31089e-30 |
| SELF_REFINE_STATE_V2 | 100 | 0 | 95 | 0 | 95 | 5.04871e-29 |


## By task family

| By task family | Mode | Points | Final success | Rate | Executable shape | Mean suffix actions |
|---|---|---:|---:|---:|---:|---:|
| pick_place | BFS_SHORT | 34 | 0 | 0.0000 | 34 | 2.000 |
| pick_place | BFS_VALID | 34 | 0 | 0.0000 | 34 | 2.000 |
| pick_place | BFS_EXEC | 34 | 0 | 0.0000 | 34 | 2.000 |
| press | BFS_SHORT | 33 | 0 | 0.0000 | 33 | 1.000 |
| press | BFS_VALID | 33 | 0 | 0.0000 | 33 | 1.000 |
| press | BFS_EXEC | 33 | 0 | 0.0000 | 33 | 1.000 |
| push | BFS_SHORT | 33 | 0 | 0.0000 | 33 | 1.000 |
| push | BFS_VALID | 33 | 0 | 0.0000 | 33 | 1.000 |
| push | BFS_EXEC | 33 | 0 | 0.0000 | 33 | 1.000 |

## Reading notes

- The planner is handed the frozen closed-world symbolic state and the goal facts, so no perception and no natural-language grounding is needed to instantiate the search. These are upper bounds for the deterministic route, not deployment numbers.
- `BFS_SHORT` accepts on the goal test alone, mirroring the frozen feasibility certificate definition.
- `BFS_VALID` additionally rejects a suffix that ends with an object still held, i.e. it searches for a suffix that passes this paper's own symbolic evaluator.
- `BFS_EXEC` additionally requires a suffix shape the frozen execution protocol can run as one fresh episode (pick/place pairs, or a single push, or a single press).
- A returned empty suffix is scored as valid with zero repair actions under the frozen evaluator, which bypasses the held-object completeness rule.
- Search cost is reported in milliseconds; model calls and tokens are zero by construction.
- Headline arm per protocol: `formal428` -> `BFS_VALID` (symbolic-layer scoring); `fair100` -> `BFS_EXEC` (end-to-end scoring with MetaWorld execution).
