"""BTC Day Reminder — One-time Signal alert
btc.day launches today (April 6, 2026). Jack Dorsey's Block Bitcoin faucet.
This script self-deletes from cron after firing.
"""
import sys, os, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_send import send

send("btc.day is LIVE today. Free Bitcoin faucet by Jack Dorsey / Block. Go to btc.day now. Have Cash App ready with BTC enabled.")

# Self-remove from cron after firing
try:
    result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    lines = [l for l in result.stdout.splitlines() if 'btc_day_reminder' not in l]
    proc = subprocess.Popen(['crontab', '-'], stdin=subprocess.PIPE, text=True)
    proc.communicate('\n'.join(lines) + '\n')
    print("Reminder sent + self-removed from cron")
except:
    print("Reminder sent (cron cleanup failed, remove manually)")
