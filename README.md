# Multi-Item Return Automation Agent

A browser agent that reads pending return tasks from an Excel sheet, places
the returns on Amazon / Flipkart, and writes the outcome back into the
sheet for every individual line item. Built with Python, Playwright and
openpyxl.

## What it does

- Reads rows where Task Status is "To Do" / "Pending" from the Excel file,
  grouping rows that share an Order ID so multi-item orders are handled
  together
- Checks each item's return window before touching the browser at all -
  items past their window are marked "Out of Window" and skipped
- Opens the order on the right platform and runs the return micro-flow
  (select item → reason → refund/pickup option → confirm)
- Detects whether the order supports one batch return for all items or
  needs the flow repeated per item, and falls back from batch to
  sequential if the batch flow errors out
- Writes the result (Return ID, Return Status, Refund Amount, Task Status,
  Timestamp, Log) back into the same row immediately after that item is
  attempted - the file is saved after every row, so a crash mid-run never
  loses completed items
- Handles partial success per line item: eligible items go through even
  when others in the same order fail or are ineligible; failed/ineligible
  items are flagged "Needs Human Review", never silently dropped. An
  order only reads as fully done once every one of its rows has a final
  state

## Architecture

![Component overview](docs/architecture.png)

Excel is the task queue and the only state store. `agent/main.py` groups
rows by Order ID and hands each order to the right platform adapter,
which shares eligibility/batch-sequential/partial-success logic through
one base class (`agent/platforms/base.py`) before either adapter talks to
the live site through a shared, persistent `BrowserSession`. Outcomes
flow back to Excel per row, independent of how the rest of the order
turns out (dashed path in the diagram).

## Project structure

```
return_agent/
├── config.yaml              # paths, platform config, pacing, defaults
├── requirements.txt
├── docs/
│   └── architecture.svg / .png   # component diagram
├── data/
│   └── returns_tasks.xlsx   # task file (input + write-back target)
└── agent/
    ├── main.py               # entry point, the read → act → write loop
    ├── models.py             # ReturnTask / OrderGroup / ReturnOutcome
    ├── excel_io.py            # Excel read + per-row write-back
    ├── browser.py              # Playwright session, pacing, human-in-the-loop pauses
    └── platforms/
        ├── base.py            # adapter interface + shared orchestration
        │                        (eligibility, batch/sequential routing,
        │                         partial success, error handling)
        ├── amazon.py           # batch + sequential flows
        └── flipkart.py         # sequential flow, OTP login, page-state detection
```

## Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Running it

```bash
python -m agent.main --create-template   # write a sample task sheet and exit
python -m agent.main --dry-run           # run the full Excel loop, no browser, no live site
python -m agent.main                     # live run against Amazon/Flipkart
```

`--dry-run` is the mode this repo was actually tested in end to end (see
"What's been tested" below) - it exercises the real read/group/eligibility/
write-back code path and substitutes a fake platform response instead of
opening a browser, so the loop, the partial-success handling and the
Excel write-back can all be verified without touching a live site.

A live run:

1. A Chrome window opens (persistent profile in `.browser_profile/`, so
   after the first login you stay signed in and later runs skip login).
2. For Flipkart, the agent fills in the phone number itself; when Flipkart
   asks for the OTP, the run pauses with a terminal prompt - request the
   OTP by phone call and enter it in the browser, then press Enter in the
   terminal to continue. Same pause-and-hand-off pattern for any CAPTCHA.
3. The agent works through each pending row/order and writes results into
   the Excel file as it goes.

### Working with the Excel file

- Columns are found by header name (row 1), so column order doesn't
  matter, but the header names must stay as in `agent/excel_io.py::HEADERS`.
- Only rows with Task Status `To Do` or `Pending` are processed; set a
  row's status back to `Pending` to re-run it.
- Close the file in Excel before running - Excel locks open files and the
  write-back will raise `PermissionError` otherwise.

## Design decisions

**Deterministic automation, not an LLM-driven agent.** Every click is
explicit Playwright code against a `SELECTORS` dict, not a model deciding
what to click. Reasoning: the actions touch real orders and refunds, so
same input should mean same behaviour every run; it's auditable (every
outcome in the Excel log traces to a specific line of code, not "the
model decided to"); and it doesn't depend on an external API key, rate
limit, or model being up. The trade-off is that it breaks if a platform
changes its DOM - mitigated by keeping every selector in one place per
platform (`SELECTORS` at the top of `amazon.py` / `flipkart.py`), so a
site change is a one-file fix, and by screenshotting on failure
(`logs/screenshots/`) so a broken selector is easy to diagnose.

**Eligibility is checked before opening the return flow**, not after a
failed attempt. This keeps "Out of Window" and "Failed" as genuinely
different states in the Log column, and avoids clicking into a return
flow the agent already knows will be rejected.

**Write-back happens per row, right after that row is attempted** rather
than being batched at the end of the run. This is what makes partial
success meaningful: if the process is interrupted on item 5 of 8, items
1-4 are already saved with their real outcome.

**Amazon and Flipkart are separate adapter classes** sharing one base
class (`platforms/base.py`) rather than one generic adapter with
if/else branches, because their flows genuinely differ (Amazon can batch,
Flipkart per the brief is sequential-only, and login/OTP handling is
platform-specific). The shared base class is what actually enforces the
eligibility → open order → batch-or-sequential → per-item outcome
sequence identically for both, so that logic isn't duplicated.

## Bot-detection avoidance

- **Persistent, visible session** - a real (non-headless) Chrome profile
  that keeps cookies/login across runs, so the agent looks like a
  returning signed-in user rather than a fresh anonymous session every
  time (`config.yaml` → `browser.user_data_dir`, `browser.headless`).
- **Randomised pacing** - delays between actions and between tasks are
  drawn from a range, not fixed (`HumanPacing` in `agent/browser.py`),
  and typing goes through Playwright's per-character delay instead of
  pasting values in.
- **One order at a time** - the loop processes orders sequentially, not
  in parallel across many tabs, which is itself a bot signal.
- **OTP and CAPTCHA are never automated.** The agent detects these
  states and pauses for a human (`BrowserSession.wait_for_human`) rather
  than attempting to solve or bypass them - this is both the safer
  choice and the one least likely to look automated.

## What's been tested vs what's assumed

This repo was built and tested in an environment with no network access
to Amazon or Flipkart, so honesty about scope matters more than pretending
otherwise:

- **Tested end to end:** template creation, Excel read + grouping by
  Order ID, eligibility check against return window, dry-run outcome
  writing, per-row write-back (Return ID/Status/Refund/Task
  Status/Timestamp/Log), and partial success within a multi-item order
  (verified: 2 items placed, 1 out-of-window, same order, correctly
  independent). See the commands under "Running it".
- **Not tested against the live sites:** the `SELECTORS` dict in
  `amazon.py` and `flipkart.py` is written from the general shape of each
  platform's returns UI, not captured from an inspected live session.
  Before a real run, open an actual order on each platform, inspect the
  DOM, and update the selectors - the surrounding logic (eligibility,
  batch/sequential routing, partial success, write-back, pacing, human
  hand-off for OTP/CAPTCHA) does not need to change to do this.
- **Flipkart login** uses the phone number given in the brief
  (9205359199, OTP by phone call). The agent fills the phone number and
  hands off to a human for the OTP itself, per the bot-avoidance point
  above.

## Adding a platform

Subclass `PlatformAdapter` in `agent/platforms/base.py` (four methods:
`ensure_logged_in`, `open_order`, `detect_flow`, `return_one_item`, plus
`return_batch` if the platform supports it), register it in `get_adapters()`
in `agent/main.py`, and add a block under `platforms:` in `config.yaml`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `PermissionError ... .xlsx` | The sheet is open in Excel - close it and re-run |
| "No pending tasks found" | All rows are Done/flagged - set Task Status back to `Pending` |
| `RuntimeError: playwright is not installed` | Run `pip install -r requirements.txt && python -m playwright install chromium` (not needed for `--dry-run`) |
| Rows all "Out of Window" | Expected - the return deadline in the sheet is in the past |
| Run pauses and does nothing | Check the terminal - it's waiting for an OTP/CAPTCHA/manual step, then Enter |
