"""Read-only Polymarket US public data bridge for poly_guide_runner.

No credentials, broker operations, forecast fabrication, or automatic eligibility.
All URLs are restricted to gateway.polymarket.us; network is opt-in. A caller must
supply independently verified rules/settlement/volume metadata before a market
can enter the paper runner. BBO alone never supplies a quote timestamp.
"""
from __future__ import annotations

import hashlib
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from poly_guide_paper import VENUE

BASE = "https://gateway.polymarket.us"


def _positive(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label}: numeric value required")
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label}: numeric value required") from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label}: positive finite value required")
    return result


def _utc(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label}: timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{label}: invalid timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{label}: timezone required")
    return parsed.astimezone(timezone.utc)


def _slug(value):
    if not isinstance(value, str) or not value or not value.isascii() or not all(
            c.isalnum() or c in "-_" for c in value):
        raise ValueError("US market slug invalid")
    return value


def parse_market(raw, verified):
    """Require explicitly supplied, independently checked settlement metadata.

    verified: rules_text, resolves_at, volume_usd, fee_per_share provenance is
    intentionally NOT implied here; caller provides the fee to PaperRunner.
    """
    if not isinstance(raw, dict) or not isinstance(verified, dict):
        raise ValueError("US market and verified metadata objects required")
    slug = _slug(raw.get("slug"))
    if raw.get("active") is not True or raw.get("closed") is not False or raw.get("archived") is not False:
        raise ValueError("US market not open")
    sides = raw.get("marketSides")
    if not isinstance(sides, list) or len(sides) != 2 or {s.get("long") for s in sides if isinstance(s, dict)} != {True, False} or not all(isinstance(s, dict) and s.get("tradable") is True for s in sides):
        raise ValueError("US binary sides not verified")
    text = verified.get("rules_text")
    if not isinstance(text, str) or not text.strip() or verified.get("market_slug") != slug or verified.get("rules_verified") is not True:
        raise ValueError("independently verified US settlement rules required")
    resolution = _utc(verified.get("resolves_at"), "verified resolution")
    if resolution <= datetime.now(timezone.utc):
        raise ValueError("verified resolution already passed")
    volume = _positive(verified.get("volume_usd"), "verified USD volume")
    tick = _positive(raw.get("orderPriceMinTickSize"), "US tick")
    if tick >= 1:
        raise ValueError("US tick out of range")
    minimum = _positive(raw.get("minimumTradeQty"), "US minimum trade quantity")
    if not minimum.is_integer():
        raise ValueError("US minimum shares not integral")
    return {"venue": VENUE, "schema": "us_binary_v1", "tradeable": True,
            "slug": slug, "rules_text": text,
            "rules_digest": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "resolves_at": resolution.isoformat(), "volume_usd": volume,
            "tick": tick, "min_shares": int(minimum)}


def parse_book(raw, slug, *, received_at):
    """Strict top-of-book, using book.transactTime rather than fetch time.

    Book levels use px.value and qty; no fallback from BBO aggregate depth.
    Reject crossed/non-USD/ambiguous/missing timestamps or sides.
    """
    slug = _slug(slug)
    if not isinstance(raw, dict) or not isinstance(raw.get("marketData"), dict):
        raise ValueError("US book marketData missing")
    md = raw["marketData"]
    if md.get("marketSlug") != slug or md.get("state") != "MARKET_STATE_OPEN":
        raise ValueError("US book identity/state mismatch")
    stamp = _utc(md.get("transactTime"), "US book transactTime")
    received = _utc(received_at, "US book received_at")
    if stamp > received or (received - stamp).total_seconds() > 120:
        raise ValueError("US book timestamp future or stale")
    def level(side, best):
        rows = md.get(side)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"US book {side} empty")
        values = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("px"), dict) or row["px"].get("currency") != "USD":
                raise ValueError(f"US book {side} currency/price missing")
            price = _positive(row["px"].get("value"), "US book price")
            qty = _positive(row.get("qty"), "US book quantity")
            if price >= 1:
                raise ValueError("US book price out of range")
            values.append((price, qty))
        chosen = (max if best == "bid" else min)(p for p, _ in values)
        if sum(1 for p, _ in values if p == chosen) != 1:
            raise ValueError("US book duplicated top level")
        return {"value": chosen, "quantity": next(q for p, q in values if p == chosen)}
    bid, ask = level("bids", "bid"), level("offers", "ask")
    if bid["value"] >= ask["value"]:
        raise ValueError("US book crossed or locked")
    return {"venue": VENUE, "slug": slug, "as_of": stamp.isoformat(),
            "book": {"marketData": {"bestBid": bid, "bestAsk": ask}}}


class PublicUSAdapter:
    """Optional GET-only transport. Without verified_metadata, no eligible markets."""
    def __init__(self, *, http=False, fetch=None, verified_metadata=None, clock=None):
        self.http = bool(http)
        self.fetch = fetch
        self.verified_metadata = verified_metadata
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _get(self, path, query=None):
        if not path.startswith("/v1/markets") or not (path == "/v1/markets" or path.endswith("/book")):
            raise ValueError("only public US market list/book GET supported")
        url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
        if self.fetch is not None:
            result = self.fetch(url)
        elif self.http:
            request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.load(response)
        else:
            raise RuntimeError("US public HTTP disabled; inject fetch or opt in")
        if not isinstance(result, dict) or "error" in result:
            raise ValueError("invalid US public response")
        return result

    def markets(self, limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 5:
            raise ValueError("candidate limit 1..5 required")
        if self.verified_metadata is None:
            return []  # No network or invented eligibility without verified rules.
        response = self._get("/v1/markets", {"limit": limit, "active": "true", "closed": "false"})
        rows = response.get("markets")
        if not isinstance(rows, list):
            raise ValueError("US market list missing")
        eligible = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                verified = self.verified_metadata(row)
                eligible.append(parse_market(row, verified))
            except ValueError:
                continue
        return eligible

    def bbo(self, market):
        if not isinstance(market, dict) or market.get("venue") != VENUE or market.get("schema") != "us_binary_v1" or market.get("tradeable") is not True:
            raise ValueError("US verified market required")
        slug = _slug(market.get("slug"))
        raw = self._get("/v1/markets/" + slug + "/book")
        return parse_book(raw, slug, received_at=self.clock().isoformat())
