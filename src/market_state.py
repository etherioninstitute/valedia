"""In-memory Kalshi order-book state: normalized book, microprice, imbalance, staleness.

Adapted from kalshi-agent's market_state.py (a book-microstructure view Valedia lacked —
it previously had only an inline spread check). Feeds execution_policy's maker/taker/no_trade
decisions. Populate via MARKET_STATE.update_book(ticker, raw_orderbook) from the Kalshi scanner.
Stdlib only.
(c) GOATWORTHY Post LLC.
"""
import time, threading, logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("valedia.market_state")


@dataclass
class BookLevel:
    price_cents: int
    size: int


@dataclass
class BookState:
    """Snapshot of one market's order book (YES-side canonical; NO derived)."""
    ticker: str
    yes_bids: list = field(default_factory=list)   # BookLevel, best-first
    yes_asks: list = field(default_factory=list)
    no_bids: list = field(default_factory=list)
    no_asks: list = field(default_factory=list)
    timestamp: float = 0.0
    source: str = "rest"

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp if self.timestamp > 0 else float("inf")

    @property
    def is_stale(self) -> bool:
        max_age = 30 if self.source == "ws" else 60
        return self.age_seconds > max_age

    @property
    def best_yes_bid(self) -> Optional[int]:
        return self.yes_bids[0].price_cents if self.yes_bids else None

    @property
    def best_yes_ask(self) -> Optional[int]:
        return self.yes_asks[0].price_cents if self.yes_asks else None

    @property
    def best_no_bid(self) -> Optional[int]:
        return self.no_bids[0].price_cents if self.no_bids else None

    @property
    def best_no_ask(self) -> Optional[int]:
        return self.no_asks[0].price_cents if self.no_asks else None

    @property
    def spread_cents(self) -> Optional[int]:
        if self.best_yes_bid is not None and self.best_yes_ask is not None:
            return self.best_yes_ask - self.best_yes_bid
        return None

    @property
    def microprice(self) -> Optional[float]:
        """Volume-weighted top-of-book price — better 'true' price than mid when imbalanced."""
        if not self.yes_bids or not self.yes_asks:
            return None
        bp, bs = self.yes_bids[0].price_cents, self.yes_bids[0].size
        ap, as_ = self.yes_asks[0].price_cents, self.yes_asks[0].size
        tot = bs + as_
        if tot == 0:
            return (bp + ap) / 2.0
        return (bp * as_ + ap * bs) / tot

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_yes_bid is not None and self.best_yes_ask is not None:
            return (self.best_yes_bid + self.best_yes_ask) / 2.0
        return None

    @property
    def imbalance(self) -> Optional[float]:
        """Top-of-book imbalance; positive = more bid pressure."""
        if not self.yes_bids or not self.yes_asks:
            return None
        bs, as_ = self.yes_bids[0].size, self.yes_asks[0].size
        tot = bs + as_
        return (bs - as_) / tot if tot else 0.0


def _parse_book_side(raw_levels) -> list:
    out = []
    for entry in (raw_levels or []):
        try:
            if isinstance(entry, (list, tuple)):
                price = float(entry[0]); size = int(float(entry[1])) if len(entry) > 1 else 1
            elif isinstance(entry, dict):
                price = float(entry.get("price", 0))
                size = int(float(entry.get("size", entry.get("quantity", 0))))
            else:
                continue
            price = round(price * 100) if 0 < price < 1 else round(price)
            price = int(price)
            if 1 <= price <= 99 and size > 0:
                out.append(BookLevel(price_cents=price, size=size))
        except (ValueError, TypeError, IndexError):
            continue
    return out


class MarketStateStore:
    """Thread-safe in-memory book store."""

    def __init__(self):
        self._books = {}
        self._lock = threading.RLock()

    def update_book(self, ticker, raw_orderbook, source="rest") -> BookState:
        bd = raw_orderbook.get("orderbook", raw_orderbook)
        yes_bids = _parse_book_side(bd.get("yes", bd.get("yes_dollars", [])))
        no_bids = _parse_book_side(bd.get("no", bd.get("no_dollars", [])))
        # Kalshi: buy NO at X == sell YES at 100-X -> derive YES asks from NO bids.
        yes_asks = [BookLevel(100 - nb.price_cents, nb.size) for nb in no_bids
                    if 1 <= 100 - nb.price_cents <= 99]
        yes_bids.sort(key=lambda x: x.price_cents, reverse=True)
        yes_asks.sort(key=lambda x: x.price_cents)
        no_bids.sort(key=lambda x: x.price_cents, reverse=True)
        no_asks = [BookLevel(100 - yb.price_cents, yb.size) for yb in yes_bids
                   if 1 <= 100 - yb.price_cents <= 99]
        no_asks.sort(key=lambda x: x.price_cents)
        state = BookState(ticker, yes_bids, yes_asks, no_bids, no_asks, time.time(), source)
        with self._lock:
            self._books[ticker] = state
        return state

    def get_book(self, ticker) -> Optional[BookState]:
        with self._lock:
            return self._books.get(ticker)

    def get_book_if_fresh(self, ticker) -> Optional[BookState]:
        b = self.get_book(ticker)
        return b if (b and not b.is_stale) else None

    def clear(self):
        with self._lock:
            self._books.clear()


MARKET_STATE = MarketStateStore()  # global singleton


if __name__ == "__main__":
    # Self-check: derive YES ask from NO bid; microprice between bid/ask.
    b = MARKET_STATE.update_book("KXTEST", {"orderbook": {
        "yes": [{"price": 40, "size": 100}], "no": [{"price": 55, "size": 100}]}})
    assert b.best_yes_bid == 40, b.best_yes_bid
    assert b.best_yes_ask == 45, b.best_yes_ask          # 100 - 55
    assert b.spread_cents == 5, b.spread_cents
    assert 40 <= b.microprice <= 45, b.microprice
    print("market_state self-check: PASS  bid=%s ask=%s micro=%.1f" % (b.best_yes_bid, b.best_yes_ask, b.microprice))
