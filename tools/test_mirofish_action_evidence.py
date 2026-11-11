"""Offline adversarial and exact archived-case evidence tests. No models/services/authority writes."""
import copy,importlib.util,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import mirofish_action_evidence as e
TRUSTED='791de32d4d878e0a3d33815569f8e943b6de885360326369771e0ff03458d89c'
ROOT=e.CASE.parent/'valedia-action-grounding-20261108'
class Fixture:
    """Mutations are memory-only synthetic trusted-test chains, never saved over real authority."""
    def __init__(self,raw):self.raw=dict(raw);self.reads=[]
    def reader(self,path,limit):self.reads.append(Path(path));return self.raw[Path(path)]
    def get(self,path):return e.strict_json(self.raw[Path(path)])
    def put(self,path,value):self.raw[Path(path)]=e.encoded(value)
    def receipt(self):return self.get(e.CASE/'builder/native-run-receipt.json')
    def event(self,name):return next(x for x in self.receipt()['events'] if x['event']==name)
    def repin(self):
        acceptance=self.get(e.ACCEPTANCE);receipt=self.receipt();ledger=self.get(e.CASE/'builder/gateway-ledger.json');prepared=next(x for x in receipt['events'] if x['event']=='native_prepared')['data'];terminal=next(x for x in receipt['events'] if x['event']=='native_terminal')['data'];sim=e.CASE/'owned-source/producer/backend/uploads/simulations'/acceptance['simulation_id'];prepared['config_sha256']=e.digest(self.raw[sim/'simulation_config.json'])
        for platform in ('twitter','reddit'):
            raw=self.raw[sim/platform/'actions.jsonl'];terminal['platform_logs'][platform]['sha256']=e.digest(raw);terminal['platform_logs'][platform]['rows']=len(raw.splitlines())
        for row in ledger['calls']:
            request=e.CASE/'run1/calls'/row['id']/'request.json';response=e.CASE/'run1/calls'/row['id']/'response.bin'
            if request in self.raw:row['payload_sha256']=row['request_capture_sha256']=e.digest(self.raw[request])
            if response in self.raw:row['response_sha256']=e.digest(self.raw[response])
        self.put(e.CASE/'builder/gateway-ledger.json',ledger);self.put(e.CASE/'builder/native-run-receipt.json',receipt);acceptance['ledger_sha256']=e.digest(self.raw[e.CASE/'builder/gateway-ledger.json']);acceptance['receipt_sha256']=e.digest(self.raw[e.CASE/'builder/native-run-receipt.json']);self.put(e.ACCEPTANCE,acceptance);return e.digest(self.raw[e.ACCEPTANCE])
    def verify(self):return e.load_verified_archive(self.repin(),_reader=self.reader)
class ActionEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,'/Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-evaluation');from storage import guard;guard();(ROOT/'test-tmp').mkdir(parents=True,exist_ok=True);cls.raw={}
        def capture(path,limit):raw=e.read_snapshot(path,limit);cls.raw[Path(path)]=raw;return raw
        cls.verified=e.load_verified_archive(TRUSTED,_reader=capture);cls.ids=[a.call_id for a in cls.verified.actions];cls.sim=e.CASE/'owned-source/producer/backend/uploads/simulations'/cls.verified.simulation_id
    def test_real_archive_four_records_eight_seed_occurrences_and_literal_proposed_recorded_gap(self):
        self.assertEqual((len(self.verified.actions),self.verified.seed_occurrences_excluded),(4,8));self.assertEqual({len(a.quote) for a in self.verified.actions},{350,1699,825,810});comment=next(a for a in self.verified.actions if a.action_type=='CREATE_COMMENT');self.assertIn('post_id',json.loads(comment.proposed_json));self.assertNotIn('post_id',json.loads(comment.recorded_json));self.assertEqual(comment.unverified_fields,('post_id',));self.assertEqual(len(self.verified.source_hashes),17)
        self.assertEqual(self.verified.artifact(),e.load_verified_archive(TRUSTED).artifact())
    def test_tamper_each_of17_trust_links_refused_without_self_rehash(self):
        for path in self.raw:
            with self.subTest(path=path.name):
                f=Fixture(self.raw);f.raw[path]+=b' '
                with self.assertRaises(e.EvidenceRefused):e.load_verified_archive(TRUSTED,_reader=f.reader)
    def test_strict_json_duplicate_nonfinite_and_overflow_in_ignored_nested_fields(self):
        for raw in [b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":Infinity}',b'{"ignored":{"x":1e999}}',b'{"x":-1e999}',b'[]junk']:
            with self.subTest(raw=raw),self.assertRaises(e.EvidenceRefused):e.strict_json(raw)
    def test_symlinks_parent_symlinks_special_and_oversize_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'test-tmp') as folder:
            p=Path(folder);(p/'real').write_bytes(b'12345');(p/'link').symlink_to(p/'real');(p/'dir').mkdir();(p/'parent').symlink_to(p/'dir',target_is_directory=True);(p/'dir/data').write_text('x');os.mkfifo(p/'fifo')
            for path,limit in [(p/'link',10),(p/'parent/data',10),(p/'real',4),(p/'fifo',10)]:
                with self.assertRaises(e.EvidenceRefused):e.read_snapshot(path,limit)
    def test_wrong_run_config_model_and_incomplete_native_call_refused(self):
        for change in ['run','config','model','unknown','undispatched','status']:
            f=Fixture(self.raw);receipt=f.receipt();cid=self.ids[0]
            if change=='run':receipt['run_id']='f'*32;f.put(e.CASE/'builder/native-run-receipt.json',receipt)
            elif change=='config':config=f.get(self.sim/'simulation_config.json');config['simulation_id']='sim_'+'f'*12;f.put(self.sim/'simulation_config.json',config)
            elif change=='model':p=e.CASE/'run1/calls'/cid/'request.json';request=f.get(p);request['model']='foreign-model';f.put(p,request)
            else:
                ledger=f.get(e.CASE/'builder/gateway-ledger.json');row=next(x for x in ledger['calls'] if x['id']==cid)
                if change=='unknown':row['state']='unknown'
                elif change=='undispatched':row['dispatched']=False
                else:row['upstream_status']=500
                f.put(e.CASE/'builder/gateway-ledger.json',ledger)
            with self.subTest(change=change),self.assertRaises(e.EvidenceRefused):f.verify()
    def test_duplicate_typed_native_events_and_call_ids_refused(self):
        for kind in ['event','ids','typed_count','bad_id']:
            f=Fixture(self.raw);receipt=f.receipt();event=next(x for x in receipt['events'] if x['event']=='native_terminal')
            if kind=='event':receipt['events'].append(copy.deepcopy(event))
            elif kind=='ids':event['completed_native_forward_calls'][1]=event['completed_native_forward_calls'][0]
            elif kind=='typed_count':event['native_chat_attempts']=True
            else:event['completed_native_forward_calls'][0]='../escape'
            f.put(e.CASE/'builder/native-run-receipt.json',receipt)
            with self.subTest(kind=kind),self.assertRaises(e.EvidenceRefused):f.verify()
    def test_global_duplicate_native_content_across_platforms_refused_before_query(self):
        f=Fixture(self.raw);twitter=next(a for a in self.verified.actions if a.platform=='twitter');reddit=next(a for a in self.verified.actions if a.platform=='reddit' and a.action_type=='CREATE_POST');p=e.CASE/'run1/calls'/reddit.call_id/'response.bin';response=f.get(p);args=json.loads(response['choices'][0]['message']['tool_calls'][0]['function']['arguments']);args['content']=twitter.quote;response['choices'][0]['message']['tool_calls'][0]['function']['arguments']=json.dumps(args);f.put(p,response)
        with self.assertRaisesRegex(e.EvidenceRefused,'duplicate native proposal'):f.verify()
    def test_global_duplicate_log_content_across_platforms_refused_before_filter(self):
        f=Fixture(self.raw);a=next(a for a in self.verified.actions if a.platform=='twitter');source=self.raw[self.sim/'twitter/actions.jsonl'].splitlines()[a.line-1];row=e.strict_json(source);row['platform']='reddit';p=self.sim/'reddit/actions.jsonl';f.raw[p]+=e.encoded(row)+b'\n'
        with self.assertRaisesRegex(e.EvidenceRefused,'duplicate recorded action'):f.verify()
    def test_seed_collision_across_actor_and_platform_refused(self):
        f=Fixture(self.raw);config=f.get(self.sim/'simulation_config.json');config['event_config']['initial_posts'][0]['content']=next(a.quote for a in self.verified.actions if a.platform=='twitter');f.put(self.sim/'simulation_config.json',config)
        with self.assertRaisesRegex(e.EvidenceRefused,'collides with seed'):f.verify()
    def test_unmatched_or_invalid_record_not_silently_native(self):
        f=Fixture(self.raw);a=self.verified.actions[0];p=self.sim/a.platform/'actions.jsonl';rows=[e.strict_json(x) for x in f.raw[p].splitlines()];rows[a.line-1]['action_args']['content']+=' changed';f.raw[p]=b'\n'.join(e.encoded(x) for x in rows)+b'\n'
        with self.assertRaisesRegex(e.EvidenceRefused,'global native-to-recorded bijection'):f.verify()
    def test_exact_typed_citation_all_fields_and_forged_bool_float_actor_refused(self):
        for a in self.verified.actions:self.assertTrue(e.verify_citation(self.verified,a.citation())['valid'])
        a=next(a for a in self.verified.actions if a.actor_id==0)
        for field,value in [('quote',a.quote+'\n'),('platform','other'),('scope','live_market_fact'),('source_id','sim-action-'+'0'*64),('association','proven_execution')]:
            citation=a.citation();citation[field]=value
            with self.subTest(field=field),self.assertRaises(e.EvidenceRefused):e.verify_citation(self.verified,citation)
        for value in [False,0.0,True]:
            citation=a.citation();citation['actor']['id']=value
            with self.assertRaises(e.EvidenceRefused):e.verify_citation(self.verified,citation)
        citation=a.citation();citation['executed_target_post_id']=1
        with self.assertRaises(e.EvidenceRefused):e.verify_citation(self.verified,citation)
    def test_research_adapter_literal_quotes_no_normalization_and_query_bounds(self):
        context=e.research_context(self.verified,'NVDA',4);self.assertIn('UNTRUSTED',context['warning']);self.assertEqual(context['graph_facts'],'not supplied by this adapter; keep graph results distinct')
        for citation in context['citations']:self.assertIn(citation['quote'],context['context']);self.assertTrue(e.verify_citation(self.verified,citation)['valid'])
        self.assertEqual(e.research_context(self.verified,'unfindablezzword')['citations'],[])
        with self.assertRaises(e.EvidenceRefused):e.research_context(self.verified,'x'*257)
        with self.assertRaises(e.EvidenceRefused):e.research_context(self.verified,'',5)
    def test_input_before_after_hashes_unchanged(self):
        for path,raw in self.raw.items():self.assertEqual(e.digest(e.read_snapshot(path)),e.digest(raw))
    def test_post121_create_comment_post_id_exact_integer_schema_no_range_added(self):
        comment=next(a for a in self.verified.actions if a.action_type=='CREATE_COMMENT');path=e.CASE/'run1/calls'/comment.call_id/'response.bin'
        for value in [False,True,1.0,{},[],None,'1']:
            f=Fixture(self.raw);response=f.get(path);tool=response['choices'][0]['message']['tool_calls'][0];args=json.loads(tool['function']['arguments']);args['post_id']=value;tool['function']['arguments']=json.dumps(args);f.put(path,response)
            with self.subTest(value=value),self.assertRaisesRegex(e.EvidenceRefused,'create_comment post_id must be integer'):f.verify()
        f=Fixture(self.raw);response=f.get(path);tool=response['choices'][0]['message']['tool_calls'][0];args=json.loads(tool['function']['arguments']);args['post_id']=-1;tool['function']['arguments']=json.dumps(args);f.put(path,response);verified=f.verify();replayed=next(a for a in verified.actions if a.action_type=='CREATE_COMMENT');self.assertEqual(json.loads(replayed.proposed_json)['post_id'],-1);self.assertNotIn('post_id',json.loads(replayed.recorded_json));self.assertEqual(replayed.unverified_fields,('post_id',))
        actual_artifact=ROOT/'artifacts/builder-candidate-v1.json';self.assertEqual(e.strict_json(e.read_snapshot(actual_artifact)),e.load_verified_archive(TRUSTED).artifact())
if __name__=='__main__':unittest.main()

