"""episode JSON 日志（JSONL 追加写），字段与《第三章思路》24 节对齐。

一个 episode 一条记录；表格全部可从日志自动统计。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class EpisodeLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


def new_episode_record(episode_id: str, scene_id: str) -> dict[str, Any]:
    """返回一个字段齐全的空记录骨架（用时逐项填充）。"""
    return {
        "episode_id": episode_id,
        "scene_id": scene_id,
        "ts": time.time(),
        # 待填充：instruction / objects / initial_state / raw_vlm_output / model_plan /
        # format_valid / object_valid / capability_valid / state_valid /
        # first_invalid_step / error_code / repair_called / repair_mode /
        # final_valid / goal_satisfied / pass_but_wrong / natural_injected /
        # planning_latency_ms / validation_latency_ms / repair_latency_ms /
        # input_tokens / output_tokens / robot_success
    }
