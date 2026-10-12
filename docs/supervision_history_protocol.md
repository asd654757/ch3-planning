# Executed-history recovery diagnostic V1

Scope: structured symbolic transitions plus actual model recovery; not physical
multi-object simulation or vision. No private history writes. Same deterministic
initial six-action plan is replayed for all methods. Actually dispatch pick(A),
place(A, binA), pick(B) through observe/validate/compile/execute interfaces. Each
receipt and command enters immutable history naturally. Then apply a documented
controlled state disturbance before the next observation.

Profiles:
1. lost_held: B returns to table, hand empty despite successful pick receipt.
2. wrong_held: B returns to table and unrelated D is held; release D then recover.
3. goal_regression_and_loss: A also returns to table; formerly satisfied goal must
   be restored alongside B and C. Current conflict detector diagnoses last-pick
   mismatch only; it does not explicitly diagnose A's historical goal regression.

No permanent forbidden-object rule for A: such a rule would make regression
recovery impossible. Full goals are checked after every candidate, preventing
planned regression. Identical observations, goal evidence, action set and safe
validator across methods. State-first direct has last receipt; without-conflict
and full have entire executed history; only full includes existing conflict field.
Shared budgets: six logical calls including replay, twelve commands including
three prefix commands, fifteen recovery observations, two attempts/preparation.
Longest oracle suffix seven actions, within frozen eight-action schema limit.

Pilot: three profiles x five naming variants x three methods = 45 sessions.
Naming variants are not independent physical tasks. Preserve every result;
exploratory, no guaranteed superiority or post-hoc seed filtering. Report profile
success, recovery calls/tokens, first repair validity, forbidden/precondition and
format errors separately; paired wins/losses by profile. Safe refusal is not task
completion. Service errors invalidate completion rather than count as failures.

Scripted smoke validates feasible suffixes, actual prefix dispatch, equal boundary
states, immutable history and presence/removal of diagnostic. It is not LLM
performance evidence. Start the actual pilot with:

```bash
/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python \
 scripts/compare_supervision_history.py --seeds 5 --output <new-jsonl-path>
```

Do not expand to formal until outputs and realized conflicts are inspected.
