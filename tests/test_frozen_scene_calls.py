import hashlib
import json
import pytest
from ch3.vlm.frozen_scene_calls import FrozenSceneCalls


def test_only_exact_matching_frontend_is_replayed(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"frame")
    record = dict(prompt="same", image_sha256=hashlib.sha256(b"frame").hexdigest(),
                  raw_response="{}", model="mock")
    for name in ("language_goal_call.json", "remaining_plan_call.json"):
        (tmp_path / name).write_text(json.dumps(record))
    class Live:
        def complete(self, **kwargs):
            return "live_repair"
    client = FrozenSceneCalls(Live(), tmp_path)
    with pytest.raises(ValueError):
        client.complete(user_prompt="different", image_path=image)
    assert client.replay_calls == 0
    for _ in range(2):
        assert client.complete(user_prompt="same", image_path=image).model == "frozen_replay:mock"
    assert client.complete(user_prompt="repair", image_path=image) == "live_repair"
    assert client.live_calls == 1
