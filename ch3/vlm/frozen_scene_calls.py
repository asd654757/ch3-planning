"""Replay exactly two image-matched front-end calls for paired diagnostics.

Not fresh model inference. Further calls use the live client (repair only).
"""
import hashlib
import json
from pathlib import Path

from ch3.vlm.client import VLMResponse


class FrozenSceneCalls:
    def __init__(self, live_client, directory):
        self.live = live_client
        root = Path(directory)
        self.records = [json.loads((root / name).read_text()) for name in
                        ("language_goal_call.json", "remaining_plan_call.json")]
        self.replay_calls = 0
        self.live_calls = 0

    def complete(self, **kwargs):
        if self.replay_calls < 2:
            record = self.records[self.replay_calls]
            digest = hashlib.sha256(Path(kwargs["image_path"]).read_bytes()).hexdigest()
            if digest != record["image_sha256"] or kwargs["user_prompt"] != record["prompt"]:
                raise ValueError("frozen call prompt/image mismatch; diagnostic stopped")
            self.replay_calls += 1
            return VLMResponse(record["raw_response"], "frozen_replay:" + record["model"], 0)
        self.live_calls += 1
        return self.live.complete(**kwargs)
