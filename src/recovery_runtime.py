"""Qualified paper branches of the recovered brain, allocator and leaf adapters."""
from decimal import Decimal, ROUND_DOWN
from recovery_state import Journal, Refused, Unsupported, require_paper, paper_sizing, cents, canonical

def _context(recovery):
    require_paper()
    if not isinstance(recovery,dict) or not isinstance(recovery.get("journal"),Journal): raise Refused("explicit recovery context required")
    for key in ("decision_id","account","day"):
        if not recovery.get(key): raise Refused("missing durable caller identity")
    recovery["journal"].validate_context(recovery["account"],recovery["day"])
    return recovery

def _start(recovery, request):
    r=_context(recovery);j=r["journal"]
    saved=j.decision(r["decision_id"],r["account"],r["day"],request)
    equity=r["journal"].equity
    if Decimal(str(saved["equity"])) != equity: raise Refused("prepared equity drift")
    if not equity.is_finite() or equity<=0: raise Refused("invalid equity")
    # Exact UTC/account limit, immutable within this day, shared across processes.
    limit=Decimal(r["journal"].limit_cents)/100
    j.budget(r["account"],r["day"],str(limit))
    legs=saved["legs"]
    if not isinstance(legs,list) or not legs or len(legs)>20: raise Refused("bounded explicit legs required")
    ids=[x.get("leg_id") for x in legs]
    if len(set(ids))!=len(ids): raise Refused("duplicate leg identity")
    return saved

def leaf(venue, symbol, amount, recovery):
    r=_context(recovery)
    import valedia_config as vc
    try: vc.require_not_halted("paper recovery submit")
    except vc.HaltedError as exc: raise Refused("persistent trading halt") from exc
    if not isinstance(symbol,str) or not symbol or len(symbol)>64: raise Refused("instrument required")
    n=cents(amount)
    payload={"account":r["account"],"venue":venue,"instrument":symbol,"side":"buy","amount":format(Decimal(n)/100,".2f")}
    transport=r.get("transport")
    if not callable(transport): raise Refused("injected paper transport required")
    def send(client_id,p):
        if venue=="coinbase":
            from coinbase_order_body import build_market_order_body
            order=build_market_order_body(symbol,"buy",usd_amount=p["amount"],client_order_id=client_id)
        elif venue=="alpaca":
            order={"symbol":symbol,"side":"buy","type":"market","time_in_force":"day","notional":p["amount"],"client_order_id":client_id}
        else: raise Unsupported("venue unsupported")
        return transport(venue,order)
    return r["journal"].submit(r["decision_id"],r["leg_id"],payload,send)

def execute_leg(a, venue, result, recovery):
    r=_context(recovery)
    if a.get("side","buy") != "buy": raise Unsupported("only prepared paper buys qualified")
    if venue=="coinbase":
        from coinbase_trader import market_buy
        answer=market_buy(a["ticker"],a["allocation"],recovery=r)
    elif venue=="alpaca":
        from alpaca_executor import market_buy
        answer=market_buy(a["ticker"],notional=a["allocation"],recovery=r)
    else: raise Unsupported("allocator venue unqualified")
    result.update(answer);return result

def allocator(allocations,dry_run,recovery):
    r=_context(recovery)
    request=None if allocations is None else {"equity":str(r["journal"].equity),"legs":allocations}
    saved=_start(r,request)
    if dry_run: return [{"status":"QUOTED","leg_id":a["leg_id"]} for a in saved["legs"]]
    import allocator_paths
    results=[]
    for a in saved["legs"]:
        rr={**r,"leg_id":a["leg_id"]}
        results.append(allocator_paths.spot_execute(a,a["platform"],{},cfg=None,fill_status=None,recovery=rr))
    return results

def brain(recovery):
    r=_context(recovery)
    request=None if r.get("legs") is None else {"equity":str(r["journal"].equity),"legs":r["legs"]}
    saved=_start(r,request)
    import unified_brain
    results=[]
    for leg in saved["legs"]:
        # Retain actual legacy stage_kelly post-multiplier/cap/floor path.
        with paper_sizing(leg["track_record_mult"],leg["risk_factor"]):
            amount=unified_brain.stage_kelly(leg["fused_score"],float(r["journal"].equity),max_pct=leg.get("max_pct",0.04),pos_multiplier=leg.get("pos_multiplier",1),expected_return_pct=leg["expected_return_pct"],regime=leg.get("regime","normal"),track_record_mult=leg["track_record_mult"],risk_factor=leg["risk_factor"],concurrent_bets=leg.get("concurrent_bets",1))
        if amount<=0: results.append({"status":"REFUSED_ZERO_SIZE","leg_id":leg["leg_id"]});continue
        results.append(execute_leg({"ticker":leg["ticker"],"allocation":amount},leg["platform"],{}, {**r,"leg_id":leg["leg_id"]}))
    return results
