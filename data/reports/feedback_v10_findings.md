# Compact feedback: matched development findings

36 physical episodes, seeds 530..535, two conditions, all retained; all succeeded.
Medium: direct / legacy / compact recovery calls 6/6/6; tokens 3480/4368/3738.
Hard: direct / legacy / compact recovery calls 6/12/7; tokens 3798/9921/4634.
Hard constraint rejections: 0/6/1. No observed task failure after recovery.
Compact vs legacy: hard tokens -53.29%, requests -41.67%; medium tokens -14.42%.
Combined: tokens 14289 -> 8372 (-41.41%), calls 18 -> 13 (-27.78%).
Against direct: compact still uses more tokens (8372 vs 7278) and one additional call.
Thus this supports a development-level improvement to the feedback format, NOT
superiority to strong direct replanning, nor statistical noninferiority from 12 pairs.
Different layouts reuse nearly identical symbolic tasks; generalization is limited.
Observed latency is noisy and should not be treated as confirmed deployment speedup.
The ablation jointly removes old-target anchoring and compresses rejected-candidate
feedback; it does not identify each edit's independent contribution.
422 tests passed with repository PYTHONPATH. Initial output shared; costs here are
recovery-only and omit common initial generation, simulation, and image processing.
