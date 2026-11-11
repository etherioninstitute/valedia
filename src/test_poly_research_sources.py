"""Offline source receipt shape tests: public display price is never a fill."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from poly_research_sources import read_receipt

class Sources(unittest.TestCase):
    def test_history_and_settlement(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"history.json"
            p.write_text(json.dumps({"history":[{"timestamp":1791417600,"longPrice":.52,"shortPrice":.51}]}))
            result=read_receipt(p,"history","fixture-us")
            self.assertFalse(result["executable"])
            self.assertEqual(len(result["points"]),1)
            self.assertNotIn("published_at",result["points"][0])
            self.assertNotIn("publication_source",result["points"][0])
            p.write_text(json.dumps({"history":[{"timestamp":1791417600,"longPrice":.52,"shortPrice":.51,
                                                 "published_at":"2026-10-07T15:00:00-07:00"}]}))
            supplied=read_receipt(p,"history","fixture-us")["points"][0]
            self.assertEqual(supplied["published_at"],"2026-10-07T22:00:00+00:00")
            self.assertIn("#sha256=",supplied["publication_source"])
            from test_poly_research_workflow import rows
            from poly_research_loop import _rows
            row=rows(1,1)[0]
            row["feature_published_at"]=supplied["published_at"]
            row["feature_publication_source"]=supplied["publication_source"]
            row["decision_at"]="2026-10-08T00:00:00+00:00"
            row["entry_at"]="2026-10-08T01:00:00+00:00"
            row["settled_at"]="2026-10-08T02:00:00+00:00"
            self.assertEqual(_rows([row])[0].isoformat(),row["decision_at"])
            p.write_text(json.dumps({"history":[{"timestamp":1791417600,"longPrice":.52,"shortPrice":.51,
                                                 "published_at":"bad"}]}))
            with self.assertRaisesRegex(ValueError,"publisher"): read_receipt(p,"history","fixture-us")
            p.write_text(json.dumps({"slug":"fixture-us","settlement":1}))
            self.assertEqual(read_receipt(p,"settlement","fixture-us")["outcome"],1)
            with self.assertRaises(ValueError): read_receipt(p,"settlement","wrong-slug")
            p.write_text(json.dumps({"history":[]}))
            with self.assertRaises(ValueError): read_receipt(p,"history","fixture-us")

    def test_malformed_source_values_raise_value_error(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"source.json"
            for point in ({"timestamp":None,"longPrice":.5,"shortPrice":.5},
                          {"timestamp":10**1000,"longPrice":.5,"shortPrice":.5},
                          {"timestamp":1791417600,"longPrice":None,"shortPrice":.5},
                          {"timestamp":1791417600,"longPrice":1e999,"shortPrice":.5},
                          None):
                with self.subTest(point=str(point)[:60]):
                    p.write_text(json.dumps({"history":[point]}))
                    with self.assertRaises(ValueError): read_receipt(p,"history","fixture-us")
            for settlement in (None, 1e999):
                p.write_text(json.dumps({"slug":"fixture-us","settlement":settlement}))
                with self.assertRaises(ValueError): read_receipt(p,"settlement","fixture-us")

if __name__=="__main__":unittest.main()
