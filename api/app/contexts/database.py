"""SQLAlchemy metadata authority for the greenfield bounded contexts."""

from sqlalchemy.orm import DeclarativeBase


class GreenfieldBase(DeclarativeBase):
    pass
