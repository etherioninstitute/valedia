#!/usr/bin/env python3
"""Bounded map fixtures plus installed-CLI integration; no trading imports."""
import sys
sys.dont_write_bytecode = True
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import shutil
import select
import signal
import socket
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('repo_map.py')
spec = importlib.util.spec_from_file_location('owned_repo_map_tool', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
RUNTIME,TMP,NODE = m.runtime_paths(SCRIPT.parents[1])

class RepositoryMapTests(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='map-test-',dir=TMP)
        self.root = Path(self.temp.name)/'repo'
        for d in ['src/pkg/nested','docs','tools','state']:
            (self.root/d).mkdir(parents=True,exist_ok=True)
        self.write('repomix.config.json',m.encoded(m.standard_config()))
        self.write('AGENTS.md','# Fixture instructions\n')
        self.write('src/top.py','import os\nimport missing_dependency\nfrom pkg import mod\n')
        self.write('src/pkg/__init__.py','from . import mod\n')
        self.write('src/pkg/mod.py','from .nested import other\nclass Thing:\n    def method(self):\n        import top\n        return 1\n')
        self.write('src/pkg/nested/__init__.py','')
        self.write('src/pkg/nested/other.py','from ..mod import Thing\nfrom ...missing import absent\nimport importlib\nimportlib.import_module("runtime_only")\n')
    def tearDown(self):
        self.temp.cleanup()
    def write(self,path,data):
        p=self.root/path
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(data if isinstance(data,bytes) else data.encode())
    def ensure(self,force=False):
        return m.ensure(self.root,RUNTIME,TMP,NODE,force)[0]
    def state(self):
        return m.inputs(self.root,RUNTIME,NODE)[1:]
    def fresh(self):
        state,fp=self.state()
        return m.current(self.root,state,fp)
    def test_recursive_relative_lazy_dynamic_symbols(self):
        blobs,_=m.collect(self.root)
        index=m.module_index(blobs)
        mods={x['module']:x for x in index['modules']}
        self.assertEqual(len(mods),5)
        self.assertIn({'name':'Thing.method','kind':'function','line':3,'end_line':5},mods['pkg.mod']['symbols'])
        self.assertTrue(any(x['target']=='top' and x['kind']=='internal' for x in mods['pkg.mod']['imports']))
        self.assertTrue(any(x['target']=='pkg.mod' and x['kind']=='internal' for x in mods['pkg.nested.other']['imports']))
        self.assertTrue(any(x['kind']=='unresolved_relative' for x in mods['pkg.nested.other']['imports']))
        self.assertEqual(mods['pkg.nested.other']['dynamic_imports'][0]['resolution'],'not_statically_resolved')
        self.assertTrue(any(x['kind']=='stdlib' for x in mods['top']['imports']))
        self.assertTrue(any(x['kind']=='external_or_unresolved' for x in mods['top']['imports']))
    def test_actual_cli_content_add_delete_rename(self):
        self.ensure()
        self.assertIsNotNone(self.fresh())
        p=self.root/'src/top.py';before=p.stat()
        self.write('src/top.py','import sys\nimport missing_dependency\nfrom pkg import mod\n')
        os.utime(p,ns=(before.st_atime_ns,before.st_mtime_ns))
        self.assertIsNone(self.fresh())
        self.ensure();self.assertIsNotNone(self.fresh())
        self.write('src/new.py','x = 1\n');self.assertIsNone(self.fresh());self.ensure()
        (self.root/'src/new.py').rename(self.root/'src/renamed.py');self.assertIsNone(self.fresh());self.ensure()
        (self.root/'src/renamed.py').unlink();self.assertIsNone(self.fresh());self.ensure()
        check=subprocess.run([sys.executable,str(SCRIPT),'check','--root',str(self.root),'--runtime',str(RUNTIME),'--tmpdir',str(TMP)],capture_output=True)
        self.assertEqual(check.returncode,0,check.stderr)
        self.write('src/new.py','x = 1\n')
        check=subprocess.run([sys.executable,str(SCRIPT),'check','--root',str(self.root),'--runtime',str(RUNTIME)],capture_output=True)
        self.assertEqual(check.returncode,1)
    def test_corrupt_artifacts_manifest_pointer(self):
        gen=self.ensure()
        (gen/'navigation.md').write_text('corrupt')
        self.assertIsNone(self.fresh())
        gen=self.ensure()
        (gen/'manifest.json').write_text('{}')
        self.assertIsNone(self.fresh())
        self.ensure()
        (self.root/'.repo-map/current.json').write_text('{')
        self.assertIsNone(self.fresh())
        self.ensure();self.assertIsNotNone(self.fresh())
    def test_syntax_failure_indexed_without_execution(self):
        self.write('src/broken.py','def no(:\n')
        self.write('src/never_run.py','raise RuntimeError("must never execute")\n')
        gen=self.ensure()
        index=json.loads((gen/'modules.json').read_text())
        bad=next(x for x in index['modules'] if x['module']=='broken')
        self.assertEqual(bad['syntax_error']['type'],'SyntaxError')
        self.assertIsNotNone(self.fresh())
    def test_sensitive_special_symlink_exclusions(self):
        self.write('state/trades.json','DO_NOT_READ_LEDGER')
        self.write('src/.env','DO_NOT_PACK_ENV')
        self.write('src/api_key.py','DO_NOT_READ_KEY')
        self.write('src/models/weights.py','DO_NOT_PACK_MODEL')
        outside=Path(self.temp.name)/'outside.py';outside.write_text('DO_NOT_PACK_ESCAPE')
        (self.root/'src/escape.py').symlink_to(outside)
        (self.root/'src/alias').symlink_to(self.root/'src/pkg',target_is_directory=True)
        sock=socket.socket(socket.AF_UNIX);sock.bind(str(self.root/'src/socket.py'))
        try:
            blobs,skipped=m.collect(self.root)
            self.assertNotIn('state/trades.json',blobs)
            self.assertNotIn('src/api_key.py',blobs)
            self.assertNotIn('src/models/weights.py',blobs)
            self.assertNotIn('src/escape.py',blobs)
            self.assertNotIn('src/socket.py',blobs)
            gen=self.ensure()
            text=(gen/'repomix.xml').read_text()
            for sentinel in ['DO_NOT_READ_LEDGER','DO_NOT_PACK_ENV','DO_NOT_READ_KEY','DO_NOT_PACK_MODEL','DO_NOT_PACK_ESCAPE']:
                self.assertNotIn(sentinel,text)
        finally:
            sock.close()
    def test_architecture_tools_and_config_stale(self):
        self.ensure()
        for path in ['docs/ARCHITECTURE.md','tools/helper.py','AGENTS.md','.repomixignore']:
            self.write(path,'# revised\n');self.assertIsNone(self.fresh());self.ensure()
        config=m.standard_config();config['security']['enableSecurityCheck']=False
        self.write('repomix.config.json',m.encoded(config));self.assertIsNone(self.fresh())
        with self.assertRaises(m.MapError):
            self.ensure()
    def test_isolation_actual_cli_no_source_by_default_explicit_pack(self):
        self.write('src/top.py','MARKER_SOURCE_CONTENT_7f91 = 1\n')
        self.write('repomix.config.cjs','throw Error("hostile executable config must not load")')
        gen=self.ensure()
        self.assertNotIn('MARKER_SOURCE_CONTENT_7f91',(gen/'repomix.xml').read_text())
        blobs,_=m.collect(self.root)
        original=subprocess.run
        observed=[]
        def poison_global(*a,**kw):
            if 'env' in kw:
                env=kw['env']
                self.assertNotIn('HOME',env)
                self.assertNotIn('CODEX_HOME',env)
                config_dir=Path(env['XDG_CONFIG_HOME'])/'repomix'
                config_dir.mkdir(parents=True)
                (config_dir/'repomix.config.cjs').write_text('throw Error("global config must not execute")')
                observed.append(True)
            return original(*a,**kw)
        subprocess.run=poison_global
        try:
            packed=m.run_repomix(self.root,RUNTIME,NODE,TMP,blobs,True)
        finally:
            subprocess.run=original
        self.assertEqual(observed,[True])
        self.assertIn(b'MARKER_SOURCE_CONTENT_7f91',packed)
    def test_concurrent_builders_and_retained_generation(self):
        old=self.ensure()
        self.write('src/changed.py','x = 1\n')
        cmd=[sys.executable,str(SCRIPT),'ensure','--root',str(self.root),'--runtime',str(RUNTIME),'--tmpdir',str(TMP)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:subprocess.run(cmd,capture_output=True,timeout=180),range(2)))
        self.assertTrue(all(r.returncode==0 for r in results))
        self.assertIsNotNone(self.fresh())
        self.assertTrue((old/'navigation.md').is_file())
    def test_mid_build_change_retries_and_never_publishes_mixed_inputs(self):
        original=m.run_repomix
        calls=[]
        def mutate(*a,**kw):
            result=original(*a,**kw)
            calls.append(1)
            if len(calls)==1:
                self.write('src/during.py','during = 1\n')
            return result
        m.run_repomix=mutate
        try:
            gen=self.ensure()
        finally:
            m.run_repomix=original
        self.assertEqual(len(calls),2)
        self.assertTrue(any(x['module']=='during' for x in json.loads((gen/'modules.json').read_text())['modules']))
        self.assertIsNotNone(self.fresh())
    def test_runtime_tool_bytes_and_pin_freshness(self):
        fixture=Path(self.temp.name)/'runtime'
        package=fixture/'node_modules/repomix'
        (package/'lib').mkdir(parents=True)
        (package/'package.json').write_text(json.dumps({'version':m.REPOMIX_VERSION}))
        (fixture/'package.json').write_text('{}')
        lock={'packages':{'node_modules/repomix':{'version':m.REPOMIX_VERSION,'integrity':m.INTEGRITY}}}
        (fixture/'package-lock.json').write_text(json.dumps(lock))
        (package/'lib/tool.js').write_text('old')
        first=m.tool_snapshot(fixture,NODE)
        (package/'lib/tool.js').write_text('new')
        self.assertNotEqual(first,m.tool_snapshot(fixture,NODE))
        lock['packages']['node_modules/repomix']['integrity']='invalid'
        (fixture/'package-lock.json').write_text(json.dumps(lock))
        with self.assertRaises(m.MapError):
            m.tool_snapshot(fixture,NODE)
    def test_actual_cli_secret_scanning_enabled(self):
        # Synthetic unmatched identifier, never a credential or network request.
        synthetic='abcd1234' * 5
        self.write('src/synthetic.py','aws_secret_access_key = "' + synthetic + '"\n')
        blobs,_=m.collect(self.root)
        packed=m.run_repomix(self.root,RUNTIME,NODE,TMP,blobs,True)
        self.assertNotIn(synthetic.encode(),packed)
    def test_optional_foreground_watch_refreshes_then_stops(self):
        cmd=[sys.executable,str(SCRIPT),'watch','--root',str(self.root),'--runtime',str(RUNTIME),'--tmpdir',str(TMP),'--interval','1']
        process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            ready,_,_=select.select([process.stdout],[],[],15)
            self.assertTrue(ready,'watch startup timed out')
            self.assertIn('REFRESHED',process.stdout.readline())
            self.write('src/watch_added.py','watch_added = 1\n')
            ready,_,_=select.select([process.stdout],[],[],15)
            self.assertTrue(ready,'watch refresh timed out')
            self.assertIn('REFRESHED',process.stdout.readline())
            self.assertIsNotNone(self.fresh())
            process.send_signal(signal.SIGINT)
            process.communicate(timeout=5)
            self.assertEqual(process.returncode,130)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    def test_unselected_orphan_and_escaping_pointer(self):
        self.ensure()
        base=self.root/'.repo-map'
        (base/'generations/orphan').mkdir()
        self.assertIsNotNone(self.fresh())
        (base/'current.json').write_text(json.dumps({'generation':'../../escape','manifest_sha256':'x'}))
        self.assertIsNone(self.fresh())

if __name__=='__main__':
    unittest.main(verbosity=2)
