"""Offline end-to-end and refusal tests: synthetic data are never live evidence."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).parent))
import poly_research_loop as r

HERE = Path(__file__).parent

def rows(start, n):
    def at(i,h): return (datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(days=i-1,hours=h)).isoformat()
    return [{"market_id": f"m{i}", "decision_at": at(i,0),
             "feature_at": at(i,-2), "feature_published_at": at(i,-1),
             "feature_publication_source": "offline synthetic fixture", "feature": .8 if i % 2 else .2,
             "entry_at": at(i,1), "entry_price": .5,
             "settled_at": at(i,2), "outcome": i % 2,
             "regime": "stress" if i % 4 == 0 else "normal"} for i in range(start, start+n)]

def proposal(version="v1"):
    return {"family": "fixture-family", "slug": "fixture-us", "version": version,
            "venue": "polymarket_us", "prediction": "feature above threshold predicts YES",
            "expected_return": .01, "expected_win_rate": .55, "failure_condition": "net<=0",
            "signal": {"kind": "threshold", "feature": "feature", "threshold": .6, "direction": "yes_above_no_below"},
            "risk": {"paper_only": True, "max_shares": 1}, "model_id": "fixture-deterministic",
            "evidence": "fixture", "resolution_rules": "binary fixture"}

def distinct(family):
    return {**proposal("v2"),"family":family,"slug":f"{family}-us","prediction":f"{family} preregistered fixture hypothesis"}

def manifest(dev, final):
    return {"development": r.identity(dev), "holdout": r.identity(final),
            "source": "offline synthetic fixture", "as_of": "2026-03-01T00:00:00+00:00",
            "holdout_events": [x["market_id"] for x in final],
            "embargo_seconds": 3600, "min_validation_folds": 2}

def costs():
    return {"round_trip_fee": .02, "slippage": .01, "inference_usd": .001,
            "delay_seconds": 0, "fee_source": "synthetic fixture only", "fee_source_kind": "synthetic",
            "as_of": "2026-03-01T00:00:00+00:00"}

class Workflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "research.db"
        self.db = r.Ledger(self.path); self.addCleanup(self.db.close)
        self.dev, self.final = rows(1, 16), rows(17, 8)
        self.hid = self.db.intake(proposal(), manifest(self.dev, self.final))["id"]

    def test_complete_synthetic_flow_and_critic(self):
        dev = self.db.develop(self.hid, self.dev, costs())
        self.assertEqual(dev["status"], "PAPER_CANDIDATE_SYNTHETIC")
        final = self.db.holdout(self.hid, self.final, costs())
        self.assertEqual(final["status"], "PAPER_CANDIDATE_SYNTHETIC")
        paper = Path(self.temp.name) / "ledger.jsonl"
        paper.write_text(json.dumps({"slug": "fixture-us", "market_id": "paper-1", "status": "DRY/SHADOW", "create_order": False,
                                     "ts": "2026-03-02T00:00:00+00:00", "fv": .8, "mid": .5,
                                     "research_candidate_sha256":r.identity(proposal())}) + "\n")
        self.db.link_paper(self.hid, paper, 1)
        observation = {"market_id": "paper-1", "slug": "fixture-us", "journal_sha256": r.identity(paper.read_text().splitlines()[0]),
                       "decision_at": "2026-03-02T00:00:00+00:00", "entry_at": "2026-03-02T01:00:00+00:00",
                       "settled_at": "2026-03-03T00:00:00+00:00", "side": "YES", "entry_price": .5,
                       "outcome": 0, "status": "PAPER_SETTLED", "create_order": False,
                       "costs": costs(), "evidence_source": "offline synthetic fixture",
                       "signal_evidence":{"feature":.8,"feature_at":"2026-03-02T00:00:00+00:00","candidate_sha256":r.identity(proposal()),"regime":"normal"}}
        self.db.import_paper(self.hid, paper, 1, observation)
        critique = self.db.weekly_critic("2026-03-09T00:00:00+00:00")
        self.assertEqual(critique["status"], "COMPARED_SYNTHETIC")
        self.assertTrue(any("negative" in x["comparison"] for x in critique["findings"]))
        self.assertEqual(self.db.weekly_critic("2026-03-16T00:00:00+00:00")["status"],"INSUFFICIENT_DATA")
        self.assertTrue(self.db.intake(proposal("v2"), manifest(self.dev, self.final))["failed_lessons"])

    def test_rejected_requests_count_and_holdout_family_one_use(self):
        with self.assertRaises(ValueError): self.db.develop(self.hid, self.dev[:-1], costs())
        self.assertEqual(self.db.attempt_count("fixture-family"), 1)
        self.db.develop(self.hid, self.dev, costs())
        self.db.holdout(self.hid, self.final, costs())
        other = self.db.intake(proposal("v2"), manifest(self.dev, self.final))["id"]
        with self.assertRaises(ValueError): self.db.holdout(other, self.final, costs())
        self.assertEqual(self.db.attempt_count("fixture-family"), 4)

    def test_publication_admission_is_strict_and_separately_sourced(self):
        from datetime import datetime, timedelta, timezone
        base = self.dev[0]
        self.assertEqual(r._rows([base])[0], datetime.fromisoformat(base["decision_at"]))
        for published in (base["decision_at"], "2026-01-01T00:00:00.000001+00:00"):
            with self.subTest(published=published), self.assertRaisesRegex(ValueError, "publication"):
                r._rows([{**base, "feature_published_at": published}])
        for broken in ({"feature_published_at": None}, {"feature_published_at": "nonsense"},
                       {"feature_published_at": "2025-12-31T23:59:59"},
                       {"feature_publication_source": ""}, {"feature_publication_source": "x" * 513}):
            row = dict(base); row.update(broken)
            with self.subTest(broken=broken), self.assertRaisesRegex(ValueError, "publication"):
                r._rows([row])
        for absent in ("feature_published_at", "feature_publication_source"):
            row = dict(base); row.pop(absent)
            with self.subTest(absent=absent), self.assertRaisesRegex(ValueError, "publication"):
                r._rows([row])
        # Offset-aware ISO timestamps denote the same instant; neither qualifies as strictly earlier.
        with self.assertRaisesRegex(ValueError, "publication"):
            r._rows([{**base, "feature_published_at": "2025-12-31T16:00:00-08:00"}])
        earlier = {**base, "feature_published_at": "2025-12-31T15:59:59-08:00"}
        self.assertEqual(r._rows([earlier])[0], datetime.fromisoformat(base["decision_at"]))

    def test_cli_equal_and_future_publication_refusals_persist_classification(self):
        for label, published in (("equal", self.dev[0]["decision_at"]),
                                 ("future", "2026-01-01T00:00:00.000001+00:00")):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                db = root / "research.sqlite"
                dev = [{**row, "feature_published_at": published} if i == 0 else row
                       for i, row in enumerate(self.dev)]
                for name, payload in (("proposal", proposal()), ("manifest", manifest(dev, self.final)),
                                      ("rows", dev), ("costs", costs())):
                    (root / (name + ".json")).write_text(json.dumps(payload))
                command = [sys.executable, str(HERE / "poly_research_loop.py")]
                intake = subprocess.run(command + ["intake", "--db", str(db),
                    "--proposal", str(root / "proposal.json"), "--manifest", str(root / "manifest.json")],
                    capture_output=True, text=True)
                self.assertEqual(intake.returncode, 0, intake.stderr)
                hid = json.loads(intake.stdout)["id"]
                developed = subprocess.run(command + ["develop", "--db", str(db), "--id", str(hid),
                    "--rows", str(root / "rows.json"), "--costs", str(root / "costs.json")],
                    capture_output=True, text=True)
                self.assertNotEqual(developed.returncode, 0)
                self.assertIn("INSUFFICIENT_EVIDENCE: feature publication must strictly precede decision", developed.stderr)
                with sqlite3.connect(db) as connection:
                    attempts = connection.execute("SELECT status,result FROM attempts WHERE hypothesis_id=?", (hid,)).fetchall()
                self.assertEqual(len(attempts), 1)
                self.assertEqual(attempts[0][0], "REFUSED")
                self.assertIn("INSUFFICIENT_EVIDENCE: feature publication must strictly precede decision",
                              json.loads(attempts[0][1])["reason"])

    def test_missing_mirofish_publication_refuses_but_real_publisher_metadata_admits(self):
        base = self.dev[0]
        for absent in ("feature_published_at", "feature_publication_source"):
            row = dict(base); row.pop(absent)
            with self.assertRaisesRegex(ValueError, "INSUFFICIENT_EVIDENCE"):
                r._rows([row])
        for published in ("2025-12-31T21:00:00+00:00",  # before feature calculation
                          base["feature_at"],                 # equal to feature calculation
                          "2025-12-31T23:00:00+00:00"):  # after calculation, before decision
            row = {**base, "feature_published_at": published,
                   "feature_publication_source": "publisher event 123 / captured source ref sha256:abc"}
            with self.subTest(published=published):
                self.assertEqual(r._rows([row])[0].isoformat(), base["decision_at"])

    def test_real_labelled_synthetic_fixture_admits_publisher_metadata(self):
        real_rows = [{**x, "feature_publication_source": "publisher event 123 / capture ref abc"} for x in self.dev]
        real_manifest = {**manifest(real_rows, rows(33, 8)), "source": "captured historical data"}
        hid = self.db.intake(distinct("real-labelled-producer"), real_manifest)["id"]
        # Costs are still synthetic: this establishes admission, not real edge or promotion.
        result = self.db.develop(hid, real_rows, costs())
        self.assertEqual(result["status"], "PAPER_CANDIDATE_SYNTHETIC")

    def test_leakage_refused_and_cost_provenance_closed(self):
        leak = [dict(x) for x in self.dev]; leak[0]["feature_at"] = "2026-01-01T00:30:00+00:00"
        alt = self.db.intake(distinct("other-family"), manifest(leak, rows(25,8)))["id"]
        with self.assertRaises(ValueError): self.db.develop(alt, leak, costs())
        missing = self.db.develop(self.hid, self.dev, {**costs(), "fee_source_kind": "unknown"})
        self.assertEqual(missing["status"], "INSUFFICIENT_DATA")
        with self.assertRaises(ValueError): self.db.develop(self.hid, [{**x, "prediction": .8} for x in self.dev], costs())

    def test_family_cannot_reseal_holdout_by_variant(self):
        changed = manifest(self.dev, rows(25, 8))
        with self.assertRaises(ValueError):
            self.db.intake(proposal("v2"), changed)

    def test_holdout_requires_own_development_and_same_costs(self):
        self.db.develop(self.hid,self.dev,costs())
        v2=self.db.intake(proposal("v2"),manifest(self.dev,self.final))["id"]
        with self.assertRaisesRegex(ValueError,"own passing development"):
            self.db.holdout(v2,self.final,costs())
        self.db.develop(v2,self.dev,costs())
        with self.assertRaisesRegex(ValueError,"cost"):
            self.db.holdout(v2,self.final,{**costs(),"slippage":.02})
        self.assertEqual(self.db.holdout(v2,self.final,costs())["status"],"PAPER_CANDIDATE_SYNTHETIC")

    def test_alias_family_cannot_register_same_holdout_or_event(self):
        with self.assertRaisesRegex(ValueError,"global.*reuse"):
            self.db.intake({**proposal(),"family":"alias","version":"new"},manifest(self.dev,self.final))
        with self.assertRaisesRegex(ValueError,"global.*reuse"):
            self.db.intake({**proposal(),"family":"alias","version":"new"},manifest(rows(1,16),rows(40,8)))
        overlapping=rows(25,8); overlapping[0]={**overlapping[0],"market_id":self.final[0]["market_id"]}
        with self.assertRaisesRegex(ValueError,"global.*reuse"):
            self.db.intake({**proposal(),"family":"alias","version":"new"},manifest(rows(1,16),overlapping))

    def test_outcome_availability_and_overlapping_validation_refused(self):
        stale=[dict(x) for x in self.dev]
        stale[0]["outcome_available_at"]="2026-02-01T00:00:00+00:00"
        hid=self.db.intake(distinct("fresh-family"),manifest(stale,rows(25,8)))["id"]
        with self.assertRaisesRegex(ValueError,"outcome availability"):
            self.db.develop(hid,stale,costs())
        overlapping=[dict(x) for x in self.dev]
        for x in overlapping: x["settled_at"]="2026-01-31T23:00:00+00:00"
        hid2=self.db.intake(distinct("overlap-family"),manifest(overlapping,rows(33,8)))["id"]
        with self.assertRaisesRegex(ValueError,"outcome availability|embargo-separated validation"):
            self.db.develop(hid2,overlapping,costs())

    def test_delayed_quote_price_size_and_adverse_cost(self):
        quoted=[{**x,"quote_at":x["entry_at"],"yes_ask":.55,"no_ask":.50,"yes_ask_size":1,"no_ask_size":1} for x in self.dev]
        hid=self.db.intake(distinct("quotes-family"),manifest(quoted,rows(33,8)))["id"]
        metric=self.db.develop(hid,quoted,costs())
        self.assertEqual(metric["execution"]["book_source"],"delayed_quote")
        self.assertGreater(metric["execution"]["adverse_cost_delta"],0)
        nofill=[dict(x) for x in quoted]; nofill[0]["yes_ask_size"]=0; nofill[0]["no_ask_size"]=0
        hid2=self.db.intake(distinct("no-fill-family"),manifest(nofill,rows(41,8)))["id"]
        with self.assertRaisesRegex(ValueError,"no fill"):
            self.db.develop(hid2,nofill,costs())

    def test_holdout_waits_for_last_development_outcome(self):
        dev=[dict(x) for x in self.dev]
        dev[-1]["outcome_available_at"]="2026-02-15T00:00:00+00:00"
        hid=self.db.intake(distinct("late-dev"),manifest(dev,rows(25,8)))["id"]
        self.db.develop(hid,dev,costs())
        with self.assertRaisesRegex(ValueError,"holdout crosses development embargo"):
            self.db.holdout(hid,rows(25,8),costs())

    def test_critic_does_not_reuse_other_weeks_paper(self):
        self.db.develop(self.hid,self.dev,costs()); self.db.holdout(self.hid,self.final,costs())
        report=self.db.weekly_critic("2026-04-01T00:00:00+00:00")
        self.assertEqual(report["status"],"INSUFFICIENT_DATA")

    def test_sibling_late_outcome_even_refused_blocks_family_holdout(self):
        first=self.db.develop(self.hid,self.dev,costs())
        self.assertEqual(first["status"],"PAPER_CANDIDATE_SYNTHETIC")
        sibling=self.db.intake(proposal("v2"),manifest(self.dev,self.final))["id"]
        late=[dict(x) for x in self.dev]
        late[-1]["outcome_available_at"]="2026-02-15T00:00:00+00:00"
        with self.assertRaisesRegex(ValueError,"dataset digest mismatch"):
            self.db.develop(sibling,late,costs())
        self.assertEqual(self.db.attempt_count("fixture-family"),2)
        with self.assertRaisesRegex(ValueError,"holdout crosses development embargo"):
            self.db.holdout(self.hid,self.final,costs())
        refused=self.db.db.execute("SELECT result FROM attempts WHERE hypothesis_id=? AND status='REFUSED'",(sibling,)).fetchone()
        self.assertEqual(json.loads(refused[0])["last_outcome_available_at"],"2026-02-15T00:00:00+00:00")

    def test_evaluated_variants_distinct_from_intake_refusals_and_requests(self):
        sibling=self.db.intake(proposal("v2"),manifest(self.dev,self.final))["id"]
        with self.assertRaises(ValueError): self.db.develop(sibling,self.dev[:-1],costs())
        self.assertEqual(self.db.counts("fixture-family"),{"intake_count":2,"request_count":1,"refusal_count":1,"evaluation_count":0,"evaluated_variant_count":0})
        one=self.db.develop(self.hid,self.dev,costs())
        self.assertEqual(one["evaluated_variant_count"],1)
        self.assertEqual(one["multiple_testing"]["trials"],1 if one["multiple_testing"]["status"]!="BLOCKED" else one["multiple_testing"].get("trials",1))
        self.db.develop(self.hid,self.dev,costs())
        self.db.develop(sibling,self.dev,costs())
        self.assertEqual(self.db.counts("fixture-family"),{"intake_count":2,"request_count":4,"refusal_count":1,"evaluation_count":3,"evaluated_variant_count":2})
        third=self.db.holdout(sibling,self.final,costs())
        self.assertEqual(third["evaluated_variant_count"],2)
        self.assertEqual(third["multiple_testing"]["trials"],4 if third["multiple_testing"]["status"]!="BLOCKED" else third["multiple_testing"].get("trials",4))

    def test_unknown_refusal_chronology_fails_closed_and_failed_trial_retained(self):
        self.db.develop(self.hid,self.dev,costs())
        sibling=self.db.intake(proposal("v2"),manifest(self.dev,self.final))["id"]
        bad=[dict(x) for x in self.dev]; bad[-1].pop("settled_at")
        with self.assertRaises(ValueError): self.db.develop(sibling,bad,costs())
        with self.assertRaisesRegex(ValueError,"outcome availability missing"):
            self.db.holdout(self.hid,self.final,costs())
        self.assertEqual(self.db.counts("fixture-family")["refusal_count"],2)
        losses=[{**x,"outcome":1-x["outcome"]} for x in self.dev]
        other=self.db.intake(distinct("failed-trial-family"),manifest(losses,rows(41,8)))["id"]
        failed=self.db.develop(other,losses,costs())
        self.assertEqual(failed["status"],"REJECTED")
        self.assertEqual(self.db.counts("failed-trial-family")["evaluated_variant_count"],1)
        self.assertEqual(self.db.counts("failed-trial-family")["evaluation_count"],1)

    def test_statistical_gate_uses_evaluations_not_refused_requests(self):
        from scripts.integrations.poly_research_loop import _selection_gate
        self.assertEqual(_selection_gate([0.1]*20,1)["trials"],1)
        self.assertEqual(_selection_gate([0.1]*20,4)["trials"],4)
        self.assertEqual(_selection_gate([0.1]*20,4)["adjusted_p"],round(4/2**20,8))

    def test_ordinary_journal_candidate_tag_cannot_link_or_import_real(self):
        self.db.develop(self.hid,self.dev,costs()); self.db.holdout(self.hid,self.final,costs())
        # Even if a caller injects the exact registered hash, the ordinary journal
        # is not an independently attested owner decision receipt.
        real=distinct('real-unverified-family')
        real_hid=self.db.intake(real,{**manifest(rows(33,16),rows(49,8)),'source':'public unverified'})['id']
        paper=Path(self.temp.name)/'ordinary.jsonl'
        paper.write_text(json.dumps({'slug':real['slug'],'market_id':'m','status':'DRY/SHADOW',
            'ts':'2026-03-02T00:00:00+00:00','fv':.55,'create_order':False,
            'research_candidate_sha256':r.identity(real)})+'\n')
        from scripts.integrations.poly_research_intake import EvidenceDB,snapshot_journal
        diagnostic=EvidenceDB(self.path)
        try:
            self.assertEqual(snapshot_journal(diagnostic,paper)['ownership'],'UNOWNED')
            self.assertIsNone(diagnostic.rows()[0]['candidate_sha256'])
        finally: diagnostic.close()
        with self.assertRaisesRegex(ValueError,'UNVERIFIED_OWNERSHIP'):
            self.db.link_paper(real_hid,paper,1)
        with self.assertRaisesRegex(ValueError,'UNVERIFIED_OWNERSHIP'):
            self.db.import_paper(real_hid,paper,1,{'status':'PAPER_SETTLED'})
    def test_valid_preregistered_no_paper_and_wrong_side_refused(self):
        self.db.develop(self.hid,self.dev,costs()); self.db.holdout(self.hid,self.final,costs())
        paper=Path(self.temp.name)/"no.jsonl"
        raw=json.dumps({"slug":"fixture-us","market_id":"no-paper","status":"DRY/SHADOW","create_order":False,
                        "ts":"2026-03-02T00:00:00+00:00","fv":.55,"mid":.5,
                        "research_candidate_sha256":r.identity(proposal())})
        paper.write_text(raw+"\n")
        self.db.link_paper(self.hid,paper,1)
        observation={"market_id":"no-paper","slug":"fixture-us","journal_sha256":r.identity(raw),
                     "decision_at":"2026-03-02T00:00:00+00:00","entry_at":"2026-03-02T01:00:00+00:00",
                     "settled_at":"2026-03-03T00:00:00+00:00","side":"NO","entry_price":.5,
                     "outcome":0,"status":"PAPER_SETTLED","create_order":False,"costs":costs(),
                     "evidence_source":"offline synthetic fixture","signal_evidence":{"feature":.55,
                     "feature_at":"2026-03-02T00:00:00+00:00","candidate_sha256":r.identity(proposal()),"regime":"normal"}}
        with self.assertRaisesRegex(ValueError,"preregistered signal"):
            self.db.import_paper(self.hid,paper,1,{**observation,"side":"YES"})
        with self.assertRaisesRegex(ValueError,'UNVERIFIED_OWNERSHIP'):
            self.db.import_paper(self.hid,paper,1,{**observation,'costs':{**costs(),'fee_source_kind':'official'}})
        self.assertEqual(self.db.import_paper(self.hid,paper,1,observation)["status"],"PAPER_SETTLED_SYNTHETIC")

    def test_cli_help_and_fixture_run(self):
        result = subprocess.run([sys.executable, str(HERE / "poly_research_loop.py"), "fixture"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["label"], "SYNTHETIC_OFFLINE_NOT_LIVE")

if __name__ == "__main__": unittest.main()
