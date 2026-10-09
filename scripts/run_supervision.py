"""Run an explicit adapter session; default factory is mock-only, never an API call."""
import argparse
from importlib import import_module
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ch3.supervision.runtime import JsonlJournal, Session, run_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factory", default="ch3.supervision.demo:build_session",
                        help="trusted local module:function returning Session")
    parser.add_argument("--max-observations", type=int, default=20)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.max_observations < 1 or ":" not in args.factory:
        parser.error("positive observation budget and module:function factory required")
    if args.output.exists():
        parser.error("output already exists; choose a new journal path")
    module, name = args.factory.rsplit(":", 1)
    with JsonlJournal(args.output) as journal:
        session = getattr(import_module(module), name)()
        if not isinstance(session, Session):
            raise TypeError("factory must return Session")
        result = run_session(session, max_observations=args.max_observations, emit=journal.emit)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["confirmed_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
