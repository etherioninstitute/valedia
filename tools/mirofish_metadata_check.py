"""One owned SDK metadata preflight. Default preparation never loads a model."""
from __future__ import annotations
import argparse, hashlib, hmac, importlib, json, os, re, signal, stat, subprocess, sys, threading, time, uuid
from datetime import datetime, timezone
from collections import namedtuple
from types import MappingProxyType
from pathlib import Path
BASE=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space')
CANDIDATE=BASE/'project/work/sol-foreman-qwen/valedia-mirofish-fresh-20261106-caller-budget'
BUILD=BASE/'project/work/sol-foreman-qwen/valedia-resume-20261108/build'
CONTROLS=BASE/'project/work/sol-foreman-qwen/valedia-resume-20261108/recon/metadata-supported/controls.json'
CANDIDATE_SHA='b7da913d7925bae6f09ef75a987409fd83a60b504ac083aee4e2be283f508e63'
HELPERS={'mirofish_native_graph':'3c3401e3fe8a418ffc8d2079f98521e4f386e8f8f3d123ef6fa683975b7d95f4','mirofish_native_caller_policy':'1ac9addd7cca1c3d32dbd3f21f47cc0aece7c90fb19bb5621d88d68116cb5e07','mirofish_native_gateway':'2251da4eab35312c4abf8a6de2aae4272d753778d945f12cc33e346cec1e99a6','mirofish_native_startup':'45ad8faf47457f6d851fcfadc207cb24ee71d90c4602ab1723a3fe01782154fd','mirofish_native_run':'5fd72f92b52330c689f62ae35ee90c36ec4f0608d925c66f77d6b10548883650','mirofish_native_model':'6e116603d4b3dee43bbf226bce4fe0a2d393958a84f9e85504f5fa953366c677','mirofish_native_children':'bd9e380ecf52b7c8564badf96be2c6ab009ff0e7dbd2b98d52351c61e4c0a7e4'}
def require(value,reason):
    if not value: raise RuntimeError(reason)
def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
def encoded(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def load(path): return json.loads(Path(path).read_text())
SAVE_LOCK=threading.RLock()
def save(path,value):
    with SAVE_LOCK:
        path=Path(path);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.new');tmp.write_bytes(encoded(value)+b'\n');os.replace(tmp,path)
def storage_guard():
    sys.path.insert(0,str(BASE/'valedia-evaluation'))
    from storage import guard
    guard()
def owned_path(path):
    path=Path(path).absolute()
    require(path.is_relative_to(BUILD) and path!=BUILD,'output must be below assigned build root')
    require(all(not x.is_symlink() for x in [path,*path.parents]),'symlinked output path refused')
    return path
def import_helpers():
    source=CANDIDATE/'builder/review-source'
    for name,digest in HELPERS.items():
        require(sha(source/(name+'.py'))==digest,'reviewed helper source changed: '+name)
        require(name not in sys.modules or Path(sys.modules[name].__file__).resolve()==source/(name+'.py'),'foreign helper already imported')
    sys.path.insert(0,str(source))
    result={name:importlib.import_module(name) for name in HELPERS}
    for name,m in result.items(): require(Path(m.__file__).resolve()==source/(name+'.py') and sha(m.__file__)==HELPERS[name],'helper import origin drift')
    return result

def freeze(value):
    if isinstance(value,dict):return MappingProxyType({k:freeze(v) for k,v in value.items()})
    if isinstance(value,list):return tuple(freeze(v) for v in value)
    return value

def thaw(value):
    if isinstance(value,MappingProxyType):return {k:thaw(v) for k,v in value.items()}
    if isinstance(value,tuple):return [thaw(v) for v in value]
    return value

CapturedPlan=namedtuple('CapturedPlan','controls_path controls_raw case_raw controls bodies')

def bounded_snapshot(path,limit):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno());require(stat.S_ISREG(info.st_mode) and 0<info.st_size<=limit,'bounded regular JSON file required')
        raw=stream.read(limit+1);require(0<len(raw)<=limit,'JSON snapshot bytes exceed bound')
    return raw

def capture_plan(path):
    raw=bounded_snapshot(path,262144);controls=json.loads(raw);case_raw=[];bodies=[]
    for case in controls['cases']:
        captured=bounded_snapshot(case['case_path'],262144)
        require(hashlib.sha256(captured).hexdigest()==case['case_file_sha256'],'case bytes changed')
        case_raw.append(captured);bodies.append(json.loads(captured))
        require(sha(case['source'].split(':')[0])==case['source_file_sha256'],'case source proof changed')
    check_controls(controls,bodies)
    for pin in controls['source_contract_pins']:require(sha(pin['path'])==pin['sha256'],'actual caller source contract changed')
    return CapturedPlan(str(Path(path).resolve()),raw,tuple(case_raw),freeze(controls),tuple(freeze(body) for body in bodies))

def plan_objects(plan):
    require(isinstance(plan,CapturedPlan),'captured immutable metadata plan required')
    controls=thaw(plan.controls);bodies=[thaw(body) for body in plan.bodies]
    require(len(plan.case_raw)==3 and all(hashlib.sha256(raw).hexdigest()==case['case_file_sha256'] for raw,case in zip(plan.case_raw,controls['cases'])),'captured plan case bytes changed')
    check_controls(controls,bodies)
    return controls,bodies

def check_controls(controls,bodies):
    require(controls['candidate_manifest_sha256']==CANDIDATE_SHA==sha(CANDIDATE/'builder/candidate-manifest.json'),'base candidate changed')
    alias=controls['model']['identifier'];require(re.fullmatch(r'mf-native-[0-9a-f]{32}',alias),'owned alias malformed')
    require(len(controls['cases'])==3,'exactly three metadata cases required')
    require(len(bodies)==3 and all(body.get('messages') and body['messages'][-1]['role'] in ('user','tool') for body in bodies),'assistant-final continuation unsupported; prediction requests must end user/tool')
    require([c['name'] for c in controls['cases']]==['hash_matched_native_strict_tools','source_based_report_history_user_final','source_based_tool_cycle_tool_final'],'exact supported source-contract case names required')
    require(controls['cases'][0]['provenance_kind']=='captured_actual_native_payload' and all(c['provenance_kind']=='source_based_representative_not_historical_capture' for c in controls['cases'][1:]),'captured versus source-based representative provenance required')
    require(len(bodies)==3,'exact captured case count required')
    for c,body in zip(controls['cases'],bodies):
        require(body['model']==alias and body['reasoning_effort']=='none','case alias/no-thinking changed')
        require(hashlib.sha256(encoded(body)).hexdigest()==c['remapped_canonical_sha256'],'case canonical hash changed')
        original=dict(body,model=c['original_alias'])
        require(hashlib.sha256(encoded(original)).hexdigest()==c['validated_original_canonical_sha256'],'case original hash changed')
    require(controls['cases'][0]['validated_original_canonical_sha256']=='2ad608cbbe8270e19245327158a8b9ead0e9e3866da82b22df28a05667db77da','native historical payload mismatch')
    require(all(t['function']['strict'] is True for t in bodies[0]['tools']),'native strict tools lost')
    report=bodies[1]['messages']
    require([m['role'] for m in report]==['system','user','assistant','user'] and report[-2]['content']=='(Response empty)' and 'tool_calls' not in report[-2] and report[-1]['content']=='Please continue generating content.','source-based report retry history changed')
    cycle=bodies[2]['messages'];require([m['role'] for m in cycle]==['system','user','assistant','tool'] and cycle[-1]['tool_call_id']==cycle[-2]['tool_calls'][0]['id'],'source-based tool-final matching cycle changed')
    require(type(cycle[-2].get('content')) is str and cycle[-2]['content']=='','CAMEL tool request assistant content must be exact empty string; null unsupported')
    require(isinstance(bodies[2]['messages'][2]['tool_calls'][0]['function']['arguments'],str),'JSON-string arguments lost')
    return bodies

def bindings(work,plan):
    controls,bodies=plan_objects(plan)
    pins=load(CANDIDATE/'review98/before-hashes.json')
    for group in ('selected_candidate_files','original318'):
        require(len(pins[group])==(713 if group=='selected_candidate_files' else 318),'pin scope changed')
        for row in pins[group]: require(sha(row['path'])==row['expected'],'frozen source changed: '+row['path'])
    for p,d in pins['raw_manifests'].items(): require(sha(p)==d,'raw manifest changed')
    require(sha(controls['sdk_helper']['path'])==controls['sdk_helper']['sha256'],'SDK helper changed')
    import_helpers()  # Entire selected closure is verified before the first helper import.
    return {'action':'one-owned-metadata-load','driver_sha256':sha(__file__),'tests_sha256':sha(Path(__file__).with_name('test_mirofish_metadata_check.py')),'support_fixture_sha256':sha(BUILD/'support-fixtures/historical-sdk-shapes.json'),'controls_path':plan.controls_path,'controls_sha256':hashlib.sha256(plan.controls_raw).hexdigest(),'candidate_sha256':CANDIDATE_SHA,'sdk_helper_sha256':controls['sdk_helper']['sha256'],'work':str(work),'receipt':str(work/'metadata-result.json'),'model':controls['model'],'cases':[{'name':c['name'],'sha256':c['case_file_sha256']} for c in controls['cases']],'helper_source_hashes':HELPERS,'limits':{'loads':1,'metadata_cases':3,'generations':0,'total_seconds':600,'load_seconds':180,'case_seconds':45,'cleanup_seconds':90,'pre_free_percent':65,'run_free_percent':30,'owned_rss_bytes':40*1024**3}}

def approval_snapshot(path):return bounded_snapshot(path,65536)

def verify_approval(path,expected_sha,binding,now=None):
    raw=approval_snapshot(path)
    require(re.fullmatch('[0-9a-f]{64}',expected_sha or '') and hashlib.sha256(raw).hexdigest()==expected_sha,'explicit exact root approval SHA required')
    value=json.loads(raw);require(value['bindings']==binding,'approval bindings differ')
    require(value['action']=='one-owned-metadata-load' and value['approved_by']=='root' and re.fullmatch('[0-9a-f]{32}',value['nonce']),'root approval schema')
    now=datetime.now(timezone.utc) if now is None else now
    expires=datetime.fromisoformat(value['expires_at_utc']);require(expires.tzinfo is not None and 0<(expires-now).total_seconds()<=3600,'approval expired or excessive')
    return value

def claim_once(work,approval_sha):
    fd=os.open(work/'one-metadata-load.claim',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:f.write(encoded({'approval_sha256':approval_sha,'claimed_utc':datetime.now(timezone.utc).isoformat()}));f.flush();os.fsync(f.fileno())

def reconcile(sdk,factory,identifier,loaded,baseline,deadline,receipt):
    """Reviewed native cleanup, strengthened to require actual reconciled instance proof."""
    uncertain=sdk.poisoned or loaded is None or sdk.process.poll() is not None
    if sdk.poisoned or sdk.process.poll() is not None:
        receipt['cleanup_unknown']='prior SDK operation uncertain; late server work remains unknown even if lookup absent'
        receipt['poisoned_process_cleanup']=sdk.group.stop(deadline);sdk=factory(deadline)
    snapshot=sdk.call('snapshot',deadline=deadline)
    receipt['cleanup_snapshot']=snapshot
    existing=[x for x in snapshot['llm'] if x['identifier']==identifier]
    before=baseline['embedding'];embedding=[x for x in snapshot['embedding'] if x['identifier']==before['identifier']]
    require(len(embedding)==1 and embedding[0]['instanceReference']==before['instanceReference'] and embedding[0]['contextLength']==2048,'embedding preservation cannot be proven')
    if not existing:
        receipt['cleanup_state']='UNKNOWN_LATE_LOAD' if uncertain else 'ABSENT_CONFIRMED';return sdk
    require(len(existing)==1,'owned alias ambiguous')
    info=existing[0];model=receipt['model']
    require(info['modelKey']==model['modelKey'] and info['path']==model['path'] and info['format']=='gguf' and info['sizeBytes']==model['size_bytes'] and info['deviceIdentifier'] is None and isinstance(info['instanceReference'],str),'owned model reconciliation failed')
    reference=(loaded or {}).get('info',{}).get('instanceReference')
    require(reference is None or reference==info['instanceReference'],'owned instance changed')
    receipt['unload']=sdk.call('unload',deadline=deadline,identifier=identifier,instanceReference=info['instanceReference'],embeddingReference=before['instanceReference'])
    after=sdk.call('snapshot',deadline=deadline);receipt['after_snapshot']=after
    require(not any(x['identifier']==identifier for x in after['llm']),'owned model remains')
    foreign_before={x['identifier']:x['instanceReference'] for x in snapshot['llm'] if x['identifier']!=identifier}
    foreign_after={x['identifier']:x['instanceReference'] for x in after['llm'] if x['identifier']!=identifier}
    require(all(foreign_after.get(k)==v for k,v in foreign_before.items()),'foreign model preservation not proven')
    require(any(x['identifier']==before['identifier'] and x['instanceReference']==before['instanceReference'] and x['contextLength']==2048 for x in after['embedding']),'embedding changed after cleanup')
    receipt['cleanup_state']='OWNED_UNLOADED_WITH_PRIOR_UNCERTAINTY' if uncertain else 'OWNED_UNLOADED';return sdk

class ResourceMonitor:
    def __init__(self,helpers,work,deadline,receipt,bridge):
        self.s=helpers['mirofish_native_startup'];self.n=helpers['mirofish_native_run'];self.work=work;self.deadline=deadline;self.receipt=receipt;self.bridge=bridge;self.stop=threading.Event();self.aborted=threading.Event();self.errors=[];self.sequence=0
        self.root=work/'resource';self.root.mkdir();self.history=self.s.ResourceHistory(self.root)
        self.nonce=uuid.uuid4().hex;self.key=uuid.uuid4().hex+uuid.uuid4().hex
        self.host={'pid':os.getpid(),'started':subprocess.run(['/bin/ps','-p',str(os.getpid()),'-o','lstart='],capture_output=True,text=True,check=True,timeout=2).stdout.strip()}
        rows=self.s.process_rows();listeners=subprocess.run(['/usr/sbin/lsof','-nP','-iTCP@127.0.0.1:62298','-sTCP:LISTEN','-Fp'],capture_output=True,text=True,check=True,timeout=2)
        pids={int(x[1:]) for x in listeners.stdout.splitlines() if x.startswith('p')};require(pids,'SDK server listener absent');self.scope=self.s.OwnedResourceScope(rows,pids)
        self.old={k:os.environ.get(k) for k in ['MIROFISH_RESOURCE_CAPSULE','MIROFISH_RESOURCE_KEY','MIROFISH_RESOURCE_NONCE','MIROFISH_RESOURCE_HOST']}
        os.environ.update(MIROFISH_RESOURCE_CAPSULE=str(self.root/'sample.json'),MIROFISH_RESOURCE_KEY=self.key,MIROFISH_RESOURCE_NONCE=self.nonce,MIROFISH_RESOURCE_HOST=json.dumps(self.host))
    def sample(self):
        self.sequence+=1;sample=self.s.sample_host(self.nonce,self.host,self.sequence,self.deadline,self.scope);self.history.publish({'sample':sample,'mac':hmac.new(self.key.encode(),encoded(sample),hashlib.sha256).hexdigest()})
    def start(self):
        self.sample();self.n.resource_sample(True)
        def loop():
            while not self.stop.wait(.4):
                try:
                    self.sample()
                    with SAVE_LOCK:
                        healthy=self.n.monitor_resource_once(self.receipt,self.work/'metadata-result.json',self.aborted,None,self.errors)
                    if healthy:continue
                except BaseException as e:self.errors.append(type(e).__name__+':'+str(e));self.aborted.set()
                try:
                    bridge=self.bridge()
                    if bridge is not None:
                        bridge.poisoned=True
                        self.receipt['guard_sdk_stop']=bridge.group.stop(min(self.deadline,time.monotonic()+10))
                except BaseException as e:self.errors.append('guard stop:'+str(e))
                return
        self.thread=threading.Thread(target=loop,name='metadata-reviewed-resource-monitor',daemon=True);self.thread.start()
    def disarm(self):
        self.stop.set()
        if hasattr(self,'thread'):self.thread.join(15)
        require(not hasattr(self,'thread') or not self.thread.is_alive(),'active guard did not disarm; cleanup bridge must not start')
        self.bridge=lambda:None
    def close(self):
        self.stop.set()
        if hasattr(self,'thread'):self.thread.join(15)
        if hasattr(self,'thread') and self.thread.is_alive():self.errors.append('resource monitor failed to terminate')
        self.history.close()
        for k,v in self.old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v

def run_live(work,plan,binding,approval_sha):
    controls,bodies=plan_objects(plan)  # Revalidate captured branch data before any load.
    require(binding['controls_sha256']==hashlib.sha256(plan.controls_raw).hexdigest() and binding['model']==controls['model'],'live plan differs from approved binding')
    helpers=import_helpers();m=helpers['mirofish_native_model'];n=helpers['mirofish_native_run'];s=helpers['mirofish_native_startup']
    model=controls['model'];model=dict(model,path=model['file'].split('/models/',1)[1]);identifier=model['identifier']
    deadline=time.monotonic()+600;active=deadline-90;receipt={'mode':'ACTUAL_SDK_METADATA','model':model,'bindings':binding,'approval_sha256':approval_sha,'observations':[],'status':'STARTED','generation_calls':0};save(work/'metadata-result.json',receipt)
    require(Path(model['file']).stat().st_size==model['size_bytes'] and sha(model['file'])==model['sha256'],'checkpoint file size/hash mismatch')
    sdk=monitor=None;loaded=baseline=None;intent=False
    old_handlers={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGINT)}
    def interrupted(sig,frame):raise InterruptedError('metadata interrupted by signal '+str(sig))
    for sig in old_handlers:signal.signal(sig,interrupted)
    bridges=[]
    def factory(end):
        bridge=m.SDKBridge(work,end);bridges.append(bridge);return bridge
    try:
        monitor=ResourceMonitor(helpers,work,deadline,receipt,lambda:sdk);monitor.start()
        sdk=factory(active);baseline=sdk.call('baseline',deadline=min(active,time.monotonic()+20));receipt['baseline']=baseline;save(work/'metadata-result.json',receipt)
        require(baseline['embedding']['instanceReference']==controls['preserve_embedding']['instanceReference'] and baseline['embeddingContext']==2048,'existing embedding baseline changed')
        require(not monitor.aborted.is_set(),'resource monitor aborted before load');n.resource_sample(True)
        intent=True;loaded=sdk.call('load',deadline=min(active,time.monotonic()+180),identifier=identifier);receipt['loaded']=loaded;save(work/'metadata-result.json',receipt)
        tokenizer_manifest={name:sha(m.TOKENIZER_ROOT/name) for name in m.TOKENIZER_FILES};admission=m.TemplateAdmission(sdk,identifier,tokenizer_manifest)
        for case,body in zip(controls['cases'],bodies):
            require(not monitor.aborted.is_set(),'resource monitor aborted');n.resource_sample(False)
            observation=sdk.call('chat_context',deadline=min(active,time.monotonic()+45),identifier=identifier,body=body)
            rawpath=work/(case['name']+'-sdk.json');save(rawpath,observation);receipt['observations'].append({'case':case['name'],'actual_sdk':str(rawpath),'actual_sdk_sha256':sha(rawpath)});save(work/'metadata-result.json',receipt)
            # Compare the actual reply without making a second SDK call.
            class Captured:
                def call(self,action,**kwargs):require(action=='chat_context','closed metadata comparison');return observation
            admission.sdk=Captured();result=admission('chat',body,min(active,time.monotonic()+45));admission.sdk=sdk
            receipt['observations'][-1]['frozen_comparison']=result;save(work/'metadata-result.json',receipt)
        require(not monitor.aborted.is_set() and not monitor.errors,'resource guard failed');receipt['status']='METADATA_QUALIFIED_PENDING_CLEANUP'
    except BaseException as e:receipt['status']='FAILED';receipt['failure']=type(e).__name__+':'+str(e)
    finally:
        if monitor is not None:
            try:monitor.disarm()
            except BaseException as e:receipt['guard_disarm_error']=str(e);receipt['status']='FAILED_GUARD_DISARM';intent=False;receipt['cleanup_state']='UNKNOWN_UNPROVEN'
        if intent and sdk is not None and baseline is not None:
            try:sdk=reconcile(sdk,factory,identifier,loaded,baseline,deadline,receipt)
            except BaseException as e:receipt['cleanup_state']='UNKNOWN_UNPROVEN';receipt['cleanup_failure']=type(e).__name__+':'+str(e)
        receipt['sdk_process_cleanup']=[]
        for owned_bridge in bridges:
            try:receipt['sdk_process_cleanup'].append({'bridge_index':bridges.index(owned_bridge),'result':owned_bridge.group.stop(deadline)})
            except BaseException as e:receipt['sdk_process_cleanup'].append({'bridge_index':bridges.index(owned_bridge),'error':str(e)});receipt['cleanup_state']='UNKNOWN_UNPROVEN'
        if monitor is not None:
            monitor.close();receipt['resource_errors']=monitor.errors
            if monitor.errors or monitor.aborted.is_set():receipt['status']='FAILED_RESOURCE_GUARD'
        if receipt['status']=='METADATA_QUALIFIED_PENDING_CLEANUP':receipt['status']='METADATA_QUALIFIED' if receipt.get('cleanup_state')=='OWNED_UNLOADED' else 'FAILED_CLEANUP_UNPROVEN'
        save(work/'metadata-result.json',receipt)
        for sig,handler in old_handlers.items():signal.signal(sig,handler)
    return receipt

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--work',type=Path,default=BUILD/'metadata-supported-once');parser.add_argument('--controls',type=Path,default=CONTROLS);parser.add_argument('--live',action='store_true');parser.add_argument('--approval',type=Path);parser.add_argument('--approval-sha256');args=parser.parse_args(argv)
    storage_guard();os.environ['TMPDIR']=str(BASE/'project/tmp');work=owned_path(args.work);work.mkdir(parents=True,exist_ok=True);(work/'builder').mkdir(exist_ok=True)
    plan=capture_plan(args.controls);binding=bindings(work,plan);controls,bodies=plan_objects(plan);target=work/'builder/sdk_native.mjs'
    if target.exists():require(sha(target)==binding['sdk_helper_sha256'],'staged helper drift')
    else:target.write_bytes(Path(controls['sdk_helper']['path']).read_bytes())
    save(work/'prepared-bindings.json',binding)
    if not args.live:
        save(work/'root-approval-template.json',{'action':'one-owned-metadata-load','approved_by':'root','nonce':'ROOT_GENERATES_32_HEX','expires_at_utc':'ROOT_SETS_CURRENT_EXPIRY_MAX_1_HOUR','bindings':binding});print(json.dumps({'status':'PREPARED_NO_LOAD','bindings':str(work/'prepared-bindings.json'),'root_approval_template':str(work/'root-approval-template.json')}));return 0
    require(args.approval is not None,'root approval required');verify_approval(args.approval,args.approval_sha256,binding);claim_once(work,args.approval_sha256)
    result=run_live(work,plan,binding,args.approval_sha256);print(json.dumps({'status':result['status'],'receipt':str(work/'metadata-result.json')}));return 0 if result['status']=='METADATA_QUALIFIED' else 1
if __name__=='__main__':raise SystemExit(main())
