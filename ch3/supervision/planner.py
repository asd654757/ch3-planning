"""Existing Qwen-compatible client adapter; credentials stay in the client."""
import json


class ClientPlanner:
    def __init__(self, client, *, seed=0, temperature=0.1, max_tokens=1024):
        self.client = client
        self.seed, self.temperature, self.max_tokens = seed, temperature, max_tokens
        self.usage = []

    def generate(self, request: dict, images: tuple[str, ...]) -> str:
        response = self.client.complete(
            system_prompt=("Return only JSON with actions, at most 8 steps. Generate ONLY the remaining "
                "plan from current observed facts, using registered skills, arms and objects. "
                "Executed history is immutable and is not current state. A held object may be placed "
                "without another pick. A pick in executed history does not prove current holding. "
                "End with empty hands and satisfy all goal facts; obey forbidden objects. "
                "Use contiguous step IDs from 1. Never add policy IDs, control vectors or extra fields."),
            user_prompt=json.dumps(request, ensure_ascii=False), image_paths=images,
            seed=self.seed, temperature=self.temperature, max_tokens=self.max_tokens, json_mode=True)
        self.usage.append({"model": response.model, "total_tokens": response.total_tokens,
            "latency_ms": response.latency_ms, "finish_reason": response.finish_reason})
        return response.content
