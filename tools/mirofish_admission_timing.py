"""Time the offline part of the gateway's chat context admission at the largest admitted size (TMF2).

Run with the MiroFish virtualenv's Python only, under nice:
  nice -n 15 /Volumes/NIMROD_SSD/Qwen-Weight-Space/runtime/mirofish-local-20261106/venv-py311/bin/python -B tools/mirofish_admission_timing.py
The admission (TemplateAdmission.__call__ in the W16 review-source mirofish_native_model.py) asks the model server for its
formatted context, renders the frozen chat template for every candidate view (two tool views x parsed/raw arguments x
thinking on/off) and encodes each rendering with the frozen tokenizer, then encodes the server's text once more. This script
times everything except the server call: render_candidates plus that one encode. It reads the frozen tokenizer files and one
recorded request of the stopped run (13 tools); it creates no SDK client, loads no model and opens no connection.
Largest admitted size: 32,768 window minus 1,024 output reserve = 31,744 template tokens.
"""
import hashlib,json,socket,sys,time
from pathlib import Path
def _refuse(*args,**kwargs):raise OSError('network refused in the offline timing')
socket.socket.connect=_refuse;socket.socket.connect_ex=_refuse;socket.create_connection=_refuse
REVIEW=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space/project/work/sol-foreman-qwen/valedia-mirofish-fresh-20261106-caller-budget/builder/review-source')
REQUEST=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space/project/work/sol-foreman-qwen/valedia-native-20261114-swarm48-b/run1/calls/7f934ab208d9443b86e4ecac0b0d7db6/request.json')
TARGET=32768-1024;REPEATS=3
sys.path.insert(0,str(REVIEW))
import mirofish_native_model as m
manifest={name:hashlib.sha256((m.TOKENIZER_ROOT/name).read_bytes()).hexdigest() for name in m.TOKENIZER_FILES}
admission=m.TemplateAdmission(None,'offline-timing',manifest)
body=json.loads(REQUEST.read_bytes())
assert len(body['tools'])==13,len(body['tools'])
filler=' '.join('alpha bravo charlie delta echo foxtrot golf hotel india juliet'.split()*8)
def sized(words,string_arguments):
    b=json.loads(json.dumps(body))
    users=[x for x in b['messages'] if x.get('role')=='user']
    users[-1]['content']=users[-1]['content']+'\n'+' '.join(filler.split()[i%80] for i in range(words))
    if not string_arguments:
        for x in b['messages']:
            for call in x.get('tool_calls') or []:
                if isinstance(call['function']['arguments'],str):call['function']['arguments']=json.loads(call['function']['arguments'])
    return b
def largest(b):return max(count for _,count in admission.render_candidates(b,b['tools']))
def fit(string_arguments):
    low,high=0,40000
    while low<high:
        middle=(low+high+1)//2
        if largest(sized(middle,string_arguments))<=TARGET:low=middle
        else:high=middle-1
    return sized(low,string_arguments)
results=[]
for string_arguments in (True,False):
    b=fit(string_arguments);tool_view=json.loads(json.dumps(b['tools']))
    times=[]
    for _ in range(REPEATS):
        start=time.monotonic()
        candidates=admission.render_candidates(b,tool_view)
        formatted=max(candidates,key=lambda c:c[1])[0]
        len(admission.tokenizer.encode(formatted,add_special_tokens=False).ids)
        times.append(time.monotonic()-start)
    # Raw string arguments drop the raw-string view (fewer candidates); with none, all eight candidates are rendered.
    results.append({'history_tool_arguments':'as recorded' if string_arguments else 'all decoded to objects',
                    'messages':len(b['messages']),'tools':len(b['tools']),'candidates':len(candidates),'largest_candidate_tokens':max(c for _,c in candidates),
                    'seconds_each':[round(t,3) for t in times],'seconds_max':round(max(times),3),'admission_limit_seconds':10})
for r in results:print(json.dumps(r))
