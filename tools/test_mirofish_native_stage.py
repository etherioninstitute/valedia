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
    EXPECTED={'swarm48':{'chats':672,'output':2800000,'wall':55800,'worker':55680,'records':139501,'mib':256,'bound':64,'rounds':3,'budget':12288,'events':114294784},
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
            manifest=json.loads((folder/'builder/candidate-manifest.json').read_text());n=s.swarm_numbers(name);p=manifest['swarm_profile']
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
if __name__=='__main__':unittest.main()




