"""Corrective safety regressions; attempt-1 source preserved under recommendations/poly-research-attempt-1."""
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import poly_research_loop as r
from test_poly_research_workflow import rows, proposal, manifest, costs

class ResearchLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"research.sqlite"
        self.ledger=r.Ledger(self.path); self.addCleanup(self.ledger.close)
        self.dev,self.final=rows(1,16),rows(17,8)
        self.manifest=manifest(self.dev,self.final)
        self.id=self.ledger.intake(proposal(),self.manifest)["id"]

    def test_intake_immutable_and_lessons(self):
        self.ledger.lesson(self.id,"bad stress forecast","failed")
        self.assertIn("bad stress forecast",self.ledger.intake(proposal("v2"),self.manifest)["failed_lessons"])
        with self.assertRaises(sqlite3.DatabaseError): self.ledger.db.execute("UPDATE hypotheses SET payload='{}' WHERE id=?",(self.id,))
        with self.assertRaises(sqlite3.IntegrityError): self.ledger.intake(proposal(),self.manifest)

    def test_fee_failure_retains_count_and_blocks_stats(self):
        insufficient=self.ledger.develop(self.id,self.dev,{**costs(),"fee_source":""})
        self.assertEqual(insufficient["status"],"INSUFFICIENT_DATA")
        result=self.ledger.develop(self.id,self.dev,costs())
        self.assertIsNone(result["deflated_sharpe"])
        self.assertEqual(result["attempt_count"],2)
        self.assertTrue(result["walk_forward"])
        self.assertIn("stress",result["regime_stress"])

    def test_refusal_then_one_use_family_across_connections(self):
        peer=r.Ledger(self.path); self.addCleanup(peer.close)
        with self.assertRaises(ValueError): self.ledger.holdout(self.id,self.dev,costs())
        self.assertEqual(self.ledger.attempt_count("fixture-family"),1)
        self.ledger.develop(self.id,self.dev,costs())
        other=self.ledger.intake(proposal("v2"),self.manifest)["id"]
        results=[]
        def attempt(hid):
            db=r.Ledger(self.path)
            try:
                try: results.append(db.holdout(hid,self.final,costs()))
                except ValueError as exc: results.append(str(exc))
            finally: db.close()
        a=threading.Thread(target=attempt,args=(self.id,)); b=threading.Thread(target=attempt,args=(other,))
        a.start(); b.start(); a.join(); b.join()
        self.assertEqual(sum(isinstance(x,dict) for x in results),1)
        self.assertEqual(sum("already used" in str(x) or "own passing development required" in str(x) for x in results),1,repr(results))
        self.assertEqual(peer.attempt_count("fixture-family"),4)

    def test_paper_link_read_only_and_no_live_row(self):
        path=Path(self.tmp.name)/"ledger.jsonl"
        row={"slug":"fixture-us","market_id":"paper-1","status":"DRY/SHADOW","create_order":False,"ts":"2026-03-02T00:00:00+00:00","fv":.8,"mid":.5,"research_candidate_sha256":r.identity(proposal())}
        path.write_text(json.dumps(row)+"\n")
        self.assertEqual(self.ledger.link_paper(self.id,path,1)["status"],"PAPER_LINKED")
        self.assertEqual(path.read_text(),json.dumps(row)+"\n")
        with self.assertRaises(ValueError): self.ledger.link_paper(self.id,path,1)
        path2=Path(self.tmp.name)/"live.jsonl"; path2.write_text(json.dumps({**row,"create_order":True})+"\n")
        with self.assertRaises(ValueError): self.ledger.link_paper(self.id,path2,1)
        path3=Path(self.tmp.name)/"other.jsonl"; path3.write_text(json.dumps({**row,"slug":"different"})+"\n")
        with self.assertRaises(ValueError): self.ledger.link_paper(self.id,path3,1)
        path4=Path(self.tmp.name)/"unowned.jsonl"; unowned=dict(row); unowned.pop("research_candidate_sha256"); path4.write_text(json.dumps(unowned)+"\n")
        with self.assertRaisesRegex(ValueError,"candidate-owned"): self.ledger.link_paper(self.id,path4,1)

    def test_missing_paper_truth_pauses_and_no_promotion(self):
        report=self.ledger.weekly_critic("2026-03-09T00:00:00+00:00")
        self.assertEqual(report["status"],"INSUFFICIENT_DATA")
        self.assertEqual(report["live_promotions"],0)
        with self.assertRaises(sqlite3.IntegrityError): self.ledger.weekly_critic("2026-03-09T00:00:00+00:00")
        self.assertTrue(any("paper" in x.lower() for x in self.ledger.intake(proposal("v2"),self.manifest)["failed_lessons"]))

    def test_rejects_international_or_order_fields(self):
        with self.assertRaises(ValueError): self.ledger.intake({**proposal("v2"),"venue":"polymarket"},self.manifest)
        with self.assertRaises(ValueError): self.ledger.intake({**proposal("v2"),"create_order":True},self.manifest)

if __name__=="__main__": unittest.main()
