"""Compatibility import for original EPM-017 tests and shared-module consumer."""
from .infrastructure.sqlite_repository import SQLiteParcelRepository

__all__ = ["SQLiteParcelRepository"]
