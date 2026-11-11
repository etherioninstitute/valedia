"""Fund Management Workflow — Abundance Agent
Implements the 70/30 profit locking rule and trade sizing.
"""
import os, sys, socket, json, requests
from datetime import datetime, timezone

sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")

# Force IPv4 for Mercury
_orig = socket.getaddrinfo
def _v4(*args, **kwargs):
    res = _orig(*args, **kwargs)
    return [r for r in res if r[0] == socket.AF_INET] or res
socket.getaddrinfo = _v4

STATE_FILE = "/Users/burtonchatelain/.openclaw/workspace/fund_state.json"

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {
            "initial_capital": 80.00,
            "realized_profits": 0.00,
            "locked_profits": 0.00,
            "trades_today": 0,
            "daily_risk_used": 0.00,
            "last_reset": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "trade_log": []
        }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def get_mercury_balance():
    key = os.environ.get("MERCURY_API_KEY", "")
    if not key:
        return 0
    r = requests.get("https://api.mercury.com/api/v1/accounts",
                     headers={"Authorization": f"Bearer {key}"})
    if r.status_code == 200:
        for a in r.json().get("accounts", []):
            if a.get("kind") == "checking":
                return a.get("availableBalance", 0)
    return 0

def calculate_position_size(state, confidence=0.7):
    """Calculate max position size based on rules."""
    balance = get_mercury_balance()
    daily_limit = balance * 0.05  # 5% daily risk
    per_trade = balance * 0.02    # 2% per trade
    remaining_risk = daily_limit - state.get("daily_risk_used", 0)
    
    # Scale by confidence
    size = min(per_trade, remaining_risk) * confidence
    return max(0, round(size, 2))

def log_trade(state, trade_type, asset, amount, price, result=None):
    """Log a trade."""
    trade = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": trade_type,
        "asset": asset,
        "amount": amount,
        "price": price,
        "result": result
    }
    state["trade_log"].append(trade)
    state["trades_today"] += 1
    state["daily_risk_used"] += abs(amount)
    
    if result and result > 0:
        state["realized_profits"] += result
        lock = result * 0.70
        state["locked_profits"] += lock
    
    save_state(state)
    return trade

def daily_reset(state):
    """Reset daily counters."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("last_reset") != today:
        state["trades_today"] = 0
        state["daily_risk_used"] = 0.00
        state["last_reset"] = today
        save_state(state)
    return state

def fund_report():
    """Generate fund management report."""
    state = load_state()
    state = daily_reset(state)
    balance = get_mercury_balance()
    
    tradeable = balance * 0.30
    locked = balance * 0.70
    daily_limit = balance * 0.05
    remaining = daily_limit - state.get("daily_risk_used", 0)
    per_trade = balance * 0.02
    
    lines = [
        "Fund Status",
        f"  Mercury Balance: ${balance:,.2f}",
        f"  Tradeable (30%): ${tradeable:,.2f}",
        f"  Locked (70%): ${locked:,.2f}",
        f"  Daily Risk Limit (5%): ${daily_limit:,.2f}",
        f"  Risk Used Today: ${state.get('daily_risk_used', 0):,.2f}",
        f"  Remaining Risk: ${remaining:,.2f}",
        f"  Max Per Trade (2%): ${per_trade:,.2f}",
        f"  Trades Today: {state.get('trades_today', 0)}",
        f"  Total Realized P&L: ${state.get('realized_profits', 0):,.2f}",
        f"  Locked Profits: ${state.get('locked_profits', 0):,.2f}",
    ]
    return "\n".join(lines)

if __name__ == "__main__":
    print(fund_report())
