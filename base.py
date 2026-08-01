"""Platform adapter interface + the per-order orchestration logic
(eligibility check, batch/sequential routing, partial success, error
handling - spec sections 4/5).

Every concrete platform (Amazon, Flipkart) only has to implement four
methods. process_order() below is what actually walks the order and is
shared by all platforms, so the partial-success/error rules live in one
place instead of being repeated per adapter.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

try:
    from playwright.sync_api import Page
except ImportError:
    Page = object

from ..browser import BrowserSession, screenshot
from ..models import OrderGroup, ReturnOutcome, ReturnStatus, ReturnTask

log = logging.getLogger(__name__)


class PlatformAdapter(ABC):
    name: str = "base"

    def __init__(self, cfg: dict, defaults: dict, session: BrowserSession, run_cfg: dict):
        self.cfg = cfg
        self.defaults = defaults
        self.session = session
        self.run_cfg = run_cfg

    # ---- each platform implements these four ----

    @abstractmethod
    def ensure_logged_in(self, page: "Page") -> None: ...

    @abstractmethod
    def open_order(self, page: "Page", order_id: str) -> bool:
        """Navigate to the order. Returns False if the order can't be found."""

    @abstractmethod
    def detect_flow(self, page: "Page", order: OrderGroup) -> str:
        """Returns 'batch' or 'sequential' for this order."""

    @abstractmethod
    def return_one_item(self, page: "Page", task: ReturnTask) -> ReturnOutcome:
        """Runs the return micro-flow for a single line item."""

    def return_batch(self, page: "Page", tasks: list[ReturnTask]
                      ) -> list[tuple[ReturnTask, ReturnOutcome]]:
        """Only implemented by platforms that actually support a batch flow."""
        raise NotImplementedError

    # ---- shared orchestration, do not override per platform ----

    def process_order(self, order: OrderGroup) -> list[tuple[ReturnTask, ReturnOutcome]]:
        page = self.session.new_page()
        results: list[tuple[ReturnTask, ReturnOutcome]] = []
        try:
            self.ensure_logged_in(page)

            # 1. Eligibility first, per item, before touching the return flow at all.
            eligible: list[ReturnTask] = []
            for task in order.items:
                if not task.window_open():
                    results.append((task, ReturnOutcome(
                        status=ReturnStatus.OUT_OF_WINDOW,
                        note=f"Return window closed on {task.return_window}",
                        needs_review=True,
                    )))
                else:
                    eligible.append(task)
            if not eligible:
                return results

            # 2. Locate the order once for the whole group.
            if not self.open_order(page, order.order_id):
                shot = screenshot(page, self.run_cfg.get("screenshot_dir", "logs"),
                                   f"{order.order_id}_not_found")
                for task in eligible:
                    results.append((task, ReturnOutcome(
                        status=ReturnStatus.FAILED,
                        note=f"Order not found on {self.name}" + (f" ({shot})" if shot else ""),
                        needs_review=True,
                    )))
                return results

            # 3. Decide batch vs sequential for this order.
            flow = self.cfg.get("flow", "auto")
            if flow == "auto":
                flow = self.detect_flow(page, order)
            log.info("[%s] order %s -> %s flow, %d eligible item(s)",
                      self.name, order.order_id, flow, len(eligible))

            if flow == "batch":
                try:
                    results.extend(self.return_batch(page, eligible))
                    return results
                except NotImplementedError:
                    flow = "sequential"
                except Exception as e:
                    log.warning("[%s] batch flow failed (%s) - falling back to sequential",
                                self.name, e)
                    self.open_order(page, order.order_id)
                    flow = "sequential"

            # 4. Sequential: one micro-flow per item, one item's failure
            #    never stops the rest of the order.
            for i, task in enumerate(eligible):
                try:
                    outcome = self.return_one_item(page, task)
                except Exception as e:
                    shot = screenshot(page, self.run_cfg.get("screenshot_dir", "logs"),
                                       f"{task.order_id}_{task.sku}")
                    outcome = ReturnOutcome(
                        status=ReturnStatus.FAILED,
                        note=f"{type(e).__name__}: {e}" + (f" ({shot})" if shot else ""),
                        needs_review=True,
                    )
                results.append((task, outcome))
                self.session.pacing.pause("between_actions")
                if i < len(eligible) - 1:
                    self.open_order(page, order.order_id)  # reset for the next item
            return results
        finally:
            page.close()


class DryRunAdapter(PlatformAdapter):
    """Simulates outcomes so the Excel loop and write-back can be tested
    end to end without a browser or network access."""

    def __init__(self, platform_name: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = f"dry-run:{platform_name}"

    def ensure_logged_in(self, page):
        pass

    def open_order(self, page, order_id):
        return True

    def detect_flow(self, page, order):
        return "sequential"

    def return_one_item(self, page, task: ReturnTask) -> ReturnOutcome:
        fake_id = f"DRY-{abs(hash((task.order_id, task.sku))) % 10**8:08d}"
        return ReturnOutcome(status=ReturnStatus.PLACED, return_id=fake_id,
                              refund_amount="0.00", note="dry run - no platform action taken")

    def process_order(self, order: OrderGroup):
        results = []
        for task in order.items:
            if not task.window_open():
                results.append((task, ReturnOutcome(
                    status=ReturnStatus.OUT_OF_WINDOW,
                    note=f"Return window closed on {task.return_window}",
                    needs_review=True,
                )))
            else:
                results.append((task, self.return_one_item(None, task)))
        return results
