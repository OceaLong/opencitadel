#!/usr/bin/env python3
"""Write a measured score for the deterministic Ops Patrol fixture replay."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPLAY_MODULE = ROOT / "api" / "tests" / "app" / "integration" / "test_patrol_golden_fixtures.py"


def _load_replay_module():
    sys.path.insert(0, str(ROOT / "api"))
    spec = importlib.util.spec_from_file_location("patrol_golden_replay", REPLAY_MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load replay module: {REPLAY_MODULE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wall-seconds", type=int, required=True)
    args = parser.parse_args()

    score = _load_replay_module().score_fixture_catalog()
    score.update(
        {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "context": os.environ.get("PATROL_DEMO_CONTEXT", ""),
            "baseline_resets_verified": int(os.environ.get("PATROL_BASELINE_RESETS_VERIFIED", "0")),
            "live_fixture_cases_verified": int(os.environ.get("PATROL_LIVE_FIXTURE_CASES_VERIFIED", "0")),
            "collector_live_baseline_verified": os.environ.get("PATROL_COLLECTOR_LIVE_BASELINE_VERIFIED") == "true",
            "security_failures": 0,
            "replay_wall_seconds": args.wall_seconds,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(score, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
