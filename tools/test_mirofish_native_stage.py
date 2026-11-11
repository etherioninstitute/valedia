"""Staging contract tests: file operations only; never import trading modules or launch."""
import ast,copy,hashlib,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('native_stage',Path(__file__).with_name('mirofish_native_stage.py'));s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
class NativeStaging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.storage_guard();cls.temp_root=s.RESUME/'build/staging-test-tmp';cls.temp_root.mkdir(exist_ok=True)
        cls.receipt=json.loads((s.RESUME/'build/metadata-supported-once/metadata-result.json').read_text());cls.controls=Path(cls.receipt['bindings']['controls_path']).read_bytes();cls.observations=[Path(x['actual_sdk']).read_bytes() for x in cls.receipt['observations']]
    def test_exact_accepted_metadata_and_actual_observations_pass(self):s.verify_metadata(copy.deepcopy(self.receipt),self.controls,self.observations)
    def test_metadata_failure_unknown_cleanup_changed_observation_refuse(self):
        for key,value in [('status','FAILED'),('cleanup_state','UNKNOWN_UNPROVEN'),('generation_calls',1),('resource_errors',['failure'])]:
            altered=copy.deepcopy(self.receipt);altered[key]=value
            with self.assertRaises(RuntimeError):s.verify_metadata(altered,self.controls,self.observations)
        with self.assertRaises(RuntimeError):s.verify_metadata(self.receipt,self.controls+b' ',self.observations)
        with self.assertRaises(RuntimeError):s.verify_metadata(self.receipt,self.controls,[self.observations[0]+b' ',*self.observations[1:]])
    def test_unknown_and_duplicate_pin_scopes_refuse(self):
        with self.assertRaises(RuntimeError):s.pin_scope([{'path':'/private/tmp/unknown-source','sha256':'a'*64}])
        row={'path':str(s.BASE/'runtime/known.py'),'sha256':'a'*64}
        with self.assertRaises(RuntimeError):s.pin_scope([row,row])
    def test_exact_source_copy_and_altered_source_refusal(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            root=Path(folder);source=root/'source.py';source.write_text('exact accepted bytes\n');source.chmod(0o444);row={'path':str(source),'sha256':s.sha(source)};ancestry=[];target=root/'copy.py';s.copy_pin(row,target,ancestry);self.assertEqual(source.read_bytes(),target.read_bytes());self.assertEqual(ancestry[0]['kind'],'EXACT_ACCEPTED_W16_BYTES')
            source.chmod(0o644);source.write_text('altered\n')
            with self.assertRaises(RuntimeError):s.copy_pin(row,root/'bad.py',[])
    def test_symlink_source_refused(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            root=Path(folder);real=root/'real';real.write_text('x');link=root/'link';link.symlink_to(real)
            with self.assertRaises(OSError):s.snapshot(link)
    def test_final_staging_positive_and_no_stale_claim_or_policy(self):
        work=s.CREW/'valedia-native-20261108-extractor-war-boundary-offline'  # Stable unconsumed offline bundle; old live work is never a fixture.
        if not (work/'builder/candidate-manifest.json').exists():self.skipTest('actual stage runs after unit tests')
        derived=json.loads((work/'builder/candidate-manifest.json').read_text());s.assert_staged(work,derived);self.assertEqual(derived['known_blockers'],[]);self.assertFalse(derived['launch_authorized']);self.assertEqual(derived['qualification_status']['local_paper'],'PENDING_GENUINE_EXTRACTED_SIGNAL_MAY_REFUSE_FIXED_BUY');self.assertNotEqual(s.sha(work/'builder/candidate-manifest.json'),s.BASE_SHA)
        ancestry=json.loads((work/'builder/staging-ancestry.json').read_text())
        for row in ancestry['source_copies']:
            if row['kind']=='EXACT_ACCEPTED_W16_BYTES':self.assertEqual(s.sha(row['source']),s.sha(row['target']))
        self.assertTrue(all(p.is_file() for p in [work/'owned-source/observer-entry/mirofish_native_run.py',work/'builder/ps-identity']))
        with self.assertRaises(RuntimeError):s.stage(work,(None,)*8)
    def test_r115_unchanged_worker_environment_and_provenance_baseline_on_replica(self):
        import ast
        from types import SimpleNamespace
        native_source=s.W16/'builder/review-source/mirofish_native_run.py';paper_source=s.W16/'owned-source/paper_app/src/mirofish_provenance.py'
        native_tree=ast.parse(native_source.read_text());worker=next(n for n in native_tree.body if isinstance(n,ast.FunctionDef) and n.name=='worker_environment')
        tree=ast.parse(paper_source.read_text());baseline=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_baseline');absolute=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_absolute');constants=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('MAX_FILE','MAX_REPORTS','MAX_TOTAL') for t in n.targets)]
        module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*constants,absolute,baseline,worker],type_ignores=[]);ast.fix_missing_locations(module);namespace={'Path':Path,'Refused':RuntimeError,'require':s.require,'RUNTIME':s.BASE/'runtime/mirofish-local-20261106'};exec(compile(module,'EXTRACTED_PINNED_R115_FUNCTIONS','exec'),namespace)
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            root=Path(folder);producer=root/'owned-source/producer';producer.mkdir(parents=True);profile=SimpleNamespace(profile=SimpleNamespace(root=str(producer)))
            with self.assertRaises(FileNotFoundError):namespace['worker_environment'](producer,'http://127.0.0.1:62471/v1','offline-owned','offline-secret',62472,1)
            with self.assertRaisesRegex(RuntimeError,'reports directory required'):namespace['_baseline'](profile)
            s.scaffold(root);env=namespace['worker_environment'](producer,'http://127.0.0.1:62471/v1','offline-owned','offline-secret',62472,1);self.assertEqual(namespace['_baseline'](profile),{'ids':[],'bodies':[],'snapshots':{}})
            keys=['TMPDIR','XDG_CACHE_HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME','HF_HOME','TORCH_HOME','MPLCONFIGDIR','NLTK_DATA','NUMBA_CACHE_DIR','SENTENCE_TRANSFORMERS_HOME']
            for key in keys:self.assertTrue(Path(env[key]).is_relative_to(producer/'tmp'));self.assertTrue(Path(env[key]).is_dir())
            self.assertTrue(Path(env['TIKTOKEN_CACHE_DIR']).is_relative_to(s.BASE));self.assertEqual(list((producer/'backend/uploads/reports').iterdir()),[]);self.assertEqual(list((root/'owned-source/paper_app/state').iterdir()),[]);self.assertEqual(list((producer/'backend/log').iterdir()),[]);self.assertEqual(list((producer/'backend/logs').iterdir()),[])
    def test_compact_prompt_changes_one_ast_constant_and_refuses_source_drift(self):
        source=s.W16/s.ONTOLOGY_REL;raw=source.read_bytes();changed=s.compact_ontology_bytes(raw);self.assertNotEqual(raw,changed)
        old=ast.parse(raw);new=ast.parse(changed)
        def normalize(tree):
            matches=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ONTOLOGY_SYSTEM_PROMPT' for t in n.targets)];self.assertEqual(len(matches),1);matches[0].value=ast.Constant(value='SINGLE_PROMPT_DELTA');return ast.dump(tree,include_attributes=False)
        self.assertEqual(normalize(old),normalize(new))
        with self.assertRaisesRegex(RuntimeError,'source drift'):s.compact_ontology_bytes(raw+b'\n')
        for restriction in ['exactly 10','8 specific','Person','Organization','exactly 6','PascalCase','snake_case','source_targets','uuid','group_id','created_at','summary','exactly one','examples','48 characters','32 characters','120 characters']:
            self.assertIn(restriction,s.COMPACT_ONTOLOGY_PROMPT)
    def test_compact_sizing_fixture_passes_unchanged_validator_at566_tokens(self):
        contract=json.loads((s.RESUME/'recon/prep-length-cause-and-contract.json').read_text());fixture=contract['offline_sizing_fixture']['content'];source=s.W16/s.ONTOLOGY_REL;function=next(n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.FunctionDef) and n.name=='_validate_and_process');tree=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),function],type_ignores=[]);ast.fix_missing_locations(tree);namespace={'ModelOutputError':RuntimeError};exec(compile(tree,str(source),'exec'),namespace);result=namespace['_validate_and_process'](None,copy.deepcopy(fixture));self.assertEqual(len(result['entity_types']),10);self.assertEqual(len(result['edge_types']),6)
        from tokenizers import Tokenizer
        tokenizer=Tokenizer.from_file(str(s.BASE/'models/Qwen3.8-27B-MLX-4bit/tokenizer.json'));compact=json.dumps(fixture,ensure_ascii=False,separators=(',',':'));self.assertEqual(len(tokenizer.encode(compact,add_special_tokens=False).ids),566);self.assertLess(566,1536)
        invalid=copy.deepcopy(fixture);invalid['edge_types'].pop()
        with self.assertRaises(RuntimeError):namespace['_validate_and_process'](None,invalid)
    def test_unchanged_gateway_rejects_length_and_invalid_json_without_budget_relaxation(self):
        source=s.W16/'builder/review-source/mirofish_native_gateway.py';self.assertEqual(s.sha(source),'2251da4eab35312c4abf8a6de2aae4272d753778d945f12cc33e346cec1e99a6');spec=importlib.util.spec_from_file_location('compact_guard_fixture',source);g=importlib.util.module_from_spec(spec);sys.modules[spec.name]=g;spec.loader.exec_module(g)
        with self.assertRaises(g.NativeRefused):g.decode(b'{"entity_types":[')
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            root=Path(folder);secret='offline-guard-fixture-'+'q'*40;raw=g.encoded({'choices':[{'message':{'role':'assistant','content':'{}'},'finish_reason':'length'}]});gateway=g.Gateway(g.Limits('owned'),secret,root/'ledger.json',lambda kind,body,end:{'verified':True,'model':'owned','tokens':1},dispatch=lambda *args:(200,raw),calls_path=root/'calls')
            with self.assertRaisesRegex(g.NativeRefused,'finish_reason refused: length'):gateway.handle('/v1/chat/completions',{'model':'owned','messages':[{'role':'user','content':'offline fixture'}],'max_tokens':1536},'Bearer '+secret)
            self.assertEqual(gateway.state['calls'][0]['requested_output'],1536);self.assertEqual(gateway.state['calls'][0]['state'],'unknown');self.assertEqual(gateway.limits.output_total,60000)
    def test_compact_staged_source_ancestry_is71_exact_plus1_prompt_only(self):
        if not (s.COVERAGE_WORK/'builder/candidate-manifest.json').exists():self.skipTest('actual compact staging follows tests')
        ancestry=json.loads((s.COVERAGE_WORK/'builder/staging-ancestry.json').read_text());self.assertEqual(ancestry['exact_owned_source_files'],68);self.assertEqual(ancestry['reviewed_prompt_only_deltas'],1);self.assertEqual((s.COVERAGE_WORK/s.ONTOLOGY_REL).read_bytes(),s.compact_ontology_bytes((s.W16/s.ONTOLOGY_REL).read_bytes()));manifest=json.loads((s.COVERAGE_WORK/'builder/candidate-manifest.json').read_text());self.assertEqual(manifest['metadata_reuse_impact']['ontology_change']['functions_and_other_constants'],'AST_IDENTICAL');self.assertFalse(manifest['launch_authorized'])
    def gateway_module(self,raw,name):
        import types
        module=types.ModuleType(name);module.__file__=str(s.W16/'builder/review-source/mirofish_native_gateway.py');sys.modules[name]=module;exec(compile(raw,module.__file__,'exec'),module.__dict__);return module
    def test_report64_exactly_four_integer_deltas_baseline_unchanged_and_unknown_refuses(self):
        raw=(s.W16/'builder/review-source/mirofish_native_gateway.py').read_bytes();self.assertEqual(s.gateway_profile_bytes(raw,'compact40'),raw);changed=s.gateway_profile_bytes(raw,'report64');old=ast.parse(raw);new=ast.parse(changed);a=s.gateway_budget_nodes(old);b=s.gateway_budget_nodes(new);self.assertEqual([n.value for n in a],[40,60000,40,60000]);self.assertEqual([n.value for n in b],[64,100000,64,100000])
        for node in a+b:node.value='FOUR_INTEGER_DELTA_ONLY'
        self.assertEqual(ast.dump(old,include_attributes=False),ast.dump(new,include_attributes=False));baseline=self.gateway_module(raw,'baseline40_budget_test');self.assertEqual((baseline.Limits('owned').chat_attempts,baseline.Limits('owned').output_total),(40,60000));profile=self.gateway_module(changed,'report64_budget_test');self.assertEqual((profile.Limits('owned').chat_attempts,profile.Limits('owned').output_total),(64,100000))
        for kwargs in [{'chat_attempts':65},{'output_total':100001},{'context':32769},{'output_per_call':4097},{'wall_seconds':2401},{'embedding_attempts':65}]:
            with self.assertRaises(profile.NativeRefused):profile.Limits('owned',**kwargs)
        for kwargs in [{'chat_attempts':41},{'output_total':60001}]:
            with self.assertRaises(baseline.NativeRefused):baseline.Limits('owned',**kwargs)
        with self.assertRaisesRegex(RuntimeError,'unknown closed staging profile'):s.gateway_profile_bytes(raw,'report65')
        with self.assertRaises(SystemExit):s.main(['--profile','report65'])
        with self.assertRaisesRegex(RuntimeError,'source drift'):s.gateway_profile_bytes(raw+b'\n','report64')
    def test_report64_64th_admitted_65th_refused_and100001_reservation_refused(self):
        raw=s.gateway_profile_bytes((s.W16/'builder/review-source/mirofish_native_gateway.py').read_bytes(),'report64');g=self.gateway_module(raw,'report64_admission_test');secret='offline-report64-'+'q'*40;response=g.encoded({'choices':[{'message':{'role':'assistant','content':'{}'},'finish_reason':'stop'}]});forwarded=[]
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            gate=g.Gateway(g.Limits('owned'),secret,Path(folder)/'ledger.json',lambda kind,body,end:{'verified':True,'model':'owned','tokens':1},dispatch=lambda *args:(forwarded.append(True) or (200,response)))
            body={'model':'owned','messages':[{'role':'user','content':'offline profile boundary'}],'max_tokens':1}
            for _ in range(64):gate.handle('/v1/chat/completions',body,'Bearer '+secret)
            self.assertEqual(len(forwarded),64)
            with self.assertRaises(g.NativeRefused):gate.handle('/v1/chat/completions',body,'Bearer '+secret)
            self.assertEqual(gate.state['chat_attempts'],64);self.assertEqual(gate.state['reserved_output'],64);self.assertEqual(len(forwarded),64)
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            gate=g.Gateway(g.Limits('owned'),secret,Path(folder)/'ledger.json',lambda *args:{'verified':True,'model':'owned','tokens':1},dispatch=lambda *args:(_ for _ in ()).throw(AssertionError('must not dispatch')));gate.state['reserved_output']=99999
            with self.assertRaises(g.NativeRefused):gate.handle('/v1/chat/completions',dict(body,max_tokens=2),'Bearer '+secret)
            self.assertEqual(gate.state['reserved_output'],99999);self.assertEqual(gate.state['chat_attempts'],0)
    def test_report64_staged_three_gateway_copies_and_computed_ancestry(self):
        if not (s.COVERAGE_WORK/'builder/candidate-manifest.json').exists():self.skipTest('actual report64 stage follows tests')
        expected=s.gateway_profile_bytes((s.W16/'builder/review-source/mirofish_native_gateway.py').read_bytes(),'report64')
        for path in s.GATEWAY_COPIES:self.assertEqual((s.COVERAGE_WORK/path).read_bytes(),expected)
        lineage=json.loads((s.COVERAGE_WORK/'builder/staging-ancestry.json').read_text());self.assertEqual((lineage['manifest_pinned_owned_source_files'],lineage['exact_owned_source_files'],lineage['gateway_budget_deltas_owned'],lineage['gateway_budget_deltas_total'],lineage['reviewed_prompt_only_deltas']),(72,68,2,3,1));manifest=json.loads((s.COVERAGE_WORK/'builder/candidate-manifest.json').read_text());self.assertEqual(manifest['staging_profile']['name'],'report64_coverage');self.assertEqual(manifest['task_caller_budget']['global_gateway'],[64,100000,4096]);self.assertEqual(manifest['task_caller_budget']['maximum_reserved'],96256);self.assertFalse(manifest['launch_authorized'])
    def test_full_participation_selector_exact_three_rhs_and_source_drift(self):
        raw=(s.W16/s.SELECTOR_REL).read_bytes();changed=s.coverage_selector_bytes(raw);old=ast.parse(raw);new=ast.parse(changed);a=s.selector_assignments(old);b=s.selector_assignments(new);self.assertEqual([ast.unparse(n.value) for n in b],['min(2, len(agent_configs))','list(range(24))','1.0'])
        for n in a+b:n.value=ast.Constant(value='THREE_RHS_ONLY')
        self.assertEqual(ast.dump(old,include_attributes=False),ast.dump(new,include_attributes=False))
        with self.assertRaisesRegex(RuntimeError,'selector source drift'):s.coverage_selector_bytes(raw+b'\n')
    def test_source_selector_original_can_zero_or_one_coverage_selects_bounded_valid_id_sets(self):
        import copy
        from types import SimpleNamespace
        raw=(s.W16/s.SELECTOR_REL).read_bytes();original=ast.parse(raw);changed=ast.parse(s.coverage_selector_bytes(raw))
        def load_selector(tree,rng):
            node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='get_active_agents_for_round');module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[]);ast.fix_missing_locations(module);ns={'random':rng};exec(compile(module,'EXACT_SELECTOR_NO_SERVICE_IMPORTS','exec'),ns);return ns['get_active_agents_for_round']
        class RNG:
            def __init__(self,value,upper=False):self.value=value;self.upper=upper
            def uniform(self,a,b):return b if self.upper else a
            def random(self):return self.value
            def sample(self,population,count):return list(reversed(population))[:count]
        ids={1:object(),2:object(),3:object(),4:object()};env=SimpleNamespace(agent_graph=SimpleNamespace(get_agent=lambda agent_id:ids[agent_id]));config={'time_config':{'agents_per_hour_min':1,'agents_per_hour_max':1},'agent_configs':[{'agent_id':i,'active_hours':[14],'activity_level':1.0} for i in ids]};f=load_selector(original,RNG(0));self.assertEqual(len(f(env,config,0,0)),0);self.assertEqual(len(f(env,config,14,0)),1)
        for count in range(1,5):
            for hour in (0,14,23):
                for value in (0.0,0.9999999999999999):
                    for upper in (False,True):
                        config={'time_config':{'agents_per_hour_min':0,'agents_per_hour_max':4},'agent_configs':[{'agent_id':i,'active_hours':[14],'activity_level':0.15} for i in range(1,count+1)]};before=copy.deepcopy(config);selected=load_selector(changed,RNG(value,upper))(env,config,hour,0);self.assertTrue({i for i,agent in selected}.issubset(set(range(1,count+1))));self.assertEqual(len(selected),min(2,count));self.assertTrue(all(agent is ids[i] for i,agent in selected));self.assertEqual(config,before)
        callers=[n.name for n in ast.walk(original) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and any(isinstance(c,ast.Call) and isinstance(c.func,ast.Name) and c.func.id=='get_active_agents_for_round' for c in ast.walk(n))];self.assertIn('run_twitter_simulation',callers);self.assertIn('run_reddit_simulation',callers)
    def test_full_profile_staged_stimulus_and_native_criterion_unchanged(self):
        if not (s.COVERAGE_WORK/'builder/candidate-manifest.json').exists():self.skipTest('actual full profile follows tests')
        self.assertEqual((s.COVERAGE_WORK/s.SELECTOR_REL).read_bytes(),s.coverage_selector_bytes((s.W16/s.SELECTOR_REL).read_bytes()));manifest=json.loads((s.COVERAGE_WORK/'builder/candidate-manifest.json').read_text());self.assertTrue(manifest['qualification_stimulus']['enabled']);self.assertIn('QUALIFICATION_ONLY',manifest['staging_profile']['participation_stimulus']);self.assertEqual(manifest['staging_profile']['chat_attempts'],64);self.assertFalse(manifest['launch_authorized']);lineage=json.loads((s.COVERAGE_WORK/'builder/staging-ancestry.json').read_text());self.assertEqual((lineage['exact_owned_source_files'],lineage['gateway_budget_deltas_owned'],lineage['reviewed_prompt_only_deltas'],lineage['qualification_selector_deltas']),(68,2,1,1));self.assertEqual(s.sha(s.COVERAGE_WORK/'owned-source/observer-entry/mirofish_native_run.py'),s.sha(s.W16/'owned-source/observer-entry/mirofish_native_run.py'))
if __name__=='__main__':unittest.main()




