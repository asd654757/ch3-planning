# Physical occlusion and alternate-view feasibility (2026-10-20)

## Design

A static, opaque, collision-enabled MuJoCo box is added at a predetermined fixture
position. No runtime object ground truth is used to position the screen or camera.
Compare screen absent/present at seeds 730–732, cameras corner2/topview. Capture
the same settled state without intervening simulation steps, object changes or resets.
Use the unchanged color detector; do not replace visibility failures with labels.

## Results

Source: `data/collections/occlusion_views_corrected_20261020_100651/view_check.json`.

| Seed | Blue pixels, clear corner2 | Blue pixels, blocked corner2 | Blue pixels, blocked topview |
|---|---:|---:|---:|
| 730 | 108 | 0 | 238 |
| 731 | 109 | 0 | 239 |
| 732 | 111 | 0 | 239 |

All three blocked corner2 observations are UNKNOWN under the existing detector;
all three alternate observations are visible. Every view pair has unchanged qpos,
one reset, zero API calls. Saved RGB pairs visually confirm opaque screen and
recovered blue-object visibility. This isolates a render-view information difference,
not physical settling, within the same screen-present state.

## Development correction retained for audit

The first attempt set env.camera_name, which Gymnasium ignores after renderer
construction. Both captures used the same actual camera; that batch is INVALID
as alternate-view evidence. It is retained at `occlusion_views_20261020_100602`.
Corrected code switches renderer.camera_id. Unit tests ensure view selection and
restoration even on render exceptions. Subsequent execution bindings must use the
matching camera calibration; screen tests do not change existing default behavior.

## Boundaries and next gate

This establishes INITIAL visibility, not post-execution goal recovery. Fixed camera
selection is simulated multi-camera acquisition, not physical robot head motion.
Known-color presence is not a calibrated uncertainty model. No learned planner,
method superiority or open-world claim follows. No evaluator truth feeds planning.

Before model comparison, test physical reachability with screen present and topview
bindings on fixed seeds 740–741. This is scripted zero-API controller testing, not a
method experiment. A static physical screen may block reachability or obscure goal
regions. Retain any failure and fix geometric/execution feasibility before spending
model calls. Then connect shared raw, timestamped view history and equal observe
budgets to BOTH methods. Never supply stale location facts for occluded entities.

## Reachability result

Both seed 740 and 741 screen-present/topview executions stopped at
`holding_not_supported` after the first pick. This is not a successful execution
chain. The cause is not yet isolated: topview-specific holding evidence versus
actual grasp/controller/screen interference. Do not disable holding checks or
attribute this to physical collision without diagnosis. Next diagnostic retains
same seeds and topview but omits screen to isolate the camera/backend interaction.

## Camera/backend diagnosis follow-up

Topview without screen also fails both original seeds 740–741 at first holding
check. Saved topview holding frames contain insufficient blue color evidence in
both samples. Thus screen collision is not a sufficient explanation; do not
classify these as proven physical grasp failures.

Added existing fixed oblique camera `corner` as a registered alternative. Across
seeds 730–732 the blocked scene has 349/336/333 blue pixels in this camera; camera2
remains blocked. This feasibility batch is recorded at
`data/collections/occlusion_oblique_20261020_103959/view_check.json`.

Screen-present execution from `corner` at fixed seeds 740–741 achieved 1/2 success:
740 completed, 741 stopped at the SECOND blue pick with holding_not_supported.
Source: `data/collections/occlusion_corner_execution_20261020_104124/check_summary.json`.
No parse, evidence or scoring threshold was weakened. This is partial controller
feasibility, not readiness for formal experiments; retain both outcomes.

The next required interface is view-specific evidence acquisition: location/goal
frames and holding frames need not use the same camera, but each must retain its
camera identity, matching calibration, raw samples and freshness. Do not mix
pixels from different cameras in co-motion/stability calculations; do not let a
visible object imply holding. Both methods must have the same registered views.
No new paid model batch is justified before this interface is physically checked.
