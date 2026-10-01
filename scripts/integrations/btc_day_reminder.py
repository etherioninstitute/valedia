"""BTC Day Reminder — One-time Signal alert for btc.day launch day (2026-04-06).
Date-guarded via signal_verify: the "LIVE today" text is only sent on the event
day (UTC). Later -> one "Report withheld ... STALE" notice, then self-removes
from cron. Earlier -> nothing sent, cron entry kept so it fires on the day.
"""
import sys, os, subprocess
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EVENT_DAY = "2026-04-06T00:00:00+00:00"
TEXT = ("Reminder: btc.day launches today (Block / Jack Dorsey Bitcoin faucet, per its "
        "announcement; not checked by Valedia). Go to btc.day; have Cash App ready with BTC enabled.")


def remove_from_cron():
    try:
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
        lines = [l for l in result.stdout.splitlines() if 'btc_day_reminder' not in l]
        proc = subprocess.Popen(['crontab', '-'], stdin=subprocess.PIPE, text=True)
        proc.communicate('\n'.join(lines) + '\n')
        return True
    except Exception:
        return False


def main(send_fn=None, now=None, cron_cleanup=remove_from_cron):
    import signal_verify as sv
    now = now or datetime.now(timezone.utc)
    if now < sv.parse_ts(EVENT_DAY):
        print(f"btc.day reminder not due until {EVENT_DAY[:10]}; nothing sent")
        return {"status": "not_due", "sent": False}
    res = sv.deliver("btc.day reminder", TEXT,
                     freshness=[sv.fresh("btc.day event date", EVENT_DAY, 86400)],
                     footer=[f"event day {EVENT_DAY[:10]}"], send_fn=send_fn, now=now,
                     dedupe_key="btc_day", dedupe_window_s=30 * 86400)
    print(f"btc.day reminder {res['status']} sent={res['sent']}")
    print("self-removed from cron" if cron_cleanup() else "cron cleanup failed, remove manually")
    return res


if __name__ == "__main__":
    main()
