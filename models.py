"""Data shapes shared across the agent.

A ReturnTask is one Excel row (one line item). An OrderGroup is several
ReturnTasks that share an Order ID - this is how the agent knows an order
is multi-item without needing a separate "order" sheet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class ReturnStatus(str, Enum):
    PLACED = "Placed"
    FAILED = "Failed"
    OUT_OF_WINDOW = "Out of Window"
    SKIPPED = "Skipped"


@dataclass
class ReturnTask:
    row_index: int          # Excel row number, for write-back
    platform: str
    order_id: str
    sku: str
    return_window: date | None   # deadline; None means "could not be parsed"

    def window_open(self, today: date | None = None) -> bool:
        today = today or datetime.now().date()
        if self.return_window is None:
            # Can't determine from the sheet -> let the platform be the judge,
            # don't guess eligibility either way.
            return True
        return today <= self.return_window


@dataclass
class ReturnOutcome:
    status: ReturnStatus
    return_id: str = ""
    refund_amount: str = ""
    note: str = ""
    needs_review: bool = False

    @property
    def task_status(self) -> str:
        return "Needs Human Review" if self.needs_review else "Done"


@dataclass
class OrderGroup:
    order_id: str
    platform: str
    items: list[ReturnTask] = field(default_factory=list)
