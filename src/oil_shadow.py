"""Oil RESEARCH/SHADOW scorer. Fee-true would-have only.

KXWTI / KXOIL settle on the official print the contract rules name
(NYMEX CL / CME WTI settlement, or the EIA print those rules cite).
rules_cite must be one of nymex_cl, cme_wti, eia_wti, contract_rules.
A number without that cite is not FV. Muse and MiroFish are not FV.

print_kind settlement: the print determines the contract (step at the strike).
print_kind forecast: pre-settlement print, gaussian sigma_usd default 1.5.
ponytail: sigma is a fixed dollar band. Replace it when a cited rule names one.

Does not load the Kalshi client. dispatchable is always 0.
create_order is always false. Does not unfence.
"""
import os
import re

import shadow_common as desk
import valedia_config as vc
import weather_shadow as ws

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
LEDGER_PATH = os.path.join(ROOT, "models", "oil_shadow_ledger.json")
LIVE_NAME = "oil_shadow_ledger.json"

PREFIXES = ("KXWTI", "KXOIL")
OFFICIAL = frozenset({"nymex", "cme", "eia", "print", "official", "nymex_cl", "cme_wti"})
RULES = frozenset({"nymex_cl", "cme_wti", "eia_wti", "contract_rules"})
REFUSED = frozenset({"muse", "mirofish", "mirofish_swarm", "gdelt", "default", "default_50", "default-50"})

_ABOVE = re.compile(r"(?:>|above|over|at least)\s*\$?\s*(\d+(?:\.\d+)?)", re.I)
_BELOW = re.compile(r"(?:<|below|under)\s*\$?\s*(\d+(?:\.\d+)?)", re.I)
_RANGE = re.compile(r"\$?\s*(\d+(?:\.\d+)?)\s*(?:-|to)\s*\$?\s*(\d+(?:\.\d+)?)", re.I)
_TICK = re.compile(r"-(?:T|B)(\d+(?:\.\d+)?)$", re.I)
# ponytail: $1.50 band until a cited rule names the settlement tolerance.
SIGMA_USD = 1.5


def is_oil_series(ticker):
    head = str(ticker or "").upper().split("-", 1)[0]
    return head in PREFIXES or head == "WTI" or head.startswith("WTI")


def parse_strike(title, ticker=None):
    title = title or ""
    m = _RANGE.search(title)
    if m:
        return "range", float(m.group(1)), float(m.group(2))
    m = _ABOVE.search(title)
    if m:
        return "above", float(m.group(1)), None
    m = _BELOW.search(title)
    if m:
        return "below", None, float(m.group(1))
    m = _TICK.search(str(ticker or ""))
    if m:
        return "above", float(m.group(1)), None
    return None, None, None


def _step(print_usd, kind, lo, hi):
    if kind == "above":
        return 0.99 if float(print_usd) >= float(lo) else 0.01
    if kind == "below":
        return 0.99 if float(print_usd) <= float(hi) else 0.01
    if kind == "range":
        return 0.99 if float(lo) <= float(print_usd) <= float(hi) else 0.01
    return None


def score(candidate, fmap=None, bankroll=100.0, now=None):
    """One would-have row. dispatchable is 0 even if the caller says otherwise."""
    import session_clocks as sc
    c = candidate if isinstance(candidate, dict) else {}
    ticker = c.get("ticker") or c.get("series")
    market = c.get("market") if isinstance(c.get("market"), dict) else dict(c)
    if ticker and "ticker" not in market:
        market = dict(market)
        market["ticker"] = ticker
    sess = sc.session_clock(
        "oil", ticker=ticker, strategy=c.get("strategy") or "OIL_BRACKET",
        market=market, now=c.get("now", now))
    res = sc.resolve_clock(
        "oil", ticker=ticker, strategy=c.get("strategy") or "OIL_BRACKET",
        market=market, now=c.get("now", now))
    fence = sc.fence_gate("oil", ticker=ticker, strategy="OIL_BRACKET")
    source = str(c.get("fv_source") or "").strip().lower()
    cite = str(c.get("rules_cite") or "").strip().lower()
    kind, lo, hi = parse_strike(c.get("title") or market.get("title"), ticker)
    print_usd = c.get("print_usd")
    model = None
    official_ok = (
        source in OFFICIAL and cite in RULES
        and print_usd is not None and kind is not None
    )
    if not is_oil_series(ticker):
        fv, tag = None, "not_oil_series"
    elif official_ok:
        px = float(print_usd)
        if str(c.get("print_kind") or "forecast").lower() == "settlement":
            model = _step(px, kind, lo, hi)
        else:
            sigma = float(c.get("sigma_usd") or SIGMA_USD)
            model = ws.bracket_prob(px, kind, lo, hi, sigma=sigma)
        fv = model
        tag = "official" if fv is not None else "no_fv"
    elif c.get("season") or c.get("mirofish_season") or source.startswith("miro"):
        fv, tag = None, "season_only"
    elif source in REFUSED or source in ("muse", "gdelt"):
        fv, tag = None, "unofficial"
    else:
        fv, tag = None, "no_fv" if source in OFFICIAL or not source else "unofficial"
    tag, fv = desk.drop_near_neutral(tag, fv)
    side = str(c.get("side") or "yes").lower()
    price = c.get("price")
    if price is None and c.get("price_cents") is not None:
        price = float(c.get("price_cents")) / 100.0
    price = None if price is None else float(price)
    fv_side = None if fv is None else (fv if side == "yes" else 1.0 - fv)
    size, fee, ev, role, fee_status = 0, None, None, None, None
    if tag == "official" and fv_side is not None and price is not None and 0 < price < 1:
        fee1, role, _mult, fee_status, _series = ws._fee_quote(ticker, price, 1, fmap)
        if fv_side - price - fee1 > 0:
            size = ws.shadow_contracts(fv_side, price, bankroll, fee1)
            if size > 0:
                fee, role, _mult, fee_status, _series = ws._fee_quote(ticker, price, size, fmap)
                ev = (fv_side - price) * size - fee
                if ev <= 0:
                    size, ev, fee = 0, None, None
    elif tag == "official":
        fee_status = "no_book" if price is None else fee_status
    settlement = c.get("settlement")
    pnl = None
    if settlement is not None and size > 0 and price is not None and fee is not None:
        payout = 1.0 if ws._side_yes(settlement) == (side == "yes") else 0.0
        pnl = (payout - price) * size - fee
    elif settlement is not None:
        pnl = 0.0
    row = {
        "ticker": ticker,
        "mode": "RESEARCH/SHADOW",
        "dispatchable": 0,
        "live_exec": False,
        "writer": None,
        "create_order": False,
        "fence": fence.get("reason"),
        "tag": tag,
        "fv_source": source or None,
        "rules_cite": cite or None,
        "fv": None if fv is None else round(float(fv), 6),
        "model_fv": None if model is None else round(float(model), 6),
        "print_usd": None if print_usd is None else float(print_usd),
        "side": side,
        "price": price,
        "fee": fee,
        "fee_role": role,
        "fee_status": fee_status,
        "fee_true_ev": None if ev is None else round(float(ev), 4),
        "would_have_size": size,
        "would_have_pnl": None if pnl is None else round(float(pnl), 4),
        "settlement": settlement,
        "enter_window": {
            "open_at": sess.get("open_at"),
            "close_at": sess.get("close_at"),
            "active": False,
            "reason": sess.get("reason"),
        },
        "resolve": {
            "known": res.get("known"),
            "kind": res.get("kind"),
            "reason": res.get("reason"),
            "resolve_at": res.get("resolve_at"),
        },
        "book": c.get("book") or ("fixture" if c.get("fixture") else "absent"),
    }
    row["ftl_shaped"] = desk.ftl_shaped(row, "OIL_BRACKET")
    return row


def universe_rows(fee_series):
    rows = []
    for series, meta in sorted((fee_series or {}).items()):
        if not is_oil_series(series):
            continue
        rows.append({
            "ticker": series,
            "origin": "fee_map",
            "title": (meta or {}).get("notes") or "",
            "fee_status": (meta or {}).get("status"),
            "book": "absent",
        })
    return rows


def build_ledger(candidates, fmap=None, bankroll=100.0, universe=None, note=""):
    scored = [score(c, fmap=fmap, bankroll=bankroll) for c in candidates or []]
    return {
        "desk": "oil",
        "mode": "RESEARCH/SHADOW",
        "dispatchable": 0,
        "live_exec": False,
        "live_writer": False,
        "fence": "LIVE_EXEC_FENCED",
        "create_order": False,
        "valedia_version": vc.VALEDIA_VERSION,
        "note": note,
        "n_universe": len(universe or []),
        "universe": universe or [],
        "n_scored": len(scored),
        "scored": scored,
    }


def write_ledger(path=None, fmap=None, fixtures=None, bankroll=100.0, mirror_live=None):
    """Write the shadow ledger. No network. No orders."""
    import kalshi_fee_map as kfm
    if fmap is None:
        fmap = (kfm.artifact_status().get("series") or {})
    uni = [score(row, fmap=fmap, bankroll=bankroll) for row in universe_rows(fmap)]
    body = build_ledger(
        fixtures or [], fmap=fmap, bankroll=bankroll, universe=uni,
        note=("Universe is KXWTI/KXOIL already on the fee-map. "
              "No Kalshi GET, no CME scrape, no order placement. "
              "FV requires an official print plus rules_cite. "
              "book=fixture is a worked example, not a live print."),
    )
    path = path or LEDGER_PATH
    body = desk.finish_ledger(path, body)
    canonical = os.path.abspath(path) == os.path.abspath(LEDGER_PATH)
    if mirror_live or (mirror_live is None and canonical):
        desk.mirror(path, LIVE_NAME)
    return body


if __name__ == "__main__":
    example = {
        "ticker": "KXWTI-26SEP23-T72",
        "title": "WTI above $72",
        "fv_source": "nymex",
        "rules_cite": "nymex_cl",
        "print_kind": "settlement",
        "print_usd": 80,
        "price": 0.40,
        "side": "yes",
        "fixture": True,
        "settlement": "yes",
    }
    doc = write_ledger(fixtures=[example])
    print(
        "OIL-SHADOW ledger n_universe=%s dispatchable=%s fence=%s create_order=%s"
        % (doc["n_universe"], doc["dispatchable"], doc["fence"], doc["create_order"])
    )
