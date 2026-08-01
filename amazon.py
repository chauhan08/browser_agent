"""Amazon adapter.

Amazon supports both models depending on the order: a single multi-item
return flow when the order page offers a "select items to return"
checklist, and a per-item flow otherwise. detect_flow() checks for the
checklist; if it's not there, we fall back to sequential.

NOTE ON SELECTORS: the CSS selectors below are placeholders written from
the general shape of Amazon's returns UI, not captured from a live
inspected session (no Amazon test account was provided in the brief, and
this environment has no network access to amazon.in). Before the first
real run, open an actual return-eligible order, inspect the DOM, and
update the SELECTORS dict - the rest of the adapter (eligibility, batch/
sequential routing, write-back) does not need to change.
"""
from __future__ import annotations

from .base import PlatformAdapter
from ..browser import BrowserSession
from ..models import OrderGroup, ReturnOutcome, ReturnStatus, ReturnTask

SELECTORS = {
    "orders_search": 'input[name="search"]',
    "order_row": '[data-order-id="{order_id}"]',
    "return_or_replace_btn": 'text="Return or Replace Items"',
    "item_checkbox": 'input[type="checkbox"][data-sku="{sku}"]',
    "multi_item_checklist": '[data-component="returnsItemChecklist"]',
    "reason_dropdown": 'select[name="return-reason"]',
    "refund_option": 'input[value="refund-original-payment"]',
    "confirm_btn": 'button:has-text("Confirm your return")',
    "return_id_text": '[data-component="returnId"]',
    "refund_amount_text": '[data-component="refundAmount"]',
    "captcha_marker": 'text="Enter the characters you see"',
}


class AmazonAdapter(PlatformAdapter):
    name = "amazon"

    def ensure_logged_in(self, page) -> None:
        page.goto(f"{self.cfg['base_url']}/gp/css/order-history")
        self.session.settle(page)
        if page.locator('input[type="password"]').count():
            BrowserSession.wait_for_human(
                "Amazon is asking to log in (and possibly OTP/CAPTCHA)."
            )

    def open_order(self, page, order_id: str) -> bool:
        page.goto(f"{self.cfg['base_url']}/gp/css/order-history")
        self.session.settle(page)
        page.fill(SELECTORS["orders_search"], order_id)
        page.keyboard.press("Enter")
        self.session.settle(page)
        row = page.locator(SELECTORS["order_row"].format(order_id=order_id))
        if row.count() == 0:
            return False
        row.locator(SELECTORS["return_or_replace_btn"]).first.click()
        self.session.settle(page)
        return True

    def detect_flow(self, page, order: OrderGroup) -> str:
        if page.locator(SELECTORS["multi_item_checklist"]).count() > 0:
            return "batch"
        return "sequential"

    def return_batch(self, page, tasks: list[ReturnTask]):
        for task in tasks:
            page.check(SELECTORS["item_checkbox"].format(sku=task.sku))
        page.click('button:has-text("Continue")')
        self.session.settle(page)
        page.select_option(SELECTORS["reason_dropdown"], label=self.defaults["return_reason"])
        page.check(SELECTORS["refund_option"])
        page.click(SELECTORS["confirm_btn"])
        self.session.settle(page)

        if page.locator(SELECTORS["captcha_marker"]).count() > 0:
            BrowserSession.wait_for_human("Amazon is showing a CAPTCHA on the batch return step.")

        return_id = page.locator(SELECTORS["return_id_text"]).inner_text().strip()
        refund_amount = page.locator(SELECTORS["refund_amount_text"]).inner_text().strip()
        # One confirmation, but write-back is still per line item (spec section 4).
        return [(task, ReturnOutcome(status=ReturnStatus.PLACED, return_id=return_id,
                                      refund_amount=refund_amount,
                                      note="Placed via batch return"))
                for task in tasks]

    def return_one_item(self, page, task: ReturnTask) -> ReturnOutcome:
        page.check(SELECTORS["item_checkbox"].format(sku=task.sku))
        page.click('button:has-text("Continue")')
        self.session.settle(page)
        page.select_option(SELECTORS["reason_dropdown"], label=self.defaults["return_reason"])
        page.check(SELECTORS["refund_option"])
        page.click(SELECTORS["confirm_btn"])
        self.session.settle(page)

        if page.locator(SELECTORS["captcha_marker"]).count() > 0:
            BrowserSession.wait_for_human(f"Amazon is showing a CAPTCHA for SKU {task.sku}.")

        return_id = page.locator(SELECTORS["return_id_text"]).inner_text().strip()
        refund_amount = page.locator(SELECTORS["refund_amount_text"]).inner_text().strip()
        return ReturnOutcome(status=ReturnStatus.PLACED, return_id=return_id,
                              refund_amount=refund_amount, note="Placed via sequential return")
