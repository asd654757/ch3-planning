"""Deterministic VLM clients for tests and offline demos."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from ch3.vlm.client import VLMResponse


class MockVLMClient:
    """Returns a scripted response; the callback may inspect the payload."""

    def __init__(
        self,
        response: str | Mapping[str, Any] | Callable[[Mapping[str, Any]], str | Mapping[str, Any]],
        *,
        model: str = "mock-vlm",
        latency_ms: int = 1,
        prompt_tokens: int = 10,
        completion_tokens: int = 20,
    ) -> None:
        self.response = response
        self.model = model
        self.latency_ms = latency_ms
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.payloads: list[Mapping[str, Any]] = []

    def complete(self, **kwargs: Any) -> VLMResponse:
        # Mirror the real client's signature enough for planner/repair.
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": kwargs.get("system_prompt", "")},
                {"role": "user", "content": kwargs.get("user_prompt", "")},
            ],
            "temperature": kwargs.get("temperature"),
            "max_tokens": kwargs.get("max_tokens"),
            "seed": kwargs.get("seed"),
            "json_mode": kwargs.get("json_mode"),
        }
        self.payloads.append(payload)
        selected = self.response
        if callable(selected):
            selected = selected(payload)
        content = selected if isinstance(selected, str) else json.dumps(selected, ensure_ascii=False)
        return VLMResponse(
            content=content,
            model=self.model,
            latency_ms=self.latency_ms,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.prompt_tokens + self.completion_tokens,
            finish_reason="stop",
            raw={"mock": True, "content": content},
        )
