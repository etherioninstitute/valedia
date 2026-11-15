"""The coupling inequalities of every swarm profile (TMF2). The derivation is pure arithmetic; the checks of its inputs read the
frozen tokenizer and the reviewed sources, and measure the worker events recorded by earlier runs (all read-only; no process,
no model). Run with the MiroFish virtualenv's Python for the vocabulary check (it needs `tokenizers`)."""
import importlib.util,json,math,os,unittest
from pathlib import Path
def load(name):
    spec=importlib.util.spec_from_file_location(name+'_under_test',Path(__file__).with_name(name+'.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
L=load('mirofish_swarm_limits');S=load('mirofish_native_stage')
class SwarmLimits(unittest.TestCase):
    def test_every_profile_in_the_stage_table_satisfies_every_inequality(self):
        self.assertEqual(set(S.SWARM),{'swarm48','swarm8'})
        for name,profile in S.SWARM.items():self.assertEqual(L.violations(profile),[],name)
    def test_worst_single_calls_follow_the_formula(self):
        self.assertAlmostEqual(L.worst_call_seconds(1024),(32768-1024)/188+1024/23.6+27,6);self.assertAlmostEqual(L.worst_call_seconds(1024),239.24,2)
        self.assertAlmostEqual(L.worst_call_seconds(4096),(32768-4096)/188+4096/23.6+27,6);self.assertAlmostEqual(L.worst_call_seconds(4096),353.07,2)
    def test_time_limits_are_worst_call_plus_ten_percent_rounded_to_thirty_and_clients_thirty_longer(self):
        for profile in S.SWARM.values():
            d=L.derive(profile)
            self.assertEqual((d['agent_slot_s'],d['caller_slot_s']),(270,390))                       # ceil30(1.1 x 239.24), ceil30(1.1 x 353.07)
            self.assertEqual((d['gateway_arrival_s'],d['gateway_queue_s']),(540,270))                # 2 agent calls in flight; 1 ahead in the queue
            self.assertEqual((d['agent_client_s'],d['caller_client_s'],d['embedding_client_s']),(570,570,570))
            self.assertEqual((d['semaphore'],d['agents_in_flight'],d['request_bytes']),(1,2,258048))
            self.assertGreaterEqual(d['gateway_arrival_s'],2*L.worst_call_seconds(1024));self.assertGreaterEqual(d['gateway_queue_s'],L.worst_call_seconds(1024))
    def test_memory_budgets_are_the_rulings_and_fit_the_window(self):
        self.assertEqual((S.SWARM['swarm48']['memory_budget'],S.SWARM['swarm8']['memory_budget']),(12288,4096))
        self.assertEqual((S.SWARM['swarm8']['actors'],S.SWARM['swarm8']['rounds']),(8,3))
        for profile in S.SWARM.values():
            self.assertLessEqual(math.ceil(1.25*profile['memory_budget'])+1750+1024,32768);self.assertNotEqual(profile['memory_budget'],2048)
        self.assertEqual(L.largest_budget(),23995)
    def test_output_budget_lets_every_admitted_call_reserve_its_cap(self):
        for profile in S.SWARM.values():
            d=L.derive(profile);self.assertGreaterEqual(d['output'],2*512+(d['chats']-2)*4096);self.assertGreaterEqual(d['chats'],d['expected_chats'])
    def test_wall_history_and_deadlines_follow_the_wall(self):
        for profile in S.SWARM.values():
            d=L.derive(profile);self.assertEqual(d['wall']%1800,0);self.assertGreaterEqual(d['wall'],1.2*d['ceiling_total_s'])
            self.assertGreaterEqual(d['history_records'],int(d['wall']/0.4)+1);self.assertEqual(d['worker_deadline_s'],d['wall']-120)
    def test_a_broken_profile_is_named_by_the_inequality_it_breaks(self):
        good=dict(S.SWARM['swarm8'])
        self.assertIn('memory budget fits the window at the assumed ratio: 1.25 B + 1750 + 1024 <= 32768',L.violations(dict(good,memory_budget=24000)))
        self.assertIn('largest persona fits the memory budget with room for a feed',L.violations(dict(good,memory_budget=1600)))
        self.assertIn('participant bound covers the named participants and the measured growth',L.violations(dict(good,agents_bound=8)))
        d=L.derive(good);d['agent_client_s']=d['gateway_arrival_s']+29
        self.assertIn('agent client timeout outlasts the gateway by 30 s',L.violations(good,d))
        d=L.derive(good);d['gateway_queue_s']=240
        self.assertIn('gateway queue covers the calls ahead of one agent call',L.violations(good,d))
    # ---- TMF2 addendum 2 -------------------------------------------------------------------------------------------------
    def test_R1_worker_event_limit_is_bounded_by_construction(self):
        self.assertEqual((L.RECENT_ACTIONS,L.ESCAPED_BYTES_PER_TOKEN,L.action_text_tokens()),(50,144,2*1024+3*4096))
        self.assertEqual(L.wait_event_bound(),8192+50*(14336*144+512))
        for name,profile in S.SWARM.items():
            d=L.derive(profile);self.assertEqual(d['worker_event_bytes'],114294784,name);self.assertEqual(d['worker_event_bytes']%2**20,0)
            self.assertGreaterEqual(d['worker_event_bytes'],1.1*(d['wait_event_bound']+1));self.assertGreaterEqual(d['worker_event_bytes'],1.1*(L.FIXED_EVENT_BYTES+1))
            broken=dict(d,worker_event_bytes=131072)                                                   # the reviewed limit
            self.assertIn('worker event limit covers the largest wait event with its margin',L.violations(profile,broken))
    def test_R1_the_bound_inputs_are_the_reviewed_code(self):
        self.assertEqual((S.W16/'owned-source/worker-entry/mirofish_native_run.py').read_bytes().count(b'self.buffer += data; require(len(self.buffer) <= 131072, "worker event byte limit")'),1)
        self.assertEqual((S.W16/'owned-source/producer/backend/app/services/simulation_runner.py').read_bytes().count(b'max_recent_actions: int = 50'),1)
        self.assertEqual((S.W16/'builder/review-source/mirofish_native_model.py').read_bytes().count(b"require(Buffer.byteLength(line)<=262144,'SDK request bytes')"),1)
    def test_R1_escaped_bytes_per_token_is_the_worst_token_of_the_frozen_vocabulary(self):
        try:from tokenizers import Tokenizer
        except ImportError:self.skipTest('needs the MiroFish virtualenv (tokenizers)')
        vocab=Tokenizer.from_file('/Volumes/NIMROD_SSD/Qwen-Weight-Space/models/Qwen3.8-27B-MLX-4bit/tokenizer.json').get_vocab()
        printable=list(range(ord('!'),ord('~')+1))+list(range(ord('\u00a1'),ord('\u00ac')+1))+list(range(ord('\u00ae'),ord('\u00ff')+1));chars=printable[:];extra=0
        for b in range(256):
            if b not in printable:printable.append(b);chars.append(256+extra);extra+=1
        to_byte={chr(c):b for b,c in zip(printable,chars)}
        def worst(b):return 2 if b in (0x22,0x5c,8,9,10,12,13) else 6 if b<0x20 else 1 if b<0x80 else 3
        self.assertEqual(max(sum(worst(to_byte[c]) for c in token) for token in vocab),L.ESCAPED_BYTES_PER_TOKEN)
        for text in ['\u00e9','\u4e2d','\U0001f600','\x01','"']:                                 # the per-byte rule bounds json.dumps itself
            self.assertLessEqual(len(json.dumps(text))-2,sum(worst(b) for b in text.encode()))
    def test_R1_every_recorded_worker_event_fits_its_bound(self):
        crew=S.CREW;files=sorted(crew.glob('*/builder/worker-events/*.json'))
        if not files:self.skipTest('no recorded worker events on this machine')
        texts={'content','quote_content','original_content','post_content','comment_content','query','original_author_name','post_author_name','comment_author_name','target_user_name'}
        def bare(action):
            a=json.loads(json.dumps(action));a['agent_name']=''
            for k,v in list(a.get('action_args',{}).items()):
                if k in texts and isinstance(v,str):a['action_args'][k]=''
            return len(json.dumps(a))+2
        seen=set()
        for path in files:
            value=json.loads(path.read_bytes());line=len(json.dumps(value))+1;stage=path.stem;seen.add(stage)   # the worker writes json.dumps(event)+newline
            if stage=='wait' and value.get('ok'):
                self.assertLessEqual(line,L.wait_event_bound(),path);state=value['data']['state'];actions=state.get('recent_actions',[])
                self.assertLessEqual(len(actions),L.RECENT_ACTIONS)
                for a in actions:self.assertLessEqual(bare(a),L.ACTION_FIXED_BYTES,path)
                empty=json.loads(json.dumps(value));empty['data']['state']['recent_actions']=[];self.assertLessEqual(len(json.dumps(empty))+1,L.STATE_FIXED_BYTES,path)
            else:self.assertLessEqual(line,L.FIXED_EVENT_BYTES,path)
        self.assertTrue({'graph_gate','prepare','start','wait','report'}<=seen)
        stopped=sorted(crew.glob('valedia-native-20261114-swarm48-b/owned-source/producer/backend/uploads/simulations/*/run_state.json'))
        if stopped:                                                                                    # the wait event the stopped run would have sent
            state=json.loads(stopped[0].read_bytes());self.assertEqual(len(state['recent_actions']),50)
            event={'stage':'wait','ok':True,'data':{'simulation_id':state['simulation_id'],'child_exit':0,'monitor_joined':True,'run_state_sha256':'0'*64,'state':state,
                   'platform_logs':{p:{'rows':0,'sha256':'0'*64,'event_types':['CREATE_POST']*12} for p in ('twitter','reddit')},'live_interviews':'unavailable after native --no-wait; no keepalive patch'}}
            self.assertLessEqual(len(json.dumps(event))+1,L.wait_event_bound())
            for a in state['recent_actions']:self.assertLessEqual(bare(a),L.ACTION_FIXED_BYTES)
    def test_R3_ceilings_and_walls_hold_for_everything_the_participant_bound_admits(self):
        self.assertEqual(S.SWARM['swarm8']['agents_bound'],12);self.assertAlmostEqual(L.CALLS_PER_TURN,110/104);self.assertAlmostEqual(L.GROWTH,52/48)
        for name,profile in S.SWARM.items():
            d=L.derive(profile)
            self.assertEqual(d['expected_agents'],math.ceil(profile['actors']*52/48),name)                # expected: named actors x measured growth
            self.assertEqual(d['turns'],profile['rounds']*d['expected_agents']*2);self.assertEqual(d['bound_turns'],profile['rounds']*profile['agents_bound']*2)
            self.assertLessEqual(1.2*d['ceiling_total_s'],d['wall'],name);self.assertGreaterEqual(d['chats'],d['bound_chats'])
        d=L.derive(S.SWARM['swarm8']);self.assertEqual(d['wall'],7200)                                   # the proving run keeps its 2-hour wall by construction
        wide=dict(S.SWARM['swarm8'],agents_bound=16);self.assertGreater(L.derive(wide)['wall'],7200)     # at 16 it would not
        self.assertIn('wall covers the ceiling at the participant bound with its margin',L.violations(wide,dict(L.derive(wide),wall=7200)))
    def test_R5_gateway_request_limit_sits_below_the_helper_cap_and_covers_a_full_window(self):
        for profile in S.SWARM.values():
            d=L.derive(profile);self.assertEqual(d['request_bytes'],258048)
            self.assertGreaterEqual(d['request_bytes'],math.ceil(32768*5.28));self.assertLessEqual(d['request_bytes'],262144-4096)
            self.assertIn('request bytes sit at least 4096 below the SDK helper cap',L.violations(profile,dict(d,request_bytes=262144)))
            self.assertIn('request bytes cover a full-window prompt',L.violations(profile,dict(d,request_bytes=173015)))
if __name__=='__main__':unittest.main()
