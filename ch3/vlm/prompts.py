"""Prompt templates and versioning for initial planning and repair."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


class PromptLibrary:
    """Loads Markdown prompt templates and records a stable content hash."""

    def __init__(self, directory: str | Path = PROMPT_DIR) -> None:
        self.directory = Path(directory)

    def render(self, name: str, **values: Any) -> tuple[str, str, str]:
        """Return (rendered_prompt, prompt_id, prompt_hash).

        Placeholders use ``{{field}}`` syntax.  Missing fields intentionally
        remain visible so collection issues are easy to diagnose.
        """
        path = self.directory / f"{name}.md"
        if not path.is_file():
            raise FileNotFoundError(f"Prompt template not found: {path}")
        text = path.read_text(encoding="utf-8")
        for key, value in values.items():
            rendered_value = value
            if not isinstance(value, str):
                rendered_value = json.dumps(value, ensure_ascii=False, indent=2)
            text = text.replace("{{" + key + "}}", rendered_value)
        prompt_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return text, name, prompt_hash
