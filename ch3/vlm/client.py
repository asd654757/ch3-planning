"""DashScope OpenAI-compatible chat client with full response provenance.

The implementation intentionally has no SDK dependency.  It posts JSON to
``/chat/completions`` so that the data-disk project can be reproduced without a
new installation.  API keys are never logged or included in exceptions.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-vl-plus"
DEFAULT_ENV_PATH: Optional[Path] = None


class VLMError(RuntimeError):
    """Raised for network/API/JSON errors; never contains credentials."""


@dataclass(frozen=True)
class VLMResponse:
    """Raw response plus the fields needed for deterministic cost metrics."""

    content: str
    model: str
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: Optional[str] = None
    response_id: Optional[str] = None
    request_id: Optional[str] = None
    raw: dict[str, Any] | None = None


class ChatTransport(Protocol):
    """Minimal injection point used by tests and future local VLM servers."""

    def chat(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        ...


class UrllibChatTransport:
    """HTTP transport for the OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_backoff_s: float = 1.0,
    ) -> None:
        if not api_key:
            raise ValueError("VLM API key is empty")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s

    def chat(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(dict(payload), ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                    return json.loads(body)
            except urllib.error.HTTPError as exc:
                # 4xx (except rate limit) is a request problem, not transient.
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code < 500 and exc.code != 429:
                    raise VLMError(
                        f"VLM API returned HTTP {exc.code}: {body}"
                    ) from exc
                last_error = VLMError(f"VLM API returned HTTP {exc.code}")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = VLMError(f"VLM API request failed: {exc}")
            if attempt < self.max_retries:
                time.sleep(self.retry_backoff_s * (2**attempt))
        raise last_error or VLMError("VLM API request failed")


class DashScopeVLMClient:
    """Small client used by planner, repair, and collector."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        env_path: Optional[str | Path] = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 120.0,
        max_retries: int = 2,
        transport: Optional[ChatTransport] = None,
    ) -> None:
        if transport is None:
            key = api_key or os.environ.get("DASHSCOPE_API_KEY")
            if key is None and env_path is not None:
                key = load_env_file(env_path).get("DASHSCOPE_API_KEY")
            transport = UrllibChatTransport(
                api_key=key or "",
                base_url=base_url,
                timeout=timeout,
                max_retries=max_retries,
            )
        self.transport = transport
        self.model = model

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_path: Optional[str | Path] = None,
        image_paths: Optional[list[str | Path] | tuple[str | Path, ...]] = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        seed: Optional[int] = None,
        json_mode: bool = True,
    ) -> VLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if image_path is not None and image_paths is not None:
            raise ValueError("use image_path or image_paths, not both")
        images = [image_path] if image_path is not None else list(image_paths or ())
        if images:
            payload["messages"][1]["content"] = [
                {"type": "text", "text": user_prompt},
                *[{"type": "image_url", "image_url": {"url": image_data_url(path)}} for path in images],
            ]
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if seed is not None:
            payload["seed"] = int(seed)

        started = time.perf_counter_ns()
        try:
            raw = self.transport.chat(payload)
        except Exception as exc:
            if isinstance(exc, VLMError):
                raise
            raise VLMError(f"VLM transport failed: {exc}") from exc
        latency_ms = int((time.perf_counter_ns() - started) / 1_000_000)
        return response_from_raw(raw, self.model, latency_ms)


def load_env_file(path: str | Path) -> dict[str, str]:
    """Load simple KEY=value .env files without exposing values."""
    env_path = Path(path)
    if not env_path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def image_data_url(path: str | Path) -> str:
    """Convert a local image to a data URL expected by OpenAI-compatible APIs."""
    image_path = Path(path).expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")
    mime, _ = mimetypes.guess_type(str(image_path))
    mime = mime or "image/png"
    if mime.split("/")[0] != "image":
        raise ValueError(f"Not an image MIME type: {mime}")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def response_from_raw(
    raw: Mapping[str, Any], fallback_model: str, latency_ms: int
) -> VLMResponse:
    choices = raw.get("choices") or []
    if not choices:
        raise VLMError("VLM API response has no choices")
    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if content is None:
        raise VLMError("VLM API response message has no content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    if not isinstance(content, str):
        raise VLMError("VLM API response content is not text")
    usage = raw.get("usage") or {}
    metadata = raw.get("metadata") or {}
    return VLMResponse(
        content=content,
        model=str(raw.get("model") or fallback_model),
        latency_ms=latency_ms,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        finish_reason=choice.get("finish_reason"),
        response_id=raw.get("id"),
        request_id=raw.get("request_id") or metadata.get("request_id"),
        raw=dict(raw),
    )
