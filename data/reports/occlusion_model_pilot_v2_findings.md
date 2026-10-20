# Occlusion acquisition v2: completed pilot and scale decision

Fixed seeds 760–764; JSON object protocol shared by both methods. Ten episodes
completed with one reset each, no missing/duplicate/unexpected rows.
Direct and compact both achieved 5/5 success, 5/5 alternate-view acquisitions,
10 noninitial model calls each. Noninitial tokens: direct 5025, compact 5418
(+7.82%). Noninitial costs include initial-localization acquisition, not merely
post-execution recovery. Source: data/collections/occlusion_model_pilot_20261020_105838.

v1 parse failure is retained separately, not pooled or silently replaced.
Full regression suite: 440 passed after implementation.

Decision: pilot execution chain works, but this condition does NOT demonstrate
compact feedback superiority. Do not automatically scale it as a superiority test.
It supports bounded fixed-camera observation acquisition under real physical
occlusion in a known-color fixture. This is INITIAL localization ambiguity followed
by controlled placement-deviation recovery, NOT evidence of post-execution historical
ambiguity or general nonstructured-environment adaptation.

Five pairs are not a noninferiority/equivalence study. The earlier scripted seed 741
holding-unknown failure is retained as a backend limitation; new success does not
erase it. Independent-view positive co-motion is a restricted heuristic, not calibrated
safety assurance. Extra holding cameras are common backend instrumentation and must
be counted separately from planner-requested acquisitions in formal sensor costs.

Before a worthwhile formal experiment, choose the scientific claim: acquisition
capability (both methods work), or explicit-feedback advantage (not yet supported).
Do not introduce hidden missing information in the baseline, remove stopping, weaken
its gate, or select seeds on winning gaps. History-dependent recovery and multiple
executable alternatives remain unimplemented in this fixture.
