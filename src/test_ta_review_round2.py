"""Round-1 adversarial fixes: stdlib-only; no upstream import/install/model prompt."""
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from typing import Any
from types import SimpleNamespace
from unittest.mock import patch

from test_ta_bridge import fixture
from ta_research.adapter import TAError
from ta_research.bridge import extract
from ta_research.runner import _bounded_child, ModelBudget, verify_local_model, run_isolated
from ta_research.owned_runs import OwnedRunRegistry
from ta_research.intake import intake_advisory

class RoundTwoRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
    def _req(self):
        req,body,_=fixture()
        body['identity']={'longName':'Apple Inc.'}
        snap=self.root/'snap.json'; raw=json.dumps(body,sort_keys=True).encode();snap.write_bytes(raw)
        req['source_sha256']=hashlib.sha256(raw).hexdigest()
        return req,snap
    def test_process_overflow_terminates_owned_descendant_and_bounds_log(self):
        pidfile=self.root/'descendant.pid';log=self.root/'log.txt'
        script='import subprocess,sys,time; c=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]); open(sys.argv[1],"w").write(str(c.pid)); print("X"*200000,flush=True); time.sleep(30)'
        with self.assertRaisesRegex(TAError,'output limit'):
            _bounded_child([sys.executable,'-c',script,str(pidfile)], b'', self.root, 5, log)
        pid=int(pidfile.read_text())
        for _ in range(30):
            try: os.kill(pid,0)
            except ProcessLookupError: break
            time.sleep(.02)
        else: self.fail('owned descendant survived overflow')
        self.assertLessEqual(log.stat().st_size,65536)
    def test_timeout_reaps_owned_process_group(self):
        pidfile=self.root/'descendant.pid';log=self.root/'log.txt'
        script='import subprocess,sys,time; c=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]); open(sys.argv[1],"w").write(str(c.pid)); time.sleep(30)'
        with self.assertRaisesRegex(TAError,'timed out'):
            _bounded_child([sys.executable,'-c',script,str(pidfile)], b'', self.root, .5, log)
        pid=int(pidfile.read_text())
        for _ in range(30):
            try: os.kill(pid,0)
            except ProcessLookupError: break
            time.sleep(.02)
        else: self.fail('owned descendant survived timeout')
    def test_counted_model_budget_fails_closed_and_reserves_before_call(self):
        b=ModelBudget(call_limit=2,output_limit=2500,per_call_limit=2048)
        b.on_chat_model_start({}, [], run_id='a')
        with self.assertRaisesRegex(TAError,'aggregate output'):b.on_llm_start({}, [], run_id='b')
        with self.assertRaisesRegex(TAError,'output budget exceeded'):
            b.on_llm_end(SimpleNamespace(llm_output={'token_usage':{'completion_tokens':2300}}),run_id='a')
        with self.assertRaisesRegex(TAError,'output budget exceeded'): b.on_llm_start({}, [],run_id='c')
        b2=ModelBudget(call_limit=1,output_limit=2048,per_call_limit=2048)
        b2.on_llm_start({}, [],run_id='a')
        with self.assertRaisesRegex(TAError,'missing output token usage'): b2.on_llm_end(SimpleNamespace(llm_output={}),run_id='a')
        with self.assertRaisesRegex(TAError,'poisoned'): b2.on_llm_start({}, [],run_id='b')
    def test_oversized_structured_return_poison_survives_nine_token_retry(self):
        budget=ModelBudget(call_limit=24,output_limit=32768,per_call_limit=2048)
        budget.on_chat_model_start({},[],run_id='structured')
        with self.assertRaisesRegex(TAError,'output budget exceeded'):
            budget.on_llm_end(SimpleNamespace(llm_output={'token_usage':{'completion_tokens':2300}}),run_id='structured')
        # Upstream catches Exception and invokes a free-text fallback: neither
        # that start nor the final success gate may clear the original failure.
        with self.assertRaisesRegex(TAError,'poisoned'):
            budget.on_chat_model_start({},[],run_id='free-text')
        with self.assertRaisesRegex(TAError,'poisoned'):
            budget.on_llm_end(SimpleNamespace(llm_output={'token_usage':{'completion_tokens':9}}),run_id='free-text')
        with self.assertRaisesRegex(TAError,'poisoned'):budget.assert_finished()
    def test_missing_usage_poison_survives_nine_token_retry(self):
        budget=ModelBudget(call_limit=24,output_limit=32768,per_call_limit=2048)
        budget.on_chat_model_start({},[],run_id='structured')
        with self.assertRaisesRegex(TAError,'missing output token usage'):
            budget.on_llm_end(SimpleNamespace(llm_output={}),run_id='structured')
        with self.assertRaisesRegex(TAError,'poisoned'):
            budget.on_chat_model_start({},[],run_id='free-text')
        with self.assertRaisesRegex(TAError,'poisoned'):budget.assert_finished()
    def test_invalid_usage_or_callback_error_poison_and_invalid_limits_refuse(self):
        for bad in (True,-1,2.3,'9'):
            budget=ModelBudget();budget.on_llm_start({},[],run_id='structured')
            with self.assertRaises(TAError):
                budget.on_llm_end(SimpleNamespace(llm_output={'token_usage':{'completion_tokens':bad}}),run_id='structured')
            with self.assertRaisesRegex(TAError,'poisoned'):budget.assert_finished()
        budget=ModelBudget();budget.on_llm_start({},[],run_id='structured')
        budget.on_llm_error(RuntimeError('provider failure'),run_id='structured')
        with self.assertRaisesRegex(TAError,'poisoned'):budget.assert_finished()
        budget=ModelBudget();setattr(budget,'calls',1.5)
        with self.assertRaisesRegex(TAError,'invalid model budget counters'):budget.assert_finished()
        with self.assertRaisesRegex(TAError,'poisoned'):budget.on_llm_start({},[],run_id='retry')
        with self.assertRaisesRegex(TAError,'invalid model budget'):ModelBudget(call_limit=True)
        with self.assertRaisesRegex(TAError,'invalid model budget'):ModelBudget(output_limit=0)
        invalid_limit:Any='2048'
        with self.assertRaisesRegex(TAError,'invalid model budget'):ModelBudget(per_call_limit=invalid_limit)
    def test_registry_requires_owned_completion_exact_receipt_bytes(self):
        req,snap=self._req();receipt=extract(req,({'company_of_interest':'AAPL','trade_date':req['trade_date'],'asset_type':'stock','market_report':'real text'},'HOLD'),req['source_sha256'])
        registry=OwnedRunRegistry(self.root/'registry')
        run_id=registry.allocate(req,req['source_sha256'],{'revision':'01477f9afb7a47b849ed4c9259d3a9a4738d9fda','archive_sha256':'fc3428f4002bc128cbbd0ba0070a9aae1e7c6c6a1286aa00e0594b061a7f96c5'},'b'*64,{'model':req['model_name'],'local':True,'loaded':True,'endpoint':req['model_base_url']},hashlib.sha256(snap.read_bytes()).hexdigest())
        receipt['run_id']=run_id;raw=json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()
        path=self.root/'receipt.json';path.write_bytes(raw)
        from poly_research_intake import EvidenceDB
        db=self.root/'research.db';EvidenceDB(db).close()
        with self.assertRaisesRegex(TAError,'owned completion'): intake_advisory(str(db),str(path),req,str(snap),registry_root=registry.root)
        registry.complete(run_id,path,hashlib.sha256(raw).hexdigest(),exit_code=0)
        got=intake_advisory(str(db),str(path),req,str(snap),registry_root=registry.root)
        self.assertEqual(got['status'],'ADVISORY_INTAKE_VERIFIED')
        self.assertEqual(got['ownership'],'UNOWNED')
        self.assertFalse(intake_advisory(str(db),str(path),req,str(snap),registry_root=registry.root)['inserted'])
        other=self.root/'foreign-receipt.json';other.write_bytes(raw)
        with self.assertRaisesRegex(TAError,'receipt hash/path not registered'):
            intake_advisory(str(db),str(other),req,str(snap),registry_root=registry.root)
        forged=dict(receipt);forged['reports']={'market_report':'FABRICATED_REVIEW_PROBE'}
        path.write_text(json.dumps(forged))
        with self.assertRaisesRegex(TAError,'receipt hash'):intake_advisory(str(db),str(path),req,str(snap),registry_root=registry.root)
        path.unlink(); path.symlink_to(self.root/'different')
        with self.assertRaisesRegex(TAError,'receipt'):intake_advisory(str(db),str(path),req,str(snap),registry_root=registry.root)
        with self.assertRaisesRegex(TAError,'conflict'):registry.complete(run_id,path,'d'*64)
    def test_locality_unknown_and_cloud_proxy_refused(self):
        req,_=self._req();req['model_base_url']='http://127.0.0.1:8081/v1';req['model_name']='local'
        with self.assertRaisesRegex(TAError,'unsupported local model backend'):verify_local_model(req)
        req['model_base_url']='http://127.0.0.1:11434/v1';req['model_name']='deepseek-v4-pro:cloud'
        with self.assertRaisesRegex(TAError,'cloud'):verify_local_model(req)
        req['model_name']='local-test'
        with patch('ta_research.review_controls._backend_json',side_effect=[{'models':[{'name':'local-test','digest':'a'*64,'size':100000000,'details':{'format':'gguf'}}]},{'models':[]}]):
            with self.assertRaisesRegex(TAError,'not loaded'):verify_local_model(req)
        req['model_base_url']='http://127.0.0.1:1234/v1'
        with patch('ta_research.review_controls._backend_json',return_value={'models':[{'key':'local-test','type':'llm','format':'mlx','loaded_instances':[{'id':'instance'}]}]}):
            self.assertEqual(verify_local_model(req)['backend'],'lmstudio')
        with patch('ta_research.review_controls._backend_json',return_value={'models':[{'key':'local-test','type':'llm','format':'cloud','loaded_instances':[{'id':'instance'}]}]}):
            with self.assertRaisesRegex(TAError,'local model'): verify_local_model(req)

    def test_ollama_missing_or_malformed_equal_digests_refuse_valid_local_passes(self):
        req,_=self._req();req['model_base_url']='http://127.0.0.1:11434/v1';req['model_name']='local-test'
        listed={'name':'local-test','size':100000000,'details':{'format':'gguf'}}
        resident={'name':'local-test','details':{'format':'gguf'}}
        for digest in (None,'not-an-artifact-digest'):
            left={**listed};right={**resident}
            if digest is not None:left['digest']=right['digest']=digest
            with patch('ta_research.review_controls._backend_json',side_effect=[{'models':[left]},{'models':[right]}]):
                with self.assertRaisesRegex(TAError,'digest|metadata unknown'):
                    verify_local_model(req)
        digest='a'*64
        left={**listed,'digest':digest};right={**resident,'digest':digest}
        with patch('ta_research.review_controls._backend_json',side_effect=[{'models':[left]},{'models':[right]}]):
            self.assertEqual(verify_local_model(req)['backend'],'ollama')
        with patch('ta_research.review_controls._backend_json',side_effect=[{'models':[{**left,'remote_host':'remote.example'}]},{'models':[right]}]):
            with self.assertRaisesRegex(TAError,'cloud|remote'):verify_local_model(req)
        with patch('ta_research.review_controls._backend_json',side_effect=[{'models':[left]},{'models':[{**right,'remote_model':'cloud/large'}]}]):
            with self.assertRaisesRegex(TAError,'cloud|remote'):verify_local_model(req)
        with patch('ta_research.review_controls._backend_json',side_effect=[{'models':[left]},{'models':[{**right,'digest':'b'*64}]}]):
            with self.assertRaisesRegex(TAError,'digest'):verify_local_model(req)

    def test_lmstudio_requires_identified_loaded_local_instance(self):
        req,_=self._req();req['model_base_url']='http://127.0.0.1:1234/v1';req['model_name']='local-test'
        row={'key':'local-test','type':'llm','format':'mlx'}
        for instances in ([],[{}],[{'id':''}],[{'name':'local-test'}]):
            with patch('ta_research.review_controls._backend_json',return_value={'models':[{**row,'loaded_instances':instances}]}):
                with self.assertRaisesRegex(TAError,'not loaded|instance|metadata unknown'):
                    verify_local_model(req)
        for fmt in ('mlx','gguf'):
            with patch('ta_research.review_controls._backend_json',return_value={'models':[{**row,'format':fmt,'loaded_instances':[{'id':'instance-1'}]}]}):
                self.assertEqual(verify_local_model(req)['backend'],'lmstudio')
        with patch('ta_research.review_controls._backend_json',return_value={'models':[{**row,'remote_model':'cloud/large','loaded_instances':[{'id':'instance-1'}]}]}):
            with self.assertRaisesRegex(TAError,'cloud|remote'):verify_local_model(req)

    def test_parent_owned_completion_requires_successful_child_return(self):
        # This is a stdlib-only parent wiring test, NOT a third-party graph execution.
        req,snap=self._req();body=json.loads(snap.read_bytes())
        body['tools']['get_stock_data'][0]['query']={}
        for name in ('get_indicators','get_verified_market_snapshot'):
            body['tools'][name]=[dict(body['tools']['get_stock_data'][0])]
        raw=json.dumps(body,sort_keys=True).encode();snap.write_bytes(raw)
        req['source_sha256']=hashlib.sha256(raw).hexdigest()
        archive=self.root/'source.tar.gz';archive.write_bytes(b'inert fixture')
        venv=self.root/'venv';venv.mkdir()
        scratch=Path.home()/'.hermes/profiles/valedia/cache/scratch/ta-1/runs'
        scratch.mkdir(parents=True,exist_ok=True)
        run_tmp=tempfile.TemporaryDirectory(dir=scratch);self.addCleanup(run_tmp.cleanup)
        work=Path(run_tmp.name).resolve();receipt_path=work/'receipt.json'
        reg=OwnedRunRegistry(self.root/'registry')
        preflight={'python':sys.executable,'archive_sha256':'fc3428f4002bc128cbbd0ba0070a9aae1e7c6c6a1286aa00e0594b061a7f96c5','revision':'01477f9afb7a47b849ed4c9259d3a9a4738d9fda'}
        locality={'model':req['model_name'],'local':True,'loaded':True,'endpoint':req['model_base_url']}
        def fake_child(argv,payload,working,timeout,log,*,env):
            p=json.loads(payload)
            result=extract(req,({'company_of_interest':req['symbol'],'trade_date':req['trade_date'],'asset_type':'stock','market_report':'test fixture only'},'HOLD'),req['source_sha256'])
            result.update(upstream_commit=preflight['revision'],model_budget={'model_calls':1,'output_tokens':7,'call_limit':24,'output_limit':32768,'per_call_limit':2048})
            (working/'result.json').write_text(json.dumps(result))
            return 1
        with patch('ta_research.runner.source_preflight',return_value=preflight),patch('ta_research.runner.verify_local_model',return_value=locality),patch('ta_research.runner.OwnedRunRegistry',return_value=reg),patch('ta_research.runner._bounded_child',side_effect=fake_child):
            with self.assertRaisesRegex(TAError,'upstream child failed'):
                run_isolated(req,str(snap),archive=str(archive),venv=str(venv),work=str(work),receipt_path=str(receipt_path))
        self.assertFalse(receipt_path.exists())
        self.assertEqual(reg.load(next(p.stem for p in self.root.joinpath('registry').glob('*.json') if not p.stem.startswith('request-')))['state'],'PREPARED')
        # Successful stubbed child response exercises the parent ledger/receipt/intake seam.
        reg2=OwnedRunRegistry(self.root/'registry2')
        run_tmp2=tempfile.TemporaryDirectory(dir=scratch);self.addCleanup(run_tmp2.cleanup)
        work2=Path(run_tmp2.name).resolve();receipt2=work2/'receipt.json'
        def success_child(*args,**kwargs):
            fake_child(*args,**kwargs)
            return 0
        with patch('ta_research.runner.source_preflight',return_value=preflight),patch('ta_research.runner.verify_local_model',return_value=locality),patch('ta_research.runner.OwnedRunRegistry',return_value=reg2),patch('ta_research.runner._bounded_child',side_effect=success_child):
            result=run_isolated(req,str(snap),archive=str(archive),venv=str(venv),work=str(work2),receipt_path=str(receipt2))
        self.assertEqual(reg2.load(result['run_id'])['state'],'COMPLETED')
        from poly_research_intake import EvidenceDB
        db=self.root/'canonical-fixture.sqlite';EvidenceDB(db).close()
        got=intake_advisory(str(db),str(receipt2),req,str(snap),registry_root=reg2.root)
        self.assertEqual((got['status'],got['ownership']),('ADVISORY_INTAKE_VERIFIED','UNOWNED'))

if __name__=='__main__': unittest.main()
