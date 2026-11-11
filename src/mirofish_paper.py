"""Manual fixed-notional paper handoff; the only transport is a local simulator.

No broker adapter, heuristic sizing, external generation or self-attested receipts.
"""
from dataclasses import dataclass, asdict
from decimal import Decimal
from recovery_state import Journal, Refused, canonical, cents, require_paper
from mirofish_provenance import AuthorityStore, sha


@dataclass(frozen=True)
class PaperReview:
    review_id: str
    account: str
    journal_path: str
    venue: str
    instrument: str
    amount: str
    equity: str
    daily_limit: str


class LocalPaperFlow:
    def __init__(self, store, journal):
        if type(store) is not AuthorityStore or type(journal) is not Journal:
            raise Refused("explicit trusted store and actual Journal required")
        self.store, self.journal = store, journal
        self._journal_binding = (journal.path, journal.account, str(journal.equity), journal.limit_cents)
        self._binding()

    def _binding(self):
        j, b = self.journal, self.store.binding
        if (j.path, j.account, str(j.equity), j.limit_cents) != self._journal_binding or j.path != b.journal_path or j.account != b.account or j.clock is not self.store.clock:
            raise Refused("pinned Journal/account/clock changed")

    def _review(self, review):
        self._binding()
        if self.store._configuration() != self.store._frozen:
            raise Refused("frozen authority/profile changed")
        if type(review) is not PaperReview or not isinstance(review.review_id, str) or not review.review_id or len(review.review_id) > 100:
            raise Refused("explicit reviewed policy required")
        b, j = self.store.binding, self.journal
        if review.account != b.account or review.journal_path != b.journal_path or (review.venue, review.instrument) not in b.registry:
            raise Refused("review account/Journal/registry mismatch")
        if not all(type(v) is str for v in (review.amount, review.equity, review.daily_limit)):
            raise Refused("reviewed cent-exact decimal strings required")
        amount, equity, limit = map(cents, (review.amount, review.equity, review.daily_limit))
        if Decimal(review.equity) != j.equity or limit != j.limit_cents:
            raise Refused("reviewed equity/cap changed")
        return format(Decimal(amount) / 100, ".2f")

    def _guard(self, rid, review):
        amount = self._review(review)
        receipt, features, now = self.store.validate(rid)
        require_paper()
        import valedia_config as vc
        try:
            vc.require_not_halted("MiroFish local paper")
        except vc.HaltedError as exc:
            raise Refused("actual persistent halt") from exc
        day = now.date().isoformat()
        self.journal.validate_context(review.account, day)
        signal = features.get("signals", {}).get(review.instrument)
        if not isinstance(signal, dict) or signal.get("direction") != "BUY" or signal.get("score", 0) <= 55:
            raise Refused("positive matching per-asset signal required")
        return receipt, features, now, amount, day

    def submit(self, proposal, review):
        if not isinstance(proposal, dict) or set(proposal) - {"receipt_ref", "instrument", "action", "reason"}:
            raise Refused("proposal overrides/unknown fields refused")
        if proposal.get("action") != "buy" or proposal.get("instrument") != getattr(review, "instrument", None) or not isinstance(proposal.get("receipt_ref"), str):
            raise Refused("exact canonical buy proposal required")
        if "reason" in proposal and (not isinstance(proposal["reason"], str) or len(proposal["reason"]) > 500):
            raise Refused("bounded reason required")
        rid = proposal["receipt_ref"]
        receipt, features, now, amount, day = self._guard(rid, review)
        identity = {**receipt["manifest"]["identity"], "account": review.account, "venue": review.venue, "instrument": review.instrument}
        decision = "mfpp-" + sha(canonical(identity).encode())
        evidence = {"receipt_ref": rid, "manifest_sha256": sha(canonical(receipt).encode()), "extractor_sha256": self.store.profile.extractor_sha256, "signal_sha256": sha(canonical(features).encode()), "review": asdict(review), "policy_sha256": sha(canonical(asdict(review)).encode()), "authority_configuration_sha256": sha(self.store._frozen.encode()), "observation_begin": receipt["begin"], "observed_complete": receipt["observed_complete"], "proof": receipt["proof"], "reviewed_day": day, "proposal_reason": proposal.get("reason", "")}
        leg = {"leg_id": "buy", "ticker": review.instrument, "platform": review.venue, "side": "buy", "allocation": amount, "mirofish_evidence": evidence}
        frozen_review = canonical(asdict(review))
        frozen_receipt = sha(canonical(receipt).encode())
        result = self.store.claim(decision, leg)  # Durable one-shot claim BEFORE allocator.
        if result is not None:
            from pathlib import Path
            path = Path(self.journal.path)
            if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
                raise Refused("replay Journal missing or replaced")
            saved = self.journal.decision(decision, review.account, day, request=None)
            if canonical(saved) != canonical({"equity": str(self.journal.equity), "legs": [leg]}):
                raise Refused("replay persisted Journal request drift")
            return [{**row, "original_attempted": row.get("attempted"), "attempted": False, "replay": True} for row in result]

        def local_simulator(venue, order):
            # Called only after the actual leaf commits SUBMITTING/reservation.
            # Acquire the store lock BEFORE the final proof/control read-recheck.
            with self.store._tx() as c:
                fresh, _, _ = self.store._validate_locked(c, rid)
                current_amount = self._review(review)
                self.journal.validate_context(review.account, day)
                final_now = self.store._clock(c)
                self.store._fresh(fresh, final_now)
                if canonical(asdict(review)) != frozen_review or sha(canonical(fresh).encode()) != frozen_receipt or current_amount != amount or final_now.date().isoformat() != day or venue != review.venue:
                    raise Refused("late evidence/review/day drift")
                require_paper()
                import valedia_config as vc
                try:
                    vc.require_not_halted("MiroFish final local acknowledgement")
                except vc.HaltedError as exc:
                    raise Refused("late actual persistent halt") from exc
                if venue == "alpaca":
                    if order.get("symbol") != review.instrument or order.get("side") != "buy" or order.get("notional") != amount or order.get("type") != "market":
                        raise Refused("constructed paper order drift")
                elif venue == "coinbase":
                    quote = order.get("order_configuration", {}).get("market_market_ioc", {}).get("quote_size")
                    if order.get("product_id") != review.instrument or order.get("side") != "BUY" or quote != amount:
                        raise Refused("constructed paper order drift")
                else:
                    raise Refused("unsupported local paper venue")
                c.execute("INSERT INTO acknowledgements VALUES(?,?)", (decision, canonical({"venue": venue, "order": order, "simulated": True})))
            return {"outcome": "accepted", "order_id": "simulated-" + decision, "simulated": True, "http_status": 200}

        import master_allocator
        result = master_allocator.execute_allocations([leg], dry_run=False, recovery={"journal": self.journal, "decision_id": decision, "account": review.account, "day": day, "transport": local_simulator})
        self.store.complete_claim(decision, result)
        return result
