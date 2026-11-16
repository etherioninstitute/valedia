"""TMF4b item 8: the admission-only format qualification tool, against a fake helper. Nothing here loads a model, starts a
helper or talks to a server: a scratch folder holds the pinned src/ modules with a plan selector (as a staged plan folder's
observer entry does), and a fake SDK renders the frozen template candidates (admitted), an altered text (refused) or fails.
Run with the MiroFish virtualenv's Python (the admission needs jinja2 and tokenizers)."""
import copy,hashlib,importlib.util,io,json,os,shutil,sys,tempfile,unittest,contextlib
from pathlib import Path
def load(name):
    spec=importlib.util.spec_from_file_location(name+'_under_test',Path(__file__).with_name(name+'.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
Q=load('mirofish_format_qualify')
SRC=Path(__file__).absolute().parents[1]/'src'
PLAN={'setup':'ternary-bonsai-2-27b','turns':'qwen3-8b'}
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def tokenizer_pins(root):return {name:sha(Path(root)/name) for name in ('config.json','tokenizer.json','tokenizer_config.json','chat_template.jinja','model.safetensors.index.json')}
TMP=tempfile.TemporaryDirectory();WORK=Path(TMP.name)/'valedia-native-qualify-test'
def setUpModule():
    """A scratch staged plan folder: the observer entry's three modules and selector, the helper the module generates, and a
    manifest that pins them and the members' tokenizer origins, as the staging tool writes them."""
    entry=WORK/'owned-source/observer-entry';entry.mkdir(parents=True);(WORK/'builder').mkdir()
    for name in ('mirofish_native_model.py','mirofish_native_run.py','mirofish_native_gateway.py'):shutil.copy2(SRC/name,entry/name)
    (entry/'mirofish_model_profile.json').write_bytes(b'{"plan": {"setup": "ternary-bonsai-2-27b", "turns": "qwen3-8b"}}\n')
    modules=Q.staged_modules(WORK)
    (WORK/'builder/sdk_native.mjs').write_text(modules.model.SDK_SCRIPT)
    files=[{'path':str(p),'sha256':sha(p)} for p in [entry/'mirofish_native_model.py',entry/'mirofish_native_run.py',entry/'mirofish_native_gateway.py',entry/'mirofish_model_profile.json',WORK/'builder/sdk_native.mjs']]
    by_profile={name:tokenizer_pins(modules.model.MODEL_PROFILES[name]['tokenizer_root']) for name in PLAN.values()}
    manifest={'files':files,'model_plan':PLAN,'model_tokenizer_by_profile':by_profile,'model_tokenizer':by_profile[PLAN['setup']],'launch_authorized':False}
    (WORK/'builder/candidate-manifest.json').write_text(json.dumps(manifest))
def tearDownModule():TMP.cleanup()
def readback(modules,profile,identifier,reference='ref-1',key=None):
    key=key or modules.model.MODEL_PROFILES[profile]['gguf_selection']['modelKey']
    return {'requested':{'key':key,'identifier':identifier},'info':{'identifier':identifier,'modelKey':key,'instanceReference':reference},'cfg':{}}
class FakeSDK:
    """The helper's protocol: baseline, load, chat_context, unload. chat_context renders the frozen template's first candidate
    for the body (what LM Studio would format if it rendered as the frozen template does), or an altered text, or fails."""
    def __init__(self,modules,profile,pins,mode='faithful',load_reply=None,unload_reply=None,fail_on=None):
        self.modules,self.profile,self.pins,self.mode,self.calls=modules,profile,pins,mode,[]
        self.load_reply,self.unload_reply,self.fail_on=load_reply,unload_reply,fail_on;self.renderer=None;self.stopped=False
        class Group:
            def stop(group,deadline):self.stopped=True;return {'stopped':True,'live_group_members':[]}
        self.group=Group()
    def call(self,action,timeout=None,*,deadline=None,**values):
        self.calls.append((action,values))
        if action==self.fail_on:raise self.modules.gateway.NativeRefused('fixed SDK operation refused: '+action+' fake failure')
        if action=='baseline':return {'embedding':{'identifier':'ym-research-embedding','instanceReference':'emb-1'},'embeddingContext':2048,'llm':[]}
        if action=='load':return copy.deepcopy(self.load_reply) if self.load_reply is not None else readback(self.modules,self.profile,values['identifier'])
        if action=='unload':return copy.deepcopy(self.unload_reply) if self.unload_reply is not None else {'unloaded':values['identifier'],'snapshot':{'llm':[],'embedding':[{'identifier':'ym-research-embedding'}]}}
        if action=='chat_context':
            if self.renderer is None:self.renderer=self.modules.model.TemplateAdmission(self,values['identifier'],self.pins,embedding_native_semantics=True,profile=self.profile)
            tools=values['body'].get('tools') or []
            text,count=self.renderer.render_candidates(values['body'],tools)[0]
            if self.mode=='altered':text=text.replace('<|im_start|>assistant','<|im_start|> assistant',1);count=len(self.renderer.tokenizer.encode(text,add_special_tokens=False).ids)
            return {'formatted':text,'tokens':count,'context':32768,'sdk_tool_view':tools,'tool_schema_source_sha256':self.modules.model.SDK_TOOL_SCHEMA_SOURCE_SHA256,'instanceReference':'ref-1'}
        raise AssertionError('the qualification asked the helper for '+action)
class FormatQualify(unittest.TestCase):
    def setUp(self):
        self.modules=Q.staged_modules(WORK);self.manifest=json.loads((WORK/'builder/candidate-manifest.json').read_text());self.out_root=tempfile.TemporaryDirectory()
    def tearDown(self):self.out_root.cleanup()
    def run_with(self,profile,**fake):
        made=[]
        def factory(out,deadline):
            sdk=FakeSDK(self.modules,profile,self.manifest['model_tokenizer_by_profile'][profile],**fake);made.append(sdk);return sdk
        result=Q.qualify(WORK,Path(self.out_root.name)/'out',profile,sdk_factory=factory,recorded=[])
        return result,made[0]
    def test_canned_bodies_are_the_kinds_the_brief_names_and_pass_the_gateway_checks(self):
        names=[name for name,_ in Q.canned_bodies('mf-native-'+'0'*32)]
        self.assertEqual(names,['plain_chat','system_and_user','assistant_tool_call_then_tool_result','assistant_empty_content','assistant_null_content_tool_call'])
        for name,body in Q.canned_bodies('mf-native-'+'0'*32):
            checked=self.modules.gateway.validate_chat(body,self.modules.gateway.Limits('mf-native-'+'0'*32));self.assertEqual(checked['reasoning_effort'],'none',name)
        bodies=dict(Q.canned_bodies('x'*12))
        call=[m for m in bodies['assistant_tool_call_then_tool_result']['messages'] if m.get('tool_calls')][0];self.assertEqual(call['content'],'')
        self.assertEqual(bodies['assistant_tool_call_then_tool_result']['messages'][-1]['role'],'tool')
        self.assertIsNone([m for m in bodies['assistant_null_content_tool_call']['messages'] if m.get('tool_calls')][0]['content'])
        self.assertIn({'role':'assistant','content':''},bodies['assistant_empty_content']['messages'])
    def test_recorded_requests_are_pinned_and_a_changed_or_missing_one_is_skipped_with_its_reason(self):
        self.assertEqual(len(Q.RECORDED),2)
        with tempfile.TemporaryDirectory() as folder:
            good=Path(folder)/'a.json';good.write_text(json.dumps({'model':'mf-native-'+'1'*32,'messages':[{'role':'user','content':'x'}],'max_tokens':8}))
            bodies,skipped=Q.recorded_bodies('mf-native-'+'0'*32,[(good,sha(good)),(good,'0'*64),(Path(folder)/'absent.json','1'*64)])
            self.assertEqual([name for name,_ in bodies],['recorded_1']);self.assertEqual(bodies[0][1]['model'],'mf-native-'+'0'*32)
            self.assertEqual([s['reason'] for s in skipped],['changed since it was pinned','not on this machine'])
        present=[p for p,_ in Q.RECORDED if p.is_file()]
        bodies,skipped=Q.recorded_bodies('mf-native-'+'0'*32)
        self.assertEqual(len(bodies)+len(skipped),2);self.assertEqual(len(bodies),len([1 for p,h in Q.RECORDED if p.is_file() and sha(p)==h]))
        for _,body in bodies:self.modules.gateway.validate_chat(body,self.modules.gateway.Limits('mf-native-'+'0'*32))
    def test_a_faithful_rendering_is_admitted_for_each_plan_member_and_nothing_is_generated(self):
        for profile in PLAN.values():
            result,sdk=self.run_with(profile)
            self.assertEqual([b['admitted'] for b in result['bodies']],[True]*5,profile)
            self.assertTrue(all(b['tokens']>0 and b['candidate_count']>=1 for b in result['bodies']))
            actions=[a for a,_ in sdk.calls];self.assertEqual(actions,['baseline','load']+['chat_context']*5+['unload'],profile)
            self.assertTrue(set(actions)<=Q.HELPER_ACTIONS);self.assertEqual(result['generation_calls'],0)
            load=sdk.calls[1][1];self.assertEqual(load,{'identifier':result['identifier'],'profile':profile})   # a plan folder's helper loads by profile
            self.assertEqual(sdk.calls[-1],('unload',{'identifier':result['identifier'],'instanceReference':'ref-1','embeddingReference':'emb-1','profile':profile}))
            self.assertTrue(result['cleanup_proven']);self.assertTrue(sdk.stopped)
            self.assertRegex(result['identifier'],'^mf-native-[0-9a-f]{32}$')
            written=json.loads((Path(self.out_root.name)/'out/qualify-result.json').read_text());self.assertEqual(written['bodies'],result['bodies'])
            self.tearDown();self.setUp()
    def test_an_altered_rendering_is_refused_with_the_exact_admission_refusal_and_still_unloads(self):
        result,sdk=self.run_with('ternary-bonsai-2-27b',mode='altered')
        self.assertEqual({b['refusal'] for b in result['bodies']},{'actual SDK template differs from all frozen no-thinking/thinking branches'})
        self.assertFalse(any(b['admitted'] for b in result['bodies']));self.assertEqual(sdk.calls[-1][0],'unload');self.assertTrue(result['cleanup_proven'])
    def test_a_failing_helper_call_is_reported_and_cleanup_still_runs(self):
        result,sdk=self.run_with('qwen3-8b',fail_on='chat_context')
        self.assertTrue(all(b['refusal'].startswith('fixed SDK operation refused: chat_context') for b in result['bodies']));self.assertEqual(sdk.calls[-1][0],'unload')
        # a readback naming another checkpoint: no body is tried, the instance is still unloaded
        result,sdk=self.run_with('qwen3-8b',load_reply=readback(self.modules,'ternary-bonsai-2-27b','mf-native-'+'2'*32,'ref-9'))
        self.assertEqual(result['bodies'],[]);self.assertIn('model load readback does not name the plan profile',result['load_refusal'])
        self.assertEqual([a for a,_ in sdk.calls],['baseline','load','unload']);self.assertEqual(sdk.calls[-1][1]['instanceReference'],'ref-9')
        # a refused load: unload of the identifier is still asked for (the helper proves what is its own)
        result,sdk=self.run_with('qwen3-8b',fail_on='load');self.assertEqual([a for a,_ in sdk.calls],['baseline','load','unload']);self.assertIn('load',result['load_refusal'])
        # an unload that leaves a language model listed: cleanup is not proven
        result,sdk=self.run_with('qwen3-8b',unload_reply={'unloaded':'mf-native-'+'3'*32,'snapshot':{'llm':[{'identifier':'x'}]}});self.assertFalse(result['cleanup_proven'])
        # a failed baseline: nothing is loaded, so nothing is unloaded
        result,sdk=self.run_with('qwen3-8b',fail_on='baseline');self.assertEqual([a for a,_ in sdk.calls],['baseline']);self.assertIsNone(result['cleanup']);self.assertTrue(result['cleanup_proven'])
    def test_refusals_before_anything_starts(self):
        out=Path(self.out_root.name)
        never=lambda out,deadline:self.fail('a helper was started')
        for profile,where,why in [('qwen3.8-27b',out/'a','not a profile of this staged folder'),('qwen3-8b',WORK/'builder/q','inside the staged folder'),('qwen3-8b',out,'fresh output folder')]:
            with self.assertRaisesRegex(RuntimeError,why):Q.qualify(WORK,where,profile,sdk_factory=never,recorded=[])
        self.assertFalse((WORK/'builder/q').exists())
        helper=WORK/'builder/sdk_native.mjs';text=helper.read_text()
        try:
            helper.write_text(text+'\n')
            with self.assertRaisesRegex(RuntimeError,'staged helper'):Q.qualify(WORK,out/'b',PLAN['setup'],sdk_factory=never,recorded=[])
        finally:helper.write_text(text)
        manifest=WORK/'builder/candidate-manifest.json';raw=manifest.read_text();changed=json.loads(raw);changed['files'][0]['sha256']='0'*64
        try:
            manifest.write_text(json.dumps(changed))
            with self.assertRaisesRegex(RuntimeError,'does not match its pin'):Q.qualify(WORK,out/'c',PLAN['setup'],sdk_factory=never,recorded=[])
        finally:manifest.write_text(raw)
        with self.assertRaisesRegex(RuntimeError,'another staged folder'):Q.staged_modules(Path(self.out_root.name))
    def test_the_command_refuses_without_the_owners_yes_in_one_line_and_never_writes(self):
        for argv in (['--work',str(WORK),'--profile','qwen3-8b','--out',str(Path(self.out_root.name)/'x')],['--work',str(WORK),'--profile','qwen9-1b','--out',str(Path(self.out_root.name)/'y'),'--owner-approved-load']):
            err=io.StringIO()
            with contextlib.redirect_stderr(err):self.assertEqual(Q.main(argv),2,argv)
            self.assertEqual(len(err.getvalue().strip().splitlines()),1,argv);self.assertTrue(err.getvalue().startswith('refused: '),argv)
        self.assertFalse((Path(self.out_root.name)/'x').exists());self.assertFalse((Path(self.out_root.name)/'y').exists())
        self.assertIn("owner's yes",Q.__doc__);self.assertIn('generates nothing',Q.__doc__)
    def test_the_printed_lines_say_admitted_or_the_exact_refusal(self):
        result,_=self.run_with('ternary-bonsai-2-27b',mode='altered');lines=Q.report_lines(result)
        self.assertTrue(all(line.endswith(': refused: actual SDK template differs from all frozen no-thinking/thinking branches') for line in lines[:5]))
        result,_=self.run_with('qwen3-8b');lines=Q.report_lines(result)
        self.assertTrue(all(': admitted (' in line for line in lines[:5]));self.assertIn('cleanup proven',lines[-1])
if __name__=='__main__':unittest.main()
