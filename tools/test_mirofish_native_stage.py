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
    def test_acknowledged_list_is_pinned_and_every_entry_matches_the_file_on_disk(self):
        table=s.acknowledged_revisions();self.assertEqual(len(table),69);self.assertEqual(s.sha(s.ACKNOWLEDGED_FILE),s.ACKNOWLEDGED_SHA)
        before=json.loads((s.W16/'review98/before-hashes.json').read_text());frozen={row['path']:row['expected'] for row in before['original318']}
        for path,row in table.items():
            self.assertEqual(frozen[path],row['expected_parent_sha256']);self.assertEqual(s.sha(path),row['acknowledged_sha256']);self.assertTrue(row['commits'])
        self.assertFalse({row['path'] for row in before['selected_candidate_files']}&set(table))  # nothing a candidate copies is on the list
        drifted={row['path'] for row in before['original318'] if s.sha(row['path'])!=row['expected']}-{str(s.EXTRACTOR_CANONICAL)}
        self.assertEqual(drifted,set(table))  # the list excuses exactly the files that differ, no more
    def test_acknowledged_witness_accepts_only_the_exact_path_parent_and_bytes(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            root=Path(folder);file=root/'module.py';file.write_text('acknowledged bytes\n');row={'path':str(file),'expected':'a'*64}
            table={str(file):{'path':str(file),'expected_parent_sha256':'a'*64,'acknowledged_sha256':s.sha(file),'commits':['abc1234']}}
            witness=s.acknowledged_revision_witness(row,table);self.assertEqual(witness['kind'],'OWNER_ACKNOWLEDGED_CANONICAL_REVISION_NOT_COPIED')
            with self.assertRaisesRegex(RuntimeError,'not in the acknowledged list'):s.acknowledged_revision_witness({'path':str(root/'other.py'),'expected':'a'*64},table)
            with self.assertRaisesRegex(RuntimeError,'not in the acknowledged list'):s.acknowledged_revision_witness({'path':str(file),'expected':'b'*64},table)  # another frozen parent
            file.write_text('changed again\n')
            with self.assertRaisesRegex(RuntimeError,'changed after its acknowledged revision'):s.acknowledged_revision_witness(row,table)
            real=root/'real.py';real.write_text('acknowledged bytes\n');file.unlink();file.symlink_to(real)
            with self.assertRaises(RuntimeError):s.acknowledged_revision_witness(row,table)
    def test_acknowledged_list_refuses_edits_other_folders_the_extractor_and_duplicates(self):
        good={'path':str(s.ACKNOWLEDGED_SCOPE/'x.py'),'expected_parent_sha256':'a'*64,'acknowledged_sha256':'b'*64,'commits':[]}
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            def load(rows,schema=1):
                file=Path(folder)/'list.json';file.write_text(json.dumps({'schema':schema,'revisions':rows}));return s.acknowledged_revisions(file,s.sha(file))
            self.assertEqual(list(load([good])),[good['path']])
            for bad in [dict(good,path=str(s.BASE/'valedia-trader/valedia_trader/x.py')),dict(good,path=str(s.EXTRACTOR_CANONICAL)),dict(good,path=str(s.ACKNOWLEDGED_SCOPE/'..'/'tools'/'x.py')),
                        dict(good,acknowledged_sha256='a'*64),dict(good,acknowledged_sha256='short'),dict(good,path='src/x.py')]:
                with self.assertRaises(RuntimeError):load([bad])
            with self.assertRaises(RuntimeError):load([good,good])
            with self.assertRaises(RuntimeError):load([good],schema=2)
            file=Path(folder)/'list.json';file.write_text(json.dumps({'schema':1,'revisions':[good]}))
            with self.assertRaisesRegex(RuntimeError,'input SHA changed'):s.acknowledged_revisions(file,'c'*64)  # an edited list no longer matches its pin
    def test_closed_integer_delta_changes_only_the_stated_integers(self):
        raw=b"limit = 40\nother = 40\nname = 'forty'\ndef f(x):\n    return x <= 4\n";digest=hashlib.sha256(raw).hexdigest()
        changed=s.closed_integer_delta(raw,digest,[('limit = 40','limit = 640',1,1),('x <= 4','x <= 64',1,1)]);self.assertEqual(changed,b"limit = 640\nother = 40\nname = 'forty'\ndef f(x):\n    return x <= 64\n")
        for edits in ([('missing = 1','missing = 2',1,1)],[('= 40','= 41',1,1)],                    # absent; present twice
                      [("name = 'forty'","name = 'sixty'",1,1)],[('return x <= 4','return x >= 4',1,1)],  # a string; an operator
                      [('limit = 40','limit = 640',1,2)]):                                      # fewer integers changed than claimed
            with self.assertRaises(RuntimeError):s.closed_integer_delta(raw,digest,edits)
        with self.assertRaisesRegex(RuntimeError,'original source drift'):s.closed_integer_delta(raw+b'#',digest,[('limit = 40','limit = 640',1,1)])
    # TMF2 (2026-11-15): every swarm number comes from tools/mirofish_swarm_limits.py; the expected literals below are that
    # derivation's results, written out so a change in the derivation shows up here as well.
    # Addendum 2: walls and ceilings follow the participant bound (swarm8's bound is 12), and the worker event limit is derived.
    # TMF3 addendum 1: the assumed ratio is 1.30, which raises swarm48's ceiling and so its wall (was 55,800 s at 1.25).
    EXPECTED={'swarm48':{'chats':672,'output':2800000,'wall':57600,'worker':57480,'records':144001,'mib':256,'bound':64,'rounds':3,'budget':12288,'events':114294784},
              'swarm8':{'chats':192,'output':800000,'wall':7200,'worker':7080,'records':18001,'mib':32,'bound':12,'rounds':3,'budget':4096,'events':114294784}}
    def test_swarm_numbers_are_the_derivation_and_every_admitted_call_can_reserve_its_cap(self):
        self.assertEqual(set(s.SWARM),set(self.EXPECTED))
        for name,e in self.EXPECTED.items():
            n=s.swarm_numbers(name);self.assertEqual(s.PROFILES[name],(n['chats'],n['output']))
            self.assertEqual((n['chats'],n['output'],n['wall'],n['worker_deadline_s'],n['history_records'],n['history_mib'],n['agents_bound'],n['rounds'],n['memory_budget']),
                             (e['chats'],e['output'],e['wall'],e['worker'],e['records'],e['mib'],e['bound'],e['rounds'],e['budget']),name)
            self.assertEqual((n['caller_output'],n['native_output'],n['semaphore'],n['gateway_arrival_s'],n['gateway_queue_s'],n['agent_client_s'],n['caller_client_s'],n['embedding_client_s'],n['request_bytes']),(4096,1024,1,540,270,570,570,570,258048),name)
            self.assertEqual(n['worker_event_bytes'],e['events'],name)
            self.assertGreaterEqual(n['output'],2*512+(n['chats']-2)*max(n['caller_output'],n['native_output']))
            self.assertNotEqual(n['memory_budget'],2048)
    def test_swarm_gateway_run_and_startup_change_exactly_the_closed_numbers(self):
        for name,e in self.EXPECTED.items():
            for relative,build,count in [(sorted(s.GATEWAY_COPIES)[0],s.swarm_gateway_bytes,17),(sorted(s.RUN_COPIES)[0],s.swarm_run_bytes,11),(sorted(s.STARTUP_COPIES)[0],s.swarm_startup_bytes,3)]:
                raw=(s.W16/relative).read_bytes();changed=build(raw,name);before,after=s.integer_literals(raw),s.integer_literals(changed)
                self.assertEqual(sum(a!=b for a,b in zip(before,after)),count,relative)
                with self.assertRaisesRegex(RuntimeError,'original source drift'):build(raw+b'\n',name)
            gateway=s.swarm_gateway_bytes((s.W16/sorted(s.GATEWAY_COPIES)[0]).read_bytes(),name).decode()
            for text in [f'chat_attempts: int = {e["chats"]}',f'"chat_attempts": {e["chats"]},',f'output_total: int = {e["output"]}',f'"output_total": {e["output"]},',f'"wall_seconds": {e["wall"]},',
                         'request_bytes: int = 258048','client_seconds: int = 540','"client_seconds": 540,','queue_seconds: int = 270','"queue_seconds": 270}','output_per_call: int = 4096','context: int = 32768','cleanup_seconds: int = 120','<= 128','+10.0']:self.assertIn(text,gateway)
            run=s.swarm_run_bytes((s.W16/sorted(s.RUN_COPIES)[0]).read_bytes(),name).decode()
            for text in [f'time.monotonic() + {e["worker"]}',f'<= {e["bound"]}, "actual generated participant count outside bound"',f'"max_rounds": {e["rounds"]},',f'deadline=start+{e["wall"]};active=deadline-120',f'research_only=False,max_age_seconds={e["wall"]})',f"receipt['wall_seconds']<={e['wall']}",
                         '"MIROFISH_PREP_LLM_TIMEOUT": "570"','"MIROFISH_REPORT_LLM_TIMEOUT": "570"',"'caller_timeout':570,'gateway_arrival_seconds':540,'queue_seconds':270",'(expires-now).total_seconds() <= 3600',"len(native)>=2 and len(completed)==len(native)",
                         f'self.buffer += data; require(len(self.buffer) <= {e["events"]}, "worker event byte limit")']:self.assertIn(text,run)
            self.assertNotIn('<= 131072, "worker event byte limit"',run)
            self.assertNotIn('"180"',run)
            startup=s.swarm_startup_bytes((s.W16/sorted(s.STARTUP_COPIES)[0]).read_bytes(),name).decode()
            for text in [f'(30 if preflight else {e["wall"]})',f'MAX_RECORDS={e["records"]}',f'MAX_BYTES={e["mib"]}*1024*1024']:self.assertIn(text,startup)
        self.assertEqual(len({s.sha(s.W16/r) for r in s.RUN_COPIES}),1);self.assertEqual(len({s.sha(s.W16/r) for r in s.STARTUP_COPIES}),1)
    def test_closed_digit_delta_changes_only_the_two_caller_timeout_strings_and_integers(self):
        raw=(s.W16/sorted(s.RUN_COPIES)[0]).read_bytes();digest=hashlib.sha256(raw).hexdigest()
        changed=s.closed_digit_delta(raw,digest,[('"MIROFISH_PREP_LLM_TIMEOUT": "180"','"MIROFISH_PREP_LLM_TIMEOUT": "570"',1,1),("'caller_timeout':180","'caller_timeout':570",1,1)])
        self.assertIn(b'"MIROFISH_PREP_LLM_TIMEOUT": "570"',changed);self.assertIn(b'"MIROFISH_REPORT_LLM_TIMEOUT": "180"',changed)
        for edits in ([('"FLASK_DEBUG": "false"','"FLASK_DEBUG": "true"',1,1)],                           # any other string
                      [('"HF_HUB_OFFLINE": "1"','"HF_HUB_OFFLINE": "2"',1,1)],                             # a digit string outside the two caller timeouts
                      [('"MIROFISH_PREP_LLM_TIMEOUT": "180"','"MIROFISH_PREP_LLM_TIMEOUT": "18O"',1,1)],   # a non-digit value
                      [('"MIROFISH_PREP_LLM_TIMEOUT": "180"','"MIROFISH_PREP_LLM_TIMEOUT": "570.0"',1,1)], # not decimal digits only
                      [('"MIROFISH_PREP_LLM_TIMEOUT": "180"','"MIROFISH_PREP_LLM_TIMEOUT": 570',1,1)],     # a string turned into an integer
                      [('"MIROFISH_PREP_LLM_TIMEOUT": "180"','"MIROFISH_PREP_LLM_TIMEOUT": "570"',1,2)],   # fewer changes than claimed
                      [("'caller_timeout':180","'caller_timeout':-570",1,1)]):                            # an operator
            with self.assertRaises(RuntimeError):s.closed_digit_delta(raw,digest,edits)
        with self.assertRaisesRegex(RuntimeError,'original source drift'):s.closed_digit_delta(raw+b'#',digest,[("'caller_timeout':180","'caller_timeout':570",1,1)])
        self.assertEqual(s.closed_integer_delta(raw,digest,[("'caller_timeout':180","'caller_timeout':570",1,1)]).count(b"'caller_timeout':570"),1)
        # Addendum 2, R4: exact to the byte. A comment-only edit beside an approved change is refused, by both proofs.
        comment=('# Remain a live group ownership anchor; the supervisor owns termination.','# Remain a live group ownership anchor; the supervisor owns termination!',1,0)
        self.assertEqual(raw.count(comment[0].encode()),1)
        with self.assertRaisesRegex(RuntimeError,'outside the approved constants'):s.closed_digit_delta(raw,digest,[("'caller_timeout':180","'caller_timeout':570",1,1),comment])
        with self.assertRaisesRegex(RuntimeError,'outside the approved constants'):s.closed_integer_delta(raw,digest,[("'caller_timeout':180","'caller_timeout':570",1,1),comment])
        spaced=("'caller_timeout':180,","'caller_timeout': 570,",1,1)                                     # whitespace inside an approved fragment
        with self.assertRaisesRegex(RuntimeError,'outside the approved constants'):s.closed_digit_delta(raw,digest,[spaced])
        quoted=('"MIROFISH_PREP_LLM_TIMEOUT": "180"',"\"MIROFISH_PREP_LLM_TIMEOUT\": '570'",1,1)          # the same value with other quotes
        with self.assertRaisesRegex(RuntimeError,'outside the approved constants'):s.closed_digit_delta(raw,digest,[quoted])
        with self.assertRaises(RuntimeError):s.closed_integer_delta(raw,digest,[('"MIROFISH_PREP_LLM_TIMEOUT": "180"','"MIROFISH_PREP_LLM_TIMEOUT": "570"',1,1)])  # the integer-only rule is unchanged elsewhere
    def test_swarm_selector_is_full_participation_with_one_request_in_flight_per_platform(self):
        raw=(s.W16/s.SELECTOR_REL).read_bytes()
        for name,e in self.EXPECTED.items():
            changed=s.swarm_selector_bytes(raw,name)
            self.assertEqual([ast.unparse(n.value) for n in s.selector_assignments(ast.parse(changed))],['len(agent_configs)','list(range(24))','1.0'])
            self.assertEqual(changed.count(b'semaphore=1,'),2);self.assertEqual(changed.count(b'semaphore=30,'),0)
            self.assertIn(f"or args.max_rounds != {e['rounds']} or args.twitter_only".encode(),changed);self.assertIn(f"len(config['agent_configs']) <= {e['bound']}):".encode(),changed)
            self.assertIn(b"'Owned child agent, round, platform or path cap violated'",changed)  # the refusal itself stays
            self.assertIn(b"'max_tokens': 1024,",changed);self.assertNotIn(b"'max_tokens': 512,",changed)
            self.assertIn(b'timeout=570,',changed);self.assertNotIn(b'timeout=180,',changed);self.assertIn(b'max_retries=0,',changed)
            before,after=s.integer_literals(s.coverage_selector_bytes(raw,['len(agent_configs)','list(range(24))','1.0'])),s.integer_literals(changed);self.assertEqual(sum(a!=b for a,b in zip(before,after)),6)
        coverage=s.coverage_selector_bytes(raw);self.assertEqual(coverage.count(b'semaphore=30,'),2);self.assertIn(b'timeout=180,',coverage)  # the earlier profile is untouched by the new code path
        self.assertEqual([ast.unparse(n.value) for n in s.selector_assignments(ast.parse(coverage))],['min(2, len(agent_configs))','list(range(24))','1.0'])
        with self.assertRaisesRegex(RuntimeError,'source drift'):s.swarm_selector_bytes(raw+b'\n','swarm48')
    def test_swarm_lifts_every_producer_cap_by_one_integer_each_and_keeps_the_refusals(self):
        for name,e in self.EXPECTED.items():
            expected={'simulation_manager.py':[f'len(filtered.entities) <= {e["bound"]}:'],'simulation_config_generator.py':[f'len(entities) <= {e["bound"]}:'],
                      'simulation_runner.py':[f'or max_rounds != {e["rounds"]} or enable_graph_memory_update',f"len(config['agent_configs']) <= {e['bound']}):"],'owned_caller_budget.py':['CALLER_OUTPUT_LIMIT = 4096'],
                      'mirofish_provenance.py':[f'<= self.max_age_seconds <= {e["wall"]}:'],'embedding_service.py':['        timeout: int = 570,']}
            self.assertEqual({Path(k).name for k in s.SWARM_PRODUCER},set(expected))   # the memory file is a reviewed replacement, not a list entry
            for relative,(original,edits) in s.SWARM_PRODUCER.items():
                raw=(s.W16/relative).read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),original);changed=s.swarm_producer_bytes(relative,raw,name)
                before,after=s.integer_literals(raw),s.integer_literals(changed);self.assertEqual(sum(a!=b for a,b in zip(before,after)),len(edits),relative)
                for text in expected[Path(relative).name]:self.assertIn(text.encode(),changed)
                self.assertEqual(raw.count(b'raise '),changed.count(b'raise '));self.assertEqual(raw.count(b"MIROFISH_TEST_ISOLATED"),changed.count(b"MIROFISH_TEST_ISOLATED"))
                with self.assertRaisesRegex(RuntimeError,'original source drift'):s.swarm_producer_bytes(relative,raw+b'\n',name)
    def test_swarm_memory_is_the_reviewed_replacement_with_the_profile_budget_and_reply_cap(self):
        raw=(s.W16/s.MEMORY_REL).read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),s.MEMORY_CANONICAL_SHA);self.assertIn(b'INPUT_CONTEXT_TOKENS = 2048',raw)
        reviewed=s.MEMORY_FILE.read_bytes();self.assertEqual(hashlib.sha256(reviewed).hexdigest(),s.MEMORY_REPLACEMENT_SHA)
        self.assertNotIn(s.MEMORY_REL,s.SWARM_PRODUCER)
        for name,e in self.EXPECTED.items():
            staged=s.swarm_memory_bytes(raw,name)
            for text in [f'INPUT_CONTEXT_TOKENS = {e["budget"]}','OUTPUT_COMPLETION_TOKENS = 1024',f'len(agents) <= {e["bound"]}:','class WholeUnitContextCreator','NO_SLICING_LIMIT = sys.maxsize',
                         "version('camel-ai') != '0.2.78'","platform not in ('twitter', 'reddit')",'Provider completion cap or model shape drift','Unreviewed agent memory',"os.environ.get('MIROFISH_TEST_ISOLATED') != '1'"]:self.assertIn(text.encode(),staged,name)
            self.assertNotIn(b'INPUT_CONTEXT_TOKENS = 2048',staged);self.assertNotIn(b'ScoreBasedContextCreator(model.token_counter, INPUT_CONTEXT_TOKENS)',staged)
            before,after=s.integer_literals(reviewed),s.integer_literals(staged);self.assertEqual(sum(a!=b for a,b in zip(before,after)),3)
            with self.assertRaisesRegex(RuntimeError,'original source drift'):s.swarm_memory_bytes(raw+b'\n',name)
        original=s.MEMORY_REPLACEMENT_SHA;s.MEMORY_REPLACEMENT_SHA='0'*64
        try:
            with self.assertRaisesRegex(RuntimeError,'reviewed memory replacement changed'):s.swarm_memory_bytes(raw,'swarm8')
        finally:s.MEMORY_REPLACEMENT_SHA=original
    def test_swarm8_scenario_names_eight_invented_participants_and_is_sealed(self):
        raw,seal=s.swarm_scenario('swarm8');value=json.loads(seal);text=raw.decode()
        self.assertEqual((value['sha256'],value['bytes'],value['actor_count'],value['actual_model_calls_so_far'],value['recorded_at_utc']),(hashlib.sha256(raw).hexdigest(),len(raw),8,0,'2026-11-15T00:00:00+00:00'))
        names=[line[2:].split(',')[0] for line in text.splitlines() if line.startswith('- ')];self.assertEqual((len(names),len(set(names))),(8,8))
        for required in ['HYPOTHETICAL RESEARCH SCENARIO, NOT CURRENT MARKET NEWS.','none is a real person or firm','not participants','do not force a BUY','No real prices','three rounds']:self.assertIn(required,text)
        self.assertEqual(json.loads(s.swarm_scenario('swarm48')[1])['recorded_at_utc'],'2026-11-11T00:00:00+00:00')  # the earlier seal is unchanged
    def test_staged_swarm_folders_carry_the_memory_replacement_and_derived_numbers(self):
        # The -a folders were staged by the tool before addendum 2 and are left as they are; the -b twins are the repaired tool's proof.
        for name,folder in [('swarm48',s.CREW/'valedia-native-20261115-swarm48-b'),('swarm8',s.CREW/'valedia-native-20261115-swarm8-b')]:
            if not (folder/'builder/candidate-manifest.json').exists():self.skipTest('staged folders follow tests')
            # TMF3: swarm48-b was staged at the ratio 1.25; the 1.30 ruling moves its wall, so it is checked against 1.25 and must be restaged before any launch.
            original=s.LIMITS.RATIO_ASSUMED;s.LIMITS.RATIO_ASSUMED=1.25 if name=='swarm48' else original
            try:n=s.swarm_numbers(name)
            finally:s.LIMITS.RATIO_ASSUMED=original
            manifest=json.loads((folder/'builder/candidate-manifest.json').read_text());p=manifest['swarm_profile']
            self.assertEqual((p['name'],p['memory_budget'],p['caller_output'],p['wall'],p['chats']),(name,n['memory_budget'],4096,n['wall'],n['chats']));self.assertFalse(manifest['launch_authorized'])
            self.assertEqual(p['memory_replacement']['canonical_sha256'],s.MEMORY_CANONICAL_SHA);self.assertEqual(p['memory_replacement']['replacement_sha256'],s.MEMORY_REPLACEMENT_SHA)
            self.assertEqual((folder/s.MEMORY_REL).read_bytes(),s.swarm_memory_bytes((s.W16/s.MEMORY_REL).read_bytes(),name))
            self.assertEqual(p['worker_event_bytes'],n['worker_event_bytes']);self.assertEqual(p['agents_bound'],n['agents_bound'])
            for relative in s.RUN_COPIES:self.assertIn(f'require(len(self.buffer) <= {n["worker_event_bytes"]}, "worker event byte limit")'.encode(),(folder/relative).read_bytes())
            self.assertFalse((folder/'root-one-native-attempt.json').exists());self.assertFalse((folder/'builder/startup-preflight.json').exists());self.assertFalse((folder/'run1').exists())
    def test_swarm_scenario_is_labelled_hypothetical_and_sealed_with_its_exact_bytes(self):
        raw,seal=s.swarm_scenario('swarm48');value=json.loads(seal);text=raw.decode()
        self.assertEqual((value['sha256'],value['bytes'],value['actor_count'],value['actual_model_calls_so_far']),(hashlib.sha256(raw).hexdigest(),len(raw),48,0))
        self.assertIn('not market observations',value['kind']);self.assertTrue(text.startswith('HYPOTHETICAL RESEARCH SCENARIO, NOT CURRENT MARKET NEWS.'))
        names=[line[2:].split(',')[0] for line in text.splitlines() if line.startswith('- ')];self.assertEqual((len(names),len(set(names))),(48,48))
        for required in ['none is a real person or firm','not participants','do not force a BUY','No real prices']:self.assertIn(required,text)
        original=dict(s.SWARM['swarm48']);s.SWARM['swarm48']=dict(original,scenario_sha256='0'*64)
        try:
            with self.assertRaisesRegex(RuntimeError,'swarm scenario changed'):s.swarm_scenario('swarm48')
        finally:s.SWARM['swarm48']=original
    def test_sdk_port_is_one_integer_in_the_model_source_and_one_rule_in_the_policy(self):
        raw=(s.W16/sorted(s.MODEL_COPIES)[0]).read_bytes();self.assertEqual(len({s.sha(s.W16/r) for r in s.MODEL_COPIES}),1)
        changed=s.sdk_port_bytes(raw,59671);before,after=s.integer_literals(raw),s.integer_literals(changed)
        self.assertEqual(sum(a!=b for a,b in zip(before,after)),1);self.assertIn(b'SDK_PORT = 59671',changed);self.assertNotIn(b'62298',changed)
        for bad in (s.ORIGINAL_SDK_PORT,s.SERVER_PORT,80,70000,'59671',59671.0):
            with self.assertRaises(RuntimeError):s.sdk_port_bytes(raw,bad)
        with self.assertRaisesRegex(RuntimeError,'original source drift'):s.sdk_port_bytes(raw+b'\n',59671)
        helper=(s.W16/s.SDK_HELPER_REL).read_text();moved_helper=s.sdk_port_helper(helper,59671);self.assertIn("ws://127.0.0.1:59671",moved_helper);self.assertNotIn('62298',moved_helper);self.assertEqual(len(helper),len(moved_helper))
        with self.assertRaises(RuntimeError):s.sdk_port_helper(moved_helper,59671)                     # the reviewed port is no longer there
        policy='(version 1)\n(deny default)\n(allow network-outbound (remote ip "localhost:1235"))\n(allow network-outbound (remote ip "localhost:62298"))\n'
        moved=s.sdk_port_policy(policy,59671);self.assertIn('"localhost:59671"',moved);self.assertIn('"localhost:1235"',moved);self.assertNotIn('62298',moved);self.assertEqual(moved.count('allow network-outbound'),2)
        with self.assertRaises(RuntimeError):s.sdk_port_policy(policy.replace('62298','62299'),59671)   # the reviewed rule is not there
        with self.assertRaises(RuntimeError):s.sdk_port_policy(policy+policy,59671)                    # ambiguous
        pathy=policy+'(allow file-read* (subpath "/work/run-p59671"))\n';self.assertIn('"localhost:59671"',s.sdk_port_policy(pathy,59671))  # the digits in a folder name are not a rule
        with self.assertRaises(RuntimeError):s.sdk_port_policy(moved+'(allow network-outbound (remote ip "localhost:62298"))\n',59671)  # the new rule is already there
    def test_ssd_device_is_one_integer_in_the_startup_source_and_only_follows_the_mounted_ssd(self):
        raw=(s.W16/sorted(s.STARTUP_COPIES)[0]).read_bytes();changed=s.ssd_device_bytes(raw,16777245);before,after=s.integer_literals(raw),s.integer_literals(changed)
        self.assertEqual(sum(a!=b for a,b in zip(before,after)),1);self.assertIn(b'CACHE_DEVICE=16777245',changed);self.assertNotIn(b'16777246',changed)
        for bad in (s.ORIGINAL_CACHE_DEVICE,0,-1,True,'16777245',16777245.0):
            with self.assertRaises(RuntimeError):s.ssd_device_bytes(raw,bad)
        with self.assertRaisesRegex(RuntimeError,'original source drift'):s.ssd_device_bytes(raw+b'\n',16777245)
        swarm=s.swarm_startup_bytes(raw,'swarm48');both=s.ssd_device_bytes(swarm,16777245,hashlib.sha256(swarm).hexdigest())  # a swarm copy takes both deltas
        n=s.swarm_numbers('swarm48')
        for text in [b'CACHE_DEVICE=16777245',f'MAX_RECORDS={n["history_records"]}'.encode(),f'(30 if preflight else {n["wall"]})'.encode()]:self.assertIn(text,both)
        self.assertEqual(s.live_ssd_device(),s.CACHE_TARGET.stat().st_dev);self.assertEqual(s.live_ssd_device(),s.SSD_MOUNT.stat().st_dev)  # read as the launcher reads it
        with self.assertRaisesRegex(RuntimeError,'not on the SSD'):s.live_ssd_device(target=Path.home())  # a folder on another volume is never patched in
        with self.assertRaisesRegex(RuntimeError,'missing'):s.live_ssd_device(target=s.BASE/'cache/bionic/no-such-prompt-cache')
        with self.assertRaisesRegex(RuntimeError,'not mounted'):s.live_ssd_device(mount=s.BASE)  # a plain folder is not the SSD's mount
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
# ---- TMF3 (2026-11-15): a swarm staged on a chosen model; addenda 1 and 2 ------------------------------------------------
# The transformed bytes of every default (no --model) path, hashed from the merged tool (main abf88d5) before TMF3 changed it.
BASELINE_ABF88D5={'coverage_selector':'7c242db3f1d32417eebdcbf5f71e7b80704fdd7d7cfd88e2f0d98a8634ce63ea','device_startup':'4852d68bbb69e08eefdc879156da18fb12ce2fb968d5a9d7547c1eb49b8926b9','ontology':'cb5f076d379945281a47b9ac3e4226738e236d7bc4fd356c700fed44ebc0a554',
 'port_helper':'7897590a47e75cafe87f9bc4512132a0e7e4be2a9f70878538230642dfee986c','port_model':'71d1811cc5453bb8bbbc0677d93fe5acbc811700d6a1a1d6f50e7b7809f9d848','report64_gateway':'c77653a4ade3a8e48165b1d737c9bcb3cdb595a2e44b1746bb941ff3de0540a5',
 'swarm48':{'embedding_service.py':'83c717318c9e6e3a792f114b2a23f331cf571965551ac7299ae9dd4f3f1895ef','gateway':'a050e47daa947e7cd03be3ddda6cc8e88dba8bda4a00bc3e00610cd528cbe76b','memory':'e505e8c3be37dce9417fc5dd7b1bbaeaa67cb13676e38d4f6bdfda0c40cf6b75','mirofish_provenance.py':'8b39ea8f2f6d9fd30a08f94d02b81c1f39830ebbee38c89be158e3ffa18b0dd6','owned_caller_budget.py':'12a708bb08a6cd1f015befc98b673abee132d8259f64b0519f317951521c59c6','run':'0d8af4917c3cd93daeb67f00deb5f791d37908a43c6c7fcd8b89cc9398910387','scenario':'999e56774f34df31ceef8fa344f061a22e704e9c2ee607faf0087f8446121357','seal':'9b46c98f08bd71fd772f4cefbc76900a3d4e8a90092d0afee423c1410015cc75','selector':'6c8b8b7bbf1e57c68d1bbf11b8347191f84c8ffa1946287b2f531a9cbf8a6081','simulation_config_generator.py':'b724bc4e264652f3fee5ad66ac5263fcfbe94bfd4660ad144f983d09dcc63ac5','simulation_manager.py':'2a8e471c8260fceeb0e6018a7a871585f0d54c682acd62ab554edffb61382265','simulation_runner.py':'277fbf85c689bc62e2151a6746cec8fec03a784670b4e1c87f61329e0fa12eb7','startup':'08e65db86facc60a94639f5de680f325a177e1a9995c543a741713dca7f850cb','startup_device':'93dbac21fb081d3024060981331dca65f83b31352854bca9fa00d993d7c984bf'},
 'swarm8':{'embedding_service.py':'83c717318c9e6e3a792f114b2a23f331cf571965551ac7299ae9dd4f3f1895ef','gateway':'530708fb80d43f4c17c4b25dd1be27def598dc56283338c51c8100e5ea084ab6','memory':'ea20ae5ab911717fca16e6d7ca51909902cef8e83b04cf308df7474533e20fd9','mirofish_provenance.py':'e952a6204c2b67464ce840bea13b1a41146497f545a72aa45ac9091b2a3725a2','owned_caller_budget.py':'12a708bb08a6cd1f015befc98b673abee132d8259f64b0519f317951521c59c6','run':'2db95d25909488f24347044d6074338b90d2c3cb68af12bb235f97a245ad2328','scenario':'641690f62eb67dd2175a45041b19278a22d07d892693b761da6a067de4f1a15e','seal':'b67ce38de930385e8cc1ab361113fa054738665a00b1b343dd74efdd89a4e839','selector':'93aca4976d1161eaecf24780869528e55bb3a18ef5f3a4d0609434538e15c655','simulation_config_generator.py':'7e41e789fdacaf14c12b19afa408da3fcbcbfc5de732df0e7cb1bc45d2f4166c','simulation_manager.py':'25f87e43ba97d329f4ede1451116e0ad5fd50e93d7fe120840e3e52232e376e2','simulation_runner.py':'9fb056d762384c7930f5b04a67abcdf8ea5f37fbb247097bb343e136248da4b5','startup':'b45fee03a1b49b5f87a7c804f6aff290fd05cf31c4c5483f0f378e26b3fa19d8','startup_device':'7150d372b4fd30935f1e1b2ad629fdc1e9fd72531228a0bc52041b017f74507b'}}
Q8_WORK=s.CREW/'valedia-native-20261115-swarm8-q8-b'  # -q8-a (staged before the V134 wording correction) is superseded and left as it is
TMF3_MODEL_SRC_SHA='ea0676a6409a0915cd564bc954790937d8c9fbcb8be2a35d51c77ebda730d103'   # the dc14f5e revisions TMF3 pinned (commits ab08400, b5a1797)
TMF3_RUN_SRC_SHA='21c4418452a7c19c16ab7164f3c24f60feac097e46bb70219c896c7029be06eb'
def default_hashes():
    """Every default-path transformed file, hashed the way BASELINE_ABF88D5 was."""
    h=lambda b:hashlib.sha256(b).hexdigest();raw=lambda rel:(s.W16/rel).read_bytes();out={}
    out['ontology']=h(s.compact_ontology_bytes(raw(s.ONTOLOGY_REL)));out['report64_gateway']=h(s.gateway_profile_bytes(raw(sorted(s.GATEWAY_COPIES)[0]),'report64'))
    out['coverage_selector']=h(s.coverage_selector_bytes(raw(s.SELECTOR_REL)));out['port_model']=h(s.sdk_port_bytes(raw(sorted(s.MODEL_COPIES)[0]),59671))
    out['port_helper']=h(s.sdk_port_helper(raw(s.SDK_HELPER_REL).decode(),59671).encode());out['device_startup']=h(s.ssd_device_bytes(raw(sorted(s.STARTUP_COPIES)[0]),16777245))
    for name in s.SWARM:
        row={'gateway':h(s.swarm_gateway_bytes(raw(sorted(s.GATEWAY_COPIES)[0]),name)),'run':h(s.swarm_run_bytes(raw(sorted(s.RUN_COPIES)[0]),name))}
        st=s.swarm_startup_bytes(raw(sorted(s.STARTUP_COPIES)[0]),name);row['startup']=h(st);row['startup_device']=h(s.ssd_device_bytes(st,16777245,h(st)))
        for rel in s.SWARM_PRODUCER:row[Path(rel).name]=h(s.swarm_producer_bytes(rel,raw(rel),name))
        row['memory']=h(s.swarm_memory_bytes(raw(s.MEMORY_REL),name));row['selector']=h(s.swarm_selector_bytes(raw(s.SELECTOR_REL),name))
        sc,seal=s.swarm_scenario(name);row['scenario']=h(sc);row['seal']=h(seal);out[name]=row
    return out
def base_manifest():return json.loads((s.W16/'builder/candidate-manifest.json').read_text())
def load_module_copy(folder,name):
    """Import a module copy from a scratch folder under its own name (never the W16 names this process may hold)."""
    sys.path.insert(0,str(folder))
    try:
        spec=importlib.util.spec_from_file_location(name,Path(folder)/'mirofish_native_model.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    finally:sys.path.remove(str(folder))
class ModelStaging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.storage_guard();cls.temp_root=s.RESUME/'build/staging-test-tmp';cls.temp_root.mkdir(exist_ok=True);cls.base=base_manifest()
    def test_outcome1_default_staging_is_byte_identical_per_transformed_file(self):
        # Every default path reproduces the merged tool's bytes. The lead's ratio of 1.30 (addendum 1) also raises swarm48's
        # ceiling and so its wall (55,800 -> 57,600 s): with the ratio set back to 1.25 swarm48 is byte-identical too, and at
        # 1.30 exactly its five wall-derived files differ; nothing else does.
        original=s.LIMITS.RATIO_ASSUMED;self.assertEqual(original,1.30)
        try:s.LIMITS.RATIO_ASSUMED=1.25;self.assertEqual(default_hashes(),BASELINE_ABF88D5)
        finally:s.LIMITS.RATIO_ASSUMED=original
        now=default_hashes();differ={(k,kk) for k,v in BASELINE_ABF88D5.items() if isinstance(v,dict) for kk in v if now[k][kk]!=v[kk]}|{(k,None) for k,v in BASELINE_ABF88D5.items() if not isinstance(v,dict) and now[k]!=v}
        self.assertEqual(differ,{('swarm48',k) for k in ['gateway','mirofish_provenance.py','run','startup','startup_device']})
        self.assertEqual(s.swarm_numbers('swarm48')['wall'],57600);self.assertEqual(s.swarm_numbers('swarm8')['wall'],7200)
    def test_outcome1_model_option_exists_and_a_default_stage_has_no_selector_or_registry(self):
        work=s.CREW/'valedia-native-20261115-swarm8-b'
        if not (work/'builder/candidate-manifest.json').exists():self.skipTest('default-staged folder absent')
        self.assertFalse(list(work.rglob(s.MODEL_SELECTOR)));self.assertFalse(list(work.rglob(s.REGISTRY_NAME)))
        port=json.loads((work/'builder/candidate-manifest.json').read_text())['sdk_port_revision']['staged'];w16=(s.W16/sorted(s.MODEL_COPIES)[0]).read_bytes()
        for relative in s.MODEL_COPIES:self.assertEqual((work/relative).read_bytes(),w16 if port==s.ORIGINAL_SDK_PORT else s.sdk_port_bytes(w16,port))   # the W16 bytes, never the src revision
    def test_addendum1_src_revisions_are_pinned_by_hash_and_a_changed_byte_refuses(self):
        self.assertEqual(s.sha(s.SRC/'mirofish_native_model.py'),s.MODEL_SRC_SHA);self.assertEqual(s.sha(s.SRC/'mirofish_native_run.py'),s.RUN_SRC_SHA)
        self.assertEqual(s.src_revision('mirofish_native_run.py',s.RUN_SRC_SHA),(s.SRC/'mirofish_native_run.py').read_bytes())
        with self.assertRaisesRegex(RuntimeError,'src revision changed since it was pinned'):s.src_revision('mirofish_native_run.py','0'*64)
        self.assertIn('ab08400',s.SRC_REVISION_NOTE);self.assertIn('b5a1797',s.SRC_REVISION_NOTE);self.assertIn('not the W16 bytes',s.SRC_REVISION_NOTE)
        # V134's verdict was a fail with conditions: the note says so and names the conditions, never a bare "reviewed".
        for text in ['reviewed on 2026-11-15 (V134)','found unsafe for registered profiles','observer\'s readable directory','its own weight pin','unescaped','accepted for the two built-in profiles only','C1 to C3',
                     'built-in names only','no registry in any staged copy','weight pin and tokenizer hashes checked at staging','no borrowed conformance evidence']:self.assertIn(text,s.SRC_REVISION_NOTE)
        self.assertNotIn('reviewed on 2026-11-15 (V134), not',s.SRC_REVISION_NOTE)
    def test_outcome2_model_pins_are_derived_from_the_pinned_module_text(self):
        profiles,files,default=s.model_profiles();self.assertEqual(tuple(profiles),s.BUILTIN_MODELS);self.assertEqual(default,'qwen3.8-27b')
        self.assertEqual(files,('config.json','tokenizer.json','tokenizer_config.json','chat_template.jinja','model.safetensors.index.json'))
        big=s.model_choice('qwen3.8-27b',self.base);self.assertEqual([dict(r,bytes=b['bytes']) for r,b in zip(big['model_files'],self.base['model_files'])],self.base['model_files']);self.assertEqual(big['model_tokenizer'],self.base['model_tokenizer'])
        small=s.model_choice('qwen3-8b',self.base);root=s.BASE/'models/Qwen3-8B-tokenizer';gguf=s.BASE/'models/lmstudio-library/Qwen/Qwen3-8B/Qwen3-8B-Q4_K_M.gguf'
        self.assertEqual([r['path'] for r in small['model_files']],[str(root/f) for f in files]+[str(gguf)]);self.assertEqual(small['paths'],{root/f for f in files}|{gguf})
        self.assertEqual((small['model_files'][-1]['bytes'],small['model_files'][-1]['sha256']),(5225374496,'a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f'))
        self.assertEqual(small['model_tokenizer'],s.TOKENIZER_PINS['qwen3-8b']);self.assertEqual([r['role'] for r in small['model_files'][:5]],[r['role'] for r in self.base['model_files'][:5]])
        # TMF4b (C3): the 8B's weight row no longer borrows the 27B's "verified by authorized preflight" claim.
        self.assertEqual(small['model_files'][-1]['role'],'sole serving GGUF Q4_K_M weight; size and SHA-256 checked at staging');self.assertNotIn('preflight',small['model_files'][-1]['role'])
        # A profile edited into the module text is not trusted: the profiles must be exactly the built-ins.
        raw=(s.SRC/'mirofish_native_model.py').read_bytes();edited=raw.replace(b"    'qwen3-8b': {",b"    'qwen3-4b': {",1);self.assertEqual(tuple(s.model_profiles(edited)[0]),('qwen3.8-27b','qwen3-4b','ternary-bonsai-2-27b'))
        with self.assertRaisesRegex(RuntimeError,'unexpected expression'):s.model_profiles(raw.replace(b"_MODEL_ROOT/'Qwen3-8B-tokenizer'",b"Path.home()/'x'",1))
    def test_outcome2_pin_scope_admits_only_the_chosen_models_files(self):
        small=s.model_choice('qwen3-8b',self.base);big=s.model_choice('qwen3.8-27b',self.base);rows=[dict(r,bytes=1) for r in small['model_files']]
        self.assertEqual(len(s.pin_scope(rows,model_paths=small['paths'])),6)
        with self.assertRaisesRegex(RuntimeError,'unknown pin scope'):s.pin_scope(rows)                                 # the default scope is the 27B's
        with self.assertRaisesRegex(RuntimeError,'unknown pin scope'):s.pin_scope([dict(r,bytes=1) for r in big['model_files']],model_paths=small['paths'])
        with self.assertRaisesRegex(RuntimeError,'unknown pin scope'):s.pin_scope([{'path':str(s.BASE/'models/other.gguf'),'sha256':'a'*64}],model_paths=small['paths'])
    def test_outcome2_C2_model_files_are_verified_against_their_pins(self):
        small=s.model_choice('qwen3-8b',self.base);gguf=small['model_files'][-1]
        verified=s.verify_model_files(copy.deepcopy(small),[gguf])                       # the five tokenizer files hashed here; the GGUF hash is the staging run's
        self.assertEqual([r['bytes'] for r in verified['model_files']],[(Path(r['path'])).stat().st_size for r in verified['model_files']])
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            root=Path(folder);(root/'config.json').write_text(json.dumps({'model_type':'qwen3','max_position_embeddings':40960}));(root/'w.gguf').write_bytes(b'weights')
            fake={'name':'qwen3-8b','entry':{'tokenizer_root':root,'tokenizer_model_type':'qwen3'},'model_files':[{'path':str(root/'config.json'),'bytes':None,'sha256':s.sha(root/'config.json')},{'path':str(root/'w.gguf'),'bytes':7,'sha256':s.sha(root/'w.gguf')}]}
            s.verify_model_files(copy.deepcopy(fake))
            for row,field,value,why in [(0,'sha256','0'*64,'does not match its pin'),(1,'sha256','0'*64,'does not match its pin'),(1,'bytes',8,'pinned size')]:
                bad=copy.deepcopy(fake);bad['model_files'][row][field]=value
                with self.assertRaisesRegex(RuntimeError,why):s.verify_model_files(bad)
            (root/'config.json').write_text(json.dumps({'model_type':'qwen3','max_position_embeddings':16384}));fake['model_files'][0]['sha256']=s.sha(root/'config.json')
            with self.assertRaisesRegex(RuntimeError,'32,768 window'):s.verify_model_files(copy.deepcopy(fake))
    def test_outcome2_C1_refusals_unknown_registry_only_and_http_only_in_one_line(self):
        with self.assertRaisesRegex(RuntimeError,'unknown model profile'):s.model_choice('qwen9-1b',self.base)
        with self.assertRaisesRegex(RuntimeError,'HTTP backends only'):s.model_choice('qwen3.8-27b-mlx',self.base)                 # the real src/ registry entry
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            registry=Path(folder)/s.REGISTRY_NAME;registry.write_text(json.dumps({'profiles':{'evil':{'native':True,'tokenizer_root':'/','weight_file':'/etc/hosts','weight_bytes':1,'weight_sha256':"x'+1+'",'tokenizer_model_type':'qwen3','gguf_selection':{}}}}))
            with self.assertRaisesRegex(RuntimeError,'registry-only'):s.model_choice('evil',self.base,registry)
        import contextlib,io
        for argv in (['--profile','swarm8','--model','qwen9-1b'],['--profile','report64','--model','qwen3-8b'],['--profile','swarm8','--model','qwen3.8-27b-mlx']):
            err=io.StringIO()
            with contextlib.redirect_stderr(err):self.assertEqual(s.main(argv),2)
            self.assertEqual(len(err.getvalue().strip().splitlines()),1,argv);self.assertTrue(err.getvalue().startswith('refused: '))
    def test_C1_selector_is_exact_bytes_and_no_registry_reaches_a_staged_copy(self):
        self.assertEqual(s.selector_bytes('qwen3-8b'),b'{"profile": "qwen3-8b"}\n');self.assertEqual(s.selector_bytes('qwen3.8-27b'),b'{"profile": "qwen3.8-27b"}\n')
        with self.assertRaises(RuntimeError):s.selector_bytes('evil')
        self.assertEqual(s.SELECTOR_COPIES,{'builder/review-source/mirofish_model_profile.json','owned-source/observer-entry/mirofish_model_profile.json'})
        self.assertEqual({str(Path(r).parent) for r in s.SELECTOR_COPIES},{str(Path(r).parent) for r in s.MODEL_COPIES})   # beside every staged model copy
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            src=Path(folder)/'src';src.mkdir()                                           # a scratch copy of the module folder, never src/ itself
            for name in ('mirofish_native_model.py','mirofish_native_gateway.py'):(src/name).write_bytes((s.SRC/name).read_bytes())
            # With a registry holding a native entry beside it, the dc14f5e code took the entry's own paths and pins; the TMF4a
            # revision (R9) refuses such a registry at import. Staged copies still carry none.
            (src/s.REGISTRY_NAME).write_text(json.dumps({'profiles':{'evil':{'native':True,'tokenizer_root':'/','weight_file':'/etc/hosts','weight_bytes':1,'weight_sha256':'a'*64,'tokenizer_model_type':'qwen3','gguf_selection':{}}}}))
            (src/s.MODEL_SELECTOR).write_text('{"profile": "evil"}\n')
            with self.assertRaisesRegex(Exception,'malformed MiroFish model registry'):load_module_copy(src,'tmf3_registry_channel')
            # A staged copy has no registry: the same selector is refused, and the built-in 8B selector takes the built-in pins.
            (src/s.REGISTRY_NAME).unlink()
            with self.assertRaisesRegex(Exception,'unknown MiroFish model profile'):load_module_copy(src,'tmf3_registry_closed')
            (src/s.MODEL_SELECTOR).write_bytes(s.selector_bytes('qwen3-8b'));module=load_module_copy(src,'tmf3_selector_8b')
            self.assertEqual((module.ACTIVE_MODEL_PROFILE,module.TOKENIZER_ROOT,module.WEIGHT_SHA256),('qwen3-8b',s.BASE/'models/Qwen3-8B-tokenizer','a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f'))
            # And the helper it generates is the reviewed helper with exactly the checkpoint selection, size and hash changed.
            reviewed=(s.W16/s.SDK_HELPER_REL).read_text();big=s.model_choice('qwen3.8-27b',self.base);small=s.model_choice('qwen3-8b',self.base)
            self.assertEqual(s.model_helper_text(reviewed,small,big),module.SDK_SCRIPT);self.assertEqual(s.model_helper_text(reviewed,big,big),reviewed)
            self.assertEqual(s.model_helper_text(s.sdk_port_helper(reviewed,59671),small,big).count('ws://127.0.0.1:59671'),1)
            with self.assertRaisesRegex(RuntimeError,'helper checkpoint text'):s.model_helper_text(reviewed+reviewed,small,big)
    def test_addendum1_observer_policy_names_only_the_chosen_checkpoint(self):
        policy=(s.W16/'builder/observer.sb').read_text();big=s.model_choice('qwen3.8-27b',self.base);small=s.model_choice('qwen3-8b',self.base)
        self.assertEqual(s.model_policy(policy,big,big),policy);moved=s.model_policy(policy,small,big)
        self.assertNotIn('Qwen3.8-27B',moved);self.assertIn('(subpath "'+str(s.BASE/'models/Qwen3-8B-tokenizer')+'")',moved);self.assertIn('(literal "'+str(small['entry']['weight_file'])+'")',moved)
        self.assertEqual(len(moved.splitlines()),len(policy.splitlines()));self.assertEqual(sum(a!=b for a,b in zip(moved.splitlines(),policy.splitlines())),2)
        with self.assertRaisesRegex(RuntimeError,'checkpoint rule missing or ambiguous'):s.model_policy(moved,small,big)
    def test_addendum1_every_run_delta_is_reproved_against_the_src_revision(self):
        w16=(s.W16/sorted(s.RUN_COPIES)[0]).read_bytes();src=(s.SRC/'mirofish_native_run.py').read_bytes()
        import difflib
        src_lines={l for l in difflib.ndiff(w16.decode().splitlines(),src.decode().splitlines()) if l[:2] in ('- ','+ ')}
        plan={'setup':'ternary-bonsai-2-27b','turns':'qwen3-8b'}
        for name in s.SWARM:
            for model,threshold in (('qwen3.8-27b',74),('qwen3-8b',74),('ternary-bonsai-2-27b',74),(plan,74)):   # TMF5 addendum 1, ruling (e): the table, for every model
                n=s.swarm_numbers(name,model);changed=s.swarm_run_bytes(src,name,model,s.RUN_SRC_SHA,threshold).decode()
                self.assertEqual(changed.count(f'START_FREE_PERCENT={threshold} '),1);self.assertEqual(changed.count('START_FREE_PERCENT='),1);self.assertNotIn('if pre else 30)',changed)
                # TMF2's 11 and the threshold; a one-hour wall leaves the reviewed one-hour freshness cap as it is (a presence check)
                self.assertEqual(sum(a!=b for a,b in zip(s.integer_literals(src),s.integer_literals(changed.encode()))),12-(n['wall']==3600),(name,str(model)))
                for text in [f'time.monotonic() + {n["worker_deadline_s"]}',f'deadline=start+{n["wall"]};active=deadline-120',f"receipt['wall_seconds']<={n['wall']}",f'research_only=False,max_age_seconds={n["wall"]})',f'"max_rounds": {n["rounds"]},',
                             f'<= {n["agents_bound"]}, "actual generated participant count outside bound"','"MIROFISH_PREP_LLM_TIMEOUT": "570"',f'require(len(self.buffer) <= {n["worker_event_bytes"]}, "worker event byte limit")','40*1024**3']:self.assertIn(text,changed,(name,str(model)))
            # The same deltas as on W16 (the threshold kept at 65 as a presence check): only the src revision's own lines differ.
            same=s.swarm_run_bytes(src,name,'qwen3.8-27b',s.RUN_SRC_SHA,65).decode();old=s.swarm_run_bytes(w16,name).decode()
            self.assertEqual({l for l in difflib.ndiff(old.splitlines(),same.splitlines()) if l[:2] in ('- ','+ ')},src_lines)
            with self.assertRaisesRegex(RuntimeError,'original source drift'):s.swarm_run_bytes(src+b'\n',name,'qwen3-8b',s.RUN_SRC_SHA,56)
            with self.assertRaisesRegex(RuntimeError,'original source drift'):s.swarm_run_bytes(src,name,'qwen3-8b',s.RUN_ORIGINAL_SHA,56)   # src bytes are not the W16 original
        moved=s.sdk_port_bytes((s.SRC/'mirofish_native_model.py').read_bytes(),59671,s.MODEL_SRC_SHA);self.assertIn(b'SDK_PORT = 59671',moved);self.assertNotIn(b'SDK_PORT = 62298',moved)
        with self.assertRaisesRegex(RuntimeError,'original source drift'):s.sdk_port_bytes((s.SRC/'mirofish_native_model.py').read_bytes(),59671)   # against the W16 pin
    def test_outcome5_start_threshold_per_model_and_the_unchanged_fragment_is_a_presence_check(self):
        # TMF5 addendum 1, ruling (e) (it replaces TMF4's per-model formula): the table's total for whatever is staged through the model path.
        self.assertEqual(s.model_choice('qwen3-8b',self.base)['start_threshold'],74);self.assertEqual(s.model_choice('qwen3.8-27b',self.base)['start_threshold'],74)
        self.assertEqual(s.model_choice('ternary-bonsai-2-27b',self.base)['start_threshold'],74)
        raw=b"x = (65 if pre else 30)\nwall = 3600\n";digest=hashlib.sha256(raw).hexdigest()
        self.assertEqual(s.closed_integer_delta(raw,digest,s.closed([('(65 if pre else 30)','(65 if pre else 30)',1,1),('wall = 3600','wall = 3600',1,1)])),raw)
        with self.assertRaisesRegex(RuntimeError,'fragment missing'):s.closed_integer_delta(raw,digest,s.closed([('(66 if pre else 30)','(66 if pre else 30)',1,1)]))   # still must be present
        with self.assertRaisesRegex(RuntimeError,'fragment missing'):s.closed_integer_delta(raw,digest,[('wall = 3600','wall = 3600',1,1)])                        # an unchanged edit claiming a change
        n=s.swarm_numbers('swarm8','qwen3-8b');self.assertEqual(n['wall'],3600)                              # a one-hour wall: the freshness cap stays as reviewed
        self.assertIn(b'<= self.max_age_seconds <= 3600:',s.swarm_producer_bytes('owned-source/paper_app/src/mirofish_provenance.py',(s.W16/'owned-source/paper_app/src/mirofish_provenance.py').read_bytes(),'swarm8','qwen3-8b'))
    def test_limits_per_model_only_the_wall_and_expected_durations_follow_the_model(self):
        for name in s.SWARM:
            big,small=s.swarm_numbers(name,'qwen3.8-27b'),s.swarm_numbers(name,'qwen3-8b');self.assertEqual(big,s.swarm_numbers(name)|{})
            for key in ['agent_slot_s','caller_slot_s','gateway_arrival_s','gateway_queue_s','agent_client_s','caller_client_s','embedding_client_s','request_bytes','worker_event_bytes','chats','output','window_need','memory_budget','agents_bound']:self.assertEqual(big[key],small[key],(name,key))
            self.assertLess(small['wall'],big['wall']);self.assertLess(small['expected_total_s'],big['expected_total_s'])
        self.assertEqual((s.swarm_numbers('swarm8','qwen3-8b')['wall'],s.swarm_numbers('swarm48','qwen3-8b')['wall']),(3600,18000))
        gateway=s.swarm_gateway_bytes((s.W16/sorted(s.GATEWAY_COPIES)[0]).read_bytes(),'swarm8','qwen3-8b').decode();self.assertIn('"wall_seconds": 3600,',gateway);self.assertIn('client_seconds: int = 540',gateway)
    def test_outcome7_staged_q8_folder_is_the_tmf3_proof_and_is_superseded(self):
        # TMF3's proof folder was staged from the dc14f5e revisions with the 47% threshold. TMF4b moved the pins, so it is
        # checked as what it is (the earlier tool's proof, still internally consistent), never as a current candidate.
        work=Q8_WORK
        if not (work/'builder/candidate-manifest.json').exists():self.skipTest('the proof folder is staged after the tests')
        manifest=json.loads((work/'builder/candidate-manifest.json').read_text())
        self.assertFalse(manifest['launch_authorized']);self.assertFalse((work/'root-one-native-attempt.json').exists());self.assertFalse((work/'builder/startup-preflight.json').exists());self.assertFalse((work/'run1').exists())
        revisions=manifest['model_profile']['src_revisions'];self.assertEqual((revisions['mirofish_native_model.py']['sha256'],revisions['mirofish_native_run.py']['sha256']),(TMF3_MODEL_SRC_SHA,TMF3_RUN_SRC_SHA))
        self.assertNotEqual((s.MODEL_SRC_SHA,s.RUN_SRC_SHA),(TMF3_MODEL_SRC_SHA,TMF3_RUN_SRC_SHA))
        for relative in s.RUN_COPIES:self.assertIn(b'(47 if pre else 30)',(work/relative).read_bytes())
        self.assertFalse(list(work.rglob(s.REGISTRY_NAME)));s.assert_staged(work,manifest)
# ---- TMF4b (2026-11-15): a plan of two models by phase; TMF4 addenda 1 and 2 -----------------------------------------------
SRC_REVIEW='blind-checked on 2026-11-15 (V136, pass with notes; the four source repairs of addendum 3 applied afterwards and awaiting their own check)'  # TMF4 addendum 3
PLAN={'setup':'ternary-bonsai-2-27b','turns':'qwen3-8b'}
PLAN_WORK=s.CREW/'valedia-native-20261115-swarm8-b2q8-a'
BONSAI_DIR=s.BASE/'models/lmstudio-library/prism-ml/Ternary-Bonsai-2-27B-gguf'
PLAN_PREDICATE='two pinned models by phase: setup model for preparation and report, turns model for the simulation, swapped at the two boundaries with the gateway idle, the same two probes after each swap'
def scratch_entry(root,selector=None):
    """A scratch observer entry holding the pinned src/ revisions (and the gateway they import), with an optional selector."""
    entry=Path(root)/'owned-source/observer-entry';entry.mkdir(parents=True)
    for name in ('mirofish_native_model.py','mirofish_native_run.py','mirofish_native_gateway.py'):(entry/name).write_bytes((s.SRC/name).read_bytes())
    if selector is not None:(entry/s.MODEL_SELECTOR).write_bytes(selector)
    return entry
class PlanStaging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.storage_guard();cls.temp_root=s.RESUME/'build/staging-test-tmp';cls.temp_root.mkdir(exist_ok=True);cls.base=base_manifest()
    def test_tmf4b_src_pins_moved_to_the_tmf4a_revisions_of_this_repository(self):
        self.assertEqual(s.SRC,Path(s.__file__).absolute().parents[1]/'src')                       # the tool's own repository, never another tree
        self.assertEqual((s.MODEL_SRC_SHA,s.RUN_SRC_SHA),('e1cd97e15a990a8418fca22e044520eb19b5bf76edb7b227a4e471e431792115','30a068cf82cf967bc3644b82c4518706611756719ac3336056feb2677ad18b79'))   # TMF5 moved the run pin (was 0426dd5a; f09d4870 before addendum 2)
        self.assertEqual((s.sha(s.SRC/'mirofish_native_model.py'),s.sha(s.SRC/'mirofish_native_run.py')),(s.MODEL_SRC_SHA,s.RUN_SRC_SHA))
        for text in ['fc99603','38bce8a','TMF4a','addendum 3',SRC_REVIEW]:self.assertIn(text,s.SRC_REVISION_NOTE)
        self.assertTrue(s.SRC_REVIEW_STATUS.startswith(SRC_REVIEW+'; the TMF5 revisions'))   # TMF5 appends its own open review
        self.assertEqual(s.BUILTIN_MODELS,('qwen3.8-27b','qwen3-8b','ternary-bonsai-2-27b'))
    def test_tmf4b_bonsai_choice_pins_its_projector_and_its_selection_size(self):
        b=s.model_choice('ternary-bonsai-2-27b',self.base);root=s.BASE/'models/Qwen3.8-27B-MLX-4bit';files=b['tokenizer_files']
        gguf,mmproj=BONSAI_DIR/'Ternary-Bonsai-2-27B-Q2_0.gguf',BONSAI_DIR/'Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf'
        self.assertEqual([r['path'] for r in b['model_files']],[str(root/f) for f in files]+[str(gguf),str(mmproj)])
        self.assertEqual([(r['bytes'],r['sha256']) for r in b['model_files'][-2:]],[(7626008928,'4f99aed01b8a877e153f9aa6569a4440fe17c59701cc0953e36d7f460549e70e'),(629246976,'6807ede61d570bb86ba34b756a0fa109edc33668604de867c6ea6d8f1d631903')])
        self.assertEqual(b['model_tokenizer'],self.base['model_tokenizer'])                         # the 27B's tokenizer origin, its reviewed pins
        self.assertEqual((b['serving_bytes'],b['start_threshold'],b['default'],b['plan'],b['active'],b['limits_key']),(8255255904,74,False,None,'ternary-bonsai-2-27b','ternary-bonsai-2-27b'))
        self.assertEqual(b['model_files'][-2]['role'],'sole serving GGUF Q2_0 weight; size and SHA-256 checked at staging')
        self.assertEqual(b['model_files'][-1]['role'],'second file LM Studio loads with the weights (vision projector); loaded and unused; size and SHA-256 checked at staging')
        self.assertEqual(s.selector_bytes('ternary-bonsai-2-27b'),b'{"profile": "ternary-bonsai-2-27b"}\n')
        # The selection check follows the profile: LM Studio's sizeBytes is the two files together.
        entry=copy.deepcopy(b['entry']);s.check_selection('ternary-bonsai-2-27b',entry)
        for change,why in [(lambda e:e['gguf_selection'].update(sizeBytes=7626008928),'selection disagrees'),(lambda e:e.update(selection_bytes=8255255905),'selection disagrees'),
                           (lambda e:e.update(second_sha256='X'*64),'hash shape'),(lambda e:e['gguf_selection'].update(maxContextLength=16384),'32,768 window'),(lambda e:e.pop('selection_bytes'),'selection disagrees')]:
            bad=copy.deepcopy(b['entry']);change(bad)
            with self.assertRaisesRegex(RuntimeError,why):s.check_selection('ternary-bonsai-2-27b',bad)
        s.check_selection('qwen3-8b',s.model_choice('qwen3-8b',self.base)['entry'])               # a profile without a second file: weights alone
    def test_tmf4b_plan_choice_pins_both_members_in_load_order(self):
        p=s.plan_choice('ternary-bonsai-2-27b','qwen3-8b',self.base);b=s.model_choice('ternary-bonsai-2-27b',self.base);q=s.model_choice('qwen3-8b',self.base)
        self.assertEqual((p['plan'],p['active'],p['name'],p['limits_key'],p['default']),(PLAN,'ternary-bonsai-2-27b',None,PLAN,False))
        self.assertEqual([r['path'] for r in p['model_files']],[r['path'] for r in b['model_files']]+[r['path'] for r in q['model_files']])
        roles=[r['role'] for r in p['model_files']];self.assertEqual(roles[:5],[r['role'] for r in self.base['model_files'][:5]])
        self.assertTrue(roles[5].startswith('setup and report model ternary-bonsai-2-27b: sole serving GGUF Q2_0 weight') and roles[6].startswith('setup and report model ternary-bonsai-2-27b: second file'))
        self.assertTrue(roles[12].startswith('turns model qwen3-8b: sole serving GGUF Q4_K_M weight'))
        self.assertEqual(p['model_tokenizer'],self.base['model_tokenizer']);self.assertEqual(p['tokenizer_by_profile'],{'ternary-bonsai-2-27b':self.base['model_tokenizer'],'qwen3-8b':s.TOKENIZER_PINS['qwen3-8b']})
        self.assertEqual(p['paths'],b['paths']|q['paths']);self.assertEqual(p['start_threshold'],74);self.assertEqual(p['members_threshold'],{'ternary-bonsai-2-27b':74,'qwen3-8b':74})
        self.assertEqual(p['selector'],b'{"plan": {"setup": "ternary-bonsai-2-27b", "turns": "qwen3-8b"}}\n');self.assertEqual(s.selector_bytes(plan=PLAN),p['selector'])
        shared=s.plan_choice('qwen3.8-27b','ternary-bonsai-2-27b',self.base)                       # a tokenizer origin two members share is listed once
        self.assertEqual(len(shared['model_files']),5+1+2);self.assertEqual(shared['start_threshold'],74)
        for args,why in [(('qwen3-8b','qwen3-8b'),'two different'),(('qwen9-1b','qwen3-8b'),'unknown model profile'),(('qwen3-8b','qwen3.8-27b-mlx'),'HTTP backends only')]:
            with self.assertRaisesRegex(RuntimeError,why):s.plan_choice(*args,self.base)
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            registry=Path(folder)/s.REGISTRY_NAME;registry.write_text(json.dumps({'profiles':{'evil':{'native':True}}}))
            with self.assertRaisesRegex(RuntimeError,'registry-only'):s.plan_choice('evil','qwen3-8b',self.base,registry)
        for bad in [{'setup':'qwen3-8b'},{'setup':'a','turns':'b','extra':'c'},{'setup':'evil','turns':'qwen3-8b'}]:
            with self.assertRaises(RuntimeError):s.selector_bytes(plan=bad)
    def test_tmf4b_plan_helper_and_policy_are_what_the_src_revisions_generate(self):
        reviewed=(s.W16/s.SDK_HELPER_REL).read_text();big=s.model_choice('qwen3.8-27b',self.base);policy_ports=(self.base['proxy_port'],self.base['graph_port'])
        choices={'plan':s.plan_choice('ternary-bonsai-2-27b','qwen3-8b',self.base),'bonsai':s.model_choice('ternary-bonsai-2-27b',self.base),'8b':s.model_choice('qwen3-8b',self.base),'27b':big}
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            default_work=Path(folder)/'default';scratch_entry(default_work)
            default_policy=s.src_observer_policy(default_work,*policy_ports)
            for key,choice in choices.items():
                work=Path(folder)/key;entry=scratch_entry(work,choice['selector']);module=load_module_copy(entry,'tmf4b_helper_'+key.replace('.','_'))
                self.assertEqual(s.model_helper_text(reviewed,choice,big),module.SDK_SCRIPT,key)
                self.assertEqual((module.ACTIVE_MODEL_PROFILE,module.MODEL_PLAN),(choice['active'],choice['plan']),key)
                staged=s.src_observer_policy(work,*policy_ports)
                self.assertEqual(s.model_policy(default_policy.replace(str(default_work),str(work)),choice,big),staged,key)
            self.assertEqual(s.model_helper_text(reviewed,big,big),reviewed)
            plan_text=s.model_helper_text(reviewed,choices['plan'],big)
            self.assertIn('const profiles=',plan_text);self.assertIn('"weightSize": 8255255904',plan_text);self.assertIn('"weightSize": 5225374496',plan_text);self.assertNotIn('weightSize=18973870528',plan_text)
            self.assertEqual(s.model_helper_text(s.sdk_port_helper(reviewed,59671),choices['plan'],big).count('ws://127.0.0.1:59671'),1)
            self.assertIn('weightSize=8255255904,',s.model_helper_text(reviewed,choices['bonsai'],big))   # the selection size, not the weight file alone
            plan_policy=s.src_observer_policy(Path(folder)/'plan',*policy_ports)
            for path in [s.BASE/'models/Qwen3.8-27B-MLX-4bit',s.BASE/'models/Qwen3-8B-tokenizer']:self.assertEqual(plan_policy.count('(subpath "'+str(path)+'")'),1)
            for path in [BONSAI_DIR/'Ternary-Bonsai-2-27B-Q2_0.gguf',BONSAI_DIR/'Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf',s.BASE/'models/lmstudio-library/Qwen/Qwen3-8B/Qwen3-8B-Q4_K_M.gguf']:self.assertEqual(plan_policy.count('(literal "'+str(path)+'")'),1)
            self.assertNotIn('Qwen3.8-27B-Q4_K_M.gguf',plan_policy)
            with self.assertRaisesRegex(RuntimeError,'checkpoint rule missing or ambiguous'):s.model_policy(plan_policy,choices['plan'],big)
            with self.assertRaisesRegex(RuntimeError,'helper checkpoint text'):s.model_helper_text(plan_text,choices['plan'],big)
    def test_tmf4b_plan_numbers_count_the_post_swap_probes_and_time_each_phase_on_its_model(self):
        for name,(chats,output,wall,expected) in {'swarm8':(192,800000,5400,3044),'swarm48':(672,2800000,19800,8787)}.items():
            n=s.swarm_numbers(name,PLAN);big=s.swarm_numbers(name)
            self.assertEqual((n['chats'],n['output'],n['wall'],n['expected_total_s'],n['worker_deadline_s']),(chats,output,wall,expected,wall-120),name)
            self.assertEqual((n['probe_chats'],n['swap_chats'],n['reserve_need']),(6,4,6*512+(chats-6)*4096),name)
            for key in ['gateway_arrival_s','gateway_queue_s','agent_client_s','caller_client_s','request_bytes','worker_event_bytes','window_need','memory_budget','agents_bound']:self.assertEqual(n[key],big[key],(name,key))
            self.assertEqual(n['setup_s'],s.swarm_numbers(name,'ternary-bonsai-2-27b')['setup_s']);self.assertEqual(n['expected_round_s'],s.swarm_numbers(name,'qwen3-8b')['expected_round_s'])
        gateway=s.swarm_gateway_bytes((s.W16/sorted(s.GATEWAY_COPIES)[0]).read_bytes(),'swarm8',PLAN).decode()
        for text in ['chat_attempts: int = 192','output_total: int = 800000','"wall_seconds": 5400,','client_seconds: int = 540']:self.assertIn(text,gateway)
    def test_tmf4b_main_refuses_bad_model_options_in_one_line(self):
        import contextlib,io
        for argv in (['--profile','swarm8','--setup-model','ternary-bonsai-2-27b'],['--profile','swarm8','--turns-model','qwen3-8b'],
                     ['--profile','swarm8','--model','qwen3-8b','--setup-model','ternary-bonsai-2-27b','--turns-model','qwen3-8b'],
                     ['--profile','swarm8','--setup-model','qwen3-8b','--turns-model','qwen3-8b'],['--profile','report64','--setup-model','ternary-bonsai-2-27b','--turns-model','qwen3-8b'],
                     ['--profile','swarm8','--setup-model','qwen9-1b','--turns-model','qwen3-8b'],['--profile','swarm8','--setup-model','ternary-bonsai-2-27b','--turns-model','qwen3.8-27b-mlx']):
            err=io.StringIO()
            with contextlib.redirect_stderr(err):self.assertEqual(s.main(argv),2,argv)
            self.assertEqual(len(err.getvalue().strip().splitlines()),1,argv);self.assertTrue(err.getvalue().startswith('refused: '),argv)
    def manifest_for(self,choice,profile='swarm8'):
        """The fields stage() has set before the model fields are written, from the base manifest."""
        derived=copy.deepcopy(self.base);derived['staging_profile']={'name':profile}
        derived['metadata_reuse_impact']={'evidence':s.METADATA_SHA,'scope':'Format conformance reused only: same roles, tools, schemas, LLMClient, SDK/model/tokenizer; ...'}
        derived['derived_from']={'removed_metadata_prerequisite':'ACTUAL_SUPPORTED_THREE_CASE_METADATA_ACCEPTED'}
        p=s.swarm_numbers(profile,choice['limits_key']);return s.model_manifest(derived,choice,p,[{'path':'/x/selector','sha256':'0'*64}]),p
    def test_tmf4b_manifest_states_the_plan_its_pins_and_nothing_borrowed(self):
        plan=s.plan_choice('ternary-bonsai-2-27b','qwen3-8b',self.base);m,p=self.manifest_for(plan)
        self.assertEqual((m['model_plan'],m['model_tokenizer_by_profile'],m['model_tokenizer']),(PLAN,plan['tokenizer_by_profile'],self.base['model_tokenizer']))
        self.assertEqual(m['model_files'],plan['model_files'])
        self.assertIn(PLAN_PREDICATE,(s.SRC/'mirofish_native_run.py').read_text());self.assertIn(PLAN_PREDICATE,m['runtime_predicates'])   # the words prepare_candidate writes
        self.assertFalse([x for x in m['runtime_predicates'] if '65pre' in x]);self.assertEqual(len([x for x in m['runtime_predicates'] if x.startswith('74pre/30floor/40GiB owned caps')]),1)
        self.assertEqual(m['startup_recovery']['start_threshold_staged']['percent_free'],74);self.assertIn('65pre',m['startup_recovery']['caps'])   # the W16 record, labelled
        for block in ['GGUF_qualification','mlx_profile_recovery']:self.assertTrue(m[block]['applies_to'].startswith('qwen3.8-27b only'),block)
        self.assertIn('NOT REUSED for ternary-bonsai-2-27b and qwen3-8b',m['metadata_reuse_impact']['scope']);self.assertNotIn('same roles, tools, schemas, LLMClient, SDK/model/tokenizer',m['metadata_reuse_impact']['scope'])
        self.assertTrue(m['derived_from']['removed_metadata_prerequisite'].startswith('NOT_SHOWN_FOR_TERNARY-BONSAI-2-27B_AND_QWEN3-8B'))
        mp=m['model_profile'];self.assertEqual((mp['plan'],mp['src_revisions_review']),(PLAN,s.SRC_REVIEW_STATUS));self.assertTrue(s.SRC_REVIEW_STATUS.startswith(SRC_REVIEW))
        for member in PLAN.values():
            self.assertIn('NOT been shown',mp['members'][member]['format_conformance']);self.assertIn('first run',mp['members'][member]['format_conformance'])
        self.assertIn('loaded and unused',mp['members']['ternary-bonsai-2-27b']['second_file']['note']);self.assertNotIn('second_file',mp['members']['qwen3-8b'])
        st=mp['start_threshold'];self.assertEqual((st['percent_free'],st['by_member'],st['reviewed'],st['stop_floor'],st['rss_bound_gib']),(74,{'ternary-bonsai-2-27b':74,'qwen3-8b':74},65,30,40))
        self.assertIn('floor + margin + setup_cost + fall',st['rule']);self.assertEqual(st['arithmetic'],{m:'floor 30 + margin 10 + setup_cost 24 + fall 10 = 74' for m in PLAN.values()})
        self.assertEqual({k:v['value'] for k,v in st['terms'].items()},{'floor':30,'margin':10,'setup_cost':24,'fall':10})
        self.assertEqual(mp['co_resident']['file'],'~/.lmstudio/models/debackerl/elephant-1-Q8_0-GGUF/elephant-1.Q8_0.gguf');self.assertEqual(mp['producer_patches']['files'],s.TMF5_PRODUCER_FILES)
        self.assertEqual({k:v['sha256'] for k,v in mp['src_revisions'].items()},{'mirofish_native_model.py':s.MODEL_SRC_SHA,'mirofish_native_run.py':s.RUN_SRC_SHA,'mirofish_native_startup.py':s.STARTUP_SRC_SHA,'mirofish_native_gateway.py':s.GATEWAY_SRC_SHA})
        self.assertEqual(len([x for x in m['runtime_predicates'] if x.startswith('TMF5 stop rule:')]),1)
        budget=mp['budgets'];self.assertEqual((budget['post_swap_probe_chats'],budget['post_swap_probe_output']),(4,2048));self.assertIn('(2 + 4) x 512 + (192 - 6) x 4096 = 764928',budget['output'])
        phases=mp['expected_by_phase'];self.assertEqual((phases['setup_s'],phases['simulation_s'],phases['swaps_s'],phases['report_s'],phases['total_s'],phases['wall_s']),(p['setup_s'],sum(p['expected_round_s']),30,p['report_s'],3044,5400))
        self.assertIn('ternary-bonsai-2-27b',phases['setup_model']);self.assertIn('qwen3-8b',phases['turns_model'])
        self.assertIn('first swap settles it',mp['open_points']['identifier_reuse'])
        self.assertEqual(m['staging_profile']['model_plan'],PLAN)
        run_text=(s.SRC/'mirofish_native_run.py').read_text()                                          # R10 as built: the 27B's identifiers go to the 27B's record only
        self.assertIn("if profile==DEFAULT_MODEL_PROFILE:  # R10",run_text)
        for member in PLAN.values():
            prov=mp['members'][member]['provenance'];self.assertNotIn('known_wrong',prov)
            self.assertIn('writes no 27B revision identifiers for it',prov['recorded_by_run']);self.assertEqual(prov['correct_source']['gguf_sha256'],s.model_choice(member,self.base)['entry']['weight_sha256'])
    def test_tmf4b_manifest_for_one_named_model(self):
        m,_=self.manifest_for(s.model_choice('qwen3.8-27b',self.base))                               # the 27B named: its own evidence, the formula's 72
        self.assertNotIn('applies_to',m['GGUF_qualification']);self.assertNotIn('model_plan',m);self.assertTrue([x for x in m['runtime_predicates'] if x.startswith('74pre/30floor/40GiB')])
        self.assertIn('same roles, tools, schemas',m['metadata_reuse_impact']['scope'])
        m,_=self.manifest_for(s.model_choice('qwen3-8b',self.base))
        self.assertTrue(m['GGUF_qualification']['applies_to'].startswith('qwen3.8-27b only'));self.assertTrue([x for x in m['runtime_predicates'] if x.startswith('74pre/30floor/40GiB')])
        self.assertIn('NOT REUSED for qwen3-8b',m['metadata_reuse_impact']['scope']);self.assertEqual(m['model_profile']['start_threshold']['percent_free'],74)
    def test_tmf4b_staged_plan_folder_is_the_plan_candidate_unlaunched(self):
        work=PLAN_WORK
        if not (work/'builder/candidate-manifest.json').exists():self.skipTest('the proof folder is staged after the tests')
        manifest=json.loads((work/'builder/candidate-manifest.json').read_text());pins={row['path']:row['sha256'] for row in manifest['files']};plan=s.plan_choice('ternary-bonsai-2-27b','qwen3-8b',self.base)
        self.assertFalse(manifest['launch_authorized']);self.assertFalse((work/'root-one-native-attempt.json').exists());self.assertFalse((work/'builder/startup-preflight.json').exists());self.assertFalse((work/'run1').exists())
        for relative in s.SELECTOR_COPIES:self.assertEqual((work/relative).read_bytes(),plan['selector']);self.assertEqual(pins[str(work/relative)],s.sha(work/relative))
        self.assertFalse(list(work.rglob(s.REGISTRY_NAME)))
        port=manifest['sdk_port_revision']['staged'];src_model=(s.SRC/'mirofish_native_model.py').read_bytes();expected=src_model if port==s.ORIGINAL_SDK_PORT else s.sdk_port_bytes(src_model,port,s.MODEL_SRC_SHA)
        for relative in s.MODEL_COPIES:self.assertEqual((work/relative).read_bytes(),expected)
        # TMF5 moved the run pin: this TMF4 folder is the proof of TMF4's plan staging and carries TMF4's run revision (0426dd5a).
        ancestry={row['target']:row for row in json.loads((work/'builder/staging-ancestry.json').read_text())['source_copies']} if (work/'builder/staging-ancestry.json').exists() else {}
        for relative in s.RUN_COPIES:
            run=(work/relative).read_bytes();self.assertIn(b'(57 if pre else 30)',run);self.assertEqual(pins[str(work/relative)],s.sha(work/relative))
            if ancestry:self.assertEqual(ancestry[str(work/relative)]['src_sha256'],'0426dd5a52ad363f92d02149258507f80bf5f9d8fe51f4a1fa066733f0816b50')
        self.assertEqual(manifest['model_files'],[dict(r,bytes=Path(r['path']).stat().st_size) for r in plan['model_files']])
        self.assertEqual((manifest['model_plan'],manifest['model_tokenizer_by_profile'],manifest['model_tokenizer']),(PLAN,plan['tokenizer_by_profile'],plan['model_tokenizer']))
        self.assertEqual((manifest['swarm_profile']['wall'],manifest['swarm_profile']['chats'],manifest['swarm_profile']['output']),(5400,192,800000))
        self.assertEqual(manifest['model_profile']['src_revisions_review'],SRC_REVIEW);self.assertIn(PLAN_PREDICATE,manifest['runtime_predicates'])
        policy=(work/'builder/observer.sb').read_text();self.assertNotIn('Qwen3.8-27B-Q4_K_M.gguf',policy);self.assertIn(str(BONSAI_DIR/'Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf'),policy)
        module=s.staged_helper_matches_model(work,port,plan);self.assertEqual(module.MODEL_PLAN,PLAN)
        s.assert_staged(work,manifest)

# ---- TMF5 addendum 1 (2026-11-17): the four src/ revisions, the start table, the co-resident, the producer patches -----------
TMF5_PLAN={'setup':'ternary-bonsai-2-27b','turns':'qwen3-8b'}
def tmf5_temp():
    import os
    return tempfile.TemporaryDirectory(dir=os.environ.get('NATIVE_TEST_ROOT') or None)
class TMF5Staging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.base=base_manifest()
    def test_TMF5_src_pins_are_this_repository_s_four_revisions(self):
        for name,pin in [('mirofish_native_model.py',s.MODEL_SRC_SHA),('mirofish_native_run.py',s.RUN_SRC_SHA),('mirofish_native_startup.py',s.STARTUP_SRC_SHA),('mirofish_native_gateway.py',s.GATEWAY_SRC_SHA)]:
            self.assertEqual(s.sha(s.SRC/name),pin,name);self.assertEqual(s.src_revision(name,pin),(s.SRC/name).read_bytes())
        self.assertNotEqual(s.STARTUP_SRC_SHA,s.STARTUP_ORIGINAL_SHA);self.assertNotEqual(s.GATEWAY_SRC_SHA,s.GATEWAY_ORIGINAL_SHA)
        self.assertEqual(s.MODEL_SRC_SHA,'e1cd97e15a990a8418fca22e044520eb19b5bf76edb7b227a4e471e431792115')   # the model module is not changed (ruling a)
    def test_TMF5_addendum2_preflight_script_is_pinned_and_staged_beside_the_run_script(self):
        self.assertEqual(s.sha(s.SRC/'mirofish_startup_preflight.py'),s.PREFLIGHT_SRC_SHA);self.assertEqual(s.PREFLIGHT_REL,'owned-source/observer-entry/mirofish_startup_preflight.py')
        self.assertEqual(Path(s.PREFLIGHT_REL).parent,Path(sorted(s.RUN_COPIES)[1]).parent)   # beside the observer's run script
        with tmf5_temp() as tmp:
            work=Path(tmp);(work/'owned-source/observer-entry').mkdir(parents=True);pins=[];ancestry=[]
            s.stage_preflight(work,pins,ancestry);target=work/s.PREFLIGHT_REL
            self.assertEqual(target.read_bytes(),(s.SRC/'mirofish_startup_preflight.py').read_bytes());self.assertEqual(target.stat().st_mode&0o777,0o444)
            self.assertEqual(pins,[{'path':str(target),'sha256':s.PREFLIGHT_SRC_SHA}]);self.assertEqual(ancestry[0]['kind'],'PINNED_SRC_STARTUP_PREFLIGHT')
            with self.assertRaises(RuntimeError):s.stage_preflight(work,pins,ancestry)   # never replaced
    def test_TMF5_startup_and_gateway_deltas_are_reproved_against_the_src_revisions(self):
        startup,gateway=(s.SRC/'mirofish_native_startup.py').read_bytes(),(s.SRC/'mirofish_native_gateway.py').read_bytes()
        w16_startup,w16_gateway=(s.W16/sorted(s.STARTUP_COPIES)[0]).read_bytes(),(s.W16/sorted(s.GATEWAY_COPIES)[0]).read_bytes()
        for name in ['swarm8','swarm48']:
            with self.subTest(profile=name):
                n=s.swarm_numbers(name,TMF5_PLAN)
                new,old=s.swarm_startup_bytes(startup,name,TMF5_PLAN,s.STARTUP_SRC_SHA),s.swarm_startup_bytes(w16_startup,name,TMF5_PLAN)
                for fragment in [f'MAX_RECORDS={n["history_records"]}',f'MAX_BYTES={n["history_mib"]}*1024*1024',f'(30 if preflight else {n["wall"]})']:
                    self.assertEqual((new.count(fragment.encode()),old.count(fragment.encode())),(1,1),fragment)
                new,old=s.swarm_gateway_bytes(gateway,name,TMF5_PLAN,s.GATEWAY_SRC_SHA),s.swarm_gateway_bytes(w16_gateway,name,TMF5_PLAN)
                for fragment in [f'"wall_seconds": {n["wall"]},',f'chat_attempts: int = {n["chats"]}',f'request_bytes: int = {n["request_bytes"]}']:
                    self.assertEqual((new.count(fragment.encode()),old.count(fragment.encode())),(1,1),fragment)
                with self.assertRaisesRegex(RuntimeError,'original source drift'):s.swarm_startup_bytes(startup,name,TMF5_PLAN)          # the W16 pin does not admit the src bytes
                with self.assertRaisesRegex(RuntimeError,'original source drift'):s.swarm_gateway_bytes(gateway+b'\n',name,TMF5_PLAN,s.GATEWAY_SRC_SHA)
    def test_TMF5_staged_start_threshold_follows_the_table(self):
        src=(s.SRC/'mirofish_native_run.py').read_bytes();self.assertEqual(s.START_FRAGMENT,'START_FREE_PERCENT=65');self.assertEqual(src.count(b'START_FREE_PERCENT=65 '),1)
        choice=s.plan_choice(TMF5_PLAN['setup'],TMF5_PLAN['turns'],self.base)
        self.assertEqual((choice['start_threshold'],choice['members_threshold']),(74,{'ternary-bonsai-2-27b':74,'qwen3-8b':74}))
        staged=s.swarm_run_bytes(src,'swarm8',choice['limits_key'],s.RUN_SRC_SHA,choice['start_threshold'])
        self.assertEqual(staged.count(b'START_FREE_PERCENT=74 '),1);self.assertNotIn(b'START_FREE_PERCENT=65',staged);self.assertIn(b'STOP_FREE_PERCENT=30 ',staged)
        saved={k:dict(v) for k,v in s.LIMITS.START_TERMS.items()}
        try:
            s.LIMITS.START_TERMS['setup_cost'].update(value=29,source='a load-only test beside the collector, 2026-11-18');s.LIMITS.START_TERMS['fall'].update(value=7,source='same test, 2026-11-18')
            moved=s.plan_choice(TMF5_PLAN['setup'],TMF5_PLAN['turns'],self.base);self.assertEqual(moved['start_threshold'],76)
            self.assertEqual(s.swarm_run_bytes(src,'swarm8',moved['limits_key'],s.RUN_SRC_SHA,moved['start_threshold']).count(b'START_FREE_PERCENT=76 '),1)
            self.assertEqual(s.model_choice('qwen3-8b',self.base)['start_threshold'],76)
        finally:
            for k,v in saved.items():s.LIMITS.START_TERMS[k]=v
        self.assertEqual(s.plan_choice(TMF5_PLAN['setup'],TMF5_PLAN['turns'],self.base)['start_threshold'],74)
    def test_TMF5_co_resident_file_is_written_from_decision_model_and_checked(self):
        raw=(s.SRC/'mirofish_native_startup.py').read_bytes();file=s.decision_model_file()
        self.assertEqual(file,'~/.lmstudio/models/debackerl/elephant-1-Q8_0-GGUF/elephant-1.Q8_0.gguf');self.assertEqual(s.DECISION_MODEL,s.SRC.parent/'valedia_trader/core/decision_model.py')
        self.assertEqual(s.co_resident_bytes(raw,file),raw)                                                   # the pinned src/ revision already carries it
        other=s.co_resident_bytes(raw,'~/.lmstudio/models/x/other.gguf');self.assertEqual(other.count(b"CO_RESIDENT_FILE='~/.lmstudio/models/x/other.gguf'"),1)
        self.assertEqual(other.replace(b'~/.lmstudio/models/x/other.gguf',file.encode()),raw)
        for bad in ["a'b","a\\b",'',None]:
            with self.subTest(bad=bad),self.assertRaises(RuntimeError):s.co_resident_bytes(raw,bad)
        with self.assertRaises(RuntimeError):s.co_resident_bytes(raw.replace(b'CO_RESIDENT_FILE=',b'CO_RESIDENT_NAME=',1),file)
        with tmf5_temp() as tmp:
            path=Path(tmp)/'decision_model.py';path.write_text('FILE = "~/other.gguf"   # home expanded at use\n');self.assertEqual(s.decision_model_file(path),'~/other.gguf')
            path.write_text('FILE = "a"\nFILE = "b"\n')
            with self.assertRaises(RuntimeError):s.decision_model_file(path)
    def test_TMF5_a_plan_whose_serving_file_is_the_co_resident_is_refused(self):
        import os
        with tmf5_temp() as tmp:
            root=Path(os.path.realpath(tmp));f=root/'m/e.gguf';f.parent.mkdir();f.write_bytes(b'G');(root/'l').symlink_to(root/'m');(root/'m/e.gguf.part').write_bytes(b'x')
            for path in [f,root/'l/e.gguf',root/'m/../m/e.gguf']:
                with self.subTest(path=str(path)),self.assertRaisesRegex(RuntimeError,"co-resident's file"):s.refuse_co_resident([path],str(f))
            s.refuse_co_resident([root/'m/e.gguf.part',root/'m',Path(str(root/'M/E.GGUF'))],str(f))      # prefix, folder, case: not equal
        self.assertEqual(s.co_resident_declared(),os.path.realpath(os.path.expanduser(s.decision_model_file())))
        weight=s.model_profiles()[0]['qwen3-8b']['weight_file'];original=s.co_resident_declared
        s.co_resident_declared=lambda path=None:os.path.realpath(str(weight))   # as if the collector served the 8B's own file
        try:
            with self.assertRaisesRegex(RuntimeError,"co-resident's file"):s.plan_choice(TMF5_PLAN['setup'],TMF5_PLAN['turns'],self.base)
            with self.assertRaisesRegex(RuntimeError,"co-resident's file"):s.model_choice('qwen3-8b',self.base)
            self.assertEqual(s.model_choice('ternary-bonsai-2-27b',self.base)['name'],'ternary-bonsai-2-27b')
        finally:s.co_resident_declared=original
        self.assertEqual(s.plan_choice(TMF5_PLAN['setup'],TMF5_PLAN['turns'],self.base)['members'],['ternary-bonsai-2-27b','qwen3-8b'])
    def test_TMF5_producer_patches_from_the_src_run_script_apply_once_to_the_staged_producer(self):
        with tmf5_temp() as tmp:
            entry=scratch_entry(tmp);patches=s.src_producer_patches(entry.parents[1])
        by_file={}
        for relative,old,new in patches:by_file.setdefault('owned-source/producer/backend/'+relative,[]).append((old,new))
        self.assertEqual({k:len(v) for k,v in by_file.items()},s.TMF5_PRODUCER_FILES)
        for relative,rows in by_file.items():
            with self.subTest(file=relative):
                raw=(s.W16/relative).read_bytes()
                if relative in s.SWARM_PRODUCER:raw=s.swarm_producer_bytes(relative,raw,'swarm8',TMF5_PLAN)   # after the integer deltas, as staging does
                changed=s.tmf5_producer_bytes(raw,rows);ast.parse(changed)
                for old,new in rows:self.assertEqual(changed.decode().count(new),1)
                with self.assertRaisesRegex(RuntimeError,'TMF5 producer patch site missing'):s.tmf5_producer_bytes(raw.replace(rows[0][0].encode(),b'# gone',1),rows)
if __name__=='__main__':unittest.main()




