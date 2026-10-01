"""Kalshi Opportunity Alert — Cron Wrapper (7am brief)
Scans Kalshi, prints the full opportunity report to the cron log, and — only if
there is a positive modeled upside — sends Burton a short VERIFIED Signal brief:
  Invested          = Kalshi open-position cost (broker, re-read to verify)
  Modeled if-all-win = sum of contracts x fee-net edge for the top unfenced picks
                       that fit cash. A best case if every pick wins — NOT profit,
                       NOT expected value, NOT fills.
Replaces kalshi_executor.send_opportunity_alert() for the cron path (that
legacy path labelled the all-win sum "Estimated profit" and sent unverified).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STRATS = ["ARBITRAGE", "EXPIRY_SCALP", "NO_HARVEST", "SPORTS_FAVORITE", "MACRO_EDGE"]


def _fenced(ke, o):
    try:
        return bool(ke._cfg is not None and ke._cfg.live_exec_fenced(o.get("strategy"),
                                                                     ticker=o.get("ticker")))
    except Exception:
        return False


def modeled_picks(ke, state, opps):
    """Same pick order as opportunity_report; unfenced dry-run sims that fit cash."""
    remaining = min(max(0.0, float(state.get("balance") or 0)),
                    max(0.0, float(state.get("max_deployable") or 0)))
    picks = []
    for strat in STRATS:
        for o in [x for x in opps if x.get("strategy") == strat][:5]:
            if _fenced(ke, o):
                continue
            sim = ke.execute_trade(o, state, dry_run=True)
            cost = float(sim.get("total_cost") or 0)
            if sim.get("status") != "DRY_RUN" or cost <= 0 or cost > remaining + 1e-9:
                continue
            remaining -= cost
            picks.append({"ticker": o["ticker"], "contracts": sim["contracts"],
                          "edge": float(o["edge"]), "cost": cost,
                          "upside": float(sim.get("potential_profit") or 0)})
    return picks


def brief(ke=None, get_positions=None, send_fn=None, now=None, state=None, opps=None):
    """Never raises: any error (scan, sim, broker, verifier) -> one withheld notice."""
    import signal_verify as sv
    kind, now = "7am brief", now or sv.utcnow()
    try:
        return _brief(sv, kind, ke, get_positions, send_fn, now, state, opps)
    except Exception as e:
        return sv.withhold(kind, "brief failed", f"{type(e).__name__}: {e}", send_fn, now)


def _brief(sv, kind, ke, get_positions, send_fn, now, state, opps):
    if ke is None:
        import kalshi_executor as ke
    try:
        state = state if state is not None else ke.get_state()
        opps = opps if opps is not None else ke.scan_all_markets()
        snap, snap2 = sv.broker_snapshot(get_positions, now), sv.broker_snapshot(get_positions, now)
    except Exception as e:
        return sv.withhold(kind, "source read failed", e, send_fn, now)
    picks = modeled_picks(ke, state, opps)
    upside = sum(p["upside"] for p in picks)
    if upside <= 0.005:
        print("[kalshi_alert] no positive modeled upside — nothing sent")
        return {"status": "not_hot", "sent": False, "text": ""}
    body = (f"Valedia 7am brief — Kalshi\n"
            f"Invested: {snap['invested']:,.2f} (Kalshi open-position cost, broker)\n"
            f"Modeled if-all-win: {upside:+,.2f} ({len(picks)} picks that fit cash, fee-incl. sim)"
            f" — not profit, not EV, not fills")
    fit = min(max(0.0, float(state.get("balance") or 0)), max(0.0, float(state.get("max_deployable") or 0)))
    claims = [sv.claim("Invested", snap["invested"], snap2["invested"], "broker re-read"),
              sv.claim("Modeled if-all-win", upside,
                       sum(p["contracts"] * p["edge"] for p in picks), "contracts x edge", tol=0.01),
              sv.claim("picks fit cash", 0.0, max(0.0, sum(p["cost"] for p in picks) - fit),
                       "cash limit"),
              sv.claim("picks", len(picks), len(picks), "model", tol=0)]
    bfresh, bfoot = sv.broker_fresh(snap)
    return sv.deliver(kind, body, claims, bfresh,
                      footer=[bfoot, f"model {len(picks)} picks"],
                      send_fn=send_fn, now=now, dedupe_key=f"brief:{now.date()}",
                      dedupe_window_s=20 * 3600, info={"picks": picks})


def main():
    import signal_verify as sv
    try:
        import kalshi_executor as ke
    except Exception as e:
        res = sv.withhold("7am brief", "scanner unavailable", e)
        print(f"[kalshi_alert] Signal {res['status']} sent={res['sent']}")
        return
    state = opps = None
    try:
        state, opps = ke.get_state(), ke.scan_all_markets()
        print(ke.opportunity_report(state=state, opps=opps))  # cron log only, not sent
    except Exception as e:
        print(f"[kalshi_alert] scan/report failed: {e}")
    res = brief(ke=ke, state=state, opps=opps)  # re-reads on None; withholds on failure
    print(f"[kalshi_alert] Signal {res['status']} sent={res['sent']}: {res.get('text', '')}")


if __name__ == "__main__":
    main()
