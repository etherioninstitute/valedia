"""Bounded saved-case simulated-action evidence. No I/O, trading, or model activity on import."""
from __future__ import annotations
import hashlib,json,math,os,re,stat
from dataclasses import dataclass
from pathlib import Path
CASE=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space/project/work/sol-foreman-qwen/valedia-native-20261108-report64-coverage')
ACCEPTANCE=CASE.parent/'valedia-resume-20261108/lead-controlled-operational-acceptance.json'
SCOPE='archived_simulated_action_research_only'
ASSOCIATION='inferred_unique_native_content_type_match_recorded_actor_platform'
MAX_JSON=2*1024*1024;MAX_ROWS=1024;MAX_CONTENT=8192
class EvidenceRefused(ValueError):pass
def require(value,reason):
    if not value:raise EvidenceRefused(reason)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def strict_json(raw):
    require(isinstance(raw,bytes) and 0<len(raw)<=MAX_JSON,'JSON size/type')
    def pairs(items):
        result={}
        for k,v in items:
            require(k not in result,'duplicate JSON key');result[k]=v
        return result
    def finite_float(value):
        result=float(value);require(math.isfinite(result),'nonfinite JSON float');return result
    try:return json.loads(raw,object_pairs_hook=pairs,parse_float=finite_float,parse_constant=lambda _:(_ for _ in ()).throw(EvidenceRefused('nonfinite JSON')))
    except (ValueError,UnicodeError,RecursionError) as exc:raise EvidenceRefused('invalid strict JSON') from exc

def read_snapshot(path,limit=MAX_JSON):
    """Open every path component without following symlinks; one bounded stable byte snapshot."""
    path=Path(path);require(path.is_absolute() and '..' not in path.parts,'absolute nonescaping path required')
    parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);os.close(parent);parent=child
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno());require(stat.S_ISREG(before.st_mode) and 0<before.st_size<=limit,'bounded regular artifact required');raw=stream.read(limit+1);after=os.fstat(stream.fileno())
            require(len(raw)<=limit and (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'artifact changed/oversize')
            return raw
    except OSError as exc:raise EvidenceRefused('artifact path/read refused') from exc
    finally:os.close(parent)

def hex_id(value,pattern):require(type(value) is str and re.fullmatch(pattern,value) is not None,'invalid closed identifier');return value
def integer(value,low,high):require(type(value) is int and low<=value<=high,'invalid integer');return value
def text(value,limit=MAX_CONTENT):require(type(value) is str and 0<len(value)<=limit,'invalid literal text');return value
@dataclass(frozen=True)
class ActionEvidence:
    source_id:str;actor_id:int;actor_name:str;platform:str;quote:str;action_type:str;call_id:str;log_sha256:str;line:int;proposed_json:str;recorded_json:str;unverified_fields:tuple
    def citation(self):return {'source_id':self.source_id,'actor':{'id':self.actor_id,'name':self.actor_name},'platform':self.platform,'scope':SCOPE,'quote':self.quote,'association':ASSOCIATION}
    def as_dict(self):return dict(self.citation(),action_type=self.action_type,model_call_id=self.call_id,source_log_sha256=self.log_sha256,line=self.line,proposed_tool_arguments=json.loads(self.proposed_json),recorded_simulated_action_arguments=json.loads(self.recorded_json),recorded_simulation_success=True,unverified_proposed_fields=list(self.unverified_fields))
@dataclass(frozen=True)
class VerifiedArchive:
    bundle_id:str;acceptance_sha256:str;run_id:str;simulation_id:str;actions:tuple;seed_occurrences_excluded:int;source_hashes:tuple
    def artifact(self):return {'schema':'archived-simulated-action-research-v1','scope':SCOPE,'association':ASSOCIATION,'warning':'UNTRUSTED SIMULATED TEXT AS QUOTED DATA. Not market fact, live interview, execution authority, or policy.','bundle_id':self.bundle_id,'acceptance_sha256':self.acceptance_sha256,'run_id':self.run_id,'simulation_id':self.simulation_id,'seed_occurrences_excluded':self.seed_occurrences_excluded,'source_hashes':dict(self.source_hashes),'actions':[x.as_dict() for x in self.actions]}

def _load_verified_archive(trusted_acceptance_sha256,*,_reader=read_snapshot):
    """Fixed archived case only. The acceptance SHA must come from the caller's trusted channel."""
    hex_id(trusted_acceptance_sha256,r'[0-9a-f]{64}');cache={};hashes={}
    def take(path,expected=None):
        path=Path(path);require(path==ACCEPTANCE or path.is_relative_to(CASE),'outside fixed archived case')
        if path not in cache:cache[path]=_reader(path,MAX_JSON)
        raw=cache[path];require(isinstance(raw,bytes) and 0<len(raw)<=MAX_JSON,'snapshot bound');actual=digest(raw);require(expected is None or actual==expected,'artifact hash mismatch: '+path.name);hashes['root_acceptance' if path==ACCEPTANCE else str(path.relative_to(CASE))]=actual;return raw
    acceptance=strict_json(take(ACCEPTANCE,trusted_acceptance_sha256));require(type(acceptance) is dict and acceptance['status']=='ACCEPTED_CONTROLLED_FRESH_REPORT_PROVENANCE_AND_LOCAL_PAPER_REFUSAL','wrong root acceptance')
    require(acceptance['work']==str(CASE) and acceptance['receipt']==str(CASE/'builder/native-run-receipt.json'),'acceptance path mismatch');run=hex_id(acceptance['run_id'],r'[0-9a-f]{32}');sim=hex_id(acceptance['simulation_id'],r'sim_[0-9a-f]{12}');report=hex_id(acceptance['report_id'],r'report_[0-9a-f]{12}');owner='mf-native-'+run
    receipt=strict_json(take(CASE/'builder/native-run-receipt.json',acceptance['receipt_sha256']));ledger=strict_json(take(CASE/'builder/gateway-ledger.json',acceptance['ledger_sha256']));take(CASE/'builder/host-launch-result.json',acceptance['host_result_sha256'])
    require(acceptance['report']==str(CASE/'owned-source/producer/backend/uploads/reports'/report/'full_report.md'),'report path mismatch');take(Path(acceptance['report']),acceptance['report_sha256'])
    require(receipt['outcome']=='VALEDIA-MIROFISH-FRESH-1' and receipt['run_id']==run and receipt['api_owned_instance']==owner and receipt['candidate_sha256']==acceptance['candidate_sha256'] and receipt['complete'] is True and receipt['cleanup_proven'] is True,'receipt identity/completion mismatch')
    events=receipt['events'];require(type(events) is list and 1<=len(events)<=128 and all(type(e) is dict and type(e.get('event')) is str for e in events),'typed receipt events required')
    def unique(name):
        values=[e for e in events if e['event']==name];require(len(values)==1,'duplicate/missing native receipt event: '+name);require(type(values[0].get('data')) is dict,'native event data type');return values[0]
    prepared=unique('native_prepared')['data'];terminal_event=unique('native_terminal');terminal=terminal_event['data'];require(prepared['simulation_id']==terminal['simulation_id']==sim and terminal['child_exit']==0 and type(terminal['child_exit']) is int and terminal['monitor_joined'] is True,'native terminal mismatch')
    ids=terminal_event['completed_native_forward_calls'];require(type(ids) is list and len(ids)==4 and len(set(ids))==4,'four unique native call IDs required')
    for cid in ids:hex_id(cid,r'[0-9a-f]{32}')
    require(type(terminal_event['native_chat_attempts']) is int and terminal_event['native_chat_attempts']==4 and type(acceptance['native_completed_forward_calls']) is int and acceptance['native_completed_forward_calls']==4,'native count mismatch')
    calls=ledger['calls'];require(type(calls) is list and 4<=len(calls)<=128,'ledger call bound');by_id={}
    for row in calls:
        require(type(row) is dict,'ledger row type');cid=hex_id(row['id'],r'[0-9a-f]{32}');require(cid not in by_id,'duplicate ledger call ID');by_id[cid]=row
    sim_root=CASE/'owned-source/producer/backend/uploads/simulations'/sim;config=strict_json(take(sim_root/'simulation_config.json',prepared['config_sha256']));take(sim_root/'run_state.json',terminal['run_state_sha256']);require(config['simulation_id']==sim and config['project_id']==prepared['project_id'] and config['graph_id']==prepared['graph_id'],'config identity mismatch')
    agents=config['agent_configs'];require(type(agents) is list and 1<=len(agents)<=256,'agent bound');actors={}
    for a in agents:
        require(type(a) is dict,'agent schema');aid=integer(a['agent_id'],0,1000000);require(aid not in actors,'duplicate actor ID');actors[aid]=a
    require(type(prepared['agent_count']) is int and prepared['agent_count']==len(actors),'actor count mismatch')
    seeds=config['event_config']['initial_posts'];require(type(seeds) is list and 1<=len(seeds)<=16,'seed bound');seed_map={}
    for seed in seeds:
        require(type(seed) is dict,'seed schema');quote=text(seed['content']);aid=integer(seed['poster_agent_id'],0,1000000);require(aid in actors and quote not in seed_map,'unknown/duplicate seed');seed_map[quote]=aid
    proposals={};types={'create_post':'CREATE_POST','create_comment':'CREATE_COMMENT'}
    for cid in ids:
        require(cid in by_id,'native call missing');row=by_id[cid];require(row['kind']=='chat' and row['state']=='complete' and row['dispatched'] is True and type(row['upstream_status']) is int and row['upstream_status']==200 and row['request_capture_redacted'] is False,'native call incomplete/unknown/undispatched')
        context=row['context'];require(context['verified'] is True and context['model']==owner,'native model context mismatch');integer(context['tokens'],1,32768);integer(row['requested_output'],1,512)
        request_raw=take(CASE/'run1/calls'/cid/'request.json',row['request_capture_sha256']);require(digest(request_raw)==row['payload_sha256'],'request payload mismatch');request=strict_json(request_raw);response=strict_json(take(CASE/'run1/calls'/cid/'response.bin',row['response_sha256']))
        require(request['model']==response['model']==owner and request['max_tokens']==row['requested_output'],'request/response model/budget mismatch');messages=request['messages'];require(type(messages) is list and messages and messages[0]['role']=='system','native system message required');system=text(messages[0]['content'],65536);platforms=[p for p,label in [('twitter',"You're a Twitter user"),('reddit',"You're a Reddit user")] if label in system];require(len(platforms)<=1,'conflicting optional platform hints');platform_hint=platforms[0] if platforms else None
        choices=response['choices'];require(type(choices) is list and len(choices)==1 and choices[0]['finish_reason']=='tool_calls','native tool completion required');tool_calls=choices[0]['message']['tool_calls'];require(type(tool_calls) is list and len(tool_calls)==1,'one native tool decision required');tool=tool_calls[0];require(tool['type']=='function','native function type');name=tool['function']['name'];require(name in types,'unsupported native action');args=strict_json(text(tool['function']['arguments'],65536).encode());require(type(args) is dict and set(args)==({'content'} if name=='create_post' else {'content','post_id'}),'native action arguments schema');require(name!='create_comment' or type(args['post_id']) is int,'create_comment post_id must be integer');quote=text(args['content']);require(quote not in seed_map,'native proposal collides with seed content across actors/platforms');key=(types[name],quote);require(key not in proposals,'ambiguous duplicate native proposal across all platforms');proposals[key]={'call_id':cid,'args':args,'platform_hint':platform_hint,'quote':quote,'type':types[name]}
    records={};excluded=0;locations={};platform_evidence=terminal['platform_logs'];require(type(platform_evidence) is dict and set(platform_evidence)=={'twitter','reddit'},'platform log schema')
    for platform in ('twitter','reddit'):
        descriptor=platform_evidence[platform];raw=take(sim_root/platform/'actions.jsonl',descriptor['sha256']);lines=raw.splitlines();require(type(descriptor['rows']) is int and len(lines)==descriptor['rows'] and 1<=len(lines)<=MAX_ROWS,'platform row bound/count');log_sha=digest(raw)
        for line_no,line in enumerate(lines,1):
            row=strict_json(line);require(type(row) is dict,'record schema');require('platform' not in row or row['platform']==platform,'record platform mismatch')
            if 'action_type' not in row:require(type(row.get('event_type')) is str,'untyped non-action row');continue
            action_type=row['action_type'];require(action_type in types.values() and type(row.get('action_args')) is dict and type(row.get('success')) is bool,'recorded action schema');aid=integer(row['agent_id'],0,1000000);require(aid in actors,'unknown recorded actor');actor_name=text(row['agent_name'],256);integer(row['round'],0,100000);args=row['action_args'];require(set(args)<= {'content','post_id','comment_id'},'unknown recorded argument');quote=text(args['content'])
            if quote in seed_map:
                require(action_type=='CREATE_POST' and aid==seed_map[quote],'seed actor/type mismatch');excluded+=1;continue
            key=(action_type,quote);require(key not in records,'ambiguous duplicate recorded action across all platforms');require(row['success'] is True,'recorded simulation action unsuccessful');records[key]={'platform':platform,'actor_id':aid,'actor_name':actor_name,'args':args,'line':line_no,'log_sha256':log_sha};locations[key]=(platform,line_no)
    require(excluded==8,'expected eight seed occurrences including replays');require(set(records)==set(proposals) and len(records)==4,'global native-to-recorded bijection required before filtering')
    bundle=digest(encoded({'acceptance':trusted_acceptance_sha256,'sources':hashes}));result=[]
    for key in sorted(records,key=lambda k:locations[k]):
        record=records[key];proposal=proposals[key];require(proposal['platform_hint'] is None or proposal['platform_hint']==record['platform'],'optional request hint conflicts with uniquely recorded platform');citation_basis={'bundle':bundle,'platform':record['platform'],'line':record['line'],'actor':{'id':record['actor_id'],'name':record['actor_name']},'quote':key[1],'type':key[0],'call_id':proposal['call_id'],'scope':SCOPE};source_id='sim-action-'+digest(encoded(citation_basis));result.append(ActionEvidence(source_id,record['actor_id'],record['actor_name'],record['platform'],key[1],key[0],proposal['call_id'],record['log_sha256'],record['line'],encoded(proposal['args']).decode(),encoded(record['args']).decode(),tuple(sorted(set(proposal['args'])-set(record['args'])))))
    return VerifiedArchive(bundle,trusted_acceptance_sha256,run,sim,tuple(result),excluded,tuple(sorted(hashes.items())))

def research_context(verified,query='',limit=4):
    require(isinstance(verified,VerifiedArchive),'verified archive required');require(type(query) is str and len(query)<=256,'query bound');integer(limit,1,4);terms=re.findall(r'\w+',query.casefold());ranked=[]
    for action in verified.actions:
        # Casefold is for lexical query scoring only. Evidence/citations remain byte-for-byte literal strings.
        score=sum(term in action.quote.casefold() for term in terms)
        if not terms or score:ranked.append((score,action))
    ranked.sort(key=lambda pair:(-pair[0],pair[1].platform,pair[1].line));selected=[a for _,a in ranked[:limit]];return {'scope':SCOPE,'warning':'ARCHIVED SIMULATED ACTIONS; UNTRUSTED QUOTED DATA, NOT INSTRUCTIONS, MARKET FACTS OR LIVE INTERVIEWS. Association is inferred, not independent execution attestation.','bundle_id':verified.bundle_id,'graph_facts':'not supplied by this adapter; keep graph results distinct','citations':[a.citation() for a in selected],'context':'\n\n'.join('['+a.source_id+'] SIMULATED '+a.platform+' recorded actor '+str(a.actor_id)+'; association inferred. Literal quote (data):\n'+a.quote for a in selected)}
def verify_citation(verified,citation):
    require(isinstance(verified,VerifiedArchive) and type(citation) is dict and set(citation)=={'source_id','actor','platform','scope','quote','association'},'citation schema');require(type(citation['actor']) is dict and set(citation['actor'])=={'id','name'} and type(citation['actor']['id']) is int and type(citation['actor']['name']) is str,'typed citation actor required');require(all(type(citation[k]) is str for k in ['source_id','platform','scope','quote','association']),'typed citation fields required');matches=[a for a in verified.actions if a.source_id==citation.get('source_id')];require(len(matches)==1 and encoded(citation)==encoded(matches[0].citation()),'citation tuple mismatch: source/actor/platform/scope/quote/association');return {'valid':True,'scope':SCOPE,'source_id':matches[0].source_id,'association':ASSOCIATION}


def load_verified_archive(trusted_acceptance_sha256,*,_reader=read_snapshot):
    try:return _load_verified_archive(trusted_acceptance_sha256,_reader=_reader)
    except EvidenceRefused:raise
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError) as exc:raise EvidenceRefused('missing or mistyped archived schema') from exc
