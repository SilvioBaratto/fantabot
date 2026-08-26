"""Declarative base and mixins for every fantabot table.

The ``naming_convention`` is not decoration. Without it SQLAlchemy emits
constraints with server-generated names, Alembic autogenerate writes
``op.drop_constraint(None, ...)``, and ``alembic downgrade base`` fails on the
constraint it cannot name — which is SPEC success criterion 4. It also cannot be
retrofitted once migrations exist without rewriting them, so it lands here, in
the first schema commit, rather than later.

SPEC's Code Style section gives this file verbatim without a naming convention.
Adding one is a deliberate departure from that snippet.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeEngine

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every fantabot table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict[Any, TypeEngine[Any]]] = {
        datetime: DateTime(timezone=True)
    }


class TimestampMixin:
    """``created_at``/``updated_at``, both server-defaulted.

    Server defaults rather than Python defaults, so a row written by a raw
    ``INSERT`` from psql or Adminer gets the same timestamps as one written
    through the ORM.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
