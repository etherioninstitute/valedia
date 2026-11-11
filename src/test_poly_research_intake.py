"""Read-only source intake never confers candidate ownership or fills."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from scripts.integrations.poly_research_intake import EvidenceDB, snapshot_journal, snapshot_source, weekly

class EvidenceIntake(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=EvidenceDB(self.root/'research.sqlite')
        self.addCleanup(self.db.close)
    def test_native_dry_shadow_unowned_idempotent_and_immutable(self):
        journal=self.root/'ledger.jsonl'
        journal.write_text(json.dumps({'ts':'2026-10-08T12:00:00Z','status':'DRY/SHADOW','market_id':'m1','slug':'market-a','fv':.6,'mid':.5})+'\n'+json.dumps({'ts':'2026-10-08T12:01:00Z','status':'BLOCKED','slug':'market-b'})+'\n')
        first=snapshot_journal(self.db,journal)
        self.assertEqual(first['inserted'],2)
        self.assertEqual(snapshot_journal(self.db,journal)['inserted'],0)
        rows=self.db.rows()
        with self.assertRaises(sqlite3.DatabaseError):
            self.db.db.execute('UPDATE source_snapshots SET ownership=? WHERE id=?',('OWNED',rows[0]['id']))
        self.assertEqual(rows[0]['ownership'],'UNOWNED')
        self.assertEqual(rows[0]['provenance'],'UNVERIFIED')
        self.assertEqual(rows[0]['kind'],'DRY/SHADOW')
        self.assertIsNone(rows[0]['candidate_sha256'])
        self.assertEqual(rows[0]['is_fill'],0)
        self.assertEqual(rows[1]['kind'],'BLOCKED')
        journal.write_text('changed\n')
        self.assertEqual(self.db.rows(),rows)
    def test_no_forged_ownership_or_order_and_refusal_diagnostic(self):
        journal=self.root/'ledger.jsonl'
        journal.write_text(json.dumps({'ts':'2026-10-08T12:00:00Z','status':'DRY/SHADOW','slug':'a','research_candidate_sha256':'f'*64,'create_order':True})+'\n'+json.dumps({'status':'DRY/SHADOW','slug':'bad'})+'\n')
        result=snapshot_journal(self.db,journal)
        self.assertEqual(result['refused'],2)
        self.assertEqual(result['inserted'],0)
        self.assertEqual(self.db.status()['candidate_owned_performance_ready'],False)
    def test_source_receipt_unverified_not_executable_or_settled_paper(self):
        p=self.root/'history.json'
        p.write_text(json.dumps({'history':[{'timestamp':1791417600,'longPrice':.55,'shortPrice':.48}]}))
        result=snapshot_source(self.db,p,'history','market-a')
        self.assertEqual(result['inserted'],1)
        self.assertEqual(snapshot_source(self.db,p,'history','market-a')['inserted'],0)
        row=self.db.rows()[0]
        self.assertEqual(row['provenance'],'UNVERIFIED')
        self.assertEqual(row['is_fill'],0)
        self.assertIsNone(row['candidate_sha256'])
        self.assertFalse(self.db.status()['candidate_owned_performance_ready'])
    def test_capture_receipt_hash_verified_locally_no_ownership(self):
        import hashlib
        from scripts.integrations.poly_research_intake import snapshot_capture
        body=self.root/'fees.body'; body.write_bytes(b'public text')
        receipt=self.root/'fees.receipt.json'
        receipt.write_text(json.dumps({'kind':'fees','url':'https://docs.polymarket.us/fees.md',
            'status':200,'sha256':hashlib.sha256(body.read_bytes()).hexdigest(),
            'bytes':len(body.read_bytes()),
            'received_at_utc':'2026-10-08T19:00:00+00:00'}))
        self.assertEqual(snapshot_capture(self.db,receipt,body)['inserted'],1)
        self.assertEqual(snapshot_capture(self.db,receipt,body)['inserted'],0)
        self.assertEqual(self.db.rows()[0]['provenance'],'UNVERIFIED')
        body.write_bytes(b'other texts')
        with self.assertRaisesRegex(ValueError,'hash mismatch'): snapshot_capture(self.db,receipt,body)
    def test_capture_metadata_coherence_before_insert(self):
        import hashlib
        from scripts.integrations.poly_research_intake import snapshot_capture
        body=self.root/'fees.body'; body.write_bytes(b'abc')
        receipt=self.root/'fees.receipt.json'
        base={'kind':'fees','url':'https://docs.polymarket.us/fees.md','status':200,
              'sha256':hashlib.sha256(b'abc').hexdigest(),'bytes':3,
              'body_path':str(body.resolve()),'requested_at_utc':'2026-10-08T18:00:00+00:00',
              'received_at_utc':'2026-10-08T19:00:00+00:00'}
        for key,value in [('bytes',999),('bytes',True),('bytes',3.0),('bytes',None),
                          ('body_path',str(self.root/'other.body')),('status',True),
                          ('received_at_utc','2026-10-08T17:00:00+00:00')]:
            bad=dict(base); bad[key]=value; receipt.write_text(json.dumps(bad))
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                snapshot_capture(self.db,receipt,body)
        self.assertEqual(self.db.rows(),[])
        receipt.write_text(json.dumps(base))
        self.assertEqual(snapshot_capture(self.db,receipt,body)['inserted'],1)
    def test_bounded_source_and_weekly_no_spurious_comparison(self):
        p=self.root/'ledger.jsonl'; p.write_text('{}\n'*4)
        with self.assertRaisesRegex(ValueError,'bounded'): snapshot_journal(self.db,p,max_lines=2)
        report=weekly(self.db,'2026-10-09T00:00:00+00:00')
        self.assertEqual(report['status'],'INSUFFICIENT_CANDIDATE_OWNED_PAPER')
        self.assertEqual(report['live_promotions'],0)
        self.assertEqual(weekly(self.db,'2026-10-09T00:00:00+00:00'),report)

if __name__=='__main__': unittest.main()
