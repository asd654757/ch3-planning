"""Identical three-arm harness with the control's duplicate matcher removed."""
import argparse
from pathlib import Path
import shutil

from ch3.revision.fragment_state_cache_compiled import CompiledStateBoundBank
from scripts import benchmark_fragment_state_cache as replay

_archive = replay.archive


def archive(output):
    hashes = _archive(output)
    for p in (Path(__file__).resolve().relative_to(Path.cwd()),
              Path("docs/fragment_state_cache_compiled_protocol_20261117.md"),
              Path("tests/test_fragment_state_cache_compiled.py")):
        hashes[str(p)] = replay.digest(p)
        dest = output / "sources" / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    return hashes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    replay.StateBoundBank = CompiledStateBoundBank
    replay.archive = archive
    replay.comparison(args.output)


if __name__ == "__main__":
    main()
