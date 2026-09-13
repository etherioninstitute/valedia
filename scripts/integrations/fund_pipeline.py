"""Fund Pipeline — Valedia ASI
Monitors balances across platforms and alerts when funding needed.
Mercury ACH to Coinbase requires manual initiation (API doesn't support outbound ACH).
(c) GOATWORTHY Post LLC.
"""
import os, sys, json, requests
from datetime import datetime, timezone
sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")

def check_funding_needs():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"[FUND PIPELINE] {ts}"]
    
    # Mercury balance
    merc_bal = 0
    try:
        h = {"Authorization": f"Bearer {os.environ.get('MERCURY_API_KEY','')}"}
        from vps_proxy import get_proxies, ensure_tunnel
        ensure_tunnel()
        r = requests.get("https://api.mercury.com/api/v1/accounts", headers=h, proxies=get_proxies(), timeout=15)
        if r.ok:
            for a in r.json().get("accounts",[]):
                b = a.get("availableBalance",0)
                lines.append(f"  Mercury {a.get('name','?')}: ${b:,.2f}")
                if "Checking" in a.get("name",""): merc_bal = b
    except Exception as e:
        lines.append(f"  Mercury error: {e}")
    
    # Coinbase balance
    cb_cash = 0
    try:
        from coinbase_trader import get_balances
        bal = get_balances()
        cb_cash = bal.get("USD",{}).get("balance",0)
        btc_val = bal.get("BTC",{}).get("balance",0) * 70000
        total_cb = cb_cash + btc_val
        lines.append(f"  Coinbase: ${cb_cash:.2f} cash, ~${total_cb:.0f} total")
    except Exception as e:
        lines.append(f"  Coinbase error: {e}")
    
    # Alpaca balance
    alp_cash = 0
    try:
        from alpaca_executor import get_account
        acct = get_account()
        alp_cash = float(acct.get("cash",0))
        lines.append(f"  Alpaca: ${alp_cash:.2f} cash")
    except Exception as e:
        lines.append(f"  Alpaca error: {e}")
    
    total = merc_bal + cb_cash + alp_cash
    lines.append(f"  TOTAL LIQUID: ${total:,.2f}")
    
    # Funding recommendations
    lines.append("")
    lines.append("FUNDING ACTIONS NEEDED:")
    
    if cb_cash < 20 and merc_bal > 50:
        amt = min(merc_bal * 0.4, 100)
        lines.append(f"  >> FUND COINBASE: Transfer ${amt:.0f} from Mercury via ACH (1-3 biz days)")
        lines.append(f"     Mercury routing: 091311229 acct: 202550172397")
        lines.append(f"     Coinbase: Add Mercury as bank, initiate deposit")
    
    if alp_cash < 10 and merc_bal > 50:
        amt = min(merc_bal * 0.3, 75)
        lines.append(f"  >> FUND ALPACA: Transfer ${amt:.0f} from Mercury via ACH")
    
    if cb_cash >= 20 and alp_cash >= 10:
        lines.append(f"  All platforms adequately funded")
    
    # Timing note
    lines.append("")
    lines.append("TIMING: ACH transfers take 1-3 business days.")
    lines.append("  Initiated Mon-Fri before 3PM ET = arrives in 1-2 days.")
    lines.append("  Initiated Fri after 3PM = arrives Tue-Wed.")
    
    return "\n".join(lines)

if __name__ == "__main__":
    print(check_funding_needs())
