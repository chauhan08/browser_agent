"""Playwright session + pacing helpers.

Import of playwright is wrapped in try/except so --dry-run and
--create-template work on a machine that hasn't installed it yet.
"""
from __future__ import annotations

import logging
import random
import time
from pathlib import Path

try:
    from playwright.sync_api import BrowserContext, Page, sync_playwright
except ImportError:  # dry-run doesn't need a real browser
    BrowserContext = Page = object
    sync_playwright = None

log = logging.getLogger(__name__)


class HumanPacing:
    """Randomised delays instead of fixed ones - see README, bot-avoidance."""

    def __init__(self, cfg: dict):
        self.between_actions = tuple(cfg.get("between_actions", [1.5, 4.0]))
        self.between_tasks = tuple(cfg.get("between_tasks", [4.0, 9.0]))
        self.typing_delay_ms = tuple(cfg.get("typing_delay_ms", [60, 160]))

    def pause(self, kind: str = "between_actions") -> None:
        lo, hi = getattr(self, kind)
        time.sleep(random.uniform(lo, hi))

    def type_into(self, page: "Page", selector: str, text: str) -> None:
        page.click(selector)
        lo, hi = self.typing_delay_ms
        page.type(selector, text, delay=random.uniform(lo, hi))


class BrowserSession:
    """One persistent Chrome context shared across platforms for the run.

    A persistent user-data dir means cookies/login survive between runs -
    after the first successful login the agent looks like a returning
    signed-in user, not a fresh anonymous session every time.
    """

    def __init__(self, cfg: dict, pacing: HumanPacing):
        self.cfg = cfg
        self.pacing = pacing
        self._pw = None
        self.context: "BrowserContext | None" = None

    def __enter__(self) -> "BrowserSession":
        if sync_playwright is None:
            raise RuntimeError("playwright is not installed - run: pip install -r requirements.txt "
                                "&& python -m playwright install chromium")
        self._pw = sync_playwright().start()
        profile = Path(self.cfg.get("user_data_dir", ".browser_profile")).resolve()
        profile.mkdir(parents=True, exist_ok=True)
        self.context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=self.cfg.get("headless", False),
            channel=self.cfg.get("channel", "chrome"),
            viewport={"width": 1366, "height": 824},
        )
        return self

    def __exit__(self, *exc) -> None:
        if self.context:
            self.context.close()
        if self._pw:
            self._pw.stop()

    def new_page(self) -> "Page":
        page = self.context.new_page()
        page.set_default_timeout(20_000)
        return page

    def settle(self, page: "Page") -> None:
        try:
            page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass
        self.pacing.pause("between_actions")

    @staticmethod
    def wait_for_human(reason: str) -> None:
        """Blocks for a manual step (OTP, CAPTCHA, anything unexpected).
        These are never automated - see README section 7."""
        log.warning("HUMAN STEP REQUIRED: %s", reason)
        input(f"\n>>> {reason}\n>>> Do it in the browser window, then press Enter here... ")


def screenshot(page: "Page", directory: str, label: str) -> str:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    safe_label = label[:40].replace(" ", "_").replace("/", "-")
    path = d / f"{int(time.time())}_{safe_label}.png"
    try:
        page.screenshot(path=str(path))
        return str(path)
    except Exception:
        return ""
