"""Reproduce the 2,048-token feed shredding, then run the same sequence through the reviewed whole-unit memory (TMF2).

Run with the MiroFish virtualenv's Python only (camel-ai 0.2.78), under nice:
  nice -n 15 /Volumes/NIMROD_SSD/Qwen-Weight-Space/runtime/mirofish-local-20261106/venv-py311/bin/python -B tools/mirofish_memory_reproduction.py
Offline: tokeniser from the runtime's tiktoken cache, no model created or called, every outbound socket connection refused.
Sizes are the stopped run's (valedia-native-20261114-swarm48-b): the largest persona (1,684 library tokens) and the three
largest feeds in the order they arrived (2,418, 6,477, 6,690), one agent turn per round: feed, tool call, tool result,
closing assistant text.
"""
import importlib.util,json,logging,os,socket,sys,types
from pathlib import Path
RUNTIME=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space/runtime/mirofish-local-20261106')
os.environ['TIKTOKEN_CACHE_DIR']=str(RUNTIME/'cache/tiktoken');os.environ['MIROFISH_TEST_ISOLATED']='1'
def _refuse(*args,**kwargs):raise OSError('network refused in the offline reproduction')
socket.socket.connect=_refuse;socket.socket.connect_ex=_refuse;socket.create_connection=_refuse
from camel.agents import ChatAgent
from camel.messages import BaseMessage,FunctionCallingMessage
from camel.models import ModelFactory
from camel.types import ModelPlatformType,OpenAIBackendRole,RoleType
TOOLS=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('stage_for_reproduction',TOOLS/'mirofish_native_stage.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
PERSONA=1684;FEEDS=(2418,6477,6690)
WORDS='alpha bravo charlie delta echo foxtrot golf hotel india juliet'.split()

class Capture(logging.Handler):
    def __init__(self):super().__init__(logging.DEBUG);self.lines=[]
    def emit(self,record):self.lines.append(record.getMessage())
def load(raw,name):
    module=types.ModuleType(name);module.__file__=name;exec(compile(raw,name,'exec'),module.__dict__);return module
def text_of(counter,tokens,seed):
    return counter.decode(counter.encode(' '.join(WORDS[(seed+i)%len(WORDS)] for i in range(tokens+8)))[:tokens])
def run(label,module):
    capture=Capture();root=logging.getLogger('camel');root.addHandler(capture);root.setLevel(logging.INFO)
    try:
        model=ModelFactory.create(model_platform=ModelPlatformType.OPENAI,model_type='mf-native-offline-reproduction',api_key='offline',url='http://127.0.0.1:9/v1',
                                  model_config_dict={'max_tokens':module.OUTPUT_COMPLETION_TOKENS,'parallel_tool_calls':False,'n':1},timeout=570,max_retries=0)
        counter=model.token_counter
        agent=ChatAgent(system_message=BaseMessage.make_assistant_message(role_name='system',content=text_of(counter,PERSONA,0)),model=model)
        module.admit_owned_agent_context(type('Graph',(),{'get_agents':lambda self:[(0,agent)]})(),model,'twitter')
        rounds=[]
        for number,size in enumerate(FEEDS,1):
            before=len(agent.memory.retrieve())
            feed=BaseMessage.make_user_message(role_name='User',content='Here is your social media environment: '+text_of(counter,size,number))
            agent.update_memory(feed,OpenAIBackendRole.USER);feed_records=len(agent.memory.retrieve())-before
            call='call_%d'%number
            agent.update_memory(FunctionCallingMessage(role_name='assistant',role_type=RoleType.ASSISTANT,meta_dict=None,content='',func_name='create_post',args={'content':text_of(counter,40,number)},tool_call_id=call),OpenAIBackendRole.ASSISTANT)
            agent.update_memory(FunctionCallingMessage(role_name='assistant',role_type=RoleType.ASSISTANT,meta_dict=None,content='',func_name='create_post',result={'success':True,'post_id':number},tool_call_id=call),OpenAIBackendRole.FUNCTION)
            agent.update_memory(BaseMessage.make_assistant_message(role_name='assistant',content=''),OpenAIBackendRole.ASSISTANT)
            records=[r.memory_record for r in agent.memory.retrieve()]
            chunks=[r for r in records if isinstance(r.message.content,str) and r.message.content.startswith('[chunk')]
            bodies=[len(counter.encode(r.message.content.split('\n',1)[1])) for r in chunks]
            try:
                context,tokens=agent.memory.get_context();shape=[m['role'] for m in context];error=None
            except RuntimeError as exc:context,tokens,shape,error=[],None,[],type(exc).__name__+': '+str(exc.args[0])
            users=[m for m in context if m.get('role')=='user']
            newest_whole=bool(users) and users[-1]['content']==feed.content
            rounds.append({'round':number,'feed_library_tokens':size,'records_written_for_the_feed':feed_records,'chunk_records_so_far':len(chunks),
                           'chunk_body_tokens_largest':max(bodies) if bodies else 0,'context_messages':len(context),'context_tokens':tokens,
                           'user_messages_given':len(users),'newest_feed_given_whole':newest_whole,
                           'two_assistants_in_a_row':any(a=='assistant'==b for a,b in zip(shape,shape[1:])),'context_roles':''.join(r[0] for r in shape[:60]),'error':error})
        count=lambda phrase:sum(phrase in line for line in capture.lines)
        return {'case':label,'budget':getattr(module,'INPUT_CONTEXT_TOKENS',None),'rounds':rounds,
                'log':{'Slicing into smaller chunks':count('Slicing into smaller chunks'),'Context truncation performed':count('Context truncation performed'),
                       'Owned memory dropped whole units':count('Owned memory dropped whole units'),'Owned memory trimmed the newest user message':count('Owned memory trimmed the newest user message'),
                       'Owned memory refused the context':count('Owned memory refused the context')}}
    finally:root.removeHandler(capture)
def main():
    canonical=(s.W16/s.MEMORY_REL).read_bytes()
    cases=[('canonical owned_context_budget.py at 2,048 (the bug)',load(canonical,'canonical_owned_context_budget'))]
    for profile in ('swarm48','swarm8'):cases.append(('reviewed whole-unit memory as staged for '+profile,load(s.swarm_memory_bytes(canonical,profile),'staged_memory_'+profile)))
    logging.getLogger('camel').propagate=False  # keep the console to the summary; the lines are counted above
    for label,module in cases:print(json.dumps(run(label,module)))
if __name__=='__main__':main()
