import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from ta_research.adapter import TAError
from ta_research.bridge import capture,vendor,extract,upstream_config,run_mocked

NOW='2026-10-14T16:00:00Z'; BEFORE='2026-10-14T15:55:00Z'
def fixture():
    body={'schema':'valedia.ta1.snapshot.v1','asset_type':'stock','symbol':'AAPL','as_of':NOW,'captured_at':BEFORE,
          'tools':{'get_stock_data':[{'observed_at':BEFORE,'available_at':BEFORE,'value':'one bounded row'}]}}
    raw=json.dumps(body,sort_keys=True).encode()
    req={'asset_type':'stock','symbol':'AAPL','as_of':NOW,'trade_date':'2026-10-14','source':'offline capture',
         'source_sha256':hashlib.sha256(raw).hexdigest(),'captured_at':BEFORE,'model_provider':'local-openai-compatible',
         'model_name':'local-test','model_base_url':'http://127.0.0.1:8081/v1','data_mode':'approved-snapshot',
         'max_cost_usd':0,'analysts':['market']}
    return req,body,raw
class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'snapshot.json';self.req,self.body,raw=fixture();self.path.write_bytes(raw)
    def test_bytes_and_vendor(self):
        snap=capture(self.req,str(self.path))['snapshot'];self.assertIn('one bounded row',vendor(snap,'get_stock_data'))
        with self.assertRaises(TAError):vendor(snap,'get_news')
    def test_tamper(self):
        self.path.write_text('{}')
        with self.assertRaises(TAError):capture(self.req,str(self.path))
    def test_symlink(self):
        link=Path(self.temp.name)/'link';link.symlink_to(self.path)
        with self.assertRaises(TAError):capture(self.req,str(link))
    def test_lookahead(self):
        self.body['tools']['get_stock_data'][0]['available_at']='2026-10-15T00:00:00Z'
        raw=json.dumps(self.body,sort_keys=True).encode();self.path.write_bytes(raw);self.req['source_sha256']=hashlib.sha256(raw).hexdigest()
        with self.assertRaises(TAError):capture(self.req,str(self.path))
    def test_signature_and_decision_advisory(self):
        req=self.req
        class Graph:
            def __init__(self, parent): self.parent=parent
            def propagate(self,symbol,date,asset_type):
                self.parent.assertEqual((symbol,date,asset_type),('AAPL','2026-10-14','stock'))
                return ({'company_of_interest':symbol,'trade_date':date,'asset_type':asset_type,
                         'market_report':'observed','investment_debate_state':{'history':'debated'},
                         'final_trade_decision':'BUY'},'BUY')
        def factory(*,selected_analysts,debug,config,callbacks,snapshot_vendor):
            self.assertEqual(selected_analysts,['market']);self.assertFalse(debug);self.assertIsNone(callbacks)
            self.assertEqual(config['llm_provider'],'openai');self.assertEqual(config['max_debate_rounds'],1)
            self.assertIn('one bounded row',snapshot_vendor('get_stock_data'))
            return Graph(self)
        receipt=run_mocked(req,str(self.path),factory,results_dir=self.temp.name)
        self.assertEqual(receipt['decision_advisory'],'BUY');self.assertFalse(receipt['order_link'])
        self.assertEqual(receipt['paper_intake'],'DIAGNOSTIC_AVAILABLE_UNOWNED')
    def test_identity_conflict(self):
        req=self.req;req['symbol']='MSFT'
        with self.assertRaises(TAError):capture(req,str(self.path))
    def test_state_mismatch(self):
        with self.assertRaises(TAError):extract(self.req,({'company_of_interest':'MSFT','trade_date':'2026-10-14','asset_type':'stock','market_report':'x'},'BUY'),self.req['source_sha256'])
    def test_config_directory_symlink(self):
        link=Path(self.temp.name)/'dir-link';link.symlink_to(self.temp.name)
        with self.assertRaises(TAError):upstream_config(self.req,str(link))
if __name__=='__main__':unittest.main()
