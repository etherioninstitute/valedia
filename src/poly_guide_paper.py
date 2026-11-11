"""Isolated Polymarket US guide experiment. Offline paper only; no HTTP or broker writes.

Callers supply verified US market metadata, BBO snapshots and forecasts. No scheduler,
model invocation, wallet, order creation or cancellation is reachable from this module.
The international Gamma/CLOB schema is deliberately rejected.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

import polymarket_us_client as us

VENUE = "polymarket_us"
STRATEGY = "poly_guide_paper"


def _money(value, name):
    if isinstance(value, bool):
        raise ValueError(name)
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(name) from None
    if not math.isfinite(result) or result < 0:
        raise ValueError(name)
    return result


def _time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp required")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid timestamp") from None
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt.astimezone(timezone.utc)


def _evidence(forecast, market, now):
    if not isinstance(forecast, dict) or forecast.get("venue") != VENUE:
        raise ValueError("US forecast venue required")
    if forecast.get("market_slug") != market["slug"]:
        raise ValueError("forecast market mismatch")
    p = _money(forecast.get("p_yes"), "p_yes")
    if not 0 < p < 1:
        raise ValueError("p_yes out of range")
    if forecast.get("confidence") not in ("medium", "high"):
        raise ValueError("confidence")
    stamp = _time(forecast.get("as_of"))
    if stamp > now or now - stamp > timedelta(hours=2):
        raise ValueError("stale forecast")
    evidence = forecast.get("evidence")
    if not isinstance(evidence, list) or len(evidence) < 3:
        raise ValueError("three evidence links required")
    for item in evidence:
        if not isinstance(item, dict) or not str(item.get("claim", "")).strip():
            raise ValueError("evidence claim required")
        u = urlparse(str(item.get("url", "")))
        if u.scheme != "https" or not u.hostname:
            raise ValueError("https evidence URL required")
    if not str(forecast.get("could_be_wrong", "")).strip():
        raise ValueError("uncertainty required")
    if not str(forecast.get("model_id", "")).strip():
        raise ValueError("model provenance required")
    if forecast.get("rules_digest") != market.get("rules_digest") or not market.get("rules_digest"):
        raise ValueError("resolution rules not verified")
    return p


def _contract(market, bbo, now):
    if not isinstance(market, dict) or market.get("venue") != VENUE or not market.get("slug"):
        raise ValueError("US market required")
    if market.get("schema") != "us_binary_v1" or not market.get("tradeable"):
        raise ValueError("US binary tradeability unverified")
    if not str(market.get("rules_text", "")).strip():
        raise ValueError("missing resolution rules")
    _time(market.get("resolves_at"))
    if not now < _time(market["resolves_at"]) <= now + timedelta(days=30):
        raise ValueError("resolve window")
    if _money(market.get("volume_usd"), "volume") < 50000:
        raise ValueError("insufficient volume")
    tick = _money(market.get("tick"), "tick")
    minimum = _money(market.get("min_shares"), "min_shares")
    if tick <= 0 or minimum <= 0 or minimum != int(minimum):
        raise ValueError("missing tick or integer minimum shares")
    if not isinstance(bbo, dict) or bbo.get("venue") != VENUE or bbo.get("slug") != market["slug"]:
        raise ValueError("US BBO mismatch")
    stamp = _time(bbo.get("as_of"))
    if stamp > now or now - stamp > timedelta(seconds=60):
        raise ValueError("stale BBO")
    quote = us.sizing_from_bbo(bbo.get("book"))
    bid, ask = quote["bid"], quote["ask"]
    if (not quote["ok"] or not all(isinstance(v, (int, float)) and math.isfinite(v)
                                    for v in (bid, ask))
            or not 0 < bid < ask < 1 or ask - bid > 0.03):
        raise ValueError("invalid or wide BBO")
    for price in (bid, ask):
        if abs(price / tick - round(price / tick)) > 1e-6:
            raise ValueError("off-tick quote")
    return quote, minimum


def _schema(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, at TEXT NOT NULL,
        strategy TEXT NOT NULL, venue TEXT NOT NULL, slug TEXT NOT NULL, side TEXT,
        status TEXT NOT NULL, reason TEXT NOT NULL, price REAL, shares INTEGER,
        forecast_json TEXT, market_json TEXT);
      CREATE TABLE IF NOT EXISTS fills(id INTEGER PRIMARY KEY, decision_id INTEGER NOT NULL,
        at TEXT NOT NULL, venue TEXT NOT NULL, slug TEXT NOT NULL, side TEXT NOT NULL,
        shares INTEGER NOT NULL, price REAL NOT NULL, fee REAL NOT NULL, kind TEXT NOT NULL,
        FOREIGN KEY(decision_id) REFERENCES decisions(id));
      CREATE TABLE IF NOT EXISTS costs(id INTEGER PRIMARY KEY, at TEXT NOT NULL,
        strategy TEXT NOT NULL, kind TEXT NOT NULL, usd REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS pnl(id INTEGER PRIMARY KEY, at TEXT NOT NULL,
        strategy TEXT NOT NULL, venue TEXT NOT NULL, slug TEXT NOT NULL, net_usd REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS halts(strategy TEXT PRIMARY KEY, at TEXT NOT NULL,
        reason TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS weekly_checks(strategy TEXT NOT NULL, boundary TEXT NOT NULL,
        net_usd REAL NOT NULL, PRIMARY KEY(strategy,boundary));
    """)


class PaperDesk:
    def __init__(self, path, starting_bankroll):
        self.path = Path(path)
        self.starting = _money(starting_bankroll, "bankroll")
        if self.starting <= 0:
            raise ValueError("bankroll")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        _schema(self.db)

    def close(self):
        self.db.close()

    def record_cost(self, at, usd, kind):
        _time(at)
        if not str(kind).strip():
            raise ValueError("cost kind")
        self.db.execute("INSERT INTO costs(at,strategy,kind,usd) VALUES(?,?,?,?)",
                        (at, STRATEGY, kind, _money(usd, "cost")))

    def settle(self, slug, at, payout):
        """Record a verified external outcome as paper settlement; no broker interaction."""
        _time(at)
        if payout not in (0, 1) or isinstance(payout, bool):
            raise ValueError("binary payout required")
        with self.db:
            row = self.db.execute("""SELECT f.id,f.shares,f.price,f.fee,f.side FROM fills f
                JOIN decisions d ON d.id=f.decision_id
                WHERE f.slug=? AND f.venue=? AND d.strategy=? AND d.venue=?
                AND f.kind='paper_entry' AND NOT EXISTS
                (SELECT 1 FROM fills e WHERE e.decision_id=f.decision_id AND e.kind='paper_settlement')""",
                (slug, VENUE, STRATEGY, VENUE)).fetchone()
            if row is None:
                raise ValueError("no unsettled paper position")
            fid, shares, price, fee, side = row
            net = shares * ((payout if side == "YES" else 1 - payout) - price) - fee
            self.db.execute("INSERT INTO fills(decision_id,at,venue,slug,side,shares,price,fee,kind) SELECT decision_id,?,?,?,?,?,?,?,'paper_settlement' FROM fills WHERE id=?",
                            (at, VENUE, slug, side, shares, float(payout), 0.0, fid))
            self.db.execute("INSERT INTO pnl(at,strategy,venue,slug,net_usd) VALUES(?,?,?,?,?)",
                            (at, STRATEGY, VENUE, slug, net))
            return net

    def _sum(self, table, field, since=None, before=None):
        sql = f"SELECT COALESCE(SUM({field}),0) FROM {table} WHERE strategy=?"
        args = [STRATEGY]
        if since is not None:
            sql += " AND at>=?"
            args.append(since)
        if before is not None:
            sql += " AND at<?"
            args.append(before)
        return float(self.db.execute(sql, args).fetchone()[0])

    def _weekly_boundary_check(self, instant):
        """Catch up completed Sunday UTC checks using trailing 14-day net after costs.

        Each boundary is the Monday 00:00 UTC immediately after a completed Sunday;
        persist every evaluated boundary so a missed Sunday cannot be silently skipped.
        The first evaluation checks the most recently completed boundary, not
        historical weeks for which this strategy had no recorded checkpoint.
        """
        latest = datetime.combine((instant - timedelta(days=instant.weekday())).date(),
                                  datetime.min.time(), tzinfo=timezone.utc)
        prior = self.db.execute("SELECT MAX(boundary) FROM weekly_checks WHERE strategy=?",
                                (STRATEGY,)).fetchone()[0]
        boundary = _time(prior) + timedelta(days=7) if prior else latest
        while boundary <= instant:
            start = (boundary - timedelta(days=14)).isoformat()
            end = boundary.isoformat()
            # julianday normalizes equivalent ISO timestamps written with Z or offsets.
            net = 0.0
            for table, field, sign in (("pnl", "net_usd", 1), ("costs", "usd", -1)):
                net += sign * float(self.db.execute(
                    f"SELECT COALESCE(SUM({field}),0) FROM {table} "
                    "WHERE strategy=? AND julianday(at)>=julianday(?) "
                    "AND julianday(at)<julianday(?)", (STRATEGY, start, end)).fetchone()[0])
            self.db.execute("INSERT INTO weekly_checks(strategy,boundary,net_usd) VALUES(?,?,?)",
                            (STRATEGY, end, net))
            if net < 0:
                self.db.execute("INSERT OR IGNORE INTO halts(strategy,at,reason) VALUES(?,?,?)",
                                (STRATEGY, end, "trailing 14-day net after costs negative"))
                raise ValueError("weekly net-profit halt")
            boundary += timedelta(days=7)

    def evaluate(self, market, bbo, forecast, *, fee_per_share, now, stop_dir,
                 max_spread=0.03, depth_caps=None):
        """Persist a decision and optional conservative paper fill; never submit orders.

        fee_per_share is an explicitly supplied nonnegative US round-trip fee estimate
        (entry plus eventual exit/settlement), conservatively reserved in full at entry.
        It is deducted once in edge, sizing and final net P&L; no additional exit
        charge is modeled. Zero is rejected until a verified fee-free schedule exists.
        Paper fills at ask (YES) or implied NO ask (1 - YES bid), not midpoint.
        """
        instant = _time(now)
        slug = market.get("slug", "") if isinstance(market, dict) else ""
        reason, side, price, shares, fee = "", None, None, 0, 0.0
        with self.db:
            try:
                self._weekly_boundary_check(instant)
                if self.db.execute("SELECT 1 FROM halts WHERE strategy=?", (STRATEGY,)).fetchone():
                    raise ValueError("strategy weekly halt latched")
                if Path(stop_dir, "STOP").exists():
                    raise ValueError("strategy STOP: paper proposals halted; no broker cancellation")
                quote, minimum = _contract(market, bbo, instant)
                if quote["spread"] > max_spread:
                    raise ValueError("spread limit")
                p = _evidence(forecast, market, instant)
                fee = _money(fee_per_share, "US fee")
                if fee <= 0:
                    raise ValueError("verified positive US fee estimate required")
                cutoff = (instant + timedelta(microseconds=1)).isoformat()
                won = self._sum("pnl", "net_usd", before=cutoff)
                costs = self._sum("costs", "usd", before=cutoff)
                balance = self.starting + won - costs
                day = instant.date().isoformat()
                day_net = self._sum("pnl", "net_usd", day, cutoff)
                if day_net <= -20 or balance <= self.starting * .60:
                    raise ValueError("daily or total loss stop")
                # Conservative: until no unresolved prior entry, reserve maximum loss.
                open_rows = self.db.execute("""SELECT f.slug,f.shares,f.price,f.fee FROM fills f
                    JOIN decisions d ON d.id=f.decision_id
                    WHERE d.strategy=? AND d.venue=? AND f.venue=? AND f.kind='paper_entry'
                    AND f.at<? AND NOT EXISTS
                    (SELECT 1 FROM fills e WHERE e.decision_id=f.decision_id AND e.kind='paper_settlement')""",
                    (STRATEGY, VENUE, VENUE, cutoff)).fetchall()
                if len(open_rows) >= 5 or any(row[0] == slug for row in open_rows):
                    raise ValueError("position limit or duplicate market")
                exposure = sum(n * px + f for _, n, px, f in open_rows)
                open_today = self.db.execute("""SELECT COALESCE(SUM(f.shares*f.price+f.fee),0)
                    FROM fills f JOIN decisions d ON d.id=f.decision_id
                    WHERE d.strategy=? AND d.venue=? AND f.venue=?
                    AND f.kind='paper_entry' AND f.at>=? AND f.at<? AND NOT EXISTS
                    (SELECT 1 FROM fills e WHERE e.decision_id=f.decision_id AND e.kind='paper_settlement')""",
                    (STRATEGY, VENUE, VENUE, day, cutoff)).fetchone()[0]
                available = balance - exposure
                if available <= 0 or balance - exposure <= self.starting * .60:
                    raise ValueError("total loss stop including open risk")
                bid, ask = quote["bid"], quote["ask"]
                yes_edge = p - ask - fee
                no_px = 1 - bid
                no_edge = (1 - p) - no_px - fee
                if max(yes_edge, no_edge) < .08:
                    raise ValueError("fee-inclusive edge below eight points")
                side = "YES" if yes_edge >= no_edge else "NO"
                price = ask if side == "YES" else no_px
                probability = p if side == "YES" else 1 - p
                # Binary contract: loss=price+fee; win=1-price-fee. Quarter Kelly.
                if fee >= 1 - price:
                    raise ValueError("fee exceeds payoff")
                fraction = .25 * (probability - price - fee) / (1 - price)
                dollars = min(available * max(0, fraction), balance * .05, 25.0,
                              available, max(0, day_net + 20 - open_today),
                              max(0, balance - exposure - self.starting * .60))
                shares = int(math.floor(dollars / (price + fee)))
                if shares < minimum or shares <= 0:
                    raise ValueError("size below contract minimum or risk budget")
                if depth_caps is not None:
                    if not isinstance(depth_caps, dict) or side not in depth_caps:
                        raise ValueError("verified side-specific depth required")
                    depth = depth_caps[side]
                    if (isinstance(depth, bool) or not isinstance(depth, (int, float))
                            or not math.isfinite(depth) or depth < 0):
                        raise ValueError("verified side-specific depth required")
                    if shares > depth:
                        raise ValueError("proposed fill exceeds verified top-of-book depth")
                reason = "limit-only paper simulation at executable quote; no broker order"
            except (ValueError, TypeError, KeyError) as err:
                reason = str(err)
                shares = 0
            status = "PAPER_FILL" if shares else "BLOCKED"
            cursor = self.db.execute("""INSERT INTO decisions
                (at,strategy,venue,slug,side,status,reason,price,shares,forecast_json,market_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (now, STRATEGY, VENUE, slug, side, status, reason, price, shares,
                 json.dumps(forecast, sort_keys=True, default=str),
                 json.dumps(market, sort_keys=True, default=str)))
            if shares:
                self.db.execute("""INSERT INTO fills
                    (decision_id,at,venue,slug,side,shares,price,fee,kind)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (cursor.lastrowid, now, VENUE, slug, side, shares, price,
                     fee * shares, "paper_entry"))
            return {"id": cursor.lastrowid, "status": status, "reason": reason,
                    "side": side, "limit_price": price, "shares": shares, "venue": VENUE}
