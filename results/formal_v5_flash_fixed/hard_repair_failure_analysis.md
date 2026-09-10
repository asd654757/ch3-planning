# Hard repair failure analysis

- Hard slices: **175**
- Invalid B1 slices repaired: **62**
- Invalid B1 with repeated picks: **54**

## Initial B1 failure layers

- `STATE_TRANSITION_ERROR` / `state`: 54
- `UNKNOWN_OBJECT` / `object`: 4
- `SCHEMA_ERROR` / `syntax`: 4

## Repair outcome by mode

| Mode | n | valid | goal | pbw | repeated picks | invalid repeated picks | top failure bucket |
|---|---:|---:|---:|---:|---:|---:|---|
| R0 | 62 | 22 | 22 | 0 | 17 | 17 | incomplete_pick_no_place |
| R1 | 62 | 16 | 9 | 7 | 46 | 46 | pick_object_not_on_table |
| R2 | 62 | 8 | 4 | 4 | 50 | 50 | pick_object_not_on_table |

## Failure buckets by mode

### R0
- incomplete_pick_no_place: 23
- pick_object_not_on_table: 17

### R1
- pick_object_not_on_table: 46

### R2
- pick_object_not_on_table: 50
- incomplete_pick_no_place: 4

## Paired valid-plan McNemar on hard repairs

| Comparison | n00 | a_only | b_only | n11 | p |
|---|---:|---:|---:|---:|---:|
| R0 vs R1 | 30 | 16 | 10 | 6 | 0.326940 |
| R0 vs R2 | 33 | 21 | 7 | 1 | 0.012541 |
| R1 vs R2 | 43 | 11 | 3 | 5 | 0.057373 |

## Key observation

The dominant hard-task failure is not generic state planning, but repeated selection
of the same object.  The current instruction uses color/shape descriptions while the
closed-world goal contains distinct object IDs, so a model can map several clauses
to the same object.  This produces a later `pick_object_not_on_table` failure after
the object has already been placed.
