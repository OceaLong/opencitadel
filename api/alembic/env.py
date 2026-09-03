from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context
from app.infrastructure.models.registry import model_metadata
from app.infrastructure.security.db_authorization import (
    configure_sync_system_authorization,
)
from core.config import (
    load_deployment_settings,
    sqlalchemy_sync_migration_database_uri,
)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

settings = config.attributes.get("deployment_settings") or load_deployment_settings()
config.set_main_option(
    "sqlalchemy.url",
    sqlalchemy_sync_migration_database_uri(settings),
)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = model_metadata

# Tables managed by raw SQL inside migrations (not by ORM metadata); exclude
# them from autogenerate/`alembic check` so they are never flagged as drift.
_RAW_SQL_TABLES = frozenset({"execution_authorization_secrets"})


def _include_object(object_, name, type_, reflected, compare_to):
    return not (type_ == "table" and name in _RAW_SQL_TABLES)


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    # The migrations depend on live GUCs (app.rls_signing_secret and the two
    # runtime-role settings) that must never be baked into generated SQL files
    # -- the signing secret would leak in plaintext. Offline SQL generation is
    # therefore unsupported; run migrations online (python -m app.migrate).
    raise NotImplementedError(
        "offline (--sql) migration generation is unsupported: migrations "
        "require session GUCs (app.rls_signing_secret et al.) that must not "
        "be emitted into SQL scripts; run them online via python -m app.migrate"
    )


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_include_object,
        )

        with context.begin_transaction():
            connection.execute(
                text("SELECT set_config('app.runtime_database_role', :value, false)"),
                {"value": "opencitadel_execution_api"},
            )
            connection.execute(
                text("SELECT set_config('app.execution_runtime_role', :value, false)"),
                {"value": "opencitadel_execution_kernel"},
            )
            connection.execute(
                text("SELECT set_config('app.rls_signing_secret', :value, false)"),
                {"value": settings.database_authorization_signing_secret},
            )
            configure_sync_system_authorization(
                connection,
                actor="alembic-migration",
                signing_secret=settings.database_authorization_signing_secret,
            )
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
