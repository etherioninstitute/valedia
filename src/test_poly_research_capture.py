import tempfile
import unittest
from pathlib import Path
from scripts.integrations.poly_research_capture import DOCS, capture, url_for

class Capture(unittest.TestCase):
    def test_allowlist_and_https(self):
        for kind,url in DOCS.items(): self.assertEqual(url_for(kind),url)
        with self.assertRaises(ValueError): url_for('fees','any')
        with self.assertRaises(ValueError): url_for('history','../order')
        with self.assertRaises(ValueError): url_for('create-order','m')
        self.assertEqual(url_for('history','market-a'),'https://gateway.polymarket.us/v1/price-history?symbol=market-a&fixedInterval=INTERVAL_1D&fidelity=5')
    def test_changed_response_recapture_keeps_both_bytes_and_db_rows(self):
        import hashlib
        from unittest.mock import patch
        from scripts.integrations.poly_research_intake import EvidenceDB, snapshot_capture
        class Response:
            status=200
            def __init__(self,body): self.body=body
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return self.body
        with tempfile.TemporaryDirectory() as folder:
            db=EvidenceDB(Path(folder)/'research.sqlite')
            with patch('urllib.request.OpenerDirector.open',side_effect=[Response(b'old'),Response(b'new')]):
                first=capture('fees',folder)
                first_body=Path(first['body_path']); first_receipt=Path(first['receipt_path'])
                self.assertEqual(snapshot_capture(db,first_receipt,first_body)['inserted'],1)
                second=capture('fees',folder)
                self.assertNotEqual(first['body_path'],second['body_path'])
                self.assertNotEqual(first['receipt_path'],second['receipt_path'])
                self.assertEqual(first_body.read_bytes(),b'old')
                self.assertEqual(hashlib.sha256(first_body.read_bytes()).hexdigest(),first['sha256'])
                self.assertEqual(snapshot_capture(db,second['receipt_path'],second['body_path'])['inserted'],1)
                self.assertEqual(db.status()['source_snapshots'],2)
                snapshots=[__import__('json').loads(row['snapshot']) for row in db.rows()]
                self.assertEqual(snapshots[0]['body_path'],first['body_path'])
                self.assertEqual(snapshots[1]['body_path'],second['body_path'])
                self.assertEqual(snapshots[0]['body_sha256'],first['sha256'])
                self.assertEqual(snapshot_capture(db,first_receipt,first_body)['inserted'],0)
            db.close()
    def test_existing_content_address_rejects_tampered_bytes(self):
        from unittest.mock import patch
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return b'original'
        with tempfile.TemporaryDirectory() as folder, patch('urllib.request.OpenerDirector.open',return_value=Response()):
            result=capture('fees',folder)
            Path(result['body_path']).write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'existing.*mismatch'):
                capture('fees',folder)
    def test_local_response_receipt_not_fill(self):
        import unittest.mock as mock
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return b'public source bytes'
        with tempfile.TemporaryDirectory() as folder, mock.patch('urllib.request.OpenerDirector.open',return_value=Response()):
            result=capture('fees',folder)
            self.assertEqual(result['status'],200)
            self.assertEqual(Path(result['body_path']).read_bytes(),b'public source bytes')
            self.assertTrue(Path(result['receipt_path']).is_file())
            self.assertEqual(result['provenance'],'HTTPS_PUBLIC_CAPTURE_NOT_EXECUTION_OR_FILL')

if __name__=='__main__':unittest.main()
