"""backtest_gate.py — backtest-first gate for edge/threshold/weight changes.

Any change to an edge threshold, category allow/pause set, or per-category size
multiplier must clear THIS gate before it ships: a fee-inclusive replay of the
REAL settlement ledger (models/forward_test_ledger.jsonl — never brain_log.json)
under a BASELINE and a CANDIDATE policy, plus a seeded bootstrap Monte-Carlo.

Offline analysis only: reads the ledger + two policy JSON files, writes nothing,
no network, no orders, no keys.

Policy JSON (unknown keys are rejected so a typo can't silently pass the gate):
  {"name": str, "min_edge": float|null, "categories_allow": [..]|null,
   "categories_block": [..], "size_mult": {"CAT": float}}
Categories are canonicalised with forward_test_ledger.canonical_category.

Fees: a settlement's realized_pnl_net is taken as net when the ledger recorded the
fee (reconcile()/settle_open() semantics). Otherwise (backfill:pnl_tracker rows,
fee_status=unverified_source) realized P&L is treated as GROSS and the nonlinear
Kalshi fee is subtracted as Kalshi charges it: forward_test_ledger.kalshi_fee =
ceil(0.07*C*P*(1-P)) to the next cent per order — never a flat fee. When
price/contracts are unknown the fee is the upper bound 0.07*cost (P->0),
i.e. conservative.

CLI:
  python3 backtest_gate.py --baseline a.json --candidate b.json \
      [--ledger PATH] [--live-only] [--seed 7] [--n-boot 2000] [--min-n 30] \
      [--max-p-worse 0.35] [--max-dd-worse 0.20]
JSON report -> stdout, one-line summary -> stderr.
Exit: 0 PASS, 1 FAIL, 2 INSUFFICIENT_DATA, 3 usage/input error.
(c) GOATWORTHY Post LLC.
"""
import os, sys, json, math, random, argparse
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from forward_test_ledger import canonical_category, _is_non_real_source, kalshi_fee  # noqa: E402

DEFAULT_LEDGER = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                              "models", "forward_test_ledger.jsonl")
DEFAULTS = {"min_n": 30, "max_p_worse": 0.35, "max_dd_worse": 0.20,
            "n_boot": 2000, "seed": 7}
POLICY_KEYS = {"name", "min_edge", "categories_allow", "categories_block", "size_mult",
               "description"}
SETTLED_RESULTS = {"yes", "no", "exit"}
PASS, FAIL, INSUFFICIENT = "PASS", "FAIL", "INSUFFICIENT_DATA"
EXIT_CODES = {PASS: 0, FAIL: 1, INSUFFICIENT: 2}


def _num(x):
    """Finite float or None (bools are not numbers here)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# ── policies ────────────────────────────────────────────────────────────────
def load_policy(obj):
    """Validate + normalise a policy (dict or path to JSON). Raises ValueError."""
    if isinstance(obj, str):
        with open(obj) as f:
            obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError("policy must be a JSON object")
    unknown = set(obj) - POLICY_KEYS
    if unknown:
        raise ValueError(f"unknown policy keys: {sorted(unknown)}")
    name = obj.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("policy.name must be a non-empty string")
    min_edge = obj.get("min_edge")
    if min_edge is not None and _num(min_edge) is None:
        raise ValueError("policy.min_edge must be a number or null")
    allow = obj.get("categories_allow")
    if allow is not None and not isinstance(allow, list):
        raise ValueError("policy.categories_allow must be a list or null")
    block = obj.get("categories_block") or []
    if not isinstance(block, list):
        raise ValueError("policy.categories_block must be a list")
    size = obj.get("size_mult") or {}
    if not isinstance(size, dict):
        raise ValueError("policy.size_mult must be an object")
    mult = {}
    for k, v in size.items():
        m = _num(v)
        if m is None or m < 0:
            raise ValueError(f"policy.size_mult[{k!r}] must be a finite number >= 0")
        mult[canonical_category(k)] = m
    return {"name": name,
            "min_edge": None if min_edge is None else float(min_edge),
            "categories_allow": None if allow is None else sorted({canonical_category(c) for c in allow}),
            "categories_block": sorted({canonical_category(c) for c in block}),
            "size_mult": mult}


def decide(policy, trade):
    """Stake multiplier for one ledger trade under a policy; 0.0 means skip.
    min_edge needs a known edge: a row with no recorded edge/prob is skipped
    (an unverifiable edge cannot clear a threshold)."""
    cat = trade["category"]
    if policy["categories_allow"] is not None and cat not in policy["categories_allow"]:
        return 0.0
    if cat in policy["categories_block"]:
        return 0.0
    if policy["min_edge"] is not None:
        if trade["edge"] is None or trade["edge"] < policy["min_edge"]:
            return 0.0
    return policy["size_mult"].get(cat, 1.0)


# ── ledger -> fee-true trades ───────────────────────────────────────────────
def load_ledger(path):
    """(records, malformed_count). Never raises on bad lines."""
    recs, bad = [], 0
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(r, dict):
                recs.append(r)
            else:
                bad += 1
    return recs, bad


def _edge(entry, sett):
    """Model edge for the side held: recorded `edge`, else P(side) - entry price.
    predicted_prob is P(yes) (reconcile()'s Brier uses outcome=result=='yes')."""
    e = _num(entry.get("edge"))
    if e is not None:
        return e
    pp = _num(entry.get("predicted_prob"))
    if pp is None:
        pp = _num(sett.get("predicted_prob"))
    price = _num(entry.get("entry_price"))
    if pp is None or price is None or not 0 < price < 1:
        return None
    side = str(entry.get("side") or "").lower()
    if side == "yes":
        return pp - price
    if side == "no":
        return (1.0 - pp) - price
    return None


def fee_true_net(entry, sett):
    """(net, fee, fee_basis) for one settlement, or None if it cannot be priced."""
    realized = _num(sett.get("realized_pnl_net"))
    if realized is None:
        return None
    venue = str(entry.get("venue") or sett.get("venue") or "kalshi").lower()
    unverified = sett.get("fee_status") == "unverified_source"
    rec_fee = _num(sett.get("fee"))
    if rec_fee is not None and not unverified:
        return realized, rec_fee, "recorded"          # reconcile(): already net
    ent_fee = _num(entry.get("entry_fee"))
    if not venue.startswith("kalshi") and not unverified:
        return realized, ent_fee or 0.0, "recorded_spot"  # settle_open(): net of fees
    if ent_fee and ent_fee > 0 and not unverified:
        return realized, ent_fee, "recorded_entry"
    # Fee not verified -> realized treated as GROSS; subtract the nonlinear Kalshi fee.
    price = _num(entry.get("entry_price"))
    qty = _num(sett.get("filled_contracts")) or _num(entry.get("contracts"))
    cost = _num(sett.get("cost"))
    if price and 0 < price < 1 and qty and qty > 0:
        fee, basis = kalshi_fee(price, qty), "modeled"   # per-order ceil to the cent
    elif cost and cost > 0 and sett.get("won") is True and realized > 0:
        qty = cost + realized                         # $1 payout per contract
        fee, basis = kalshi_fee(cost / qty, qty), "modeled_inferred"
    elif cost and cost > 0:
        fee, basis = 0.07 * cost, "modeled_bound"     # 0.07*C*P*(1-P) <= 0.07*cost
    else:
        return None
    return realized - fee, fee, basis


def build_trades(records, live_only=False):
    """Join settlements to entries -> (fee-true trades in time order, stats)."""
    stats = Counter()
    entries, order = {}, {}
    for i, r in enumerate(records):
        if r.get("type") == "entry" and isinstance(r.get("id"), str):
            entries.setdefault(r["id"], r)
            order.setdefault(r["id"], i)
    stats["entries"] = len(entries)
    seen, pending, trades = set(), set(), []
    for i, s in enumerate(records):
        if s.get("type") != "settlement":
            continue
        stats["settlements"] += 1
        eid = s.get("entry_id")
        if not isinstance(eid, str):
            if eid is not None:
                stats["malformed_skipped"] += 1
                continue
            eid = f"_orphan_{i}"                     # no entry id: never dedup'd
        if eid in seen:
            stats["duplicate_settlements_ignored"] += 1
            continue
        if str(s.get("result") or "").lower() not in SETTLED_RESULTS:
            pending.add(eid)                         # counted as unsettled below
            continue
        seen.add(eid)
        entry = entries.get(eid, {})
        src_s, src_e = s.get("source"), entry.get("source")
        if "backfill" in str(src_s).lower() or "backfill" in str(src_e).lower():
            src = "backfill"
        elif _is_non_real_source(src_s) or _is_non_real_source(src_e):
            stats["nonreal_excluded"] += 1           # paper/sandbox/shadow/unreconciled
            continue
        else:
            src = "live"
        if live_only and src != "live":
            stats["backfill_excluded"] += 1
            continue
        priced = fee_true_net(entry, s)
        if priced is None:
            stats["malformed_skipped"] += 1
            continue
        net, fee, basis = priced
        stats["fee_basis:" + basis] += 1
        stats["source:" + src] += 1
        trades.append({"id": eid, "ts": str(entry.get("ts") or s.get("logged_at") or ""),
                       "_ord": order.get(eid, i),
                       "category": canonical_category(s.get("strategy") or entry.get("strategy") or "?"),
                       "edge": _edge(entry, s), "net": net, "fee": fee,
                       "fee_basis": basis, "source": src, "won": s.get("won") is True})
    stats["unsettled_excluded"] = len((set(entries) | pending) - seen)
    trades.sort(key=lambda t: (t["ts"], t["_ord"]))
    for t in trades:
        del t["_ord"]
    stats["eligible"] = len(trades)
    return trades, dict(stats)


# ── replay + Monte-Carlo ────────────────────────────────────────────────────
def _max_drawdown(contribs):
    cum = peak = dd = 0.0
    for c in contribs:
        cum += c
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def percentile(vals, q):
    """Linear-interpolated percentile, q in [0,100]."""
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * q / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def replay(trades, policy):
    """Deterministic fee-true replay of the ledger in time order under a policy."""
    contribs, by_src, by_cat = [], {}, {}
    n = wins = 0
    fees = net = 0.0
    for t in trades:
        m = decide(policy, t)
        c = m * t["net"]
        contribs.append(c)
        if m <= 0:
            continue
        n += 1
        wins += 1 if t["won"] else 0
        net += c
        fees += m * t["fee"]
        for bucket, key in ((by_src, t["source"]), (by_cat, t["category"])):
            b = bucket.setdefault(key, {"n": 0, "net": 0.0})
            b["n"] += 1
            b["net"] += c
    for bucket in (by_src, by_cat):
        for b in bucket.values():
            b["net"] = round(b["net"], 4)
    return {"name": policy["name"], "n_taken": n, "wins": wins, "net": round(net, 4),
            "fees": round(fees, 4), "max_drawdown": round(_max_drawdown(contribs), 4),
            "by_source": by_src, "by_category": by_cat}, contribs


def monte_carlo(base_c, cand_c, n_boot, seed):
    """Paired bootstrap: resample the eligible ledger rows with replacement (same draw
    for both policies), so candidate-baseline is compared on identical histories."""
    rng = random.Random(seed)
    n = len(base_c)
    bn, cn, bdd, cdd, diff = [], [], [], [], []
    if n == 0:
        return None
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        b = [base_c[i] for i in idx]
        c = [cand_c[i] for i in idx]
        sb, sc = sum(b), sum(c)
        bn.append(sb); cn.append(sc); diff.append(sc - sb)
        bdd.append(_max_drawdown(b)); cdd.append(_max_drawdown(c))

    def dist(nets, dds):
        return {"net_mean": round(sum(nets) / len(nets), 4),
                "net_p05": round(percentile(nets, 5), 4),
                "net_p50": round(percentile(nets, 50), 4),
                "p_net_lt_0": round(sum(1 for x in nets if x < 0) / len(nets), 4),
                "max_dd_p50": round(percentile(dds, 50), 4),
                "max_dd_p95": round(percentile(dds, 95), 4)}
    return {"baseline": dist(bn, bdd), "candidate": dist(cn, cdd),
            "diff": {"mean": round(sum(diff) / len(diff), 4),
                     "p05": round(percentile(diff, 5), 4),
                     "p50": round(percentile(diff, 50), 4),
                     "p_lt_0": round(sum(1 for x in diff if x < 0) / len(diff), 4)}}


def evaluate(trades, baseline, candidate, min_n=DEFAULTS["min_n"],
             max_p_worse=DEFAULTS["max_p_worse"], max_dd_worse=DEFAULTS["max_dd_worse"],
             n_boot=DEFAULTS["n_boot"], seed=DEFAULTS["seed"]):
    """Pure gate: trades + two normalised policies -> report dict with verdict."""
    base_r, base_c = replay(trades, baseline)
    cand_r, cand_c = replay(trades, candidate)
    mc = monte_carlo(base_c, cand_c, n_boot, seed)
    reasons = []
    if cand_r["n_taken"] < min_n or mc is None:
        verdict = INSUFFICIENT
        reasons.append(f"candidate n_taken={cand_r['n_taken']} < min_n={min_n}")
    else:
        if cand_r["net"] < base_r["net"]:
            reasons.append(f"candidate net {cand_r['net']:+.2f} < baseline {base_r['net']:+.2f}")
        if mc["diff"]["p_lt_0"] > max_p_worse:
            reasons.append(f"P(cand-base<0)={mc['diff']['p_lt_0']:.3f} > {max_p_worse}")
        bdd, cdd = mc["baseline"]["max_dd_p95"], mc["candidate"]["max_dd_p95"]
        if cdd > bdd * (1.0 + max_dd_worse) + 1e-9:
            reasons.append(f"candidate tail max-DD ${cdd:.2f} > baseline ${bdd:.2f} x{1 + max_dd_worse:.2f}")
        verdict = FAIL if reasons else PASS
    return {"verdict": verdict, "reasons": reasons,
            "thresholds": {"min_n": min_n, "max_p_worse": max_p_worse,
                           "max_dd_worse": max_dd_worse, "n_boot": n_boot, "seed": seed},
            "baseline": base_r, "candidate": cand_r, "monte_carlo": mc}


def summary_line(report):
    b, c = report["baseline"], report["candidate"]
    mc = report.get("monte_carlo") or {}
    p = mc.get("diff", {}).get("p_lt_0")
    ls = report.get("ledger_stats", {})
    line = (f"BACKTEST GATE {report['verdict']}: {c['name']} n={c['n_taken']} net=${c['net']:+.2f} "
            f"vs {b['name']} n={b['n_taken']} net=${b['net']:+.2f}; "
            f"P(cand<base)={'n/a' if p is None else f'{p:.3f}'}; "
            f"eligible={ls.get('eligible', '?')} (live={ls.get('source:live', 0)}, "
            f"backfill={ls.get('source:backfill', 0)})")
    if report["reasons"]:
        line += " | " + "; ".join(report["reasons"])
    return line


def run_gate(ledger_path, baseline, candidate, live_only=False, **kw):
    """Load ledger + policies, evaluate, attach ledger stats. Pure w.r.t. the ledger (read-only)."""
    recs, bad = load_ledger(ledger_path)
    trades, stats = build_trades(recs, live_only=live_only)
    stats["records"] = len(recs) + bad
    stats["malformed_skipped"] = stats.get("malformed_skipped", 0) + bad
    report = evaluate(trades, load_policy(baseline), load_policy(candidate), **kw)
    report.update({"tool": "backtest_gate", "ledger": os.path.abspath(ledger_path),
                   "live_only": bool(live_only), "ledger_stats": stats,
                   "fee_fn": "forward_test_ledger.kalshi_fee (ceil(0.07*C*P*(1-P)) per order)"})
    report["summary"] = summary_line(report)
    return report


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # exit 2 is reserved for INSUFFICIENT_DATA
        self.print_usage(sys.stderr)
        sys.stderr.write(f"backtest_gate: error: {message}\n")
        sys.exit(3)


def main(argv=None):
    ap = _Parser(prog="backtest_gate", description="Backtest-first gate (offline, read-only).")
    ap.add_argument("--baseline", required=True, help="baseline policy JSON")
    ap.add_argument("--candidate", required=True, help="candidate policy JSON")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--live-only", action="store_true", help="exclude backfill rows")
    ap.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    ap.add_argument("--n-boot", type=int, default=DEFAULTS["n_boot"])
    ap.add_argument("--min-n", type=int, default=DEFAULTS["min_n"])
    ap.add_argument("--max-p-worse", type=float, default=DEFAULTS["max_p_worse"])
    ap.add_argument("--max-dd-worse", type=float, default=DEFAULTS["max_dd_worse"])
    a = ap.parse_args(argv)
    if a.n_boot < 1 or a.min_n < 1:
        ap.error("--n-boot and --min-n must be >= 1")
    try:
        report = run_gate(a.ledger, a.baseline, a.candidate, live_only=a.live_only,
                          min_n=a.min_n, max_p_worse=a.max_p_worse,
                          max_dd_worse=a.max_dd_worse, n_boot=a.n_boot, seed=a.seed)
    except (OSError, ValueError) as e:
        sys.stderr.write(f"backtest_gate: input error: {e}\n")
        return 3
    print(json.dumps(report, indent=2, sort_keys=True))
    sys.stderr.write(report["summary"] + "\n")
    return EXIT_CODES[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
