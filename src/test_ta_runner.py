"""TA-1 negative tests: no upstream third-party install/import/execution required."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_ta_bridge import fixture
from ta_research.adapter import TAError
from ta_research.bridge import capture,extract
from ta_research.runner import ARCHIVE_SHA, ANCHORS, TOOL_MODULES, TOOLS, _profile, _child_env, _frozen_tool_response, run_isolated, source_preflight
from ta_research.intake import intake_advisory
from ta_research_cli import main

ARCHIVE=Path.home()/'.hermes/profiles/valedia/cache/scratch/ta-1/tradingagents-01477f9.tar.gz'

class RunnerOfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.req,self.body,_=fixture()
        self.body['identity']={'longName':'Apple Inc.','sector':'Technology'}
        for name in TOOL_MODULES['market'][1]:
            self.body['tools'][name]=[{'observed_at':self.req['captured_at'],
                                     'available_at':self.req['captured_at'],
                                     'query':{'symbol':'AAPL'},'value':'frozen row'}]
        self.snap=self.root/'snapshot.json';self._save()
        self.request=self.root/'request.json';self.request.write_text(json.dumps(self.req))
    def _save(self):
        raw=json.dumps(self.body,sort_keys=True).encode()
        self.snap.write_bytes(raw);self.req['source_sha256']=hashlib.sha256(raw).hexdigest()
    def test_archive_pinned_and_static_graph_anchors(self):
        if not ARCHIVE.exists(): self.skipTest('cached pinned archive absent')
        self.assertEqual(hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),ARCHIVE_SHA)
        with tarfile.open(ARCHIVE) as tf:
            for name,anchors in ANCHORS.items():
                members=[m for m in tf if m.name.endswith('/'+name)]
                self.assertEqual(len(members),1)
                text=tf.extractfile(members[0]).read().decode()
                for a in anchors:self.assertIn(a,text)
        self.assertEqual(set().union(*(x[1] for x in TOOL_MODULES.values())),TOOLS)
    def test_installed_archive_parity_accepts_exact_root_directory(self):
        venv=Path.home()/'.hermes/profiles/valedia/runtimes/tradingagents-01477f9'
        if not venv.is_dir():self.skipTest('isolated runtime absent')
        report=source_preflight(ARCHIVE,venv)
        self.assertEqual(report['archive_sha256'],ARCHIVE_SHA)
        self.assertGreater(report['source_files'],0)
    def test_graph_gate_refuses_before_subprocess(self):
        self.body['tools']['get_indicators'][0].pop('query')
        self._save()
        with patch('ta_research.runner.subprocess.run',side_effect=AssertionError('launched')):
            with self.assertRaisesRegex(TAError,'query/response'):
                run_isolated(self.req,str(self.snap),archive=str(ARCHIVE),venv=str(self.root),work=str(self.root))
    def test_exact_frozen_tool_query_and_future_refusal(self):
        snapshot=capture(self.req,str(self.snap))['snapshot']
        self.assertEqual(_frozen_tool_response(snapshot,'get_stock_data',{'symbol':'AAPL'},self.req),'frozen row')
        for args,match in (({'symbol':'MSFT'},'ticker mismatch'),
                           ({'symbol':'AAPL','end_date':'2026-10-15'},'future tool query'),
                           ({'symbol':'AAPL','end_date':'2026-10-13'},'uncaptured')):
            with self.assertRaisesRegex(TAError,match):
                _frozen_tool_response(snapshot,'get_stock_data',args,self.req)
        snapshot['tools']['get_stock_data'].append(snapshot['tools']['get_stock_data'][0])
        with self.assertRaisesRegex(TAError,'ambiguous'):
            _frozen_tool_response(snapshot,'get_stock_data',{'symbol':'AAPL'},self.req)
    def test_missing_identity_and_social_lookahead(self):
        self.body.pop('identity');self._save()
        with patch('ta_research.runner.subprocess.run',side_effect=AssertionError('launched')):
            with self.assertRaisesRegex(TAError,'identity required'):
                run_isolated(self.req,str(self.snap),archive=str(ARCHIVE),venv=str(self.root),work=str(self.root))
        self.body['identity']={'longName':'Apple Inc.'}
        future='2026-10-15T00:00:00Z'
        self.body['social']={k:{'observed_at':self.req['captured_at'],'available_at':future,'value':'post'} for k in ('stocktwits','reddit')}
        self._save()
        with self.assertRaisesRegex(TAError,'social look-ahead'):capture(self.req,str(self.snap))
    def test_cli_requires_explicit_action_time_gate(self):
        self.request.write_text(json.dumps(self.req))
        with patch('ta_research_cli.run_isolated',side_effect=AssertionError('launched')):
            self.assertEqual(main(['run','--request',str(self.request),'--snapshot',str(self.snap),
                                   '--work',str(self.root),'--receipt',str(self.root/'r.json')]),2)
    def test_snapshot_request_dry_run_cli_roundtrip(self):
        tools=self.root/'tools.json';tools.write_text(json.dumps(self.body['tools']))
        identity=self.root/'identity.json';identity.write_text(json.dumps(self.body['identity']))
        frozen=self.root/'frozen.json';request=self.root/'new-request.json'
        self.assertEqual(main(['snapshot','--asset-type','stock','--symbol','AAPL',
            '--as-of',self.req['as_of'],'--captured-at',self.req['captured_at'],
            '--tools-json',str(tools),'--identity-json',str(identity),'--output',str(frozen)]),0)
        self.assertEqual(main(['request','--snapshot',str(frozen),'--source','offline capture',
            '--model-name','local-test','--endpoint','http://127.0.0.1:8081/v1',
            '--analysts','market','--output',str(request)]),0)
        self.assertEqual(main(['dry-run','--request',str(request),'--snapshot',str(frozen)]),0)
        self.assertEqual(json.loads(request.read_text())['source_sha256'],hashlib.sha256(frozen.read_bytes()).hexdigest())
    def test_child_env_no_ambient_credentials(self):
        env=_child_env(self.root)
        for secret in ('ALPACA_API_KEY','OPENAI_API_KEY_OLD','MERCURY_TOKEN','POLYMARKET_PRIVATE_KEY'):
            self.assertNotIn(secret,env)
        self.assertEqual(env['HOME'],str(self.root));self.assertEqual(env['OPENAI_API_KEY'],'local-no-cloud-key')
    @unittest.skipUnless(sys.platform=='darwin' and shutil.which('sandbox-exec'),'macOS seatbelt only')
    def test_installed_interpreter_starts_in_actual_seatbelt_without_graph_import(self):
        venv=Path.home()/'.hermes/profiles/valedia/runtimes/tradingagents-01477f9'
        archive=venv/'provenance/source/tradingagents-01477f9.tar.gz'
        if not (venv/'bin/python3').is_file() or not archive.is_file():
            self.skipTest('private installed runtime absent')
        runs=Path.home()/'.hermes/profiles/valedia/cache/scratch/ta-1/runs'
        runs.mkdir(mode=0o700,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runs) as d:
            work=Path(d)
            profile=_profile(venv,work,1234,archive)
            proc=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,str(venv/'bin/python3'),'-I','-c',
                                 'print("TA_SANDBOX_STARTUP_OK")'],capture_output=True,timeout=8,
                                cwd=work,env=_child_env(work))
            self.assertEqual(proc.returncode,0,proc.stderr.decode(errors='replace'))
            self.assertEqual(proc.stdout,b'TA_SANDBOX_STARTUP_OK\n')
            # The exact installed interpreter must not gain unrelated home data,
            # outside-work writes, or nonlocal egress from the metadata fix.
            secret=Path.home()/'.openclaw/workspace-v3/scripts/integrations/poly_research_intake.py'
            outside=work.parent/'ta-outside-work-write-probe'
            self.assertTrue(secret.is_file())
            self.assertFalse(outside.exists())
            code='import sys; from pathlib import Path; print("SECRET",Path(sys.argv[1]).read_text()[:8],flush=True)'
            denied=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,str(venv/'bin/python3'),'-I','-c',
                                   code,str(secret)],capture_output=True,timeout=8,
                                  cwd=work,env=_child_env(work))
            self.assertNotEqual(denied.returncode,0)
            self.assertIn(b'Operation not permitted',denied.stderr)
            self.assertNotIn(b'SECRET ',denied.stdout)
            blocked_write=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,str(venv/'bin/python3'),'-I','-c',
                                          'import sys; open(sys.argv[1],"w").write("blocked")',str(outside)],
                                         capture_output=True,timeout=8,cwd=work,env=_child_env(work))
            self.assertNotEqual(blocked_write.returncode,0)
            self.assertFalse(outside.exists())
            egress=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,str(venv/'bin/python3'),'-I','-c',
                                   'import socket; print(socket.socket().connect_ex(("1.1.1.1",80)))'],
                                  capture_output=True,timeout=8,cwd=work,env=_child_env(work))
            self.assertNotEqual(egress.stdout.strip(),b'0')
    @unittest.skipUnless(sys.platform=='darwin' and shutil.which('sandbox-exec'),'macOS seatbelt only')
    def test_seatbelt_denies_external_and_workspace_reads(self):
        # This runs only stdlib Python, no TradingAgents code or import.
        runs=Path.home()/'.hermes/profiles/valedia/cache/scratch/ta-1/runs'
        runs.mkdir(mode=0o700,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runs) as d:
            work=Path(d)
            secret=Path.home()/'.openclaw/workspace-v3/scripts/integrations/poly_research_intake.py'
            self.assertTrue(secret.is_file())
            with socket.socket() as listener:
                listener.bind(('127.0.0.1',0));listener.listen(1)
                port=listener.getsockname()[1]
                script="import socket,sys; a=socket.socket().connect_ex(('1.1.1.1',80)); b=socket.socket().connect_ex(('127.0.0.1',int(sys.argv[2]))); print('EGRESS',a,'LOCAL',b,flush=True); open(sys.argv[1]).read()"
                profile=_profile(Path('/usr'),work,port,ARCHIVE)
                proc=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,sys.executable,'-I','-c',script,str(secret),str(port)],
                                    capture_output=True,timeout=5,env=_child_env(work))
            self.assertNotEqual(proc.returncode,0)
            self.assertIn(b'Operation not permitted',proc.stderr)
            self.assertIn(b'LOCAL 0',proc.stdout)
            self.assertNotIn(b'EGRESS 0',proc.stdout)
            blocked=work.parent/'outside-work-write-probe'
            profile=_profile(Path('/usr'),work,port,ARCHIVE)
            denied=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,sys.executable,'-I','-c',
                'import sys; open(sys.argv[1],"w").write("blocked")',str(blocked)],
                capture_output=True,timeout=5,env=_child_env(work))
            self.assertNotEqual(denied.returncode,0)
            self.assertFalse(blocked.exists())
    def test_caller_supplied_receipt_cannot_enter_canonical_schema(self):
        outcome=({'company_of_interest':'AAPL','trade_date':'2026-10-14','asset_type':'stock',
                  'market_report':'frozen advisory evidence','final_trade_decision':'HOLD'},'HOLD')
        receipt=extract(self.req,outcome,self.req['source_sha256'])
        path=self.root/'receipt.json';path.write_text(json.dumps(receipt))
        db=self.root/'research.db'
        with self.assertRaisesRegex(TAError,'existing research database'):
            intake_advisory(str(db),str(path),self.req,str(self.snap))
        from poly_research_intake import EvidenceDB
        EvidenceDB(db).close()
        with self.assertRaisesRegex(TAError,'owned run identity'):
            intake_advisory(str(db),str(path),self.req,str(self.snap))
        import sqlite3
        with sqlite3.connect(db) as sql:
            self.assertIsNone(sql.execute('SELECT ownership FROM source_snapshots WHERE kind=?',('ta-advisory',)).fetchone())
        receipt['identity']['symbol']='MSFT';path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(TAError,'owned run identity'):intake_advisory(str(db),str(path),self.req,str(self.snap))

if __name__=='__main__': unittest.main()
