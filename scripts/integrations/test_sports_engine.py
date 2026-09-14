#!/usr/bin/env python3
"""B1 hardening checks for sports_engine. Run: python3 test_sports_engine.py
Asserts the ledger-truth properties Sprint D required: fee-true sizing (canonical
Kalshi fee), real fills recorded to the ONE ledger, DRY_RUN never treated as real,
and settlement P&L fee-inclusive on losses. No network — deps are monkeypatched."""
import sports_engine as se
import forward_test_ledger as ftl
import kalshi_auth


def _game(price=0.80, edge=30):
    return {'game_id': 'g', 'league': 'NBA', 'fav_ticker': 'KXNBAGAME-T', 'fav_title': 'X',
            'fav_price': price, 'fav_bid': price - 0.01, 'spread': 0.01, 'dog_ticker': 'd',
            'dog_price': 0.20, 'exp': '2026', 'edge_score': edge, 'edge_reasons': []}


def _bet():
    return {'fav_ticker': 'KXNBAGAME-T', 'fav_title': 'X', 'fav_price': 0.80, 'contracts': 5,
            'cost': 4.0, 'fee': 0.02, 'profit_if_win': 0.98, 'league': 'NBA', 'exp': '2026'}


def run():
    # 1. Sizing is fee-true: profit_if_win == gross - canonical Kalshi fee, and strictly < gross.
    bets = se.size_bets([_game()], bankroll=100.0)
    assert len(bets) == 1, bets
    b = bets[0]
    n = b['contracts']
    fee = ftl.kalshi_fee(0.80, n)
    gross = n * (1.0 - 0.80)
    assert abs(b['fee'] - round(fee, 2)) < 1e-9, b['fee']
    assert abs(b['profit_if_win'] - round(gross - fee, 2)) < 1e-9, b['profit_if_win']
    assert b['profit_if_win'] < gross, "profit must be net of fee"

    # 2. EXECUTED fills record to the ONE ledger under SPORTS; DRY_RUN records nothing / no order.
    calls = {'orders': 0, 'records': []}
    se.create_order = lambda *a, **k: calls.__setitem__('orders', calls['orders'] + 1)
    kalshi_auth.get_positions = lambda: {'market_positions': []}
    se.ftl.record_entry = lambda *a, **k: calls['records'].append((a, k))

    res = se.execute_bets([_bet()], dry_run=False)
    assert res[0]['status'] == 'EXECUTED', res[0]
    assert calls['orders'] == 1 and len(calls['records']) == 1, calls
    assert calls['records'][0][0][0] == se.CATEGORY, "must record under SPORTS category"

    calls['orders'] = 0; calls['records'] = []
    res = se.execute_bets([_bet()], dry_run=True)
    assert res[0]['status'] == 'DRY_RUN', res[0]
    assert calls['orders'] == 0 and len(calls['records']) == 0, "DRY_RUN must not order or record"

    # 3. Settlement skips DRY_RUN entirely; loss P&L is fee-inclusive.
    log = {'bets': [
        {'fav_ticker': 'A', 'status': 'DRY_RUN', 'settled': False, 'profit_if_win': 1.0,
         'cost': 4.0, 'fee': 0.01, 'fav_title': 'dry'},
        {'fav_ticker': 'B', 'status': 'EXECUTED', 'settled': False, 'profit_if_win': 0.98,
         'cost': 4.0, 'fee': 0.02, 'fav_title': 'real'},
    ], 'stats': {}}
    se.load_log = lambda: log
    se.save_log = lambda l: None
    se.get = lambda path: {'market': {'status': 'finalized', 'result': 'no'}}  # B loses
    se.check_settlements()
    a = next(x for x in log['bets'] if x['fav_ticker'] == 'A')
    bb = next(x for x in log['bets'] if x['fav_ticker'] == 'B')
    assert not a.get('settled'), "DRY_RUN must never settle as real"
    assert bb.get('settled') is True, "executed bet should settle"
    assert abs(bb['pnl'] - (-(4.0 + 0.02))) < 1e-9, "loss must include entry fee"

    print("sports_engine B1 checks: PASS")


if __name__ == "__main__":
    run()
