"""Settlement Checker — Valedia ASI
Checks pending bets for settlement. Sends Signal ONLY when profit/loss realized.
(c) GOATWORTHY Post LLC
"""
import sys, os, json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.expanduser("~/.openclaw/workspace/models")

def settle_sports():
    os.environ.setdefault('KALSHI_API_KEY_ID', '86a77487-629d-43d7-a5c0-0058bd8f899a')
    os.environ.setdefault('KALSHI_PRIVATE_KEY_PATH', '/Users/burtonchatelain/.openclaw/kalshi_private_key.pem')
    from kalshi_auth import get

    log_path = os.path.join(MODELS, "sports_engine_log.json")
    with open(log_path) as f:
        log = json.load(f)

    new_settlements = []
    for bet in log['bets']:
        if bet.get('settled') or bet.get('status') not in ('EXECUTED',):
            continue
        ticker = bet.get('fav_ticker', '')
        if not ticker:
            continue
        try:
            r = get(f'/markets/{ticker}')
            m = r.get('market', r)
            result = m.get('result', '')
            if result in ('yes', 'no'):
                won = (result == 'yes')
                pnl = bet['profit_if_win'] if won else -(bet['cost'] + bet.get('fee', 0.0))
                bet['settled'] = True
                bet['result'] = result
                bet['won'] = won
                bet['pnl'] = round(pnl, 2)
                new_settlements.append(bet)
        except:
            pass

    if new_settlements:
        settled = [b for b in log['bets'] if b.get('settled') and b.get('status') == 'EXECUTED']
        wins = sum(1 for b in settled if b.get('won'))
        losses = len(settled) - wins
        net = sum(b.get('pnl', 0) for b in settled)
        log['stats'] = {
            'total_bets': len(settled), 'wins': wins, 'losses': losses,
            'win_rate': round(wins / len(settled) * 100, 1) if settled else 0,
            'net_pnl': round(net, 2),
            'updated': datetime.now(timezone.utc).isoformat(),
        }
        with open(log_path, 'w') as f:
            json.dump(log, f, indent=2)

    return new_settlements, log.get('stats', {})

def format_settled_signal(new_pnl, w, l, total_net, total_record):
    """Keep Profit: prefix (signal-protocol.md). Body must not claim FTL realized P&L."""
    return (f"Profit: {new_pnl:+.2f} ({w}W/{l}L settled) sports-log not FTL\n"
            f"Total: {total_net:+.2f} ({total_record})")

def format_arb_signal(arb):
    return (f"Profit: {arb['net_pnl']:+.2f} ({arb['wins']}W/{arb['losses']}L) "
            f"arb-log not FTL")

def run():
    new_sports, stats = settle_sports()

    if new_sports:
        # Signal: profit realized
        new_pnl = sum(b['pnl'] for b in new_sports)
        w = sum(1 for b in new_sports if b['won'])
        l = len(new_sports) - w
        total_net = stats.get('net_pnl', 0)
        total_record = f"{stats.get('wins',0)}W/{stats.get('losses',0)}L"

        from signal_send import send
        send(format_settled_signal(new_pnl, w, l, total_net, total_record))

    # Also check arb settlements
    try:
        from latency_arb import check_arb_settlements
        arb = check_arb_settlements()
        if arb and arb.get('settled', 0) > 0:
            from signal_send import send
            send(format_arb_signal(arb))
    except:
        pass

if __name__ == "__main__":
    run()
