import subprocess
import sys

import pytest


@pytest.mark.parametrize("flags", [
    ["--occlude-early-feedback"],
    ["--early-recovery-setting", "fixed_retry"],
    ["--model-initial-plan", "--visual-follow", "--language-instruction", "move blue",
     "--early-recovery-setting", "fixed_retry", "--skip-pick"],
    ["--model-initial-plan", "--visual-follow", "--language-instruction", "move blue",
     "--early-recovery-setting", "model_replan", "--recover-early-pick"],
])
def test_invalid_comparison_configuration_fails_before_scene_creation(tmp_path, flags):
    output = tmp_path / "should_not_exist"
    process = subprocess.run([sys.executable, "scripts/multiobject_pickplace_smoke.py",
        "--output-dir", str(output), *flags], capture_output=True, text=True)
    assert process.returncode == 2
    assert not output.exists()
