"""Combinatorial arbitrage scanner for logically related Kalshi markets.

Adapted (clean-room-rewired) from kalshi-agent's combinatorial.py — a capability Valedia
lacked. Pure-math, risk-free-when-correct arb across markets with subset/superset or
mutual-exclusion relationships:
  - "BTC above $70k" must cost <= "BTC above $65k" (subset), else threshold arb.
  - Mutually-exclusive outcomes in one event must sum to 100c, else sum-to-100 arb.

SINGLE-SOURCE: fees route through fee_engine.kalshi_fee_per_contract (Valedia's REAL
nonlinear Kalshi fee) — NOT the flat 0.07 the source used. Detection only; route opps
into the existing arb_run/executor path (no execution here).
(c) GOATWORTHY Post LLC.
"""
import os, re, sys, logging
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fee_engine import kalshi_fee_per_contract  # canonical real Kalshi fee (single source)

log = logging.getLogger("valedia.combinatorial")


def _fee_cents(price_cents):
    """Real Kalshi per-contract fee for a price given in CENTS, returned in CENTS."""
    p = max(0.0, min(1.0, float(price_cents) / 100.0))
    return kalshi_fee_per_contract(p) * 100.0


def _extract_threshold(title):
    """(direction, value[, high]) from titles like 'BTC above $70,000', or None."""
    t = title.lower()
    m = re.search(r'(?:above|over|higher than|at least|>=?)\s*\$?([\d,]+\.?\d*)', t)
    if m:
        return ('above', float(m.group(1).replace(',', '')))
    m = re.search(r'(?:below|under|lower than|at most|<=?)\s*\$?([\d,]+\.?\d*)', t)
    if m:
        return ('below', float(m.group(1).replace(',', '')))
    return None  # 'range'/between markets aren't consumed by the threshold scanner — don't parse them


def _extract_event_key(market):
    """Grouping key for related markets: event_ticker, else a topic keyword."""
    ev = market.get("event_ticker", "")
    if ev:
        return ev
    title = market.get("title", "").lower()
    for kw in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "temperature",
               "unemployment", "gdp", "inflation", "s&p", "nasdaq", "fed funds"]:
        if kw in title:
            return f"_topic_{kw}"
    return None


class CombinatorialScanner:
    """Scan groups of related markets for combinatorial arbitrage (detection only)."""

    def group_related_markets(self, markets):
        groups = defaultdict(list)
        for m in markets:
            key = _extract_event_key(m)
            if key:
                groups[key].append(m)
        return {k: v for k, v in groups.items() if len(v) >= 2}

    def scan_threshold_arbs(self, markets):
        above, below = [], []
        for m in markets:
            parsed = _extract_threshold(m.get("title", ""))
            if parsed is None:
                continue
            yes = m.get("yes_ask", m.get("yes_bid", m.get("last_price", 0))) or 0
            if yes <= 0:
                continue
            if parsed[0] == 'above':
                above.append((parsed[1], yes, m))
            elif parsed[0] == 'below':
                below.append((parsed[1], yes, m))

        opps = []
        # "above": higher threshold must cost <= lower threshold (subset).
        above.sort(key=lambda x: x[0])
        for i in range(len(above)):
            for j in range(i + 1, len(above)):
                lo_t, lo_p, lo_m = above[i]
                hi_t, hi_p, hi_m = above[j]
                if hi_p > lo_p:
                    net = (hi_p - lo_p) - (_fee_cents(lo_p) + _fee_cents(hi_p))
                    if net > 0:
                        opps.append({"type": "threshold_arb",
                                     "description": f"'{hi_m.get('title','')[:50]}' > '{lo_m.get('title','')[:50]}' (subset violation)",
                                     "buy_ticker": lo_m.get("ticker", ""), "sell_ticker": hi_m.get("ticker", ""),
                                     "buy_price": lo_p, "sell_price": hi_p,
                                     "profit_cents": round(net, 1), "thresholds": (lo_t, hi_t)})
        # "below": higher threshold must cost >= lower threshold (superset).
        below.sort(key=lambda x: x[0])
        for i in range(len(below)):
            for j in range(i + 1, len(below)):
                lo_t, lo_p, lo_m = below[i]
                hi_t, hi_p, hi_m = below[j]
                if lo_p > hi_p:
                    net = (lo_p - hi_p) - (_fee_cents(lo_p) + _fee_cents(hi_p))
                    if net > 0:
                        opps.append({"type": "threshold_arb",
                                     "description": f"'{lo_m.get('title','')[:50]}' > '{hi_m.get('title','')[:50]}' (superset violation)",
                                     "buy_ticker": hi_m.get("ticker", ""), "sell_ticker": lo_m.get("ticker", ""),
                                     "buy_price": hi_p, "sell_price": lo_p,
                                     "profit_cents": round(net, 1), "thresholds": (lo_t, hi_t)})
        return opps

    def scan_mutual_exclusion(self, markets):
        if len(markets) < 3:
            return []
        total_ask = total_bid = valid = 0
        for m in markets:
            ask = m.get("yes_ask", 0) or 0
            bid = m.get("yes_bid", 0) or 0
            if ask > 0:
                total_ask += ask
                valid += 1
            if bid > 0:
                total_bid += bid
        if valid < 3:
            return []

        opps = []
        ev = markets[0].get("event_ticker", "?")
        # Long: sum of asks < 100c -> buy all for guaranteed $1.
        if 0 < total_ask < 100:
            fees = sum(_fee_cents(m.get("yes_ask", 0) or 0) for m in markets if (m.get("yes_ask", 0) or 0) > 0)
            net = 100 - total_ask - fees
            if net > 0:
                opps.append({"type": "mutual_exclusion_long",
                             "description": f"Event {ev}: sum of asks ({total_ask:.0f}c) < 100c",
                             "event_ticker": ev, "total_cost_cents": round(total_ask + fees, 1),
                             "profit_cents": round(net, 1), "n_markets": valid})
        # Short: sum of bids > 100c -> sell all.
        if total_bid > 100:
            fees = sum(_fee_cents(m.get("yes_bid", 0) or 0) for m in markets if (m.get("yes_bid", 0) or 0) > 0)
            net = total_bid - fees - 100
            if net > 0:
                opps.append({"type": "mutual_exclusion_short",
                             "description": f"Event {ev}: sum of bids ({total_bid:.0f}c) > 100c",
                             "event_ticker": ev, "total_receive_cents": round(total_bid - fees, 1),
                             "profit_cents": round(net, 1), "n_markets": valid})
        return opps

    def scan_all(self, groups):
        out = []
        for key, mkts in groups.items():
            try:
                out.extend(self.scan_threshold_arbs(mkts))
                out.extend(self.scan_mutual_exclusion(mkts))
            except Exception as e:
                log.debug(f"combinatorial scan error for {key}: {e}")
        out.sort(key=lambda x: x.get("profit_cents", 0), reverse=True)
        return out


def scan(markets):
    """Convenience: markets -> sorted arb opportunities."""
    s = CombinatorialScanner()
    return s.scan_all(s.group_related_markets(markets))


if __name__ == "__main__":
    # Self-check: a real subset violation must be found only when it clears the real fee.
    mkts = [
        {"event_ticker": "KXBTC", "ticker": "A", "title": "BTC above $65,000", "yes_ask": 60},
        {"event_ticker": "KXBTC", "ticker": "B", "title": "BTC above $70,000", "yes_ask": 75},  # >65k dearer than 65k -> arb
    ]
    opps = scan(mkts)
    assert opps and opps[0]["type"] == "threshold_arb", opps
    assert opps[0]["buy_ticker"] == "A" and opps[0]["sell_ticker"] == "B", opps
    # Fee sanity: gross 15c minus two real fees (<1.5c each) stays positive.
    assert opps[0]["profit_cents"] > 0
    # No false positive when prices are consistent (monotone).
    ok = [{"event_ticker": "E", "ticker": "A", "title": "BTC above $65,000", "yes_ask": 75},
          {"event_ticker": "E", "ticker": "B", "title": "BTC above $70,000", "yes_ask": 60}]
    assert scan(ok) == []
    print("combinatorial_arb self-check: PASS", opps[0]["profit_cents"], "cents")
