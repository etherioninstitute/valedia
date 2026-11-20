"""Writes the data files of the "Valedia Mission Board" page (the owner's live view of goals, to-dos and the path to
live trading). The curated rows below are the lead's reading of docs/MASTER-LIST.md and are edited by hand when it
changes; the backlog counts are read from that file and the collector's counts from its store (status and time only,
never a decision's content). Usage: python tools/mission_board_data.py OUT_DIR "<as-of text>"."""
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# who: system | owner | time (data must arrive) ; status: done | doing | next | queued | owner | watch | failed | blocked
PATH_LIVE = [
    # ---- Track A: the fund book (stocks and ETFs on Webull): closest to a first real order
    ("A", 1, "Order step with fractional shares, practice proof", "system", "done", "Lead", "Merged 17 Nov; two fractional buys and one fractional sale accepted on the paper account."),
    ("A", 2, "Shared-account accounting, slices S1 to S4b", "system", "done", "Lead + builders", "Merged 18 Nov: her book is her own fills; the owner's hand trades no longer stop it; his entries by his own command."),
    ("A", 3, "Shared mode in practice (S4c), live (S4d), alerts and owner page (S4e)", "system", "next", "Lead + builders; Paperclip after its sandbox trial", "Next build. First practice run on the new gate: 19 Nov 06:55 PST, then read its ledger."),
    ("A", 4, "Build B: proof reading, live consent field, first-contact rule", "system", "next", "Lead + builders", "Production allows one fractional order per run until one has filled and reconciled cleanly."),
    ("A", 5, "Practice behaviours still to be seen", "time", "queued", "Daily practice runs", "A sale, a partial fill, an unfilled day order, a cancel answered 'not yet', one clean week."),
    ("A", 6, "Review of the order path by another model family", "system", "next", "Lead through ChatGPT and Grok", "Covered now by the owner's standing yes of 18 Nov; no Claude tokens; to be run before any live key."),
    ("A", 7, "His live key pair, placed by him", "owner", "owner", "Owner", "Two lines in ~/.hermes/secrets/webull-live.env, mode 600. Nobody else places it."),
    ("A", 8, "His three files: confirmation, go-live figures, daily order cap", "owner", "owner", "Owner", "The numbers are his."),
    ("A", 9, "His stake", "owner", "owner", "Owner", "Every fund clears Webull's 5 dollar minimum from about 120 dollars."),
    ("A", 10, "First attended order", "owner", "owner", "Owner", "His word at the time; live commands are run by him at a terminal."),
    # ---- Track B: prediction markets (his first wish): the order side is waiting for a strategy with evidence
    ("B", 1, "Copy Polymarket's leaders, historical test (T47)", "system", "failed", "Done", "Failed 18 Nov at the first stage: mean -1.86 cents a contract, p 0.9265, 1,718 trades. Sealed months stay closed."),
    ("B", 2, "Forward copy test on Webull (screen, then up to 20 wallets, read at 200 closed copies)", "system", "next", "Lead + builders", "Copier merged 18 Nov; the wallet screen is next. Locked document, one read at the end."),
    ("B", 3, "Order-book loop with Elephant-1 (Kalshi)", "time", "watch", "Collector running", "Scored 18 Nov 4 PM to 3 Dec; first fit after; 16 decisions lost so far."),
    ("B", 4, "Public pick lists as a forecaster", "time", "watch", "Hand audit, then test", "Grading waits for the hand audit of 40 picks; test line 12 Dec."),
    ("B", 5, "Reddit chatter against Kalshi prices", "time", "watch", "7 days of audit, then 28", "No evaluation code written yet."),
    ("B", 6, "Event order step: sizing, risk limits, wiring from a passed strategy (T45-2)", "system", "queued", "Paperclip (sandbox trial first), checked by a paid model", "Can be built before a strategy passes, so that a passing idea trades at once."),
    ("B", 7, "Event settlement follow-ups (T45-3)", "system", "queued", "Paperclip after the trial", "A third witness, an unreadable-fee trace, settlements on the owner's page."),
    ("B", 8, "Risk limits for event contracts", "owner", "owner", "Owner", "His numbers; the step cannot be sized without them."),
    ("B", 9, "The ATL practice contract", "owner", "owner", "Owner", "Sold by hand, or his word on what happened to it."),
    ("B", 10, "A strategy with evidence", "system", "blocked", "Tests above", "No prediction-market strategy has passed yet. Nothing goes live without a paper record, his numbers and his go."),
]

OWNER_WAITING = [
    (1, "Stake for the fund book", "Fund book's first real order"),
    (2, "Live key pair, placed by him", "Any live order"),
    (3, "Three files: confirmation, go-live figures, daily order cap", "Any live order"),
    (4, "Risk limits for event contracts", "Event order step (T45-2)"),
    (5, "The ATL practice contract", "Clean practice record for events"),
    (6, "Unattended from the first order, or after a proving run", "Hands-off trading"),
    (7, "Assign one small sandbox issue to the Paperclip Planner", "Starts the pipeline trial"),
    (8, "Leaders' pass: once a day or every hour", "Faster copy test"),
    (9, "OMH in the default profile: keep, or switch off; Kanban review dispatch: on or off", "Clean Hermes set-up"),
    (10, "Leave both LM Studio apps alone until 3 Dec", "Collector's scored period"),
    (11, "MiroFish: load-only test, model choices", "Swarm research"),
]

STATUS_NOW = [
    ("Prediction-market strategies that passed a test", "0", "Master list, section 3", "2026-11-19"),
    ("Copy-leaders historical test", "FAILED: -1.86 cents a contract, p 0.9265, 1,718 trades from 1,175 events", "develop run 8:34 to 9:01 PM, 18 Nov", "2026-11-18"),
    ("Order-book collector, scored period", "1,626 decisions, 1,610 ok, 16 lost, 0 overruns", "Collector store: status and time only, read 1:40 AM 19 Nov", "2026-11-19"),
    ("Practice order step", "Gate at ff3e0bc; first run on it 19 Nov 06:55 PST", "Master list, section 2", "2026-11-19"),
    ("Forward copier tests on main", "140 passed", "Gate run, main 063e87d, 8:12 PM 18 Nov", "2026-11-18"),
    ("Paperclip seats on Hermes harness", "3 seats cut over 10:52 PM 18 Nov; 0 runs; nothing assigned", "Paperclip session report", "2026-11-19"),
    ("FreeLLMAPI coding chain", "End to end OK 7:38 PM 18 Nov (Cloudflare GLM-4.7-flash, first try)", "FreeLLMAPI session report", "2026-11-18"),
    ("Local worker model", "etherion's hs27b is OFF for Hermes (owner: too slow); the Mac mini's arex-2, bonsai-27b and splash are linked but not loaded", "Owner 19 Nov; lms ls", "2026-11-19"),
    ("Claude weekly limit (owner's figure)", "73% used on 18 Nov at 5:55 PM", "Owner's message", "2026-11-18"),
]


def backlog_counts() -> list:
    text = (ROOT / "docs" / "MASTER-LIST.md").read_text()
    out = []
    for tag in ("NOW", "NEXT", "OWNER", "WATCH", "QUEUED", "IDEA", "DONE"):
        out.append((tag, len(re.findall(r"\*\*%s[^*]{0,12}\*\*" % tag, text))))
    return out


def collector_row(asof: str):
    """The collector's counts since the scored period began (2026-11-18 16:00 PST), read from its store: status and time only,
    never a decision's content. Falls back to None when the store cannot be read."""
    import calendar
    t0 = float(calendar.timegm((2026, 11, 19, 0, 0, 0)))
    path = "/Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-trader/memory/orderbook_loop.sqlite"
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5)
        rows = con.execute("select status, count(*), max(decision_ts) from decisions where decision_ts >= ? group by status", (t0,)).fetchall()
        ov = con.execute("select count(*) from events where kind = 'overrun' and ts >= ?", (t0,)).fetchone()[0]
        con.close()
    except sqlite3.Error:
        return None
    total = sum(n for _, n, _ in rows)
    ok = sum(n for st, n, _ in rows if st == "ok")
    last = max((m for _, _, m in rows if m), default=0)
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "none"
    return ("Order-book collector, scored period", "%d decisions, %d ok, %d lost, %d overruns; latest at %s" % (total, ok, total - ok, ov, when),
            "Collector store: status and time only", time.strftime("%Y-%m-%d %H:%M"))


def main() -> int:
    out = Path(sys.argv[1])
    asof = sys.argv[2] if len(sys.argv) > 2 else time.strftime("%Y-%m-%d %H:%M %Z")
    out.mkdir(parents=True, exist_ok=True)
    keys = ("track", "step", "title", "who", "status", "route", "detail")
    files = {
        "path_live.json": [dict(zip(keys, r)) for r in PATH_LIVE],
        "owner_waiting.json": [dict(zip(("n", "item", "unblocks"), r)) for r in OWNER_WAITING],
        "status_now.json": [dict(zip(("metric", "value", "source", "as_of"), r)) for r in
                            [(collector_row(asof) if r[0].startswith("Order-book collector") and collector_row(asof) else r) for r in STATUS_NOW]],
        "backlog_counts.json": [{"tag": t, "count": n} for t, n in backlog_counts()],
    }
    for name, rows in files.items():
        (out / name).write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        print(name, len(rows), "rows")
    print("as of", asof)
    return 0


if __name__ == "__main__":
    sys.exit(main())
