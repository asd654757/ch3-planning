from copy import deepcopy

from scripts.audit_recovery_observability import public_options


def inputs():
    return dict(holding={"status": "unknown"}, target_visible=True,
        initial_pixel=(100, 100), fresh_pixel=(100, 100),
        pick_result={"reason": "waypoint_timeout", "trace": [{"phase": 0, "steps": 20}]},
        precontact_probe_preserved=True, observations_left=1, observation_id="fresh:1")


def test_timeout_retry_requires_command_trace_and_probe_preservation():
    args = inputs()
    assert public_options(**args)["retry_authorized"]
    args["precontact_probe_preserved"] = False
    assert not public_options(**args)["retry_authorized"]
    args = inputs()
    args["pick_result"]["trace"].append({"phase": 2, "steps": 1})
    assert not public_options(**args)["retry_authorized"]


def test_missing_rgb_cannot_authorize_pick_or_place_even_with_timeout_receipt():
    args = inputs()
    args.update(fresh_pixel=None, target_visible=False)
    assert public_options(**args)["allowed"] == ["stop", "observe"]


def test_unknown_after_successful_receipt_does_not_authorize_retry():
    args = inputs()
    args["pick_result"] = {"completed": True, "reason": "waypoints_completed_not_grasp_verified"}
    args.update(precontact_probe_preserved=False, observations_left=0)
    assert public_options(**args)["allowed"] == ["stop"]


def test_positive_holding_allows_only_fresh_validated_continuation():
    args = inputs()
    args.update(holding={"status": "holding_supported"}, precontact_probe_preserved=False,
                observations_left=0)
    before = deepcopy(args)
    result = public_options(**args)
    assert result["allowed"] == ["stop", "continue"]
    assert result["continuation_guard"]["observation_id"] == "fresh:1"
    assert args == before


def test_positive_holding_cannot_also_authorize_pick_retry():
    args = inputs()
    args.update(holding={"status": "holding_supported"}, observations_left=0)
    assert public_options(**args)["allowed"] == ["stop", "continue"]
