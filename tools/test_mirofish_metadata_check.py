"""Safety/protocol checks; fake SDKs never connect or load model weights."""
import copy,importlib.util,json,sys,tempfile,unittest,uuid
from pathlib import Path
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import patch
P=Path(__file__).with_name('mirofish_metadata_check.py')
spec=importlib.util.spec_from_file_location('metadata_driver',P);d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
class FakeSDK:
    def __init__(self,snapshot,dead=False,poisoned=False):
        self.snapshot=snapshot;self.poisoned=poisoned;self.calls=[];self.process=SimpleNamespace(poll=lambda:0 if dead else None);self.group=SimpleNamespace(stop=lambda end:{'owned_process_stopped':True})
    def call(self,action,**kw):
        self.calls.append((action,kw))
        if action=='snapshot':return self.snapshot
        if action=='unload':
            self.snapshot={'llm':[r for r in self.snapshot['llm'] if r['identifier']!=kw['identifier']],'embedding':self.snapshot['embedding']};return {'unloaded':kw['identifier'],'snapshot':self.snapshot}
        raise AssertionError('generation/load action forbidden in reconciliation test')
class MetadataSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d.storage_guard();(d.BUILD/'test-tmp').mkdir(parents=True,exist_ok=True)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=d.BUILD/'test-tmp');self.work=Path(self.tmp.name)
        fixture=d.BUILD/'support-fixtures/historical-sdk-shapes.json'
        self.assertEqual(d.sha(fixture),'2dce45099379dd40c7b47404da59eed5bdfd79d120d985c53e4c0fa61f8b01a9')
        self.artifacts=d.load(fixture)
        self.embedding=copy.deepcopy(self.artifacts['baseline']['embedding'])
        self.model={'modelKey':self.artifacts['load']['info']['modelKey'],'path':self.artifacts['load']['info']['path'],'size_bytes':self.artifacts['load']['info']['sizeBytes']}
        self.owned=copy.deepcopy(self.artifacts['load']['info'])
        self.foreign=dict(self.owned,identifier='foreign-math',instanceReference='preserve-foreign')
    def tearDown(self):self.tmp.cleanup()
    def receipt(self):return {'model':self.model}
    def snapshot(self,owned=True,embedding=None):return {'llm':([self.owned] if owned else [])+[self.foreign],'embedding':[embedding or self.embedding]}
    def test_actual_controls_preserve_three_required_cases(self):
        bodies=d.plan_objects(d.capture_plan(d.CONTROLS))[1];self.assertEqual(len(bodies),3);self.assertEqual(len(bodies[0]['tools']),6);self.assertEqual(bodies[1]['messages'][-1]['role'],'user');self.assertNotIn('tool_calls',bodies[1]['messages'][-2]);self.assertEqual(bodies[2]['messages'][-1]['role'],'tool');self.assertEqual(json.loads(bodies[2]['messages'][2]['tool_calls'][0]['function']['arguments']),{'ok':True})
    def test_expired_tampered_and_wrong_binding_approvals_refuse(self):
        now=datetime.now(timezone.utc);p=self.work/'approval.json';v={'action':'one-owned-metadata-load','approved_by':'root','nonce':uuid.uuid4().hex,'expires_at_utc':(now+timedelta(minutes=5)).isoformat(),'bindings':{'driver':'exact'}};d.save(p,v);digest=d.sha(p)
        self.assertEqual(d.verify_approval(p,digest,v['bindings'],now),v)
        for binding,stamp in [({'driver':'changed'},now),(v['bindings'],now+timedelta(hours=1))]:
            with self.assertRaises(RuntimeError):d.verify_approval(p,digest,binding,stamp)
        with self.assertRaises(RuntimeError):d.verify_approval(p,'0'*64,v['bindings'],now)
    def test_claim_is_one_use_even_after_failure(self):
        d.claim_once(self.work,'a'*64)
        with self.assertRaises(FileExistsError):d.claim_once(self.work,'a'*64)
    def test_poisoned_load_timeout_uses_fresh_bridge_preserves_foreign(self):
        old=FakeSDK(self.snapshot(),poisoned=True);fresh=FakeSDK(self.snapshot());receipt=self.receipt()
        result=d.reconcile(old,lambda end:fresh,self.owned['identifier'],None,{'embedding':self.embedding},100,receipt)
        self.assertIs(result,fresh);self.assertFalse(old.calls);self.assertEqual(receipt['cleanup_state'],'OWNED_UNLOADED_WITH_PRIOR_UNCERTAINTY');self.assertEqual(fresh.snapshot['llm'],[self.foreign]);self.assertEqual([x[0] for x in fresh.calls],['snapshot','unload','snapshot']);self.assertEqual(fresh.calls[1][1]['instanceReference'],self.owned['instanceReference'])
    def test_guard_killed_stream_reconciles_fresh_without_reuse(self):
        dead=FakeSDK(self.snapshot(),dead=True);fresh=FakeSDK(self.snapshot());receipt=self.receipt()
        d.reconcile(dead,lambda end:fresh,self.owned['identifier'],{'info':self.owned},{'embedding':self.embedding},100,receipt)
        self.assertFalse(dead.calls);self.assertIn('prior SDK operation uncertain',receipt['cleanup_unknown'])
    def test_timeout_absence_remains_unknown(self):
        old=FakeSDK(self.snapshot(False),poisoned=True);fresh=FakeSDK(self.snapshot(False));receipt=self.receipt()
        d.reconcile(old,lambda end:fresh,self.owned['identifier'],None,{'embedding':self.embedding},100,receipt)
        self.assertEqual(receipt['cleanup_state'],'UNKNOWN_LATE_LOAD');self.assertEqual([x[0] for x in fresh.calls],['snapshot'])
    def test_replaced_instance_or_embedding_never_unloaded(self):
        for baseline,loaded in [({'embedding':dict(self.embedding,instanceReference='other')},None),({'embedding':self.embedding},{'info':dict(self.owned,instanceReference='different')})]:
            sdk=FakeSDK(self.snapshot())
            with self.assertRaises(RuntimeError):d.reconcile(sdk,lambda end:sdk,self.owned['identifier'],loaded,baseline,100,self.receipt())
            self.assertNotIn('unload',[x[0] for x in sdk.calls])
    def test_foreign_checkpoint_under_alias_never_unloaded(self):
        snap=self.snapshot();snap['llm'][0]=dict(self.owned,modelKey='foreign')
        sdk=FakeSDK(snap)
        with self.assertRaises(RuntimeError):d.reconcile(sdk,lambda end:sdk,self.owned['identifier'],None,{'embedding':self.embedding},100,self.receipt())
        self.assertNotIn('unload',[x[0] for x in sdk.calls])
    def test_first_case_refusal_aborts_remaining_calls_and_cleans_owned(self):
        plan=d.capture_plan(d.CONTROLS);controls,bodies=d.plan_objects(plan);binding={'controls_sha256':__import__('hashlib').sha256(plan.controls_raw).hexdigest(),'model':controls['model']};sdk=FakeSDK(self.snapshot(False));sdk.poisoned=False;calls=[]
        def call(action,**kw):
            calls.append(action)
            if action=='baseline':
                baseline=copy.deepcopy(self.artifacts['baseline']);baseline['embedding']['instanceReference']=controls['preserve_embedding']['instanceReference'];return baseline
            if action=='load':return copy.deepcopy(self.artifacts['load'])
            if action=='chat_context':raise RuntimeError('actual SDK refusal')
            raise AssertionError(action)
        sdk.call=call
        class Monitor:
            def __init__(self,*a):self.aborted=SimpleNamespace(is_set=lambda:False);self.errors=[];self.stop=SimpleNamespace(set=lambda:None)
            def start(self):pass
            def close(self):pass
            def disarm(self):pass
        fake_m=SimpleNamespace(SDKBridge=lambda *a:sdk,WEIGHT_FILE=Path('/unavailable'),TOKENIZER_ROOT=d.BASE/'models/Qwen3.8-27B-MLX-4bit',TOKENIZER_FILES=['config.json'],TemplateAdmission=lambda *a:SimpleNamespace())
        helpers={'mirofish_native_model':fake_m,'mirofish_native_run':SimpleNamespace(resource_sample=lambda pre:None),'mirofish_native_startup':SimpleNamespace()}
        real_sha=d.sha
        with patch.object(d,'import_helpers',return_value=helpers),patch.object(d,'ResourceMonitor',Monitor),patch.object(d,'sha',side_effect=lambda path:controls['model']['sha256'] if str(path)==controls['model']['file'] else real_sha(path)),patch.object(Path,'stat',return_value=SimpleNamespace(st_size=controls['model']['size_bytes'])),patch.object(d,'reconcile',return_value=sdk) as cleanup:
            result=d.run_live(self.work,plan,binding,'x'*64)
        self.assertEqual(calls,['baseline','load','chat_context']);self.assertEqual(result['status'],'FAILED');cleanup.assert_called_once()
    def test_real_guard_records_exact_bad_sample_then_stops_only_owned_sdk(self):
        import threading,time
        helpers=d.import_helpers();native=helpers['mirofish_native_run'];bad=copy.deepcopy(self.artifacts['resource_sample']);bad['free_percent']=29
        failure=RuntimeError('memory free threshold');failure.resource_sample=bad
        stopped=threading.Event();sdk=FakeSDK(self.snapshot());sdk.group=SimpleNamespace(stop=lambda end:stopped.set() or {'owned_only':True})
        monitor=d.ResourceMonitor.__new__(d.ResourceMonitor);monitor.n=native;monitor.work=self.work;monitor.deadline=time.monotonic()+5;monitor.receipt={};monitor.bridge=lambda:sdk;monitor.stop=threading.Event();monitor.aborted=threading.Event();monitor.errors=[];monitor.sample=lambda:None
        def resource(pre):
            if not pre:raise failure
        with patch.object(native,'resource_sample',side_effect=resource):
            monitor.start();self.assertTrue(stopped.wait(2));monitor.stop.set();monitor.thread.join(2)
        self.assertTrue(monitor.aborted.is_set());self.assertTrue(sdk.poisoned);self.assertEqual(monitor.receipt['resource_failure_samples'][0]['sample'],bad);persisted=d.load(self.work/'metadata-result.json');self.assertEqual(persisted['resource_failure_samples'][0]['sample'],bad)
    def test_approval_uses_one_nofollow_snapshot_and_rejects_symlink(self):
        p=self.work/'approval.json';v={'action':'one-owned-metadata-load','approved_by':'root','nonce':uuid.uuid4().hex,'expires_at_utc':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),'bindings':{}}
        d.save(p,v)
        with patch.object(d,'load',side_effect=AssertionError('must not reread approval')):d.verify_approval(p,d.sha(p),{})
        link=self.work/'link.json';link.symlink_to(p)
        with self.assertRaises(OSError):d.verify_approval(link,d.sha(p),{})
    def test_disarm_joins_guard_before_cleanup_bridge_can_be_created(self):
        import threading
        m=d.ResourceMonitor.__new__(d.ResourceMonitor);m.stop=threading.Event();m.thread=SimpleNamespace(join=lambda timeout:None,is_alive=lambda:False);m.bridge=lambda:object();m.disarm();self.assertTrue(m.stop.is_set());self.assertIsNone(m.bridge())
    def test_review112_controls_and_case_replacement_cannot_change_approved_dispatch(self):
        import hashlib
        controls=d.load(d.CONTROLS);old_alias=controls['model']['identifier'];copies=[]
        for index,case in enumerate(controls['cases']):
            case_path=self.work/('case-'+str(index)+'.json');case_path.write_bytes(Path(case['case_path']).read_bytes());case['case_path']=str(case_path);copies.append(case_path)
        controls_path=self.work/'controls.json';d.save(controls_path,controls);real_bind=d.bindings;real_verify=d.verify_approval;seen={}
        def bind_swap(work,plan):
            bound=real_bind(work,plan);changed=copy.deepcopy(controls);changed['model']['identifier']='mf-native-'+'f'*32;d.save(controls_path,changed)
            changed_case=d.load(copies[0]);changed_case['tools'][0]['function']['strict']=False;d.save(copies[0],changed_case)
            with self.assertRaises(TypeError):plan.controls['model']['identifier']='mutation'
            return bound
        def verify(path,digest,binding):
            approval=d.encoded({'action':'one-owned-metadata-load','approved_by':'root','nonce':'a'*32,'expires_at_utc':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),'bindings':binding})
            with patch.object(d,'approval_snapshot',return_value=approval):return real_verify(path,hashlib.sha256(approval).hexdigest(),binding)
        def dispatched(work,plan,binding,approval):
            captured,cases=d.plan_objects(plan);seen['alias']=captured['model']['identifier'];seen['bound']=binding['model']['identifier'];seen['strict']=cases[0]['tools'][0]['function']['strict'];return {'status':'METADATA_QUALIFIED'}
        with patch.object(d,'bindings',side_effect=bind_swap),patch.object(d,'verify_approval',side_effect=verify),patch.object(d,'claim_once'),patch.object(d,'run_live',side_effect=dispatched):
            self.assertEqual(d.main(['--work',str(self.work),'--controls',str(controls_path),'--live','--approval',str(self.work/'unused.json'),'--approval-sha256','0'*64]),0)
        self.assertEqual(seen,{'alias':old_alias,'bound':old_alias,'strict':True})
    def test_review112_recovery_snapshot_failure_stops_every_created_bridge(self):
        import hashlib
        plan=d.capture_plan(d.CONTROLS);controls,bodies=d.plan_objects(plan);binding={'controls_sha256':hashlib.sha256(plan.controls_raw).hexdigest(),'model':controls['model']};stops=[]
        old=FakeSDK(self.snapshot());fresh=FakeSDK(self.snapshot());old.group=SimpleNamespace(stop=lambda end:stops.append('old'));fresh.group=SimpleNamespace(stop=lambda end:stops.append('fresh'))
        def old_call(action,**kw):
            if action=='baseline':
                baseline=copy.deepcopy(self.artifacts['baseline']);baseline['embedding']['instanceReference']=controls['preserve_embedding']['instanceReference'];return baseline
            if action=='load':old.poisoned=True;raise RuntimeError('load timeout: ownership unknown')
            raise AssertionError('poisoned stream reused')
        old.call=old_call
        def fresh_call(action,**kw):raise RuntimeError('recovery snapshot timeout')
        fresh.call=fresh_call
        class Monitor:
            def __init__(self,*a):self.aborted=SimpleNamespace(is_set=lambda:False);self.errors=[];self.stop=SimpleNamespace(set=lambda:None)
            def start(self):pass
            def disarm(self):pass
            def close(self):pass
        created=[]
        def factory(*args):bridge=[old,fresh][len(created)];created.append(bridge);return bridge
        helpers={'mirofish_native_model':SimpleNamespace(SDKBridge=factory),'mirofish_native_run':SimpleNamespace(resource_sample=lambda pre:None),'mirofish_native_startup':SimpleNamespace()};real_sha=d.sha
        with patch.object(d,'import_helpers',return_value=helpers),patch.object(d,'ResourceMonitor',Monitor),patch.object(d,'sha',side_effect=lambda path:controls['model']['sha256'] if str(path)==controls['model']['file'] else real_sha(path)),patch.object(Path,'stat',return_value=SimpleNamespace(st_size=controls['model']['size_bytes'])):
            result=d.run_live(self.work,plan,binding,'x'*64)
        self.assertEqual(len(created),2);self.assertIn('fresh',stops);self.assertEqual(result['cleanup_state'],'UNKNOWN_UNPROVEN');self.assertEqual(result['status'],'FAILED');self.assertIn('recovery snapshot timeout',result['cleanup_failure']);self.assertIn('prior SDK operation uncertain',result['cleanup_unknown']);self.assertEqual(len(result['sdk_process_cleanup']),2)
    def test_old_failed_assistant_final_controls_explicitly_unsupported_and_preserved(self):
        old=d.CONTROLS.parent.parent/'metadata-controls.json';self.assertEqual(d.sha(old),'0282db0869cac310da456557da8b1bca77031e1e60992ee76daf0bd4c78d4080')
        with self.assertRaisesRegex(RuntimeError,'assistant-final continuation unsupported'):d.capture_plan(old)
        plan=d.capture_plan(d.CONTROLS)
        with patch.object(d,'import_helpers',side_effect=AssertionError('must refuse before helpers/load')):
            controls,bodies=d.plan_objects(plan);bodies[2]['messages'].append({'role':'assistant','content':'unsupported continuation'})
            with self.assertRaisesRegex(RuntimeError,'assistant-final continuation unsupported'):d.check_controls(controls,bodies)
    def test_source_based_report_and_tool_shapes_are_honestly_labeled(self):
        controls,bodies=d.plan_objects(d.capture_plan(d.CONTROLS));self.assertEqual(controls['cases'][0]['validated_original_canonical_sha256'],'2ad608cbbe8270e19245327158a8b9ead0e9e3866da82b22df28a05667db77da')
        self.assertTrue(all(c['provenance_kind']=='source_based_representative_not_historical_capture' for c in controls['cases'][1:]))
        report_source=Path(controls['cases'][1]['source'].split(':')[0]).read_text();self.assertIn('messages.append({"role": "assistant", "content": "(Response empty)"})',report_source);self.assertIn('messages.append({"role": "user", "content": "Please continue generating content."})',report_source)
        camel_source=Path(controls['cases'][2]['source'].split(':')[0]).read_text();self.assertIn('"role": "tool"',camel_source);self.assertIn('"tool_call_id": self.tool_call_id or "null"',camel_source)
        cycle=bodies[2]['messages'];self.assertEqual(cycle[-1]['tool_call_id'],cycle[-2]['tool_calls'][0]['id']);self.assertIsInstance(cycle[-2]['tool_calls'][0]['function']['arguments'],str)
    def test_r114_tool_representative_exactly_matches_ast_extracted_camel_schema(self):
        import ast
        controls,bodies=d.plan_objects(d.capture_plan(d.CONTROLS));source=Path(controls['cases'][2]['source'].split(':')[0]);self.assertEqual(d.sha(source),'edee87b14d859a20f0c6140216151e9c0866b3ab3dab6da8976120ec525adf2c')
        nodes=[n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.FunctionDef) and n.name in ('to_openai_assistant_message','to_openai_tool_message')]
        tree=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[]);ast.fix_missing_locations(tree);namespace={'json':json};exec(compile(tree,str(source),'exec'),namespace)
        assistant=bodies[2]['messages'][-2];tool=bodies[2]['messages'][-1];call=assistant['tool_calls'][0];message=SimpleNamespace(content=None,func_name=call['function']['name'],args=json.loads(call['function']['arguments']),tool_call_id=call['id'],mask_output=False,result=tool['content'])
        self.assertEqual(assistant,namespace['to_openai_assistant_message'](message));self.assertEqual(tool,namespace['to_openai_tool_message'](message));self.assertEqual(assistant['content'],'')
        broken=copy.deepcopy(bodies);broken[2]['messages'][-2]['content']=None
        with self.assertRaisesRegex(RuntimeError,'content must be exact empty string'):
            # Keep hashes consistent so this regression specifically exercises the schema gate.
            original=dict(broken[2],model=controls['cases'][2]['original_alias']);controls['cases'][2]['validated_original_canonical_sha256']=__import__('hashlib').sha256(d.encoded(original)).hexdigest();controls['cases'][2]['remapped_canonical_sha256']=__import__('hashlib').sha256(d.encoded(broken[2])).hexdigest();d.check_controls(controls,broken)
if __name__=='__main__':unittest.main()





