import json

import pytest

from ch3.supervision import ClientPlanner, Evidence, Observation, Receipt, Supervisor, Task, Truth
from ch3.validator.pipeline import Validator
from ch3.vlm.client import DashScopeVLMClient


GOAL = "on(red_cube_0, tray_0)"


def action(skill, obj="red_cube_0", target=None, arm="left", index=1):
    result = dict(step_id=index, skill=skill, object_id=obj, arm=arm)
    if target is not None:
        result["target_id"] = target
    return result


def encoded(*actions):
    return json.dumps({"actions": list(actions)})


PAIR = encoded(action("pick"), action("place", target="tray_0", index=2))


class ScriptedPlanner:
    def __init__(self, *outputs):
        self.outputs = iter(outputs)
        self.requests = []

    def generate(self, request, images):
        self.requests.append(request)
        return next(self.outputs)


class Backend:
    def __init__(self, status="success"):
        self.status = status
        self.commands = []

    def execute(self, executable, **kwargs):
        self.commands.append((executable, kwargs))
        return Receipt(self.status)


def make(registry, scene_objects, *outputs, goal=(GOAL,), **kwargs):
    planner = ScriptedPlanner(*outputs)
    return Supervisor(Task("Move red to tray", goal), Validator(scene_objects, registry),
                      planner, **kwargs), planner


def observation(state, seq=0, evidence=None, occupancy=None):
    return Observation("episode", seq, state, occupancy if occupancy is not None else
                       {a: state.holding.get(a) for a in ("left", "right")}, evidence or {})


def test_failed_pick_repair_reaches_backend(registry, scene_objects, init_state):
    supervisor, planner = make(registry, scene_objects, PAIR, PAIR)
    supervisor.observe(observation(init_state))
    assert supervisor.prepare() == "ready"
    backend = Backend("failed")
    supervisor.execute_next(backend)
    assert supervisor.remaining_goals == (GOAL,)
    with pytest.raises(RuntimeError):
        supervisor.execute_next(backend)
    supervisor.observe(observation(init_state, 1))  # grasp failed, object still on table
    assert supervisor.prepare() == "ready"
    assert planner.requests[1]["rejection_feedback"]["reason"] == "execution_feedback"
    backend.status = "success"
    supervisor.execute_next(backend)
    assert [command[0].source_skill for command in backend.commands] == ["pick", "pick"]
    assert backend.commands[-1][1]["instruction"] == "Move red to tray"
    assert supervisor.model_calls == 2
    # Returned history is detached from internal immutable history.
    external = supervisor.history
    external[0]["action"]["skill"] = "place"
    assert supervisor.history[0]["action"]["skill"] == "pick"


def test_partial_observation_requires_reobserve_without_model_call(registry, scene_objects, init_state):
    supervisor, _ = make(registry, scene_objects, PAIR)
    supervisor.observe(observation(init_state, occupancy={"left": None}))
    assert supervisor.prepare() == "need_observation"
    assert supervisor.model_calls == 0
    partial = init_state.copy()
    del partial.at["red_cube_0"]
    supervisor.observe(observation(partial, 1))
    assert supervisor.prepare() == "need_observation"


def test_cross_boundary_place_and_no_predicted_completion(registry, scene_objects, init_state):
    init_state.holding["left"] = "red_cube_0"
    supervisor, _ = make(registry, scene_objects, encoded(action("place", target="tray_0")))
    supervisor.observe(observation(init_state))
    assert supervisor.prepare() == "ready"
    assert supervisor.remaining_goals == (GOAL,)
    supervisor.execute_next(Backend())
    assert supervisor.status == "need_observation"
    init_state.holding.clear()
    init_state.at["red_cube_0"] = "tray_0"
    supervisor.observe(observation(init_state, 1))
    assert supervisor.remaining_goals == (GOAL,)  # no evidence is not proof
    supervisor.observe(observation(init_state, 2, {GOAL: Evidence(Truth.TRUE, "camera")}))
    assert supervisor.prepare() == "complete"
    init_state.at["red_cube_0"] = "table"
    supervisor.observe(observation(init_state, 3, {GOAL: Evidence(Truth.FALSE, "camera")}))
    assert supervisor.remaining_goals == (GOAL,)


def test_revalidate_suffix_after_drop(registry, scene_objects, init_state):
    supervisor, _ = make(registry, scene_objects, PAIR, PAIR)
    supervisor.observe(observation(init_state))
    supervisor.prepare()
    supervisor.execute_next(Backend())
    supervisor.observe(observation(init_state, 1))  # receipt said success; camera shows no grasp
    assert supervisor.prepare() == "ready"
    assert supervisor.audit[1]["request"]["rejection_feedback"]["reason"] == "validator"
    backend = Backend()
    supervisor.execute_next(backend)
    assert backend.commands[0][0].source_skill == "pick"


@pytest.mark.parametrize("bad", [
    encoded(action("press", "blue_cube_0")),  # valid but wrong
    encoded(action("pick", "ghost")),
    '{"actions":[],"policy":"unsafe"}',
    '{"actions":[],"actions":[]}',
    encoded({**action("press"), "policy_id": "unsafe"}),
    encoded(action("pick", index=2)),
    encoded({**action("pick"), "step_id": "1"}),
    encoded({**action("pick"), "step_id": True}),
])
def test_bad_candidates_never_execute(registry, scene_objects, init_state, bad):
    supervisor, _ = make(registry, scene_objects, bad, attempts=1)
    supervisor.observe(observation(init_state))
    assert supervisor.prepare() == "safe_stop"
    assert not supervisor.audit[0]["accepted"]
    with pytest.raises(RuntimeError):
        supervisor.execute_next(Backend())


def test_rejection_is_fed_back_and_budgets_are_bounded(registry, scene_objects, init_state):
    supervisor, planner = make(registry, scene_objects, encoded(action("press")), PAIR,
                               max_model_calls=2, max_commands=1)
    supervisor.observe(observation(init_state))
    assert supervisor.prepare() == "ready"
    assert planner.requests[1]["rejection_feedback"]["reason"] == "predicted_goal_incomplete"
    supervisor.execute_next(Backend("failed"))
    supervisor.observe(observation(init_state, 1))
    assert supervisor.prepare() == "safe_stop"
    assert supervisor.model_calls == 2


def test_episode_replay_conflicts_and_scoring_truth_rejected(registry, scene_objects, init_state):
    supervisor, _ = make(registry, scene_objects, PAIR)
    supervisor.observe(observation(init_state))
    with pytest.raises(ValueError):
        supervisor.observe(observation(init_state))
    other = observation(init_state, 1)
    other.episode_id = "other"
    with pytest.raises(ValueError):
        supervisor.observe(other)
    with pytest.raises(ValueError):
        supervisor.observe(observation(init_state, 1, {GOAL: Evidence(Truth.TRUE, "camera")}))
    with pytest.raises(ValueError):
        Evidence(Truth.TRUE, "scoring_truth")
    supervisor.prepare()
    # A new observation invalidates the accepted version until revalidation.
    supervisor.observe(observation(init_state, 1))
    with pytest.raises(RuntimeError):
        supervisor.execute_next(Backend())
    supervisor.prepare()
    supervisor.execute_next(Backend())
    with pytest.raises(RuntimeError):
        supervisor.execute_next(Backend())


def test_mutable_input_is_snapshotted_and_forbidden_objects(registry, scene_objects, init_state):
    planner = ScriptedPlanner(PAIR)
    supervisor = Supervisor(Task("Move red", (GOAL,), frozenset({"red_cube_0"})),
                            Validator(scene_objects, registry), planner, attempts=1)
    supervisor.observe(observation(init_state))
    init_state.objects.clear()
    assert supervisor.prepare() == "safe_stop"
    assert supervisor.last_rejection["reason"] == "forbidden_object"


def test_multiskill_sequence_survives_fresh_observations(registry, scene_objects, init_state):
    goals = ("pushed_to(red_cube_0, goal_pad)", "pressed(blue_cube_0)")
    output = encoded(action("push", target="goal_pad", arm="right"),
                     action("press", "blue_cube_0", arm="left", index=2))
    supervisor, planner = make(registry, scene_objects, output, goal=goals)
    supervisor.observe(observation(init_state))
    backend = Backend()
    assert supervisor.prepare() == "ready"
    supervisor.execute_next(backend)
    init_state.at["red_cube_0"] = "goal_pad"
    init_state.pushed.add("red_cube_0")
    supervisor.observe(observation(init_state, 1, {goals[0]: Evidence(Truth.TRUE, "observer")}))
    assert supervisor.prepare() == "ready"
    supervisor.execute_next(backend)
    init_state.pressed.add("blue_cube_0")
    supervisor.observe(observation(init_state, 2, {g: Evidence(Truth.TRUE, "observer") for g in goals}))
    assert supervisor.prepare() == "complete"
    assert supervisor.model_calls == 1
    assert [s.source_skill for s, _ in backend.commands] == ["push", "press"]
    assert planner.requests[0]["skills"] == ["pick", "place", "press", "push"]


def test_client_planner_and_multicamera_transport(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"test image")

    class Transport:
        def chat(self, payload):
            self.payload = payload
            return {"choices": [{"message": {"content": PAIR}}],
                    "usage": {"total_tokens": 12}}

    transport = Transport()
    client = DashScopeVLMClient(transport=transport)
    planner = ClientPlanner(client)
    assert planner.generate({"instruction": "move"}, (str(image), str(image))) == PAIR
    assert len(transport.payload["messages"][1]["content"]) == 3
    assert planner.usage[0]["total_tokens"] == 12
    with pytest.raises(ValueError):
        client.complete(system_prompt="", user_prompt="", image_path=image, image_paths=[image])


def test_backend_exception_is_unknown_and_cannot_replay(registry, scene_objects, init_state):
    supervisor, _ = make(registry, scene_objects, PAIR)
    supervisor.observe(observation(init_state))
    supervisor.prepare()

    class Broken:
        def execute(self, *args, **kwargs):
            raise RuntimeError("sensitive transport details")

    receipt = supervisor.execute_next(Broken())
    assert receipt == Receipt("unknown", "RuntimeError")
    assert "sensitive" not in str(supervisor.history)
    assert len(supervisor.history) == 1
    with pytest.raises(RuntimeError):
        supervisor.execute_next(Broken())


def test_command_budget_and_unknown_revocation(registry, scene_objects, init_state):
    supervisor, _ = make(registry, scene_objects, PAIR, max_commands=1)
    supervisor.observe(observation(init_state))
    supervisor.prepare()
    supervisor.execute_next(Backend())
    init_state.holding["left"] = "red_cube_0"
    supervisor.observe(observation(init_state, 1))
    assert supervisor.prepare() == "ready"
    with pytest.raises(RuntimeError, match="budget"):
        supervisor.execute_next(Backend())
    assert supervisor.status == "safe_stop"
    init_state.holding.clear()
    init_state.at["red_cube_0"] = "tray_0"
    supervisor.observe(observation(init_state, 2, {GOAL: Evidence(Truth.TRUE, "camera")}))
    assert not supervisor.remaining_goals
    supervisor.observe(observation(init_state, 3, {GOAL: Evidence(Truth.UNKNOWN, "camera")}))
    assert supervisor.remaining_goals == (GOAL,)
