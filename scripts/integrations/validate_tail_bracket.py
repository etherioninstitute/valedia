"""validate_tail_bracket.py — Does NO-on-overpriced-tails have a real edge?

Tests the favorite-longshot bias (the structural edge behind the only real
winner, the ETH-bracket NO trades) on REAL settled Kalshi markets.

Method: paginate settled markets; for each, last_price_dollars = the market's
pre-settlement YES price, result = actual outcome. Bucket by YES price and ask:
in each bucket, is the realized YES-rate BELOW the price (i.e. YES overpriced →
buying NO is +EV after fees)? Report EV of buying NO per bucket, liquidity-
filtered. Honest: prints the edge (or its absence) with sample sizes.

Kalshi fee ≈ 0.07 · P · (1-P) per contract (rounded up in practice); we apply
it to both sides so EV is net-of-fees.
"""
import sys, json, time
sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")
import kalshi_auth as K

FEE = lambda p: 0.07 * p * (1.0 - p)          # per-contract fee approx (fraction of $1)
MIN_VOL = 20.0                                # liquidity filter (volume_fp)


def pull_settled(max_markets=6000, page=200):
    out, cursor = [], None
    while len(out) < max_markets:
        ep = f"/markets?limit={page}&status=settled"
        if cursor:
            ep += f"&cursor={cursor}"
        try:
            r = K.get(ep)
        except Exception as e:
            print(f"  pull error: {e}"); break
        ms = r.get("markets", [])
        if not ms:
            break
        for m in ms:
            res = m.get("result")
            if res not in ("yes", "no"):
                continue
            try:
                yp = float(m.get("last_price_dollars") or 0)
                vol = float(m.get("volume_fp") or 0)
            except (TypeError, ValueError):
                continue
            out.append({"ticker": m.get("ticker", ""), "yes_price": yp,
                        "result": 1 if res == "yes" else 0, "vol": vol})
        cursor = r.get("cursor")
        if not cursor:
            break
        time.sleep(0.15)
    return out


def analyze(rows, label=""):
    rows = [r for r in rows if r["vol"] >= MIN_VOL and 0.02 <= r["yes_price"] <= 0.98]
    print(f"\n=== {label}  (liquid settled markets: {len(rows)}) ===")
    if len(rows) < 50:
        print("  too few liquid markets to judge."); return
    buckets = [(0.02, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 0.50),
               (0.50, 0.65), (0.65, 0.80), (0.80, 0.90), (0.90, 0.98)]
    print(f"  {'yes_price':>12} {'n':>5} {'implied_Y':>9} {'realized_Y':>10} "
          f"{'NO_ev/$':>8} {'YES_ev/$':>8}  verdict")
    total_no_ev = total_n = 0
    for lo, hi in buckets:
        b = [r for r in rows if lo <= r["yes_price"] < hi]
        if len(b) < 10:
            continue
        n = len(b)
        implied = sum(r["yes_price"] for r in b) / n           # market P(yes)
        realized = sum(r["result"] for r in b) / n             # actual P(yes)
        # Buy NO at no_price = 1 - yes_price; pays $1 if result==no.
        # EV per $1 staked on NO = P(no)·(1/no_price - 1) - P(yes) - fee/no_price
        no_ev = 0.0
        yes_ev = 0.0
        for r in b:
            yp = r["yes_price"]; nop = 1 - yp
            fee = FEE(yp)
            # NO position, $1 stake:
            if r["result"] == 0:  # no wins
                no_ev += (1.0 - nop - fee) / nop
            else:
                no_ev += -1.0
            # YES position, $1 stake:
            if r["result"] == 1:
                yes_ev += (1.0 - yp - fee) / yp
            else:
                yes_ev += -1.0
        no_ev /= n; yes_ev /= n
        total_no_ev += no_ev * n; total_n += n
        verd = "NO +EV" if no_ev > 0.02 else ("YES +EV" if yes_ev > 0.02 else "~flat")
        print(f"  {lo:.2f}-{hi:.2f}   {n:5d} {implied:9.3f} {realized:10.3f} "
              f"{no_ev:+8.3f} {yes_ev:+8.3f}  {verd}")
    if total_n:
        print(f"  → volume-weighted NO edge across all buckets: {total_no_ev/total_n:+.3f} per $1")
    # The favorite-longshot signature: realized_Y < implied_Y in low buckets.


if __name__ == "__main__":
    print("Pulling settled Kalshi markets (paginated)...")
    rows = pull_settled(max_markets=6000)
    print(f"collected {len(rows)} settled markets with result+price")
    json.dump(rows, open("/tmp/kalshi_settled.json", "w"))
    analyze(rows, "ALL settled markets")
    brackets = [r for r in rows if ("-B" in r["ticker"] or "-T" in r["ticker"])]
    analyze(brackets, "BRACKET markets only (-B/-T)")
