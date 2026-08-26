"""Alembic environment.

The database URL is NOT in alembic.ini. It is read at runtime from
``fantabot.config.settings``, so `alembic upgrade head` and the application
can never disagree about which database they mean, and no DSN is committed.

``target_metadata`` comes from ``fantabot.db.models``, which imports every model
module. A model that is not re-exported there is invisible to autogenerate,
which then silently proposes dropping its table.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from fantabot.config import settings
from fantabot.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.fantabot_database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection.

    ``compare_type=True`` so a Numeric(4,2) narrowed to Numeric(3,2) shows up in
    autogenerate instead of passing silently. ``NullPool`` because migrations
    are a one-shot process, not a long-lived service.
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
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
