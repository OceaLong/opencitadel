"""Export the FastAPI OpenAPI document without starting a server."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    repository_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository_root / "api"))
    os.environ.setdefault("ENV", "test")

    from app.main import create_app
    from core.config import DeploymentSettings

    app = create_app(DeploymentSettings(env="test"))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            app.openapi(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
