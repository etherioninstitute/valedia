"""Local, offline Polymarket US research ledger. No broker imports or order writer.

Synthetic fixtures are diagnostics, never evidence for real-money promotion.
Real costs require reviewed official-source receipts and a verified settlement import.
Historical row admission requires feature_published_at and a bounded
feature_publication_source reference, distinct from feature_at. The source
reference is caller-asserted, not authenticated here. Upstream capture may pass
through explicit publisher metadata, but observation/receipt/mtime are not
publication and absent metadata remains absent. Existing desk/MiroFish reports
have no documented historical publication field; their rows must refuse until
an actual source supplies it. Rows and receipts remain immutable.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(x):
    return hashlib.sha256(canonical(x).encode()).hexdigest()


def _time(x):
    t = datetime.fromisoformat(x)
    if t.tzinfo is None:
        raise ValueError("timezone required")
    return t.astimezone(timezone.utc)


def _number(x, lo=None, hi=None):
    if isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x):
        raise ValueError("finite numeric value required")
    if lo is not None and x < lo or hi is not None and x > hi:
        raise ValueError("numeric value outside limits")
    return x


def _rows(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("nonempty observations required")
    seen = set(); prior = None
    needed = {"market_id", "decision_at", "feature_at", "feature_published_at", "feature_publication_source", "feature", "entry_at", "entry_price", "settled_at", "outcome", "regime"}
    optional = {"outcome_available_at", "quote_at", "yes_ask", "no_ask", "yes_ask_size", "no_ask_size"}
    for row in rows:
        if not isinstance(row, dict) or not {"feature_published_at", "feature_publication_source"} <= set(row):
            raise ValueError("INSUFFICIENT_EVIDENCE: separately sourced feature publication time required")
        if not needed <= set(row) or set(row)-needed-optional:
            raise ValueError("mixed prediction/outcome or undeclared feature columns")
        f, d, e, s = (_time(row[k]) for k in ("feature_at", "decision_at", "entry_at", "settled_at"))
        # Source/reference is asserted by the captured input, not authenticated by
        # this ledger. Do not substitute observation, receipt or file time for it.
        source = row["feature_publication_source"]
        if not isinstance(source, str) or not source.strip() or len(source) > 512:
            raise ValueError("INSUFFICIENT_EVIDENCE: bounded feature publication source/reference required; authenticity unverified")
        try:
            published = _time(row["feature_published_at"])
        except (TypeError, AttributeError, ValueError, OverflowError) as exc:
            raise ValueError("INSUFFICIENT_EVIDENCE: invalid feature publication time") from exc
        if published >= d:
            raise ValueError("INSUFFICIENT_EVIDENCE: feature publication must strictly precede decision; source authenticity unverified")
        available = _time(row.get("outcome_available_at",row["settled_at"]))
        if available < s:
            raise ValueError("outcome availability before settlement")
        if not f <= d < e < s or prior and d <= prior:
            raise ValueError("feature as-of, execution, settlement or ordering leakage")
        if row["market_id"] in seen or not isinstance(row["market_id"], str) or not row["market_id"]:
            raise ValueError("dependent/repeated market")
        if "quote_at" in row:
            if not {"yes_ask","no_ask","yes_ask_size","no_ask_size"} <= set(row) or _time(row["quote_at"]) < e or _time(row["quote_at"]) >= s:
                raise ValueError("delayed quote as-of execution required")
            for side in ("yes","no"):
                _number(row[side+"_ask"],0,1); _number(row[side+"_ask_size"],0)

        seen.add(row["market_id"]); prior = d
        _number(row["feature"],0,1); _number(row["entry_price"],0,1)
        if not 0 < row["entry_price"] < 1 or type(row["outcome"]) is not int or row["outcome"] not in (0, 1) or not row["regime"]:
            raise ValueError("invalid entry or settlement")
    return _time(rows[0]["decision_at"]), prior


def _costs(cost):
    if not isinstance(cost, dict):
        return False
    try:
        for k in ("round_trip_fee", "slippage", "inference_usd"):
            _number(cost[k], 0)
        _number(cost["delay_seconds"], 0)
        _time(cost["as_of"])
        kind = cost["fee_source_kind"]
        source = cost["fee_source"]
        if kind == "synthetic":
            return bool(source and "fixture" in source.lower())
        # No official US fee receipt is bundled or authenticated here. A caller's
        # URL/hash/reviewer assertion cannot turn self-supplied costs into evidence.
        return False
    except (KeyError, ValueError, TypeError, OverflowError):
        return False


def _signal(row, spec):
    value = row[spec["feature"]]
    return "YES" if value >= spec["threshold"] else "NO"


def _selection_gate(returns, trials):
    """Conservative one-sided sign-test Bonferroni bound; no DSR without moments."""
    n=len(returns)
    if n<20 or any(x==0 for x in returns) or trials<1:
        return {"status":"BLOCKED", "reason":"at least 20 independent nonzero event returns required", "trials":trials, "deflated_sharpe":None}
    wins=sum(x>0 for x in returns)
    p=sum(math.comb(n,k) for k in range(wins,n+1))/2**n
    return {"status":"PASS" if p*trials<.05 else "REJECT", "method":"one-sided exact sign test, Bonferroni family trials", "n":n,"wins":wins,"trials":trials,"adjusted_p":min(1,round(p*trials,8)),"deflated_sharpe":None}


def _evaluate(rows, spec, cost):
    returns = []; regimes = {}; predictions = []; adverse=[]; mode="delayed_quote" if all("quote_at" in x for x in rows) else "declared_entry_unverified"
    if any(("quote_at" in x) != (mode=="delayed_quote") for x in rows): raise ValueError("mixed quoted and unquoted execution")
    for row in rows:
        if _time(row["entry_at"]) < _time(row["decision_at"]) + timedelta(seconds=cost["delay_seconds"]):
            raise ValueError("execution precedes declared inference delay")
        side = _signal(row, spec); predictions.append({"market_id": row["market_id"], "side": side, "decision_at": row["decision_at"]})
        if mode=="delayed_quote":
            if row[side.lower()+"_ask_size"] < 1: raise ValueError("no fill: insufficient delayed book size")
            entry=row[side.lower()+"_ask"]
        else:
            entry = row["entry_price"] if side == "YES" else 1-row["entry_price"]
        if not 0 < entry < 1: raise ValueError("unexecutable entry price")
        net = (row["outcome"] if side == "YES" else 1-row["outcome"]) - entry
        net -= cost["round_trip_fee"] + cost["slippage"] + cost["inference_usd"]
        returns.append(net); regimes.setdefault(row["regime"], []).append(net)
        adverse.append(net-cost["round_trip_fee"]-cost["slippage"])
    return {"n": len(returns), "net_total": round(sum(returns), 8), "returns_sha256": identity(returns), "net_returns": [round(x,8) for x in returns],
            "predictions": predictions, "execution":{"book_source":mode,"adverse_cost_delta":round(sum(returns)-sum(adverse),8),"adverse_net_total":round(sum(adverse),8),"regime_worst_net":round(min(sum(v) for v in regimes.values()),8)},
            "regime_stress": {k: {"n": len(v), "net_total": round(sum(v), 8)} for k, v in sorted(regimes.items())}}


def _outcome_boundary(rows):
    """Best-effort chronology for submitted development requests, including refusals.

    Malformed or unavailable timestamps are not credited as a completed evaluation;
    their refusal is retained but cannot establish an evidence-backed boundary.
    """
    if not isinstance(rows,list) or not rows: return None
    try:
        dates=[]
        for row in rows:
            settled=_time(row["settled_at"])
            available=_time(row.get("outcome_available_at",row["settled_at"]))
            if available<settled: return None
            dates.append(available)
        return max(dates).isoformat()
    except (TypeError,ValueError,KeyError,AttributeError):
        return None


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=30)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS hypotheses(id INTEGER PRIMARY KEY, family TEXT NOT NULL, slug TEXT NOT NULL,
            version TEXT NOT NULL, payload TEXT NOT NULL, manifest TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(family,version));
        CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
            family TEXT NOT NULL, stage TEXT NOT NULL, dataset_id TEXT NOT NULL, input_digest TEXT NOT NULL,
            status TEXT NOT NULL, result TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE UNIQUE INDEX IF NOT EXISTS one_family_holdout ON attempts(family) WHERE stage='holdout' AND status!='REFUSED';
        CREATE TABLE IF NOT EXISTS holdout_registry(key TEXT PRIMARY KEY, family TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS lessons(id INTEGER PRIMARY KEY, hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
            status TEXT NOT NULL, content TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS paper_links(id INTEGER PRIMARY KEY, hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
            source_path TEXT NOT NULL, line INTEGER NOT NULL, row_sha256 TEXT NOT NULL, paper_row TEXT NOT NULL,
            UNIQUE(source_path,line));
        CREATE TABLE IF NOT EXISTS paper_observations(id INTEGER PRIMARY KEY, hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
            link_id INTEGER NOT NULL UNIQUE REFERENCES paper_links(id), payload TEXT NOT NULL, net REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS critiques(id INTEGER PRIMARY KEY, week TEXT NOT NULL UNIQUE, report TEXT NOT NULL);
        """)
        for table in ("hypotheses", "attempts", "lessons", "paper_links", "paper_observations", "critiques"):
            for op in ("UPDATE", "DELETE"):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS frozen_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable'); END")

    def close(self): self.db.close()

    def _get(self, hid):
        row = self.db.execute("SELECT payload,manifest,family FROM hypotheses WHERE id=?", (hid,)).fetchone()
        if not row: raise ValueError("unknown hypothesis")
        return json.loads(row[0]), json.loads(row[1]), row[2]

    def attempt_count(self, family):
        return self.db.execute("SELECT COUNT(*) FROM attempts WHERE family=?", (family,)).fetchone()[0]

    def counts(self, family):
        """Separate intake, requests, refusals, and completed evaluations (including failed trials)."""
        intake=self.db.execute("SELECT COUNT(*) FROM hypotheses WHERE family=?",(family,)).fetchone()[0]
        requests,refusals,evaluations,variants=self.db.execute("""
            SELECT COUNT(*),
                COALESCE(SUM(CASE WHEN status='REFUSED' THEN 1 ELSE 0 END),0),
                COALESCE(SUM(CASE WHEN status='REJECTED' OR status LIKE 'PAPER_CANDIDATE%' THEN 1 ELSE 0 END),0),
                COUNT(DISTINCT CASE WHEN status='REJECTED' OR status LIKE 'PAPER_CANDIDATE%' THEN hypothesis_id END)
            FROM attempts WHERE family=?""",(family,)).fetchone()
        return {"intake_count":intake,"request_count":requests,"refusal_count":refusals,
                "evaluation_count":evaluations,"evaluated_variant_count":variants}

    def intake(self, proposal, manifest):
        required = ("family", "slug", "prediction", "expected_return", "expected_win_rate", "failure_condition", "signal", "risk", "version", "model_id", "evidence", "resolution_rules")
        if not isinstance(proposal, dict) or proposal.get("venue") != "polymarket_us" or "create_order" in proposal or any(not proposal.get(k) for k in required):
            raise ValueError("complete paper-only preregistration required")
        signal = proposal["signal"]; risk = proposal["risk"]
        if not isinstance(signal, dict) or set(signal) != {"kind", "feature", "threshold", "direction"} or signal["kind"] != "threshold" or signal["feature"] != "feature" or signal["direction"] != "yes_above_no_below":
            raise ValueError("unsupported executable signal")
        _number(signal["threshold"], 0, 1)
        if risk != {"paper_only": True, "max_shares": 1}:
            raise ValueError("paper-only one-share risk binding required")
        _number(proposal["expected_return"]); _number(proposal["expected_win_rate"], 0, 1)
        if not isinstance(manifest, dict) or any(not manifest.get(k) for k in ("development", "holdout", "source", "as_of")) or manifest["development"] == manifest["holdout"]:
            raise ValueError("distinct sealed datasets and source required")
        _time(manifest["as_of"]); _number(manifest["embargo_seconds"], 0)
        if type(manifest.get("min_validation_folds")) is not int or manifest["min_validation_folds"] < 2:
            raise ValueError("minimum validation folds required")
        if not isinstance(manifest.get("holdout_events"),list) or not manifest["holdout_events"] or any(not isinstance(e,str) or not e for e in manifest["holdout_events"]) or len(set(manifest["holdout_events"])) != len(manifest["holdout_events"]):
            raise ValueError("sealed holdout event ids required")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing=self.db.execute("SELECT manifest FROM hypotheses WHERE family=? LIMIT 1",(proposal["family"],)).fetchone()
            if existing:
                original=json.loads(existing[0])
                if original != manifest:
                    raise ValueError("family datasets, events and source already sealed")
            keys=["dataset:"+manifest["holdout"],"idea:"+identity({"slug":proposal["slug"],"prediction":proposal["prediction"],"resolution_rules":proposal["resolution_rules"]})]+["event:"+e for e in manifest["holdout_events"]]
            for key in keys:
                owner=self.db.execute("SELECT family FROM holdout_registry WHERE key=?",(key,)).fetchone()
                if owner and owner[0]!=proposal["family"]:
                    raise ValueError("global holdout reuse across family aliases")
            if self.db.execute("SELECT COUNT(*) FROM hypotheses WHERE family=?", (proposal["family"],)).fetchone()[0] >= 5:
                raise ValueError("family revision limit")
            failed = [x[0] for x in self.db.execute("SELECT content FROM lessons WHERE status IN ('failed','paused') ORDER BY id DESC LIMIT 30")]
            cur = self.db.execute("INSERT INTO hypotheses(family,slug,version,payload,manifest) VALUES(?,?,?,?,?)",
                                  (proposal["family"], proposal["slug"], proposal["version"], canonical(proposal), canonical(manifest)))
            for key in keys:
                self.db.execute("INSERT OR IGNORE INTO holdout_registry(key,family) VALUES(?,?)",(key,proposal["family"]))
            self.db.execute("COMMIT")
            return {"id": cur.lastrowid, "failed_lessons": failed, "manifest_sha256": identity(manifest)}
        except BaseException:
            self.db.execute("ROLLBACK"); raise

    def lesson(self, hid, content, status="failed"):
        self._get(hid)
        if status not in ("failed", "paused", "observation") or not isinstance(content, str) or not content.strip():
            raise ValueError("invalid lesson")
        self.db.execute("INSERT INTO lessons(hypothesis_id,status,content) VALUES(?,?,?)", (hid,status,content))

    def _request(self, hid, rows, costs, stage):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            proposal, manifest, family = self._get(hid)
            dataset = manifest["development" if stage == "development" else "holdout"]
            digest = identity({"rows":rows,"costs":costs})
            def record(status, result):
                if stage=="development":
                    boundary=_outcome_boundary(rows)
                    if boundary: result={**result,"last_outcome_available_at":boundary}
                self.db.execute("INSERT INTO attempts(hypothesis_id,family,stage,dataset_id,input_digest,status,result) VALUES(?,?,?,?,?,?,?)",
                                (hid,family,stage,dataset,digest,status,canonical(result)))
            try:
                if stage == "holdout" and self.db.execute("SELECT 1 FROM attempts WHERE family=? AND stage='holdout' AND status!='REFUSED'",(family,)).fetchone():
                    raise ValueError("family holdout already used")
                if stage == "development" and self.db.execute("SELECT 1 FROM attempts WHERE family=? AND stage='holdout' AND status!='REFUSED'",(family,)).fetchone():
                    raise ValueError("development closed after family holdout")
                if identity(rows) != dataset: raise ValueError("dataset digest mismatch")
                first,last = _rows(rows)
                family_latest=None
                if stage == "holdout":
                    prior = self.db.execute("SELECT input_digest,result FROM attempts WHERE hypothesis_id=? AND stage='development' AND status LIKE 'PAPER_CANDIDATE%' ORDER BY id DESC LIMIT 1", (hid,)).fetchone()
                    if not prior: raise ValueError("own passing development required before holdout")
                    bounds = json.loads(prior[1])
                    if bounds["dataset_id"]!=manifest["development"] or bounds["stage"]!="development" or bounds["cost_sha256"] != identity(costs) or bounds["candidate_sha256"] != identity(proposal) or bounds["manifest_sha256"] != identity(manifest):
                        raise ValueError("holdout candidate, data or cost identity mismatch")
                    if [x["market_id"] for x in rows] != manifest["holdout_events"]:
                        raise ValueError("holdout event registry mismatch")
                    histories=[json.loads(item[0]) for item in self.db.execute("SELECT result FROM attempts WHERE family=? AND stage='development'",(family,))]
                    if not histories or any(not history.get("last_outcome_available_at") for history in histories):
                        raise ValueError("family development outcome availability missing; review historical refusal")
                    family_latest=max(_time(history["last_outcome_available_at"]) for history in histories)
                    if first <= family_latest + timedelta(seconds=manifest["embargo_seconds"]):
                        raise ValueError("holdout crosses development embargo: family maximum outcome availability")
                    if set(bounds["market_ids"]) & {x["market_id"] for x in rows}: raise ValueError("holdout repeats development market")
                if not _costs(costs):
                    result = {"status":"INSUFFICIENT_DATA", "reason":"unverified fee/cost provenance", "deflated_sharpe":None, "multiple_testing":"BLOCKED"}
                    record(result["status"],result); self.db.execute("COMMIT"); return result
                if stage == "development":
                    folds = manifest["min_validation_folds"]
                    if len(rows) < folds*4: raise ValueError("insufficient independent development observations")
                    # Expanding chronological training prefix, embargo, then disjoint validation chunks.
                    splits=[]; width=max(2,len(rows)//(folds+1)); prior_stop=0
                    for i in range(folds):
                        end=(i+1)*width
                        if end < prior_stop: raise ValueError("overlapping embargo-separated validation")
                        train_cutoff=_time(rows[end]["decision_at"])
                        if any(_time(x.get("outcome_available_at",x["settled_at"])) > train_cutoff for x in rows[:end]):
                            raise ValueError("outcome availability after training cutoff")
                        last_available=max(_time(x.get("outcome_available_at",x["settled_at"])) for x in rows[:end])
                        start=next((j for j in range(end,len(rows)) if _time(rows[j]["decision_at"]) > last_available + timedelta(seconds=manifest["embargo_seconds"])),len(rows))
                        stop=min(start+width,len(rows))
                        if start < prior_stop or stop-start < 2: raise ValueError("insufficient embargo-separated validation")
                        prior_stop=stop
                        splits.append({"train_end":rows[end-1]["decision_at"], "validation_start":rows[start]["decision_at"],
                                      "validation_end":rows[stop-1]["decision_at"], **_evaluate(rows[start:stop],proposal["signal"],costs)})
                    validation_n=sum(x["n"] for x in splits); validation_net=sum(x["net_total"] for x in splits)
                    metric=_evaluate(rows,proposal["signal"],costs); metric["walk_forward"]=splits
                    qualifies=validation_n>=4 and validation_net>0 and all(x["net_total"]>0 for x in splits)
                else:
                    metric=_evaluate(rows,proposal["signal"],costs); metric["walk_forward"]=[]
                    qualifies=metric["n"]>=4 and metric["net_total"]>0
                synthetic=costs["fee_source_kind"]=="synthetic" or "fixture" in manifest["source"].lower()
                tallies=self.counts(family)
                prior_variant=self.db.execute("SELECT 1 FROM attempts WHERE hypothesis_id=? AND (status='REJECTED' OR status LIKE 'PAPER_CANDIDATE%') LIMIT 1",(hid,)).fetchone()
                trials=tallies["evaluation_count"]+1
                selection=_selection_gate(metric["net_returns"],trials)
                if not synthetic:
                    qualifies=qualifies and metric["execution"]["book_source"]=="delayed_quote" and metric["execution"]["adverse_net_total"]>0 and selection["status"]=="PASS"
                status=("PAPER_CANDIDATE_SYNTHETIC" if synthetic else "PAPER_CANDIDATE") if qualifies else "REJECTED"
                result={"status":status,"stage":stage,"dataset_id":dataset,"candidate_sha256":identity(proposal),"manifest_sha256":identity(manifest),"cost_sha256":identity(costs),"first_at":rows[0]["decision_at"],
                        "last_at":rows[-1]["decision_at"],"last_outcome_available_at":max((_time(x.get("outcome_available_at",x["settled_at"])) for x in rows)).isoformat(),"market_ids":[x["market_id"] for x in rows],
                        "attempt_count":tallies["request_count"]+1,"family_attempt_count":tallies["request_count"]+1,
                        "intake_count":tallies["intake_count"],"refusal_count":tallies["refusal_count"],
                        "evaluation_count":trials,"evaluated_variant_count":tallies["evaluated_variant_count"]+(0 if prior_variant else 1),
                        "family_max_outcome_available_at":family_latest.isoformat() if family_latest is not None else None,
                        "deflated_sharpe":None,"multiple_testing":selection,
                        "promotion":"PAPER_ONLY_NEVER_LIVE",**metric}
                record(status,result); self.db.execute("COMMIT"); return result
            except (ValueError,TypeError,KeyError) as exc:
                record("REFUSED",{"reason":str(exc),"status":"REFUSED"}); self.db.execute("COMMIT"); raise ValueError(str(exc)) from exc
        except BaseException:
            if self.db.in_transaction: self.db.execute("ROLLBACK")
            raise

    def develop(self,hid,rows,costs): return self._request(hid,rows,costs,"development")
    def holdout(self,hid,rows,costs): return self._request(hid,rows,costs,"holdout")

    def _synthetic_paper_only(self,hid):
        proposal,manifest,_=self._get(hid)
        if proposal.get('evidence')!='fixture' or manifest.get('source')!='offline synthetic fixture':
            raise ValueError('UNVERIFIED_OWNERSHIP: ordinary journal candidate tag is not a trusted owner decision receipt')
        return proposal

    def link_paper(self,hid,path,line):
        proposal=self._synthetic_paper_only(hid)
        if type(line) is not int or line<1: raise ValueError("invalid line")
        path=Path(path).resolve(strict=True)
        with path.open() as source:
            raw=next((x for i,x in enumerate(source,1) if i==line),None)
        if not raw: raise ValueError("paper row missing")
        row=json.loads(raw)
        if not isinstance(row,dict) or row.get("slug")!=proposal["slug"] or row.get("status")!="DRY/SHADOW" or (row.get("create_order") is not None and row.get("create_order") is not False) or not row.get("market_id") or row.get("research_candidate_sha256")!=identity(proposal):
            raise ValueError("not a matching candidate-owned DRY/SHADOW paper row")
        try:
            cur=self.db.execute("INSERT INTO paper_links(hypothesis_id,source_path,line,row_sha256,paper_row) VALUES(?,?,?,?,?)",
                                (hid,str(path),line,hashlib.sha256(raw.encode()).hexdigest(),canonical(row)))
        except sqlite3.IntegrityError as exc: raise ValueError("paper row already linked") from exc
        return {"status":"PAPER_LINKED","id":cur.lastrowid,"row_sha256":hashlib.sha256(raw.encode()).hexdigest()}

    def import_paper(self,hid,path,line,observation):
        self._synthetic_paper_only(hid)
        if not isinstance(observation,dict) or not isinstance(observation.get('costs'),dict) or observation['costs'].get('fee_source_kind')!='synthetic' or observation.get('evidence_source')!='offline synthetic fixture':
            raise ValueError('UNVERIFIED_OWNERSHIP: real paper import requires reviewed owner receipt integration')
        path=Path(path).resolve(strict=True)
        link=self.db.execute("SELECT id,paper_row,row_sha256 FROM paper_links WHERE hypothesis_id=? AND source_path=? AND line=?",(hid,str(path),line)).fetchone()
        if not link: raise ValueError("paper link required")
        with path.open() as source:
            raw=next((x for i,x in enumerate(source,1) if i==line),None)
        if not raw or hashlib.sha256(raw.encode()).hexdigest()!=link[2]: raise ValueError("paper source changed")
        paper=json.loads(link[1]); o=observation
        if not isinstance(o,dict) or o.get("create_order") is not False or o.get("status")!="PAPER_SETTLED" or o.get("market_id")!=paper["market_id"] or o.get("slug")!=paper["slug"] or o.get("journal_sha256")!=identity(raw.rstrip("\n")):
            raise ValueError("unverified paper observation or real fill")
        d,e,s=(_time(o[k]) for k in ("decision_at","entry_at","settled_at"))
        if not d<e<s or d!=_time(paper["ts"]) or o.get("side") not in ("YES","NO") or type(o.get("outcome")) is not int or o["outcome"] not in (0,1): raise ValueError("paper chronology/outcome invalid")
        proposal,manifest,_=self._get(hid)
        evidence=o.get("signal_evidence")
        if not isinstance(evidence,dict) or set(evidence)!={"feature","feature_at","candidate_sha256","regime"} or evidence["candidate_sha256"]!=identity(proposal) or _time(evidence["feature_at"])>d:
            raise ValueError("matched paper signal evidence missing")
        _number(evidence["feature"],0,1)
        if _number(paper.get("fv"),0,1)!=evidence["feature"]:
            raise ValueError("paper decision feature does not match journal")
        if _signal({proposal["signal"]["feature"]:evidence["feature"]},proposal["signal"])!=o["side"]:
            raise ValueError("paper side contradicts preregistered signal")
        if not any(x["status"].startswith("PAPER_CANDIDATE") and x["candidate_sha256"]==identity(proposal) for x in (json.loads(row[0]) for row in self.db.execute("SELECT result FROM attempts WHERE hypothesis_id=? AND stage='holdout'",(hid,)))):
            raise ValueError("matching validated holdout required")
        _number(o["entry_price"],0,1)
        if not 0<o["entry_price"]<1 or not _costs(o.get("costs")) or not o.get("evidence_source"):
            raise ValueError("paper costs or provenance missing")
        c=o["costs"]; net=(o["outcome"] if o["side"]=="YES" else 1-o["outcome"])-o["entry_price"]-c["round_trip_fee"]-c["slippage"]-c["inference_usd"]
        try:
            self.db.execute("INSERT INTO paper_observations(hypothesis_id,link_id,payload,net) VALUES(?,?,?,?)",(hid,link[0],canonical(o),net))
        except sqlite3.IntegrityError as exc: raise ValueError("paper settlement already imported") from exc
        return {"status":"PAPER_SETTLED_SYNTHETIC" if c["fee_source_kind"]=="synthetic" else "PAPER_SETTLED_UNVERIFIED", "net":net}

    def weekly_critic(self,week):
        boundary=_time(week)
        findings=[]
        for hid,payload in self.db.execute("SELECT id,payload FROM hypotheses"):
            proposal=json.loads(payload)
            evaluation=self.db.execute("SELECT result FROM attempts WHERE hypothesis_id=? AND status LIKE 'PAPER_CANDIDATE%' ORDER BY id DESC LIMIT 1",(hid,)).fetchone()
            papers=[p for p in self.db.execute("SELECT payload,net FROM paper_observations WHERE hypothesis_id=?",(hid,)).fetchall() if boundary-timedelta(days=7) <= _time(json.loads(p[0])["settled_at"]) < boundary]
            if not evaluation or not papers:
                comparison="INSUFFICIENT_DATA: validated evaluation or settled paper evidence missing"; state="paused"
            else:
                e=json.loads(evaluation[0]); observations=[json.loads(p[0]) for p in papers]
                synthetic=any(x["costs"]["fee_source_kind"]=="synthetic" for x in observations)
                regimes={x["signal_evidence"]["regime"] for x in observations}
                comparable=all(r in e["regime_stress"] for r in regimes)
                net=sum(p[1] for p in papers)
                matched_backtest=sum(e["regime_stress"][r]["net_total"] for r in regimes) if comparable else None
                if not comparable or (not synthetic and len(papers)<20):
                    comparison="INSUFFICIENT_DATA: matched regime missing or fewer than 20 independent paper events"; state="paused"
                else:
                    comparison=("negative paper net versus positive matched backtest" if net<=0 and matched_backtest is not None and matched_backtest>0 else "matched paper/backtest direction agrees") + (" [SYNTHETIC]" if synthetic else " [SETTLEMENT SOURCE UNVERIFIED]")
                    state="failed" if net<=0 else "observation"
            findings.append({"hypothesis_id":hid,"prediction":proposal["prediction"],"comparison":comparison,"paper_n":len(papers)})
        status="COMPARED_SYNTHETIC" if any("[SYNTHETIC]" in f["comparison"] for f in findings) else "INSUFFICIENT_DATA" if any("INSUFFICIENT_DATA" in f["comparison"] for f in findings) else "COMPARED_UNVERIFIED"
        report={"week":week,"status":status,"live_promotions":0,"findings":findings}
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT INTO critiques(week,report) VALUES(?,?)",(week,canonical(report)))
            for f in findings: self.lesson(f["hypothesis_id"],f"Week {week}: {f['comparison']}", "failed" if "negative" in f["comparison"] else "paused" if "INSUFFICIENT_DATA" in f["comparison"] else "observation")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK"); raise
        return report


def main(argv=None):
    parser=argparse.ArgumentParser(description="Offline paper-only research; never sends orders")
    parser.add_argument("command",choices=("intake","develop","holdout","link-paper","import-paper","critic","lessons","fixture"))
    parser.add_argument("--db",type=Path); parser.add_argument("--proposal",type=Path); parser.add_argument("--manifest",type=Path)
    parser.add_argument("--rows",type=Path); parser.add_argument("--costs",type=Path); parser.add_argument("--observation",type=Path)
    parser.add_argument("--id",type=int); parser.add_argument("--journal",type=Path); parser.add_argument("--line",type=int)
    parser.add_argument("--week"); parser.add_argument("--family")
    a=parser.parse_args(argv)
    if a.command=="fixture":
        from test_poly_research_workflow import rows,proposal,manifest,costs
        with tempfile.TemporaryDirectory() as folder:
            l=Ledger(Path(folder)/"fixture.sqlite")
            dev,final=rows(1,16),rows(17,8)
            hid=l.intake(proposal(),manifest(dev,final))["id"]
            development=l.develop(hid,dev,costs()); held=l.holdout(hid,final,costs())
            paper=Path(folder)/"ledger.jsonl"; paper.write_text(canonical({"slug":"fixture-us","market_id":"paper-1","status":"DRY/SHADOW","create_order":False,"ts":"2026-03-02T00:00:00+00:00","fv":.8,"mid":.5,"research_candidate_sha256":identity(proposal())})+"\n")
            l.link_paper(hid,paper,1)
            obs={"market_id":"paper-1","slug":"fixture-us","journal_sha256":identity(paper.read_text().splitlines()[0]),"decision_at":"2026-03-02T00:00:00+00:00","entry_at":"2026-03-02T01:00:00+00:00","settled_at":"2026-03-03T00:00:00+00:00","side":"YES","entry_price":.5,"outcome":0,"status":"PAPER_SETTLED","create_order":False,"costs":costs(),"evidence_source":"offline synthetic fixture","signal_evidence":{"feature":.8,"feature_at":"2026-03-02T00:00:00+00:00","candidate_sha256":identity(proposal()),"regime":"normal"}}
            imported=l.import_paper(hid,paper,1,obs); critique=l.weekly_critic("2026-03-09T00:00:00+00:00")
            lessons=l.intake(proposal("v2"),manifest(dev,final))["failed_lessons"]
            result={"label":"SYNTHETIC_OFFLINE_NOT_LIVE","development":development["status"],"holdout":held["status"],"paper":imported["status"],"critic":critique["status"],"lessons":lessons,"attempts":l.attempt_count("fixture-family")}
            l.close()
    else:
        if not a.db: parser.error("--db required")
        l=Ledger(a.db)
        try:
            load=lambda p: json.loads(p.read_text()) if p else None
            if a.command=="intake": result=l.intake(load(a.proposal),load(a.manifest))
            elif a.command in ("develop","holdout"): result=getattr(l,a.command)(a.id,load(a.rows),load(a.costs))
            elif a.command=="link-paper": result=l.link_paper(a.id,a.journal,a.line)
            elif a.command=="import-paper": result=l.import_paper(a.id,a.journal,a.line,load(a.observation))
            elif a.command=="critic": result=l.weekly_critic(a.week)
            else: result={"family":a.family,**l.counts(a.family),"attempt_count":l.attempt_count(a.family),"lessons":[x[0] for x in l.db.execute("SELECT content FROM lessons ORDER BY id DESC LIMIT 30")]}
        finally: l.close()
    print(canonical(result))

if __name__=="__main__": main()
