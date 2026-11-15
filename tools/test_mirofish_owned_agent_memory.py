"""The reviewed agent memory (TMF2 addendum 1), P1 to P6, through the agent library's own update_memory and get_context.

Run only with the MiroFish virtualenv's Python (camel-ai 0.2.78, camel-oasis 0.2.5); the repository's own environment has no
CAMEL. Offline: the tokeniser comes from the runtime's tiktoken cache, no model is created or called, and every outbound
socket connection is refused for the whole process.
"""
import importlib.util,json,logging,os,socket,sys,types,unittest
from pathlib import Path
RUNTIME=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space/runtime/mirofish-local-20261106')
os.environ['TIKTOKEN_CACHE_DIR']=str(RUNTIME/'cache/tiktoken');os.environ['MIROFISH_TEST_ISOLATED']='1'
def _refuse(*args,**kwargs):raise OSError('network refused in the offline memory tests')
socket.socket.connect=_refuse;socket.socket.connect_ex=_refuse;socket.create_connection=_refuse
from camel.agents import ChatAgent
from camel.memories import ChatHistoryMemory,ScoreBasedContextCreator
from camel.messages import BaseMessage,FunctionCallingMessage
from camel.models import ModelFactory
from camel.types import ModelPlatformType,OpenAIBackendRole
spec=importlib.util.spec_from_file_location('native_stage_for_memory',Path(__file__).with_name('mirofish_native_stage.py'));s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
CANONICAL=s.W16/s.MEMORY_REL
WORDS=' alpha bravo charlie delta echo foxtrot golf hotel india juliet'.split(' ')[1:]

def staged_module(profile):
    """The exact bytes the stage tool writes for this profile, loaded as a module."""
    raw=s.swarm_memory_bytes(CANONICAL.read_bytes(),profile);module=types.ModuleType('owned_memory_'+profile);module.__file__='staged:'+profile;exec(compile(raw,module.__file__,'exec'),module.__dict__);return module
def canonical_module():
    module=types.ModuleType('owned_memory_canonical');module.__file__=str(CANONICAL);exec(compile(CANONICAL.read_bytes(),module.__file__,'exec'),module.__dict__);return module
def make_model(max_tokens):
    return ModelFactory.create(model_platform=ModelPlatformType.OPENAI,model_type='mf-native-offline-test',api_key='offline-test-key',url='http://127.0.0.1:9/v1',
                               model_config_dict={'max_tokens':max_tokens,'parallel_tool_calls':False,'n':1},timeout=570,max_retries=0)
def text_of(counter,tokens,seed=0):
    """Plain text of about `tokens` library tokens."""
    text=' '.join(WORDS[(seed+i)%len(WORDS)] for i in range(tokens+8))
    return counter.decode(counter.encode(text)[:tokens])
class Graph:
    def __init__(self,agents):self.agents=agents
    def get_agents(self):return list(enumerate(self.agents))
def admitted_agent(module,persona_tokens=1106):
    model=make_model(module.OUTPUT_COMPLETION_TOKENS);persona=text_of(model.token_counter,persona_tokens)
    agent=ChatAgent(system_message=BaseMessage.make_assistant_message(role_name='system',content=persona),model=model)
    module.admit_owned_agent_context(Graph([agent]),model,'twitter');return agent
def feed(text):return BaseMessage.make_user_message(role_name='User',content='Here is your social media environment: '+text)
def one_turn(agent,counter,feed_tokens,round_number,closing=True):
    """What one OASIS agent turn writes: the feed, the tool call, its result, and the closing assistant text."""
    agent.update_memory(feed(text_of(counter,feed_tokens,round_number)),OpenAIBackendRole.USER)
    call_id='call_%d'%round_number;args={'content':text_of(counter,40,round_number+3)}
    agent.update_memory(FunctionCallingMessage(role_name='assistant',role_type=BaseMessage.make_assistant_message('a','').role_type,meta_dict=None,content='',func_name='create_post',args=args,tool_call_id=call_id),OpenAIBackendRole.ASSISTANT)
    agent.update_memory(FunctionCallingMessage(role_name='assistant',role_type=BaseMessage.make_assistant_message('a','').role_type,meta_dict=None,content='',func_name='create_post',result={'success':True,'post_id':round_number},tool_call_id=call_id),OpenAIBackendRole.FUNCTION)
    if closing:agent.update_memory(BaseMessage.make_assistant_message(role_name='assistant',content=''),OpenAIBackendRole.ASSISTANT)
def records(agent):return [r.memory_record for r in agent.memory.retrieve()]
def chunked(agent):return [r for r in records(agent) if isinstance(r.message.content,str) and r.message.content.startswith('[chunk')]
class Capture(logging.Handler):
    def __init__(self):super().__init__(logging.DEBUG);self.lines=[]
    def emit(self,record):self.lines.append((record.levelname,record.name,record.getMessage()))

class OwnedAgentMemory(unittest.TestCase):
    def setUp(self):
        self.capture=Capture();logging.getLogger('camel').addHandler(self.capture);logging.getLogger('camel').setLevel(logging.INFO)
    def tearDown(self):logging.getLogger('camel').removeHandler(self.capture)
    def warnings(self,phrase):return [m for level,name,m in self.capture.lines if phrase in m]

    def test_red_canonical_2048_shreds_the_measured_feed(self):
        agent=admitted_agent(canonical_module(),1684);counter=agent.memory.get_context_creator().token_counter
        one_turn(agent,counter,2418,1)
        self.assertTrue(chunked(agent),'the canonical 2,048 budget no longer slices the measured feed');self.assertTrue(self.warnings('Slicing into smaller chunks'))
        bodies=[len(counter.encode(r.message.content.split('\n',1)[1])) for r in chunked(agent)];self.assertLessEqual(max(bodies),40)   # a few tokens per chunk
        agent.memory.get_context();self.assertTrue(self.warnings('Context truncation performed'))

    def test_P1_nothing_is_ever_sliced_for_any_role_or_size(self):
        for profile in ('swarm8','swarm48'):
            agent=admitted_agent(staged_module(profile));counter=agent.memory.get_context_creator().token_counter;budget=agent.memory.get_context_creator().budget
            huge=text_of(counter,3*budget+500)
            for message,role in [(feed(huge),OpenAIBackendRole.USER),(BaseMessage.make_assistant_message('assistant',huge),OpenAIBackendRole.ASSISTANT),
                                 (FunctionCallingMessage(role_name='assistant',role_type=BaseMessage.make_assistant_message('a','').role_type,meta_dict=None,content='',func_name='refresh',result=huge,tool_call_id='c9'),OpenAIBackendRole.FUNCTION),
                                 (BaseMessage.make_user_message('User',huge),OpenAIBackendRole.SYSTEM)]:
                before=len(records(agent));agent.update_memory(message,role);self.assertEqual(len(records(agent)),before+1,(profile,role))
            self.assertEqual(chunked(agent),[]);self.assertEqual(self.warnings('Slicing into smaller chunks'),[]);self.assertEqual(self.warnings('Context truncation performed'),[])

    def test_P2_P5_whole_units_newest_kept_oldest_dropped_first_within_budget(self):
        module=staged_module('swarm48');agent=admitted_agent(module,1684);counter=agent.memory.get_context_creator().token_counter
        for r,size in enumerate((2418,6477,6690),1):one_turn(agent,counter,size,r)
        context,total=agent.memory.get_context();creator=agent.memory.get_context_creator()
        self.assertLessEqual(total,creator.budget);self.assertEqual(total,sum(counter.count_tokens_from_messages([m]) for m in context))   # P5
        self.assertEqual(context[0]['role'],'system');self.assertEqual(context[1]['role'],'user')
        users=[m for m in context if m['role']=='user'];self.assertTrue(users[-1]['content'].startswith('Here is your social media environment: '+text_of(counter,6690,3)[:200]))
        self.assertEqual(len(context),1+4*len(users))                                    # whole units only: user, call, result, closing text
        self.assertEqual([m['role'] for m in context[1:]],['user','assistant','tool','assistant']*len(users))
        self.assertLess(len(users),3);self.assertTrue(self.warnings(module.DROPPED_PHRASE));self.assertEqual(self.warnings(module.TRIMMED_PHRASE),[])
        everything=[r.to_openai_message() for r in records(agent)];tail=everything[len(everything)-4*len(users):]
        self.assertEqual(context[1:],tail)                                                 # the kept units are the newest ones, unchanged and in order
        self.assertEqual(self.warnings('Slicing into smaller chunks'),[]);self.assertEqual(self.warnings('Context truncation performed'),[])

    def test_P3_never_two_assistant_messages_in_a_row(self):
        module=staged_module('swarm8');agent=admitted_agent(module);counter=agent.memory.get_context_creator().token_counter
        for r in (1,2,3):one_turn(agent,counter,900,r)
        context,_=agent.memory.get_context()
        self.assertFalse(any(a['role']=='assistant'==b['role'] for a,b in zip(context,context[1:])))
        agent.update_memory(BaseMessage.make_assistant_message('assistant','a second closing text'),OpenAIBackendRole.ASSISTANT)
        with self.assertRaises(module.OwnedMemoryShapeError) as raised:agent.memory.get_context()
        self.assertEqual(type(raised.exception.args[1]),int)                               # the library reads args[1] as a token count
        lone=admitted_agent(module);lone.update_memory(feed('hello'),OpenAIBackendRole.USER)
        lone.update_memory(FunctionCallingMessage(role_name='assistant',role_type=BaseMessage.make_assistant_message('a','').role_type,meta_dict=None,content='',func_name='x',result='orphan',tool_call_id='never-called'),OpenAIBackendRole.FUNCTION)
        with self.assertRaises(module.OwnedMemoryShapeError):lone.memory.get_context()     # a tool result without its call

    def test_P4_the_newest_user_message_is_trimmed_from_its_end_only_when_it_cannot_fit_alone(self):
        module=staged_module('swarm8');agent=admitted_agent(module,1684);counter=agent.memory.get_context_creator().token_counter;budget=agent.memory.get_context_creator().budget
        one_turn(agent,counter,6690,1,closing=False)
        context,total=agent.memory.get_context();self.assertLessEqual(total,budget)
        user=context[1]['content'];original=records(agent)[1].message.content
        self.assertEqual(user.splitlines()[-1][:len('[Owned memory budget: this message was cut here;')],'[Owned memory budget: this message was cut here;')
        kept=user.rsplit('\n',1)[0];self.assertTrue(original.startswith(kept[:-8]))          # the beginning is kept; the end is cut
        self.assertEqual([m['role'] for m in context],['system','user','assistant','tool'])  # nothing else trimmed or dropped
        self.assertEqual(context[2:],[r.to_openai_message() for r in records(agent)[2:]])
        lines=self.warnings(module.TRIMMED_PHRASE);self.assertEqual(len(lines),1);self.assertRegex(lines[0],r'kept=\d+ tokens of \d+; budget=4096')
        self.assertEqual([lvl for lvl,name,m in self.capture.lines if module.TRIMMED_PHRASE in m],['WARNING'])
        self.assertEqual(records(agent)[1].message.content,original)                        # the stored record is untouched
        agent.memory.get_context();self.assertEqual(len(self.warnings(module.TRIMMED_PHRASE)),1)  # one line per event, not per rebuild
        small=admitted_agent(module);one_turn(small,counter,500,1);small.memory.get_context();self.assertEqual(self.warnings(module.TRIMMED_PHRASE),lines)

    def test_P6_same_records_same_context(self):
        module=staged_module('swarm8');a=admitted_agent(module,1300);counter=a.memory.get_context_creator().token_counter
        for r,size in enumerate((1500,2400,3100),1):one_turn(a,counter,size,r)
        first=a.memory.get_context();second=a.memory.get_context()
        replay=module.WholeUnitContextCreator(counter,module.INPUT_CONTEXT_TOKENS).create_context(a.memory.retrieve())
        self.assertEqual(json.dumps(first,sort_keys=True),json.dumps(second,sort_keys=True));self.assertEqual(json.dumps(first,sort_keys=True),json.dumps(replay,sort_keys=True))

    def test_guards_of_the_canonical_file_are_kept(self):
        module=staged_module('swarm8');model=make_model(module.OUTPUT_COMPLETION_TOKENS)
        agent=ChatAgent(system_message=BaseMessage.make_assistant_message(role_name='system',content='persona'),model=model)
        with self.assertRaisesRegex(RuntimeError,'Unreviewed platform'):module.admit_owned_agent_context(Graph([agent]),model,'mastodon')
        with self.assertRaisesRegex(RuntimeError,'Owned agent count outside bound'):module.admit_owned_agent_context(Graph([agent]*13),model,'reddit')   # swarm8's bound is 12
        wrong=make_model(512)
        with self.assertRaisesRegex(RuntimeError,'Provider completion cap or model shape drift'):module.admit_owned_agent_context(Graph([agent]),wrong,'reddit')
        other=ChatAgent(system_message=BaseMessage.make_assistant_message(role_name='system',content='persona'),model=model);other.memory=ChatHistoryMemory(ScoreBasedContextCreator(model.token_counter,4096),agent_id=other.agent_id)
        with self.assertRaisesRegex(RuntimeError,'Unreviewed agent context initialization'):module.admit_owned_agent_context(Graph([other]),model,'reddit')
        os.environ['MIROFISH_TEST_ISOLATED']='0'
        try:
            with self.assertRaisesRegex(RuntimeError,'requires isolated runner'):module.admit_owned_agent_context(Graph([agent]),model,'reddit')
        finally:os.environ['MIROFISH_TEST_ISOLATED']='1'
        module.admit_owned_agent_context(Graph([agent]),model,'reddit');creator=agent.memory.get_context_creator()
        self.assertEqual((type(creator).__name__,creator.budget,creator.token_limit),('WholeUnitContextCreator',4096,sys.maxsize))
    # ---- TMF2 addendum 2 -------------------------------------------------------------------------------------------------
    def call_message(self,counter,words,call_id):
        args=json.dumps({'content':' '.join(WORDS[i%len(WORDS)] for i in range(words))})
        return {'role':'assistant','content':'','tool_calls':[{'id':call_id,'type':'function','function':{'name':'create_post','arguments':args}}]},args
    def test_R2_tool_call_arguments_are_counted_by_the_memory_itself(self):
        module=staged_module('swarm8');agent=admitted_agent(module);creator=agent.memory.get_context_creator();counter=creator.token_counter
        library=[];own=[]
        for words in (10,1000,5000):
            message,args=self.call_message(counter,words,'c%d'%words)
            library.append(counter.count_tokens_from_messages([message]));own.append(creator._count(message))
            self.assertGreaterEqual(own[-1],library[-1]+len(counter.encode(args)),words)
        self.assertEqual(len(set(library)),1)                                                            # the library sees the same few tokens whatever the length
        self.assertGreater(own[1]-own[0],900);self.assertGreater(own[2]-own[1],3500)
    def test_R2_a_context_with_long_tool_calls_stays_within_the_budget_in_real_content(self):
        module=staged_module('swarm8');agent=admitted_agent(module,1300);creator=agent.memory.get_context_creator();counter=creator.token_counter
        long_args={'content':' '.join(WORDS[i%len(WORDS)] for i in range(2000))}
        for r in (1,2,3):
            agent.update_memory(feed(text_of(counter,300,r)),OpenAIBackendRole.USER);call='call_%d'%r
            agent.update_memory(FunctionCallingMessage(role_name='assistant',role_type=BaseMessage.make_assistant_message('a','').role_type,meta_dict=None,content='',func_name='create_post',args=long_args,tool_call_id=call),OpenAIBackendRole.ASSISTANT)
            agent.update_memory(FunctionCallingMessage(role_name='assistant',role_type=BaseMessage.make_assistant_message('a','').role_type,meta_dict=None,content='',func_name='create_post',result={'success':True,'post_id':r},tool_call_id=call),OpenAIBackendRole.FUNCTION)
            agent.update_memory(BaseMessage.make_assistant_message(role_name='assistant',content=''),OpenAIBackendRole.ASSISTANT)
        context,total=agent.memory.get_context()
        real=sum(counter.count_tokens_from_messages([m])+sum(len(counter.encode(c['function']['arguments'])) for c in m.get('tool_calls') or []) for m in context)
        self.assertLessEqual(real,creator.budget);self.assertLessEqual(real,total)
        self.assertEqual(len([m for m in context if m['role']=='user']),1)                               # two units of ~2,350 real tokens do not fit 4,096
    def test_R6_every_refusal_logs_one_fixed_phrase_with_the_newest_role(self):
        module=staged_module('swarm8');agent=admitted_agent(module);counter=agent.memory.get_context_creator().token_counter
        one_turn(agent,counter,300,1);agent.update_memory(BaseMessage.make_assistant_message('assistant','a second closing text'),OpenAIBackendRole.ASSISTANT)
        with self.assertRaises(module.OwnedMemoryShapeError):agent.memory.get_context()
        lines=[(lvl,m) for lvl,name,m in self.capture.lines if module.REFUSED_PHRASE in m]
        self.assertEqual(len(lines),1);self.assertEqual(lines[0][0],'WARNING');self.assertIn('newest record role=assistant',lines[0][1])
        with self.assertRaises(module.OwnedMemoryShapeError):agent.memory.get_context()
        self.assertEqual(len(self.warnings(module.REFUSED_PHRASE)),2)                                   # every raise is a line, so they can be counted
        lone=admitted_agent(module);lone.update_memory(feed('hello'),OpenAIBackendRole.USER)
        lone.update_memory(FunctionCallingMessage(role_name='assistant',role_type=BaseMessage.make_assistant_message('a','').role_type,meta_dict=None,content='',func_name='x',result='orphan',tool_call_id='never-called'),OpenAIBackendRole.FUNCTION)
        with self.assertRaises(module.OwnedMemoryShapeError):lone.memory.get_context()
        self.assertIn('newest record role=tool',self.warnings(module.REFUSED_PHRASE)[-1])
        full=admitted_agent(module,4080);full.update_memory(feed(text_of(counter,200,2)),OpenAIBackendRole.USER)   # persona leaves no room for any feed
        with self.assertRaises(module.OwnedMemoryBudgetError):full.memory.get_context()
        self.assertIn('newest record role=user',self.warnings(module.REFUSED_PHRASE)[-1])
if __name__=='__main__':unittest.main()
