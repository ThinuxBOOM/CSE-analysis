"""
P3 capture scheduler: decides WHAT capture work is due and runs it through the frozen P2 capture layer.

    python -m worker.scheduler run            # one wake-up (systemd: cse-capture-scheduler.timer)
    python -m worker.scheduler status         # read-only

systemd only WAKES the scheduler (every 15 minutes and shortly after boot). PostgreSQL holds the business schedule:
the owner-approved settings (armed or not), one work item per Colombo trading date, the item state history and the
wake-up lease ledger (migration 0014). Each wake-up, under P2's global capture lock:

  1. discover   weekday trading dates from the armed start date to today (Colombo) that have no work item yet
  2. reconcile  every open item, oldest first, against P2's own run evidence; finalise items whose capture window
                has closed WITHOUT contacting CSE (missed / reprocessed from the archive / not_applicable)
  3. capture    at most ONE capture action (P2 resume of the item's run, or a new P2 run), only when armed, due,
                inside the window, within the daily request budget and with no unacknowledged G-1 block

Everything that touches CSE is P2's code, unchanged: one request at a time, >= 1.5 s apart, bounded backoff, circuit
breaker, stop on blocks, identifiable User-Agent, spool first. The scheduler never makes an HTTP request itself and
never derives a trading date from the UTC calendar: dates are Colombo dates (fixed +05:30), and P2's E1 evidence must
show the date's own session before anything is derived.

G-1: CSE data use is an owner-accepted risk, NOT CSE authorization (docs/governance/G-1_CSE_DATA_USE.md). Arming is
an owner decision recorded in the database; nothing here deletes, purges or resets anything.
"""
TOOL_VERSION = "p3.scheduler.1"
