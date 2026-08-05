"""Actuator CLI. Streamable HTTP is default; stdio must be explicitly enabled.

Mirrors ops-collector/src/opencitadel_ops_collector/main.py.
"""
from __future__ import annotations

import argparse

from .config import ActuatorSettings
from .server import create_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenCitadel registered-write Ops Actuator MCP")
    parser.add_argument("--transport", choices=("streamable-http", "stdio"), default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = ActuatorSettings()
    transport = args.transport or settings.transport
    if transport == "stdio" and not settings.allow_stdio:
        raise SystemExit("stdio transport is disabled; set OPS_ACTUATOR_ALLOW_STDIO=true for development")
    create_server(settings).run(transport=transport)


if __name__ == "__main__":
    main()
