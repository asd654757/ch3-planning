"""Run unchanged independent attribution with the common compiled matcher."""
from pathlib import Path

from ch3.revision.fragment_state_cache_compiled import CompiledStateBoundBank
from scripts import analyze_fragment_state_cache as analysis

_close = analysis.close


def close(output, hashes, inputs):
    path = Path(__file__).resolve().relative_to(Path.cwd())
    return _close(output, {**hashes, str(path): analysis.digest(path)}, inputs)


if __name__ == "__main__":
    analysis.StateBoundBank = CompiledStateBoundBank
    analysis.COMPARISON = analysis.ROOT / "fragment_state_cache_compiled_comparison_20261117_v1"
    analysis.close = close
    analysis.main()
