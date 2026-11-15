"""The coupling inequalities of every swarm profile (TMF2). Pure arithmetic; no file outside tools/, no process, no model."""
import importlib.util,math,unittest
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
            self.assertEqual((d['semaphore'],d['agents_in_flight'],d['request_bytes']),(1,2,262144))
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
        self.assertIn('participant bound covers the named participants and the extra personas',L.violations(dict(good,agents_bound=8)))
        d=L.derive(good);d['agent_client_s']=d['gateway_arrival_s']+29
        self.assertIn('agent client timeout outlasts the gateway by 30 s',L.violations(good,d))
        d=L.derive(good);d['gateway_queue_s']=240
        self.assertIn('gateway queue covers the calls ahead of one agent call',L.violations(good,d))
if __name__=='__main__':unittest.main()
