"""Deterministic standard capacity fixture construction.

Import and --help never connect to a deployment. Explicit invocation drives
the real historical cohort; later published/live cohorts remain separately required.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5


def event_count(index: int) -> int:
    """Number of formal events, never progress observations, in the standard run."""
    if type(index) is not int or not 0 <= index < 100_000:
        raise ValueError("outside standard fixture")
    return 10_000 if index < 10 else 100 if index < 1000 else 99


def run_identity(fixture_id: UUID, seed: int, index: int) -> UUID:
    event_count(index)
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    return uuid5(fixture_id, f"capacity:{seed}:run:{index}")


def run_started_at(index: int, window_end: datetime) -> datetime:
    event_count(index)
    if window_end.tzinfo is None or window_end.utcoffset() is None:
        raise ValueError("window_end requires a timezone")
    end = window_end.astimezone(UTC)
    # Reserve 20 seconds for the hot run's 10,000 millisecond-spaced events.
    if index < 10:
        return end - timedelta(seconds=20)
    start = end - timedelta(days=90)
    return start + (end - timedelta(seconds=20) - start) * ((index - 10) / 99_990)


def main(argv=None):
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Construct the real standard history, published 5000-result batch and additional 10000-step probe on an explicitly owned test deployment."
    )
    parser.add_argument("--workspace-prefix", required=True)
    parser.add_argument("--runs", type=int, required=True)
    parser.add_argument("--events", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Private invocation directory (0700), retained for recovery",
    )
    parser.add_argument(
        "--target-binding",
        type=Path,
        required=True,
        help="Private operator-provisioned exact test deployment binding (0600)",
    )
    args = parser.parse_args(argv)
    if args.runs != 100_000 or args.events != 10_000_000:
        parser.error("standard fixture requires exactly 100000 runs and 10000000 formal events")
    import json

    from scripts.execution_capacity.host import run_host

    end = datetime.now(UTC)
    if args.output.exists():
        end = datetime.fromisoformat(
            json.loads((args.output / "fixture.json").read_text())["window_end"]
        )
    result = run_host(
        args.target_binding,
        args.output,
        workspace_prefix=args.workspace_prefix,
        seed=args.seed,
        window_end=end,
    )
    if result.get("restoration") == "withheld_for_offline_seal":
        from scripts.execution_capacity.seal_handoff import parent_done

        parent_done(args.output, result)


if __name__ == "__main__":
    main()
