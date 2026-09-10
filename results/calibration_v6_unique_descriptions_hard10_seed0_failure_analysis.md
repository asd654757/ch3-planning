# Hard repair failure analysis

- Hard slices: **10**
- Invalid B1 slices repaired: **0**
- Invalid B1 with repeated picks: **0**

## Initial B1 failure layers


## Repair outcome by mode

| Mode | n | valid | goal | pbw | repeated picks | invalid repeated picks | top failure bucket |
|---|---:|---:|---:|---:|---:|---:|---|

## Failure buckets by mode

## Paired valid-plan McNemar on hard repairs

| Comparison | n00 | a_only | b_only | n11 | p |
|---|---:|---:|---:|---:|---:|
| R0 vs R1 | 0 | 0 | 0 | 0 | 1.000000 |
| R0 vs R2 | 0 | 0 | 0 | 0 | 1.000000 |
| R1 vs R2 | 0 | 0 | 0 | 0 | 1.000000 |

## Key observation

The dominant hard-task failure is not generic state planning, but repeated selection
of the same object.  The current instruction uses color/shape descriptions while the
closed-world goal contains distinct object IDs, so a model can map several clauses
to the same object.  This produces a later `pick_object_not_on_table` failure after
the object has already been placed.
