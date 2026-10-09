"""Run with the deployment's kernel database credentials; never creates schema."""

import argparse
import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.composition.environment_capacity import activate_environment_capacity
from core.config import load_deployment_settings


async def main():
    parser = argparse.ArgumentParser(
        description="Activate an immutable environment capacity policy revision"
    )
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--expected-revision", required=True, type=int)
    args = parser.parse_args()
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri, pool_size=1, max_overflow=0)
    try:
        sessions = async_sessionmaker(
            engine,
            expire_on_commit=False,
            info={
                "database_authorization_signing_secret": settings.database_authorization_signing_secret,
            },
        )
        policy = await activate_environment_capacity(
            session_factory=sessions, path=args.policy, expected_revision=args.expected_revision
        )
        print(f"Activated environment capacity policy revision {policy.revision}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
