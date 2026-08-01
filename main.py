"""Entry point - the loop described in spec section 3:

  read pending row -> open platform -> place return -> capture
  id/status/refund -> write back per line item -> Done / Needs human review

Run:
    python -m agent.main --create-template   # generate a sample task sheet
    python -m agent.main --dry-run           # test the whole loop, no browser
    python -m agent.main                     # live run
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from .browser import BrowserSession, HumanPacing
from .excel_io import ExcelStore, create_template
from .models import ReturnOutcome, ReturnStatus
from .platforms.base import DryRunAdapter


def get_adapters() -> dict:
    """Imported lazily so --dry-run / --create-template work even before
    playwright is installed."""
    from .platforms.amazon import AmazonAdapter
    from .platforms.flipkart import FlipkartAdapter
    return {"amazon": AmazonAdapter, "flipkart": FlipkartAdapter}


def setup_logging(log_file: str) -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(log_file, encoding="utf-8")],
    )


def run(cfg: dict) -> int:
    log = logging.getLogger("agent")
    store = ExcelStore(cfg["excel"]["path"], cfg["excel"].get("sheet", "Tasks"))
    orders = store.pending_by_order()
    if not orders:
        log.info("No pending tasks found (Task Status = 'To Do' / 'Pending'). Nothing to do.")
        return 0

    total_items = sum(len(o.items) for o in orders)
    log.info("Found %d pending line item(s) across %d order(s).", total_items, len(orders))

    max_tasks = cfg["run"].get("max_tasks_per_run", 0) or total_items
    dry_run = cfg["run"].get("dry_run", False)
    pacing = HumanPacing(cfg.get("pacing", {}))
    processed = 0

    def handle(make_adapter) -> None:
        nonlocal processed
        for order in orders:
            if processed >= max_tasks:
                return
            adapter = make_adapter(order.platform)
            if adapter is None:
                for task in order.items:
                    store.write_outcome(task, ReturnOutcome(
                        status=ReturnStatus.SKIPPED,
                        note=f"No adapter registered for platform '{order.platform}'",
                        needs_review=True,
                    ))
                    processed += 1
                continue

            for task in order.items:
                store.mark_in_progress(task)

            # process_order() gives one final outcome per line item -
            # partial success (spec section 5) falls out of writing each
            # one back independently, right here.
            for task, outcome in adapter.process_order(order):
                store.write_outcome(task, outcome)
                processed += 1
            pacing.pause("between_tasks")

    if dry_run:
        log.info("DRY RUN - no browser will be opened, no live platform action taken.")
        handle(lambda platform: DryRunAdapter(platform, {}, cfg.get("defaults", {}), None, cfg["run"]))
    else:
        adapters = get_adapters()
        with BrowserSession(cfg.get("browser", {}), pacing) as session:
            def make(platform: str):
                cls = adapters.get(platform)
                if cls is None:
                    return None
                return cls(cfg["platforms"].get(platform, {}), cfg.get("defaults", {}),
                            session, cfg["run"])
            handle(make)

    log.info("Run complete: %d line item(s) processed.", processed)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-item return automation agent")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="simulate outcomes, no browser")
    parser.add_argument("--create-template", action="store_true",
                         help="write a sample task sheet and exit")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if args.dry_run:
        cfg["run"]["dry_run"] = True
    setup_logging(cfg["run"].get("log_file", "logs/agent.log"))

    if args.create_template:
        soon = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        past = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        path = create_template(cfg["excel"]["path"], sample_rows=[
            ["amazon", "404-1234567-1234567", "USB-C Cable 1m", soon, "", "", "", "To Do", "", ""],
            ["amazon", "404-1234567-1234567", "Wireless Mouse", soon, "", "", "", "To Do", "", ""],
            ["amazon", "404-1234567-1234567", "Laptop Sleeve 14in", past, "", "", "", "To Do", "", ""],
            ["flipkart", "OD4345678901234560", "Bluetooth Headphones", soon, "", "", "", "To Do", "", ""],
            ["flipkart", "OD4345678901234560", "Phone Case - Black", soon, "", "", "", "To Do", "", ""],
        ])
        print(f"Template created: {path}")
        return 0

    return run(cfg)


if __name__ == "__main__":
    sys.exit(main())
