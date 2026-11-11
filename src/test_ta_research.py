"""Offline TA-1 contract checks: never import the upstream graph here."""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from ta_research.adapter import validate_request, make_receipt, run_with_graph, ReceiptStore, TAError, graph_config


def request():
    return dict(asset_type='stock', symbol='AAPL', as_of='2026-10-14T16:00:00Z',
                trade_date='2026-10-14', source='local approved snapshot',
                source_sha256='a'*64, captured_at='2026-10-14T15:55:00Z',
                model_provider='local-openai-compatible', model_name='explicit-local-model',
                model_base_url='http://127.0.0.1:8081/v1', data_mode='approved-snapshot',
                max_cost_usd=0, analysts=['market', 'news'])


class TestTAResearch(unittest.TestCase):
    def test_validated_advisory_and_shared_config(self):
        req=request(); validate_request(req)
        config=graph_config(req, '/tmp/ta-isolated')
        self.assertEqual(config['backend_url'], 'http://127.0.0.1:8081/v1')
        self.assertEqual(config['data_mode'], 'approved-snapshot')
        receipt=make_receipt(req, {'market_report':'market notes', 'news_report':'news notes', 'final_trade_decision':'BUY'})
        self.assertEqual(receipt['status'],'ADVISORY_ONLY')
        self.assertEqual(receipt['ownership'],'UNOWNED')
        self.assertFalse(receipt['performance_eligible'])
        self.assertEqual(receipt['paper_intake'],'NOT_CONNECTED')
        self.assertNotIn('create_order', json.dumps(receipt))
        self.assertNotIn('BUY', json.dumps(receipt))

    def test_negative_validation(self):
        cases=[('symbol','../AAPL'),('asset_type','unknown'),('as_of','2026-10-14'),
               ('captured_at','2026-10-14T17:00:00Z'),('source_sha256','wrong'),
               ('trade_date','2026-10-15'),('model_base_url','https://outside.test/v1'),
               ('max_cost_usd',1),('analysts',['trader']),('source','')]
        for field,value in cases:
            with self.subTest(field=field):
                bad=request();bad[field]=value
                with self.assertRaises(TAError): validate_request(bad)
        bad=request();bad['create_order']=True
        with self.assertRaises(TAError):validate_request(bad)

    def test_graph_injection_is_explicit_and_no_ambient_credentials(self):
        req=request(); calls=[]
        def graph_factory(analysts, config):
            calls.append(config)
            def graph(symbol, date, asset_type):
                calls.append((symbol,date,asset_type))
                return {'market_report':'bounded research','final_trade_decision':'SELL'}
            return graph
        prior=os.environ.get('OPENAI_API_KEY')
        os.environ['OPENAI_API_KEY']='sentinel-never-use'
        try:
            receipt=run_with_graph(req,graph_factory)
        finally:
            if prior is None:os.environ.pop('OPENAI_API_KEY',None)
            else:os.environ['OPENAI_API_KEY']=prior
        self.assertEqual(len(calls),2)
        self.assertNotIn('sentinel',repr(calls))
        self.assertEqual(calls[1],('AAPL','2026-10-14','stock'))
        self.assertNotIn('SELL',json.dumps(receipt))

    def test_schema_and_provenance_refusal(self):
        req=request()
        for result in ({}, {'market_report':42}, {'market_report':'x'*50000}, {'market_report':'x','unexpected':'y'}):
            with self.subTest(result=str(result)[:40]):
                with self.assertRaises(TAError): make_receipt(req,result)
        bad=request();bad['captured_at']='2026-10-14T15:00:00Z';bad['as_of']='2026-10-14T16:00:00Z';bad['max_age_seconds']=60
        with self.assertRaises(TAError):validate_request(bad)

    def test_concurrent_idempotent_receipts_and_conflict(self):
        req=request(); result={'market_report':'a'}
        with tempfile.TemporaryDirectory() as d:
            store=ReceiptStore(Path(d)/'receipts.sqlite')
            one=store.save(make_receipt(req,result))
            self.assertEqual(one['receipt_sha256'],store.save(make_receipt(req,result))['receipt_sha256'])
            different=copy.deepcopy(one);different['reports']={'market_report':'b'}
            with self.assertRaises(TAError): store.save(different)
            store.close()

    def test_no_upstream_or_order_import_on_module_load(self):
        env=os.environ.copy();env['PYTHONPATH']=str(HERE)
        program="import sys; import ta_research.adapter; print(sorted(x for x in sys.modules if x.startswith(('tradingagents','coinbase_trader','unified_brain','etherion_risk','forward_test_ledger'))))"
        p=subprocess.run([sys.executable,'-c',program],env=env,capture_output=True,text=True,check=True)
        self.assertEqual(p.stdout.strip(),'[]')

if __name__=='__main__': unittest.main()
