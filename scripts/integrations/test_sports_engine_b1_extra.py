"""B1 hardening checks for sports_engine — GAP FILL for test_sports_engine.py.
Run: python3 test_sports_engine_b1_extra.py

Covers what the existing suite does not exercise: SKIP_HELD in execute_bets, the
should_trade_category gate inside run() (both blocking and fail-open), run()'s
log-persistence filter (only EXECUTED, with fee), the fee-net win branch of
check_settlements, and size_bets regression edges (edge==-10 boundary, MAX_GAMES cap).

No network — every I/O boundary (create_order, get_positions, get, get_balance,
signal_send.send, ftl.record_entry/should_trade_category) is monkeypatched.
"""
import sys, types
import sports_engine as se
import forward_test_ledger as ftl
import kalshi_auth
from test_sports_engine import _game, _bet


def _patch(obj, name, value, patches):
    patches.append((obj, name, getattr(obj, name)))
    setattr(obj, name, value)


def _restore(patches):
    for obj, name, orig in reversed(patches):
        setattr(obj, name, orig)


def _result(status, ticker):
    return {'fav_ticker': ticker, 'fav_title': 'Title', 'fav_price': 0.80, 'contracts': 5,
            'cost': 4.0, 'fee': 0.02, 'profit_if_win': 0.98, 'league': 'NBA', 'exp': '2026',
            'ts': '2026-01-01T00:00:00Z', 'status': status}


# ── 6. size_bets regression: edge<-10 skip boundary + MAX_GAMES cap ──────────

def test_size_bets_edge_skip_boundary():
    g_keep = _game(edge=-10); g_keep['league'] = 'XFL'; g_keep['fav_ticker'] = 'KEEP'
    g_skip = _game(edge=-11); g_skip['league'] = 'XFL'; g_skip['fav_ticker'] = 'SKIP'
    bets = se.size_bets([g_keep, g_skip], bankroll=100.0)
    tickers = {b['fav_ticker'] for b in bets}
    assert 'KEEP' in tickers, "edge == -10 (not < -10) must NOT be skipped"
    assert 'SKIP' not in tickers, "edge == -11 (< -10) must be skipped"


def test_size_bets_max_games_cap():
    games = []
    for i in range(15):
        g = _game(edge=30)
        g['fav_ticker'] = f'G{i}'
        games.append(g)
    bets = se.size_bets(games, bankroll=1000.0)
    assert len(bets) == se.MAX_GAMES, len(bets)
    assert {b['fav_ticker'] for b in bets} == {f'G{i}' for i in range(se.MAX_GAMES)}


# ── 2. execute_bets SKIP_HELD: no order, no ledger record ────────────────────

def test_execute_bets_skip_held():
    patches = []
    calls = {'orders': 0, 'records': []}
    try:
        _patch(se, 'create_order', lambda *a, **k: calls.__setitem__('orders', calls['orders'] + 1), patches)
        _patch(kalshi_auth, 'get_positions',
               lambda: {'market_positions': [{'ticker': 'KXNBAGAME-T', 'position_fp': '5'}]}, patches)
        _patch(se.ftl, 'record_entry', lambda *a, **k: calls['records'].append((a, k)), patches)

        res = se.execute_bets([_bet()], dry_run=False)
        assert res[0]['status'] == 'SKIP_HELD', res[0]
        assert calls['orders'] == 0 and len(calls['records']) == 0, \
            "SKIP_HELD must place no order and record nothing"
    finally:
        _restore(patches)


# ── 3. run(): should_trade_category gate blocks / fail-opens ─────────────────

def test_run_gate_blocks_execution():
    patches = []
    execute_calls = []
    try:
        _patch(se, 'check_settlements', lambda: ([], {}), patches)
        _patch(se, 'get_balance', lambda: {'balance': 100000}, patches)
        _patch(se, 'get_fear_greed', lambda: 50, patches)
        _patch(se, 'get_optimal_bet_pct', lambda: 0.06, patches)
        _patch(se, 'scan_games', lambda: [_game()], patches)
        _patch(se.ftl, 'should_trade_category',
               lambda cat: (False, "VERIFIED recent loss"), patches)
        _patch(se, 'execute_bets', lambda bets, dry_run, **k: execute_calls.append(1) or [], patches)
        _patch(se, 'load_log', lambda: {"bets": [], "stats": {}}, patches)
        _patch(se, 'save_log', lambda l: None, patches)

        report = se.run(dry_run=False)
        assert execute_calls == [], "gate block must prevent execute_bets from running (no orders)"
        assert "CATEGORY GATE" in report and se.CATEGORY in report, report
    finally:
        _restore(patches)


def test_run_gate_fail_open_on_exception():
    patches = []
    execute_calls = []
    try:
        _patch(se, 'check_settlements', lambda: ([], {}), patches)
        _patch(se, 'get_balance', lambda: {'balance': 100000}, patches)
        _patch(se, 'get_fear_greed', lambda: 50, patches)
        _patch(se, 'get_optimal_bet_pct', lambda: 0.06, patches)
        _patch(se, 'scan_games', lambda: [_game()], patches)

        def _raise(cat):
            raise RuntimeError("ledger unavailable")
        _patch(se.ftl, 'should_trade_category', _raise, patches)
        _patch(se, 'execute_bets', lambda bets, dry_run, **k: execute_calls.append(1) or [], patches)
        _patch(se, 'load_log', lambda: {"bets": [], "stats": {}}, patches)
        _patch(se, 'save_log', lambda l: None, patches)

        se.run(dry_run=False)
        assert execute_calls == [1], "gate exception must fail-open (still execute)"
    finally:
        _restore(patches)


# ── 5. run() persistence: only EXECUTED written, with fee ────────────────────

def test_run_persists_only_executed_with_fee():
    patches = []
    saved = {}
    had_signal_module = 'signal_send' in sys.modules
    orig_signal_module = sys.modules.get('signal_send')
    fake_signal = types.ModuleType('signal_send')
    sent = []
    fake_signal.send = lambda msg: sent.append(msg)
    sys.modules['signal_send'] = fake_signal
    try:
        _patch(se, 'check_settlements', lambda: ([], {}), patches)
        _patch(se, 'get_balance', lambda: {'balance': 100000}, patches)
        _patch(se, 'get_fear_greed', lambda: 50, patches)
        _patch(se, 'get_optimal_bet_pct', lambda: 0.06, patches)
        _patch(se, 'scan_games', lambda: [_game()], patches)
        _patch(se.ftl, 'should_trade_category', lambda cat: (True, "ok"), patches)
        results = [_result('EXECUTED', 'T1'), _result('DRY_RUN', 'T2'), _result('SKIP_HELD', 'T3')]
        _patch(se, 'execute_bets', lambda bets, dry_run, **k: results, patches)
        _patch(se, 'load_log', lambda: {"bets": [], "stats": {}}, patches)
        _patch(se, 'save_log', lambda l: saved.setdefault('log', l), patches)

        se.run(dry_run=False)

        logged = saved['log']['bets']
        assert len(logged) == 1, "only the EXECUTED result may be persisted"
        assert logged[0]['fav_ticker'] == 'T1' and logged[0]['status'] == 'EXECUTED'
        assert logged[0]['fee'] == 0.02, "fee must be persisted"
        assert sent, "signal_send.send should fire for a real (non-dry-run) execution"
    finally:
        _restore(patches)
        if had_signal_module:
            sys.modules['signal_send'] = orig_signal_module
        else:
            sys.modules.pop('signal_send', None)


# ── 4. check_settlements: win branch is fee-net (uses stored profit_if_win) ──

def test_check_settlements_win_is_fee_net():
    patches = []
    log = {'bets': [
        {'fav_ticker': 'W1', 'status': 'EXECUTED', 'settled': False,
         'profit_if_win': 0.98, 'cost': 4.0, 'fee': 0.02, 'fav_title': 'winner'},
    ], 'stats': {}}
    try:
        _patch(se, 'load_log', lambda: log, patches)
        _patch(se, 'save_log', lambda l: None, patches)
        _patch(se, 'get', lambda path: {'market': {'status': 'finalized', 'result': 'yes'}}, patches)

        se.check_settlements()
        w = log['bets'][0]
        assert w['settled'] is True and w['won'] is True
        assert abs(w['pnl'] - 0.98) < 1e-9, \
            f"win pnl must equal fee-net profit_if_win, got {w['pnl']}"
    finally:
        _restore(patches)


def run():
    test_size_bets_edge_skip_boundary()
    test_size_bets_max_games_cap()
    test_execute_bets_skip_held()
    test_run_gate_blocks_execution()
    test_run_gate_fail_open_on_exception()
    test_run_persists_only_executed_with_fee()
    test_check_settlements_win_is_fee_net()
    print("sports_engine B1 extra checks: PASS")


if __name__ == "__main__":
    run()
