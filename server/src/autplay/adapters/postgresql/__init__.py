"""PostgreSQL persistence metadata without domain or workflow behavior."""

from .base import Base
from .metadata import metadata

__all__ = ("Base", "metadata")
