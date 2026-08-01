"""Flipkart adapter.

Flipkart is treated as sequential-only per the brief (config.yaml pins
flow: sequential for this platform, so detect_flow is never actually
asked to guess).

Login is phone number + OTP. The brief's test login is 9205359199, with
the OTP requested by phone call rather than SMS. The agent enters the
phone number itself but never touches the OTP field - that's typed by
the operator in the terminal prompt, same as any CAPTCHA (see README,
bot-avoidance). Once logged in, the persistent browser profile
(config.yaml -> browser.user_data_dir) keeps the session alive, so this
prompt should only appear on the very first run.

NOTE ON SELECTORS: as with Amazon, these are placeholders based on the
general shape of Flipkart's order/return pages, not captured from a live
inspected session - this environment has no network access to
flipkart.com to record real ones. Confirm/update SELECTORS against the
actual test account before the first live run.
"""
from __future__ import annotations

from .base import PlatformAdapter
from ..browser import BrowserSession
from ..models import OrderGroup, ReturnOutcome, ReturnStatus, ReturnTask

SELECTORS = {
    "login_phone_input": 'input[type="text"][autocomplete="tel"]',
    "login_continue_btn": 'button:has-text("Continue")' ,
    "otp_input": 'input[autocomplete="one-time-code"]',
    "otp_submit_btn": 'button:has-text("Verify")',
    "account_menu": 'a[href="/account/orders"]',
    "order_search_input": 'input[placeholder="Search for Orders"]',
    "order_card": '[data-order-id="{order_id}"]',
    "return_btn": 'text="Return"',
    "reason_option": 'label:has-text("{reason}")',
    "refund_pickup_option": 'input[value="refund-to-source"]',
    "confirm_btn": 'button:has-text("Confirm Return")',
    "return_id_text": '[data-testid="returnId"]',
    "refund_amount_text": '[data-testid="refundAmount"]',
    # page-state phrases checked verbatim on the order page before
    # assuming a normal in-window order (spec section 5: log the reason,
    # don't just throw a raw timeout)
    "state_cancelled": 'text=/cancelled/i',
    "state_refunded": 'text=/refund.*(complete|processed)/i',
    "state_not_delivered": 'text=/not.*delivered|out for delivery|in transit/i',
    "state_policy_ended": 'text=/return (window|policy).*(ended|closed|expired)/i',
}

PAGE_STATE_REASONS = [
    ("state_cancelled", "Order already cancelled"),
    ("state_refunded", "Refund already processed"),
    ("state_not_delivered", "Order not yet delivered"),
    ("state_policy_ended", "Return policy window has ended on the platform"),
]


class FlipkartAdapter(PlatformAdapter):
    name = "flipkart"

    def ensure_logged_in(self, page) -> None:
        page.goto(f"{self.cfg['base_url']}/account/login")
        self.session.settle(page)
        if page.locator(SELECTORS["account_menu"]).count() > 0:
            return  # persistent profile already has a live session

        phone = self.cfg.get("login_phone", "9205359199")
        self.session.pacing.type_into(page, SELECTORS["login_phone_input"], phone)
        page.click(SELECTORS["login_continue_btn"])
        self.session.settle(page)

        BrowserSession.wait_for_human(
            f"Flipkart needs the OTP for {phone}. Call {phone} to request it, "
            "then enter it in the browser window."
        )
        # confirm we actually landed in a logged-in state before moving on
        page.wait_for_selector(SELECTORS["account_menu"], timeout=30_000)

    def open_order(self, page, order_id: str) -> bool:
        page.goto(f"{self.cfg['base_url']}/account/orders")
        self.session.settle(page)
        page.fill(SELECTORS["order_search_input"], order_id)
        page.keyboard.press("Enter")
        self.session.settle(page)
        card = page.locator(SELECTORS["order_card"].format(order_id=order_id))
        if card.count() == 0:
            return False
        card.click()
        self.session.settle(page)
        return True

    def detect_flow(self, page, order: OrderGroup) -> str:
        return "sequential"  # Flipkart never gets the batch flow, per brief

    def _ineligible_reason(self, page) -> str | None:
        for key, reason in PAGE_STATE_REASONS:
            if page.locator(SELECTORS[key]).count() > 0:
                return reason
        return None

    def return_one_item(self, page, task: ReturnTask) -> ReturnOutcome:
        reason = self._ineligible_reason(page)
        if reason:
            return ReturnOutcome(status=ReturnStatus.FAILED, note=reason, needs_review=True)

        if page.locator(SELECTORS["return_btn"]).count() == 0:
            return ReturnOutcome(status=ReturnStatus.FAILED,
                                  note="No Return option shown for this item on Flipkart",
                                  needs_review=True)

        page.click(SELECTORS["return_btn"])
        self.session.settle(page)
        page.click(SELECTORS["reason_option"].format(reason=self.defaults["return_reason"]))
        page.check(SELECTORS["refund_pickup_option"])
        page.click(SELECTORS["confirm_btn"])
        self.session.settle(page)

        return_id = page.locator(SELECTORS["return_id_text"]).inner_text().strip()
        refund_amount = page.locator(SELECTORS["refund_amount_text"]).inner_text().strip()
        return ReturnOutcome(status=ReturnStatus.PLACED, return_id=return_id,
                              refund_amount=refund_amount, note="Placed via sequential return")
