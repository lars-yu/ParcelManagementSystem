"""Shared types for application ports and adapters."""
from dataclasses import dataclass
from typing import Any
from ..contracts import ParcelError


class Unauthorized(ParcelError):
    pass


class NotFound(KeyError, ParcelError):
    pass


@dataclass(frozen=True)
class Actor:
    username: str
    role: str


@dataclass(frozen=True)
class Page:
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


STATUSES = ('stored', 'exception', 'picked_up', 'returned')
EXCEPTION_TYPES = ('damaged', 'address_error', 'refused', 'lost', 'other')
ROLE_PERMISSIONS = {
    'admin': frozenset({'read', 'receive', 'pickup', 'exception', 'relocate',
                        'audit', 'export', 'dispatch', 'users'}),
    'manager': frozenset({'read', 'receive', 'pickup', 'exception', 'relocate',
                          'audit', 'export', 'dispatch'}),
    'clerk': frozenset({'read', 'receive', 'pickup', 'exception', 'relocate'}),
    'auditor': frozenset({'read', 'audit', 'export'}),
}
