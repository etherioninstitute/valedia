"""Portfolio Monitor — 3pm EOD broker snapshot. LOG ONLY: prints to the cron log
(cron_runner_mac.sh never sends to Signal); not a Signal sender.
Reports broker balances/positions for Mercury, Alpaca, Kraken, OKX. Every figure is
a live broker read stamped with its read time; position P&L is UNREALIZED; missing
fields print "n/a" (never a guessed number). Money via signal_verify.money (round
half away, no -$0.00, real minus); missing / None / NaN / garbage -> "n/a", never $0.00.
"""
import os, sys, json, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _m(x, signed=False):
    """Broker money field -> "$1,234.50" / "−$5.00"; unparseable -> "n/a"."""
    import signal_verify as sv
    v = sv.num(x)
    return "n/a" if v is None else sv.money(v, signed=signed)


def fmt_alpaca(data, positions, paper):
    data = data if isinstance(data, dict) else {}
    positions = positions if isinstance(positions, list) else []
    pos_str = f"{len(positions)} positions"
    if positions:
        pos_str += ": " + ", ".join(
            f"{p.get('symbol', 'n/a')} {p.get('qty', 'n/a')}@{_m(p.get('current_price'))} "
            f"(unrealized P&L:{_m(p.get('unrealized_pl'), signed=True)})"
            for p in positions[:5] if isinstance(p, dict))
    mode = "PAPER" if paper else "LIVE"
    return (f"Alpaca ({mode}, broker): {_m(data.get('equity'))} equity | {_m(data.get('cash'))} cash | "
            f"{_m(data.get('buying_power'))} buying power | {pos_str} | "
            f"Status: {data.get('status', 'n/a')}")


def fmt_mercury(accounts):
    parts = [f"{a.get('kind', 'n/a')}: {_m(a.get('availableBalance'))}"
             for a in (accounts or []) if isinstance(a, dict)]
    return "Mercury: " + " | ".join(parts) if parts else "Mercury: No accounts"


def fmt_kraken(bal):
    import signal_verify as sv
    if not isinstance(bal, dict):
        return f"Kraken: {bal}"
    parts = [f"{k}: {'n/a' if sv.num(v) is None else v}" for k, v in bal.items()
             if sv.num(v) is None or sv.num(v) > 0]
    return "Kraken: " + " | ".join(parts[:8]) if parts else "Kraken: Empty"

def check_alpaca():
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    try:
        from alpaca_executor import alpaca_is_live
        paper = not alpaca_is_live()
    except Exception:
        env = os.environ.get("ALPACA_PAPER")
        paper = False if env is None or str(env).strip() == "" else str(env).strip().lower() in ("1", "true", "yes", "on")
    base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    if not key:
        return "Alpaca: No API key configured"
    try:
        req = urllib.request.Request(f"{base}/v2/account")
        req.add_header("APCA-API-KEY-ID", key)
        req.add_header("APCA-API-SECRET-KEY", secret)
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        req2 = urllib.request.Request(f"{base}/v2/positions")
        req2.add_header("APCA-API-KEY-ID", key)
        req2.add_header("APCA-API-SECRET-KEY", secret)
        positions = json.loads(urllib.request.urlopen(req2, timeout=10).read())
        return fmt_alpaca(data, positions, paper)
    except Exception as e:
        return f"Alpaca: [API ERROR] {e}"

def check_mercury():
    try:
        from mercury_client import get_accounts
        return fmt_mercury(get_accounts())
    except Exception as e:
        return f"Mercury: [API ERROR] {e}"

def check_kraken():
    try:
        from kraken_client import get_balance
        return fmt_kraken(get_balance())
    except Exception as e:
        return f"Kraken: [API ERROR] {e}"

def check_okx():
    try:
        from okx_client import OKXClient
        return OKXClient().portfolio_summary()
    except Exception as e:
        return f"OKX: [API ERROR] {e}"

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"PORTFOLIO STATUS ({now}) [broker reads, log only — not sent to Signal]", ""]
    lines.append(check_mercury())
    lines.append(check_alpaca())
    lines.append(check_kraken())
    lines.append(check_okx())
    lines.append("")
    lines.append("All platforms checked (errors shown inline). Next run: next scheduled scan.")
    return "\n".join(lines)

if __name__ == "__main__":
    print(main())
