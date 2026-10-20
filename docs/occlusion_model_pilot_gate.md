# Occlusion model pilot and formal gate (2026-10-20)

Fixed development seeds 750–754; two methods, ten physical episodes. Registered
screen geometry unchanged. Primary corner2 visibility checked with unchanged color
detector, no object-position truth. Missing evidence triggers MODEL observe/stop.
Both methods can request corner; the alternate is acquired before plan execution.
Each method receives identical raw evidence and available observation description;
compact adds only a derived uncertainty summary. Observation decisions share the
two noninitial call budget with suffix repair. Initial plan may be shared only when
prompt and image hashes match. Different prompts must not replay the other's output.

After observation: corner-based RGB localization/goal evidence, separate per-camera
holding evidence. Both methods get the SAME backend sensor implementation. This is
bounded multi-camera acquisition in a known-color fixture, not physical head motion,
open-world planning, or proof that uncertainty diagnosis is necessary.

Record every seed and outcome including API/parse/observation/physical failures;
no winning-seed selection. Recovery tokens include acquisition calls, exclude shared
initial planning. Compare final success, actual observations, unsupported completion,
failed physical transfers, validator rejections, calls and tokens separately.

Before any formal-scale run:
1. All ten episodes accounted for (including safe stops), no hidden exceptions.
2. Primary occlusion and alternate images persist with camera/time provenance.
3. No source/goal binding depends on evaluator truth; no mixed-camera pixels.
4. Every attempted transfer has supported holding before place; unknown stops.
5. Real API requests and usage audited for BOTH methods, inference protocol equal.
6. Any execution failure investigated with its evidence retained, not removed.
7. Evidence of opportunity must be distinguished from method superiority. If both
   simply observe once and tie, stop seeking a gap by repeating this same fixture.
8. Freeze new held-out formal layouts and source hashes only AFTER the above audit;
   no formal run auto-starts from this pilot. No statistical equivalence claim from ties.

Known development failure: seed 741 remains holding-unknown in both available
oblique views at the second pick. Retain as unresolved evidence limitation.
