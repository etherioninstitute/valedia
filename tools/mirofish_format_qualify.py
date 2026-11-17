"""Format qualification of one built-in checkpoint, ADMISSION ONLY (TMF4b item 8). It generates nothing.

    python tools/mirofish_format_qualify.py --work <staged folder> --profile <name> --out <fresh folder> --owner-approved-load

With a staged folder's own observer-entry modules and its own helper (both checked against the folder's manifest pins), it
loads the checkpoint in LM Studio as the run would (the run's identifier form, the profile's pinned selection and load
settings), reads the load back, builds the run's template admission for that profile from the folder's tokenizer pins, and
then, for each canned request body (a plain chat, a system plus user turn, an assistant turn carrying a tool call followed by
its tool result, an assistant turn with empty content, an assistant tool call with null content) and the two recorded agent
requests when they are on disk with their pinned hashes, runs the gateway's own request check and the admission: LM Studio's
formatted prompt and token count against the frozen template candidates. The helper is asked only for baseline, load,
chat_context and unload; the tool generates nothing. It always unloads what it loaded and prints, per body, admitted or the
exact refusal; the full result goes to <out>/qualify-result.json. Nothing is written into the staged folder.

It LOADS A MODEL: run it only with the owner's yes, which --owner-approved-load records. Without the flag it refuses.
"""
from __future__ import annotations
import argparse,hashlib,json,shutil,sys,time,uuid
from pathlib import Path
CREW=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space/project/work/sol-foreman-qwen')
HELPER_ACTIONS={'baseline','load','chat_context','unload'}  # never a generation action
# TMF5: the startup module is the fourth: the run module imports it by name when it reads a resource sample, so it is imported
# from the staged entry and pin-checked like the other three, and nothing needs a hand-set path.
ENTRY_MODULES=('mirofish_native_model.py','mirofish_native_run.py','mirofish_native_gateway.py','mirofish_native_startup.py')
SELECTOR='mirofish_model_profile.json'
# Two real agent requests captured by the gateway of the 2026-11-15 swarm8 run (valedia-native-20261115-swarm8-d), pinned by
# hash: a 10-message twitter turn with six tools and a 6-message one. Their model field is replaced by this run's identifier.
RECORDED=[(CREW/'valedia-native-20261115-swarm8-d/run1/calls/09d1cbb2d02e438cbd9f6481d0137286/request.json','49e8a4801eba2e8541080b0f6d3826100b111d77a4c8d6a0aa11eddf5037f15c'),
          (CREW/'valedia-native-20261115-swarm8-d/run1/calls/50d7b40c54974b3a8ffa7a26fe3ebd5c/request.json','f73e34965bf9e45bba49d5fffd9dbcebaa65683f2a1378f410b56d36dbf37a2a')]
LOAD_S,UNLOAD_S,BASELINE_S,ADMISSION_S,WHOLE_S=180,45,15,10,900
class Modules:
    def __init__(self,entry,model,run,gateway,startup):self.entry,self.model,self.run,self.gateway,self.startup=entry,model,run,gateway,startup
_LOADED:dict={}
def require(value,why):
    if not value:raise RuntimeError(why)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def staged_modules(work):
    """The staged folder's own observer-entry modules, imported under their real names (the run module reads the model module
    by name), once per process: a second staged folder in the same process is refused."""
    entry=(Path(work)/'owned-source/observer-entry').absolute()
    if _LOADED:
        require(_LOADED['entry']==entry,'another staged folder\'s modules are already loaded in this process: '+str(_LOADED['entry']));return _LOADED['modules']
    require(all((entry/name).is_file() for name in ENTRY_MODULES),'not a staged folder: no observer entry modules under '+str(entry))
    names=[name[:-3] for name in ENTRY_MODULES];require(not any(name in sys.modules for name in names),'native modules from elsewhere are already loaded in this process')
    sys.path.insert(0,str(entry))
    try:
        import importlib
        model,run,gateway,startup=(importlib.import_module(name) for name in names)
    finally:sys.path.remove(str(entry))
    for module in (model,run,gateway,startup):require(Path(module.__file__).absolute().parent==entry,'a native module was not imported from the staged entry: '+module.__name__)
    _LOADED.update(entry=entry,modules=Modules(entry,model,run,gateway,startup));return _LOADED['modules']
def canned_bodies(identifier):
    tool={'type':'function','function':{'name':'create_post','description':'Create a post.','parameters':{'type':'object','properties':{'content':{'type':'string'}},'required':['content']}}}
    call={'id':'call_1','type':'function','function':{'name':'create_post','arguments':'{"content": "Format check post."}'}}
    base=lambda messages,**extra:dict({'model':identifier,'messages':messages,'max_tokens':64,'temperature':0},**extra)
    return [('plain_chat',base([{'role':'user','content':'Non-market format check. Reply with one word.'}])),
            ('system_and_user',base([{'role':'system','content':'You are a format check. Answer briefly.'},{'role':'user','content':'Non-market format check. Reply with one word.'}])),
            ('assistant_tool_call_then_tool_result',base([{'role':'system','content':'You post on a social platform.'},{'role':'user','content':'Write one short post.'},
                {'role':'assistant','content':'','tool_calls':[call]},{'role':'tool','tool_call_id':'call_1','content':'{"success": true, "post_id": 7}'}],tools=[tool])),
            ('assistant_empty_content',base([{'role':'user','content':'First turn.'},{'role':'assistant','content':''},{'role':'user','content':'Second turn. Reply with one word.'}])),
            ('assistant_null_content_tool_call',base([{'role':'user','content':'Write one short post.'},{'role':'assistant','content':None,'tool_calls':[call]},
                {'role':'tool','tool_call_id':'call_1','content':'{"success": true, "post_id": 7}'}],tools=[tool]))]
def recorded_bodies(identifier,recorded=None):
    """The recorded agent requests that are on disk with their pinned hashes, the model field set to this run's identifier;
    the others are listed as skipped with the reason."""
    bodies,skipped=[],[]
    for i,(path,pinned) in enumerate(RECORDED if recorded is None else recorded,1):
        path=Path(path)
        if not path.is_file():skipped.append({'path':str(path),'reason':'not on this machine'});continue
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=pinned:skipped.append({'path':str(path),'reason':'changed since it was pinned'});continue
        bodies.append(('recorded_%d'%i,dict(json.loads(raw),model=identifier)))
    return bodies,skipped
def check_folder(work,modules,profile):
    """The folder's manifest pins its entry modules, selector and helper; the helper is what the staged model module generates;
    the profile is one this folder's selector loads. Returns the tokenizer pins for the profile."""
    work=Path(work);manifest=json.loads((work/'builder/candidate-manifest.json').read_text());pins={row['path']:row['sha256'] for row in manifest.get('files',[])}
    helper=work/'builder/sdk_native.mjs';selector=modules.entry/SELECTOR
    require(helper.read_text()==modules.model.SDK_SCRIPT,'the staged helper is not what the staged model module generates')
    for path in [modules.entry/name for name in ENTRY_MODULES]+([selector] if selector.exists() else [])+[helper]:
        require(pins.get(str(path))==sha(path),'staged file does not match its pin in the folder manifest: '+str(path))
    require(profile in modules.model.PLAN_PROFILES,'%r is not a profile of this staged folder (it loads %s)'%(profile,', '.join(modules.model.PLAN_PROFILES)))
    if modules.model.MODEL_PLAN is not None:
        require(manifest.get('model_plan')==modules.model.MODEL_PLAN,'the folder manifest does not bind the plan its selector names');tokenizer=manifest['model_tokenizer_by_profile'][profile]
    else:tokenizer=manifest['model_tokenizer']
    return tokenizer
def unload_confirmed(reply,identifier):
    return type(reply) is dict and reply.get('unloaded')==identifier and not reply.get('alreadyAbsent') and type(reply.get('snapshot')) is dict and reply['snapshot'].get('llm')==[]
def qualify(work,out,profile,*,sdk_factory=None,recorded=None):
    """Load, admission only on every body, unload. sdk_factory(out, deadline) gives the helper (the staged SDK bridge by default,
    run from a copy of the staged helper in out/builder, so nothing is written into the staged folder)."""
    work,out=Path(work).absolute(),Path(out).absolute();modules=staged_modules(work)
    require(not out.is_relative_to(work),'the output folder lies inside the staged folder, which this tool never writes to')
    require(not out.exists(),'a fresh output folder is required: '+str(out))
    tokenizer=check_folder(work,modules,profile)
    identifier='mf-native-'+uuid.uuid4().hex;plan=modules.model.MODEL_PLAN;owned={'profile':profile} if plan is not None else {}
    bodies=canned_bodies(identifier);extra,skipped=recorded_bodies(identifier,recorded);bodies+=extra
    (out/'builder').mkdir(parents=True);shutil.copy2(work/'builder/sdk_native.mjs',out/'builder/sdk_native.mjs')
    deadline=time.monotonic()+WHOLE_S
    sdk=(sdk_factory or (lambda folder,end:modules.model.SDKBridge(folder,end)))(out,deadline)
    result={'work':str(work),'profile':profile,'plan':plan,'identifier':identifier,'helper_sha256':sha(work/'builder/sdk_native.mjs'),'generation_calls':0,
            'bodies':[],'skipped':skipped,'baseline':None,'load':None,'load_refusal':None,'cleanup':None,'cleanup_proven':True}
    loaded=None;load_asked=False;result['baseline_refusal']=None
    try:
        try:baseline=result['baseline']=sdk.call('baseline',BASELINE_S);embedding=(baseline.get('embedding') or {}).get('instanceReference')
        except (modules.gateway.NativeRefused,RuntimeError) as exc:result['baseline_refusal']=str(exc);return result   # nothing loaded, nothing to unload
        load_asked=True
        try:
            loaded=result['load']=sdk.call('load',LOAD_S,identifier=identifier,**owned);modules.run.check_load_readback(loaded,identifier,profile)
        except (modules.gateway.NativeRefused,RuntimeError) as exc:result['load_refusal']=str(exc)
        if result['load_refusal'] is None:
            admission=modules.model.TemplateAdmission(sdk,identifier,tokenizer,embedding_native_semantics=True,profile=profile)
            limits=modules.gateway.Limits(identifier)
            for name,body in bodies:
                try:
                    context=admission('chat',modules.gateway.validate_chat(body,limits),time.monotonic()+ADMISSION_S)
                    result['bodies'].append({'body':name,'admitted':True,'tokens':context['tokens'],'candidate_count':context['candidate_count']})
                except (modules.gateway.NativeRefused,RuntimeError) as exc:result['bodies'].append({'body':name,'admitted':False,'refusal':str(exc)})
    finally:
        if load_asked:
            reference=((loaded if type(loaded) is dict else {}).get('info') or {}).get('instanceReference')
            try:
                result['cleanup']=sdk.call('unload',UNLOAD_S,identifier=identifier,instanceReference=reference,embeddingReference=embedding,**owned)
                result['cleanup_proven']=unload_confirmed(result['cleanup'],identifier)
            except BaseException as exc:result['cleanup']={'failure':str(exc)};result['cleanup_proven']=False
        try:result['helper_stop']=sdk.group.stop(deadline+60) if hasattr(sdk,'group') else None
        except BaseException as exc:result['helper_stop']={'failure':str(exc)};result['cleanup_proven']=False
        (out/'qualify-result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
def report_lines(result):
    lines=['%s: admitted (%d tokens, %d candidates)'%(b['body'],b['tokens'],b['candidate_count']) if b['admitted'] else '%s: refused: %s'%(b['body'],b['refusal']) for b in result['bodies']]
    lines+=['%s: skipped: %s'%(s['path'],s['reason']) for s in result['skipped']]
    if result.get('baseline_refusal'):lines.append('baseline refused (nothing loaded): '+result['baseline_refusal'])
    if result['load_refusal']:lines.append('load refused: '+result['load_refusal'])
    lines.append(result['profile']+': cleanup proven' if result['cleanup_proven'] else result['profile']+': CLEANUP NOT PROVEN: '+json.dumps(result['cleanup'])[:400])
    return lines
def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__.split('\n')[0]);parser.add_argument('--work',type=Path,required=True);parser.add_argument('--profile',required=True)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--owner-approved-load',action='store_true',help='the owner said yes to loading this checkpoint in LM Studio')
    args=parser.parse_args(argv)
    try:
        require(args.owner_approved_load,'this loads %s in LM Studio; run it only with the owner\'s yes and pass --owner-approved-load'%args.profile)
        result=qualify(args.work,args.out,args.profile)
    except RuntimeError as refused:print('refused: '+str(refused).replace('\n',' '),file=sys.stderr);return 2
    for line in report_lines(result):print(line)
    return 0 if result['cleanup_proven'] and result['load_refusal'] is None and result['baseline_refusal'] is None else 1
if __name__=='__main__':raise SystemExit(main())
