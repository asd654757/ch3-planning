import pytest

from ch3.vlm.task_switch_recovery import evaluate_switch_plan, switch_fixture


def action(step, skill, obj, target=None):
    return dict(step_id=step, skill=skill, object_id=obj, target_id=target, arm="right")


def correct():
    return {"actions": [action(1, "place", "blue_candidate", "table"),
        action(2, "pick", "yellow_candidate"), action(3, "place", "yellow_candidate", "green_region")]}


def test_current_state_needs_three_actions_not_historical_pick_replay():
    audit, plan = evaluate_switch_plan(correct(), switch_fixture())
    assert audit["accepted"] and not audit["physical_execution_authorized"]
    assert [step.source_skill for step in plan.steps] == ["place", "pick", "place"]
    assert plan.steps[0].args["object_id"] == "blue_candidate"


def test_old_blue_place_is_valid_but_wrong_for_updated_task():
    audit, plan = evaluate_switch_plan({"actions": [action(1, "place", "blue_candidate", "green_region")]}, switch_fixture())
    assert audit["valid"] and not audit["goal_satisfied"]
    assert plan is None


def test_yellow_pick_without_release_is_invalid():
    audit, plan = evaluate_switch_plan({"actions": [action(1, "pick", "yellow_candidate"),
        action(2, "place", "yellow_candidate", "green_region")]}, switch_fixture())
    assert not audit["valid"] and plan is None


def test_rejects_unknown_extra_fields_and_unconfirmed_state():
    raw = correct()
    raw["actions"][0]["hidden_control"] = "release"
    with pytest.raises(ValueError):
        evaluate_switch_plan(raw, switch_fixture())
    state = switch_fixture()
    state.holding = {}
    with pytest.raises(ValueError):
        evaluate_switch_plan(correct(), state)
    raw = correct()
    raw["actions"][1]["object_id"] = "unseen_object"
    audit, plan = evaluate_switch_plan(raw, switch_fixture())
    assert not audit["valid"] and plan is None


def test_live_call_rejects_task_changed_while_pending(tmp_path):
    import json
    from hashlib import sha256
    from types import SimpleNamespace
    from ch3.execution.recovery_context import RecoveryContext
    from ch3.vlm.task_switch_recovery import generate_switch_plan
    image = tmp_path / "image.png"
    image.write_bytes(b"fixture-image")
    c = RecoveryContext("Cancel the unexecuted blue-to-green placement. Now transport the yellow cube to the green region, returning the held blue cube to table.",
        {"on(yellow_candidate, green_region)", "on(blue_candidate, table)", "hand_empty(right)"})
    c.observe(facts=switch_fixture().facts(), image_sha256=sha256(image.read_bytes()).hexdigest(), evidence_source="explicit_fixture_not_visual_state_estimation")
    class Client:
        def complete(self, **kwargs):
            c.update_task("another task", {"on(blue_candidate, green_region)"})
            return SimpleNamespace(content=json.dumps(correct()), model="test", total_tokens=1, latency_ms=1)
    with pytest.raises(ValueError, match="stale"):
        generate_switch_plan(Client(), image_path=image, log_path=tmp_path / "call.json", state=switch_fixture(), context=c)
    assert (tmp_path / "call.json").exists()
    assert "stale" in json.loads((tmp_path / "switch_request_binding.json").read_text())["rejection"]


def test_live_call_records_binding_without_model_echo(tmp_path):
    import json
    from types import SimpleNamespace
    from ch3.vlm.task_switch_recovery import generate_switch_plan
    image = tmp_path / "image.png"
    image.write_bytes(b"fixture-image")
    class Client:
        def complete(self, **kwargs):
            assert "request_context" in kwargs["user_prompt"]
            return SimpleNamespace(content=json.dumps(correct()), model="test", total_tokens=1, latency_ms=1)
    executable = generate_switch_plan(Client(), image_path=image, log_path=tmp_path / "call.json", state=switch_fixture())
    assert executable is not None
    assert json.loads((tmp_path / "switch_request_binding.json").read_text())["response_current"]
