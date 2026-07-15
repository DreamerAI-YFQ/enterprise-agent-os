"""Shared value types used across packages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

T = TypeVar("T")


@dataclass(frozen=True)
class ID:
    """Typed identifier wrapper."""

    value: UUID


@dataclass(frozen=True)
class TimeRange:
    """Inclusive time range for queries."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class PageResult(Generic[T]):
    """Paginated list result."""

    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def has_next(self) -> bool:
        return self.page * self.page_size < self.total
