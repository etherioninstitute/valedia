"""Stage exact accepted W16 sources for one later root-gated native qualification. Never launch."""
from __future__ import annotations
import argparse,ast,copy,hashlib,importlib.util,json,os,shutil,stat,sys
from pathlib import Path
BASE=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space');CREW=BASE/'project/work/sol-foreman-qwen'
W16=CREW/'valedia-mirofish-fresh-20261106-caller-budget';RESUME=CREW/'valedia-resume-20261108'
WORK=CREW/'valedia-native-20261108-compact-ontology'
REPORT64_WORK=CREW/'valedia-native-20261108-report64'
COVERAGE_WORK=CREW/'valedia-native-20261108-report64-coverage'
PROFILES={'compact40':(40,60000),'report64':(64,100000),'report64_coverage':(64,100000),'swarm48':(640,1000000)}
# The swarm profile (owner go-ahead 2026-11-11): a real many-agent run. Every number is closed here; none is a command-line option.
# 48 named invented participants, every one acting in each of 3 rounds on both platforms, at most 2 requests in flight per platform so
# nothing waits past the gateway's unchanged 170-second queue limit. One model call at a time, so the wall clock is hours, not minutes.
SWARM={'swarm48':{'actors':48,'agents_bound':64,'rounds':3,'chats':640,'output':1000000,'embedding_attempts':1024,'embedding_texts':4096,
                  'embedding_bytes':2097152,'wall':14400,'semaphore':2,'history_records':36001,'history_mib':64,
                  'scenario':'mirofish_swarm48_scenario.txt','scenario_sha256':'999e56774f34df31ceef8fa344f061a22e704e9c2ee607faf0087f8446121357'}}
SWARM_WORK=CREW/'valedia-native-20261111-swarm48'
RUN_ORIGINAL_SHA='5fd72f92b52330c689f62ae35ee90c36ec4f0608d925c66f77d6b10548883650'
STARTUP_ORIGINAL_SHA='45ad8faf47457f6d851fcfadc207cb24ee71d90c4602ab1723a3fe01782154fd'
RUN_COPIES={'builder/review-source/mirofish_native_run.py','owned-source/observer-entry/mirofish_native_run.py','owned-source/worker-entry/mirofish_native_run.py'}
STARTUP_COPIES={'builder/review-source/mirofish_native_startup.py','owned-source/observer-entry/mirofish_native_startup.py','owned-source/worker-entry/mirofish_native_startup.py'}
SCENARIO_COPIES={'scenario-input.txt','owned-source/producer/scenario-input.txt'}
SCENARIO_SEAL_REL='scenario-input-seal.json'
EXTRACTOR_CANONICAL=BASE/'valedia-trader/src/mirofish_signal_extractor.py'
EXTRACTOR_PARENT_SNAPSHOT=W16/'owned-source/paper_app/src/mirofish_signal_extractor.py'
EXTRACTOR_REL='owned-source/paper_app/src/mirofish_signal_extractor.py'
EXTRACTOR_PARENT_SHA='3c0530021cce36b37bb8110e33bfa7a1a242846229865859b1d219d5c7ea1dae'
EXTRACTOR_NEW_SHA='647521bf06c30e35c8af692cdc70846a804ca37ebdf9a941f518b3b30139ec31'
EXTRACTOR_REVISION='event-war-whole-word-v2'
GATEWAY_ORIGINAL_SHA='2251da4eab35312c4abf8a6de2aae4272d753778d945f12cc33e346cec1e99a6'
GATEWAY_COPIES={'builder/review-source/mirofish_native_gateway.py','owned-source/observer-entry/mirofish_native_gateway.py','owned-source/worker-entry/mirofish_native_gateway.py'}
BASE_SHA='b7da913d7925bae6f09ef75a987409fd83a60b504ac083aee4e2be283f508e63'
METADATA_SHA='6d1d5dfee9518d8a2a8db88e0ed8fe8c6bc21a9481a2ceec56535b5eb347fe2c'
DRIVER_SHA='09d41b57b476426726361c120acb2e83a4af535ecb6f307f3422757117b30844'
EMPTY_SCAFFOLDS=['owned-source/producer/tmp','owned-source/producer/backend/uploads/reports','owned-source/producer/backend/log','owned-source/producer/backend/logs','owned-source/paper_app/state']
REGENERATED={'builder/observer.sb','builder/worker.sb','builder/graph.sb','builder/graph/conf/neo4j.conf'}
# Owner-acknowledged later revisions of original canonical files (2026-11-11). The list is pinned by its own hash,
# so it cannot grow or change without this file changing too. See its "scope" for what it does and does not allow.
ACKNOWLEDGED_FILE=Path(__file__).with_name('mirofish_acknowledged_revisions.json')
ACKNOWLEDGED_SHA='67f8def55322b6ede1e99d17785f7195571325bd771b197affdd32613a923534'
ACKNOWLEDGED_SCOPE=BASE/'valedia-trader/src'
ACKNOWLEDGED_WITNESSES=[]  # filled by inputs(); written into the staged candidate's ancestry
EXPECTED_CASES=['hash_matched_native_strict_tools','source_based_report_history_user_final','source_based_tool_cycle_tool_final']
def require(value,why):
    if not value:raise RuntimeError(why)
def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def snapshot(path,limit=4*1024*1024):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno());require(stat.S_ISREG(st.st_mode) and 0<st.st_size<=limit,'bounded regular source required');raw=f.read(limit+1);require(len(raw)<=limit,'source byte cap')
    return raw
def decoded(path,expected=None,limit=32*1024*1024):
    raw=snapshot(path,limit);require(expected is None or hashlib.sha256(raw).hexdigest()==expected,'input SHA changed: '+str(path));return json.loads(raw),raw
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')
def storage_guard():
    sys.path.insert(0,str(BASE/'valedia-evaluation'));from storage import guard;guard()
def pin_scope(rows,source=W16):
    model_paths={BASE/'models/Qwen3.8-27B-MLX-4bit'/name for name in ['config.json','tokenizer.json','tokenizer_config.json','chat_template.jinja','model.safetensors.index.json']}|{BASE/'models/ggml-org/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf'}
    seen=set();allowed=[BASE/'runtime',Path('/Library/Java/JavaVirtualMachines/temurin-25.jdk/Contents/Home'),Path('/opt/homebrew/Cellar/python@3.11/3.11.12_1/Frameworks')]
    for row in rows:
        p=Path(row['path']);require(p.is_absolute() and str(p) not in seen and '..' not in p.parts,'duplicate/relative pin');seen.add(str(p))
        require(p.is_relative_to(source) or any(p.is_relative_to(root) for root in allowed) or p==Path('/usr/local/bin/node') or p in model_paths,'unknown pin scope: '+str(p))
        require(len(row['sha256'])==64,'pin digest shape')
    return seen
def verify_metadata(receipt,controls,observations):
    require(len(observations)==3,'all three actual SDK observations required')
    require(receipt['status']=='METADATA_QUALIFIED' and receipt['cleanup_state']=='OWNED_UNLOADED' and receipt['generation_calls']==0 and receipt['resource_errors']==[],'metadata incomplete/uncertain')
    require([x['case'] for x in receipt['observations']]==EXPECTED_CASES,'three exact supported cases required')
    require(receipt['bindings']['candidate_sha256']==BASE_SHA and receipt['bindings']['driver_sha256']==DRIVER_SHA,'metadata base/source binding changed')
    require(receipt['bindings']['controls_sha256']==hashlib.sha256(controls).hexdigest(),'metadata controls changed')
    require(receipt['after_snapshot']['llm']==[],'metadata owned/unowned LLM remains')
    embedding=receipt['baseline']['embedding'];require(embedding['identifier']=='ym-research-embedding' and embedding['instanceReference']=='byxRvY4Yb6SW/RfhubedreSu' and receipt['baseline']['embeddingContext']==2048,'baseline embedding drift')
    require(any(x['identifier']==embedding['identifier'] and x['instanceReference']==embedding['instanceReference'] and x['contextLength']==2048 for x in receipt['after_snapshot']['embedding']),'embedding preservation not proved')
    require(receipt['sdk_process_cleanup'] and all(not x.get('error') and x['result']['stopped'] is True and x['result']['live_group_members']==[] for x in receipt['sdk_process_cleanup']),'helper cleanup unproven')
    for row,raw in zip(receipt['observations'],observations):
        require(hashlib.sha256(raw).hexdigest()==row['actual_sdk_sha256'],'actual SDK observation changed');actual=json.loads(raw);comparison=row['frozen_comparison'];require(comparison['verified'] is True and comparison['tokens']>=actual['tokens'] and actual['context']==32768,'metadata comparison missing')
def canonical_revision_witness(path,expected):
    require(Path(path)==EXTRACTOR_CANONICAL and expected==EXTRACTOR_PARENT_SHA,'unknown canonical revision path or expected parent')
    require(EXTRACTOR_CANONICAL.is_file() and not EXTRACTOR_CANONICAL.is_symlink(),'canonical extractor path changed')
    require(sha(EXTRACTOR_PARENT_SNAPSHOT)==EXTRACTOR_PARENT_SHA,'parent W16 extractor snapshot drift')
    require(sha(EXTRACTOR_CANONICAL)==EXTRACTOR_NEW_SHA,'unreviewed canonical extractor revision')
    return {'repair_outcome':'VALEDIA-EXTRACTOR-WORD-BOUNDARIES-1','revision':EXTRACTOR_REVISION,'canonical_path':str(EXTRACTOR_CANONICAL),'expected_parent_sha256':EXTRACTOR_PARENT_SHA,'parent_snapshot':str(EXTRACTOR_PARENT_SNAPSHOT),'parent_snapshot_sha256':EXTRACTOR_PARENT_SHA,'reviewed_new_sha256':EXTRACTOR_NEW_SHA,'scope':'One exact canonical file only; all other selected/original pins unchanged.'}

def acknowledged_revisions(path=None,expected=None):
    data,_=decoded(path or ACKNOWLEDGED_FILE,ACKNOWLEDGED_SHA if path is None else expected,4*1024*1024);rows=data['revisions'];table={row['path']:row for row in rows}
    require(data.get('schema')==1 and len(table)==len(rows),'acknowledged revision list malformed or has a duplicate path')
    for row in rows:
        require(len(row['expected_parent_sha256'])==64 and len(row['acknowledged_sha256'])==64 and row['expected_parent_sha256']!=row['acknowledged_sha256'],'acknowledged revision digest shape')
        p=Path(row['path']);require(p.is_absolute() and '..' not in p.parts and p.is_relative_to(ACKNOWLEDGED_SCOPE) and p!=EXTRACTOR_CANONICAL,'acknowledged revision outside the original canonical source folder: '+row['path'])
    return table
def acknowledged_revision_witness(row,table):
    """One original canonical file that differs from its frozen hash. Accepted only if the owner-acknowledged list names this
    exact path with this exact frozen parent and the file's exact current bytes. It is never copied into a candidate."""
    ack=table.get(row['path']);require(ack is not None and ack['expected_parent_sha256']==row['expected'],'unreviewed canonical revision (not in the acknowledged list): '+str(row['path']))
    p=Path(row['path']);require(p.is_file() and not p.is_symlink() and sha(p)==ack['acknowledged_sha256'],'canonical file changed after its acknowledged revision: '+str(row['path']))
    return {'kind':'OWNER_ACKNOWLEDGED_CANONICAL_REVISION_NOT_COPIED','path':str(p),'expected_parent_sha256':row['expected'],'acknowledged_sha256':ack['acknowledged_sha256'],'commits':ack.get('commits',[])}

def inputs():
    manifest,raw=decoded(W16/'builder/candidate-manifest.json',BASE_SHA);require(len(manifest['files'])==32954 and len(manifest['model_files'])==6,'base closure count changed');pin_scope(manifest['files']+manifest['model_files'])
    for row in manifest['files']+manifest['model_files']:
        p=Path(row['path']);require(p.is_file() and not p.is_symlink() and sha(p)==row['sha256'],'frozen input changed: '+str(p))
    before,_=decoded(W16/'review98/before-hashes.json');table=acknowledged_revisions();acknowledged=[]
    for group,count in [('selected_candidate_files',713),('original318',318)]:
        require(len(before[group])==count,'selected/original count changed')
        revised=[]
        for row in before[group]:
            if sha(row['path'])!=row['expected']:
                # Selected candidate files are never excused. An original file is excused only by the closed extractor witness or the acknowledged list.
                if group=='original318' and Path(row['path'])!=EXTRACTOR_CANONICAL:acknowledged.append(acknowledged_revision_witness(row,table))
                else:revised.append(canonical_revision_witness(row['path'],row['expected']))
        canonical_rows=[row for row in before[group] if Path(row['path'])==EXTRACTOR_CANONICAL];require(all(row['expected']==EXTRACTOR_PARENT_SHA for row in canonical_rows),'unexpected canonical parent row');require(len(canonical_rows)<=1 and len(revised)==len(canonical_rows),'only exact present canonical row may be revised in '+group)
    canonical_revision_witness(EXTRACTOR_CANONICAL,EXTRACTOR_PARENT_SHA);ACKNOWLEDGED_WITNESSES[:]=acknowledged
    receipt,receipt_raw=decoded(RESUME/'build/metadata-supported-once/metadata-result.json',METADATA_SHA)
    acceptance,acceptance_raw=decoded(RESUME/'lead-supported-metadata-acceptance.json');require(acceptance['status']=='ACCEPTED_SUPPORTED_MIROFISH_SDK_METADATA_ONLY' and acceptance['receipt_sha256']==METADATA_SHA,'root metadata acceptance missing')
    controls=snapshot(receipt['bindings']['controls_path']);observations=[snapshot(row['actual_sdk']) for row in receipt['observations']];verify_metadata(receipt,controls,observations)
    driver=Path(__file__).with_name('mirofish_metadata_check.py');require(sha(driver)==DRIVER_SHA,'accepted metadata/helper import source changed');spec=importlib.util.spec_from_file_location('accepted_metadata_stage_import',driver);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);helpers=module.import_helpers()
    return manifest,raw,receipt,receipt_raw,acceptance_raw,controls,observations,helpers

def copy_pin(row,target,ancestry):
    source=Path(row['path']);raw=snapshot(source,16*1024*1024);require(hashlib.sha256(raw).hexdigest()==row['sha256'],'copy source drift');target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);target.chmod(source.stat().st_mode&0o777);require(sha(target)==row['sha256'],'copied source mismatch');ancestry.append({'source':str(source),'target':str(target),'sha256':row['sha256'],'kind':'EXACT_ACCEPTED_W16_BYTES'});return {'path':str(target),'sha256':row['sha256']}

ONTOLOGY_REL='owned-source/producer/backend/app/services/ontology_generator.py'
ONTOLOGY_ORIGINAL_SHA='39c4ef32294d8af3b20813d589216a21fb66953098ebf77415fb6594ed3e854e'
COMPACT_ONTOLOGY_PROMPT="""Design an ontology for social media opinion simulation from the supplied text and requirements. Return only one complete minified JSON object on one line: no markdown, commentary, or reasoning.
Use exactly 10 unique entity types: 8 specific types derived from the text, then Person and Organization as the last two fallback types. Types must represent real-world people, organizations, institutions, official accounts, platforms or concrete group representatives able to voice and interact. Never use abstract concepts, topics, attitudes, supporters or opponents as actor types. Do not invent observed actors, facts, events, prices, or trade conclusions. Preserve the supplied scenario and uncertainty.
Use English PascalCase entity names, at most 24 characters, with clear nonoverlapping boundaries. Specific descriptions distinguish each type from its fallback. Person covers natural persons outside specific types; Organization covers organizations outside specific types, including small organizations and temporary groups.
Use exactly 6 meaningful relationship types (the allowed 6-10 minimum), reflecting real social connections/interactions appropriate to the text. English UPPER_SNAKE_CASE names, at most 24 characters. Each has exactly one source_targets pair; source and target must exactly match declared entity names. Across the six pairs cover all ten entity types.
Root keys only: entity_types, edge_types, analysis_summary. Each entity has name, description, attributes, examples. Each entity has exactly one key attribute (within the allowed 1-3), with name in English snake_case, type "text", and description. Attribute names must not be name, uuid, group_id, created_at, or summary. Use descriptive names such as full_name or org_name. Set examples to []: do not invent example actors.
Each edge has name, description, source_targets, attributes; source_targets contains one object with source and target; set edge attributes to []. Entity and edge descriptions are concise English, at most 48 characters; attribute descriptions at most 32 characters; analysis_summary at most 120 characters. Keep all required keys, arrays, and complete valid JSON. Do not repeat the input or include extra prose. No fixed taxonomy or answer is supplied: derive the types and relationships from the input."""
def compact_ontology_bytes(raw):
    require(hashlib.sha256(raw).hexdigest()==ONTOLOGY_ORIGINAL_SHA,'original ontology source drift')
    old=ast.parse(raw);matches=[n for n in old.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ONTOLOGY_SYSTEM_PROMPT' for t in n.targets)]
    require(len(matches)==1 and isinstance(matches[0].value,ast.Constant) and isinstance(matches[0].value.value,str),'exact ontology constant assignment required')
    value=matches[0].value;lines=raw.splitlines(keepends=True);start=sum(map(len,lines[:value.lineno-1]))+value.col_offset;end=sum(map(len,lines[:value.end_lineno-1]))+value.end_col_offset
    changed=raw[:start]+repr(COMPACT_ONTOLOGY_PROMPT).encode()+raw[end:];new=ast.parse(changed);newmatch=next(n for n in new.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ONTOLOGY_SYSTEM_PROMPT' for t in n.targets));require(newmatch.value.value==COMPACT_ONTOLOGY_PROMPT,'prompt replacement drift')
    matches[0].value=ast.Constant(value='ONLY_REVIEWED_PROMPT_DELTA');newmatch.value=ast.Constant(value='ONLY_REVIEWED_PROMPT_DELTA');require(ast.dump(old,include_attributes=False)==ast.dump(new,include_attributes=False),'ontology functions or other constants changed')
    return changed

def gateway_budget_nodes(tree):
    limits=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Limits');nodes=[]
    for name in ['chat_attempts','output_total']:
        matches=[n.value for n in limits.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id==name];require(len(matches)==1,'exact Limits default missing');nodes.extend(matches)
    method=next(n for n in limits.body if isinstance(n,ast.FunctionDef) and n.name=='__post_init__');maxima=next(n.value for n in method.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='maxima' for t in n.targets))
    for name in ['chat_attempts','output_total']:
        matches=[value for key,value in zip(maxima.keys,maxima.values) if isinstance(key,ast.Constant) and key.value==name];require(len(matches)==1,'exact maxima missing');nodes.extend(matches)
    require(len(nodes)==4 and all(isinstance(n,ast.Constant) and type(n.value) is int for n in nodes),'four integer nodes required');return nodes

def gateway_profile_bytes(raw,profile):
    require(profile in PROFILES,'unknown closed staging profile');require(hashlib.sha256(raw).hexdigest()==GATEWAY_ORIGINAL_SHA,'original gateway source drift')
    old=ast.parse(raw);nodes=gateway_budget_nodes(old);require([n.value for n in nodes]==[40,60000,40,60000],'original four budget literals changed')
    if profile=='compact40':return raw
    lines=raw.splitlines(keepends=True);edits=[]
    for node,new_value in zip(nodes,[64,100000,64,100000]):
        start=sum(map(len,lines[:node.lineno-1]))+node.col_offset;end=sum(map(len,lines[:node.end_lineno-1]))+node.end_col_offset;require(raw[start:end]==str(node.value).encode(),'budget integer source span changed');edits.append((start,end,str(new_value).encode()))
    changed=raw
    for start,end,replacement in sorted(edits,reverse=True):changed=changed[:start]+replacement+changed[end:]
    new=ast.parse(changed);newnodes=gateway_budget_nodes(new);require([n.value for n in newnodes]==[64,100000,64,100000],'report64 literal mismatch')
    for node in nodes+newnodes:node.value='ONLY_FOUR_REVIEWED_BUDGET_INTEGERS'
    require(ast.dump(old,include_attributes=False)==ast.dump(new,include_attributes=False),'gateway AST changed outside four reviewed literals');return changed

def integer_literals(source):return [n.value for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Constant) and type(n.value) is int]
def closed_integer_delta(raw,original_sha,edits):
    """Replace exact source fragments, each of which must occur exactly the stated number of times, and prove by the syntax tree
    that the result differs from the original in integer literals only and in exactly as many of them as the edits say."""
    require(hashlib.sha256(raw).hexdigest()==original_sha,'original source drift before a closed integer delta');changed=raw;expected=0
    for old,new,count,literals in edits:
        old,new=old.encode(),new.encode();require(old!=new and changed.count(old)==count,'closed delta fragment missing or repeated: '+old.decode());changed=changed.replace(old,new);expected+=count*literals
    def masked(source):
        tree=ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node,ast.Constant) and type(node.value) is int:node.value=0
        return ast.dump(tree,include_attributes=False)
    require(masked(raw)==masked(changed),'source changed outside integer literals')
    before,after=integer_literals(raw),integer_literals(changed);require(len(before)==len(after) and sum(a!=b for a,b in zip(before,after))==expected,'number of changed integer literals differs from the closed delta')
    return changed
def swarm_gateway_bytes(raw,profile):
    p=SWARM[profile];pairs=[('chat_attempts',40,p['chats']),('output_total',60000,p['output']),('embedding_attempts',64,p['embedding_attempts']),('embedding_texts',256,p['embedding_texts']),('embedding_total_bytes',131072,p['embedding_bytes']),('wall_seconds',2400,p['wall'])]
    edits=[(f'{name}: int = {old}',f'{name}: int = {new}',1,1) for name,old,new in pairs]+[(f'"{name}": {old},',f'"{name}": {new},',1,1) for name,old,new in pairs]
    changed=closed_integer_delta(raw,GATEWAY_ORIGINAL_SHA,edits);nodes=gateway_budget_nodes(ast.parse(changed));require([n.value for n in nodes]==[p['chats'],p['output'],p['chats'],p['output']],'swarm gateway budget mismatch');return changed
def swarm_run_bytes(raw,profile):
    p=SWARM[profile];wall=p['wall']
    return closed_integer_delta(raw,RUN_ORIGINAL_SHA,[('time.monotonic() + 2280',f'time.monotonic() + {wall-120}',1,1),
        ('<= 4, "actual generated participant count outside bound"',f'<= {p["agents_bound"]}, "actual generated participant count outside bound"',1,1),
        ('"max_rounds": 1,',f'"max_rounds": {p["rounds"]},',1,1),("'wall_seconds':2400}",f"'wall_seconds':{wall}}}",1,1),
        ('deadline=start+2400',f'deadline=start+{wall}',1,1),("receipt['wall_seconds']<=2400",f"receipt['wall_seconds']<={wall}",1,1)])
def swarm_startup_bytes(raw,profile):
    p=SWARM[profile]
    return closed_integer_delta(raw,STARTUP_ORIGINAL_SHA,[('(30 if preflight else 2400)',f'(30 if preflight else {p["wall"]})',1,1),
        ('MAX_RECORDS=6001',f'MAX_RECORDS={p["history_records"]}',1,1),('MAX_BYTES=16*1024*1024',f'MAX_BYTES={p["history_mib"]}*1024*1024',1,1)])
def swarm_scenario(profile):
    p=SWARM[profile];raw=snapshot(Path(__file__).with_name(p['scenario']));require(hashlib.sha256(raw).hexdigest()==p['scenario_sha256'],'swarm scenario changed')
    text=raw.decode();require(text.startswith('HYPOTHETICAL RESEARCH SCENARIO, NOT CURRENT MARKET NEWS.') and sum(line.startswith('- ') for line in text.splitlines())==p['actors'],'swarm scenario must be labelled hypothetical and name exactly the closed number of participants')
    seal={'recorded_at_utc':'2026-11-11T00:00:00+00:00','sha256':p['scenario_sha256'],'bytes':len(raw),'kind':'Pre-generation hypothetical public qualification input, not market observations','actor_count':p['actors'],'actual_model_calls_so_far':0}
    return raw,(json.dumps(seal,indent=2)+'\n').encode()

SELECTOR_REL='owned-source/producer/backend/scripts/run_parallel_simulation.py'
SELECTOR_ORIGINAL_SHA='8738ca61335ca7f4f206a0672c062bead8734ef1bf1bc17a601000e728538c40'
def selector_assignments(tree):
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='get_active_agents_for_round');result=[]
    for name in ['target_count','active_hours','activity_level']:
        matches=[n for n in ast.walk(function) if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id==name];require(len(matches)==1,'exact selector assignment missing');result.extend(matches)
    return result

def swarm_selector_bytes(raw,profile):
    """Every configured agent takes part in every round (three selector right-hand sides), and at most `semaphore` requests are in
    flight per platform (two integers). Agents' profiles, prompts and what they say stay the model's own."""
    step=coverage_selector_bytes(raw,['len(agent_configs)','list(range(24))','1.0'])
    return closed_integer_delta(step,hashlib.sha256(step).hexdigest(),[('semaphore=30,',f'semaphore={SWARM[profile]["semaphore"]},',2,1)])
def coverage_selector_bytes(raw,expressions=None):
    require(hashlib.sha256(raw).hexdigest()==SELECTOR_ORIGINAL_SHA,'original selector source drift');old=ast.parse(raw);assignments=selector_assignments(old);lines=raw.splitlines(keepends=True);edits=[];expressions=expressions or ['min(2, len(agent_configs))','list(range(24))','1.0']
    require([ast.unparse(n.value) for n in assignments]==['int(random.uniform(base_min, base_max) * multiplier)',"cfg.get('active_hours', list(range(8, 23)))","cfg.get('activity_level', 0.5)"],'original selector RHS changed')
    for assignment,expression in zip(assignments,expressions):
        node=assignment.value;start=sum(map(len,lines[:node.lineno-1]))+node.col_offset;end=sum(map(len,lines[:node.end_lineno-1]))+node.end_col_offset;edits.append((start,end,expression.encode()))
    changed=raw
    for start,end,replacement in sorted(edits,reverse=True):changed=changed[:start]+replacement+changed[end:]
    new=ast.parse(changed);updated=selector_assignments(new);require([ast.unparse(n.value) for n in updated]==expressions,'bounded coverage RHS mismatch')
    for n in assignments+updated:n.value=ast.Constant(value='ONLY_THREE_QUALIFICATION_SELECTOR_RHS')
    require(ast.dump(old,include_attributes=False)==ast.dump(new,include_attributes=False),'selector changed outside three RHS');return changed

def scaffold(work):
    for relative in EMPTY_SCAFFOLDS:
        path=Path(work)/relative;path.mkdir(parents=True,exist_ok=True);require(not any(path.iterdir()),'runtime scaffold must be empty: '+relative)

def stage(work,data,profile='compact40'):
    require(profile in PROFILES,'unknown closed staging profile')
    manifest,base_raw,receipt,receipt_raw,acceptance_raw,controls,observations,helpers=data;work=Path(work).absolute();require(work.parent==CREW and not work.exists() and all(not p.is_symlink() for p in [work,*work.parents]),'fresh direct crew work required');work.mkdir();(work/'builder').mkdir();(work/'tmp').mkdir();ancestry=[];pins=[]
    for row in manifest['files']:
        source=Path(row['path'])
        if source.is_relative_to(W16):
            relative=source.relative_to(W16)
            if str(relative) not in REGENERATED:
                pins.append(copy_pin(row,work/relative,ancestry))
                if str(relative)==EXTRACTOR_REL:
                    witness=canonical_revision_witness(EXTRACTOR_CANONICAL,row['sha256']);target=work/relative;raw_new=snapshot(EXTRACTOR_CANONICAL);require(hashlib.sha256(raw_new).hexdigest()==EXTRACTOR_NEW_SHA,'canonical revision changed during copy');target.chmod(0o644);target.write_bytes(raw_new);target.chmod(0o444);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='ONE_CLOSED_CANONICAL_EXTRACTOR_REVISION',source_sha256=row['sha256'],sha256=sha(target),revision_witness=witness)
                if str(relative)==ONTOLOGY_REL:
                    target=work/relative;changed=compact_ontology_bytes(target.read_bytes());target.chmod(0o644);target.write_bytes(changed);target.chmod(0o444);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='REVIEWED_ONTOLOGY_SYSTEM_PROMPT_ONLY',source_sha256=row['sha256'],sha256=sha(target),single_ast_constant='ONTOLOGY_SYSTEM_PROMPT')
                if profile in ('report64','report64_coverage') and str(relative) in GATEWAY_COPIES:
                    target=work/relative;changed=gateway_profile_bytes(target.read_bytes(),profile);target.chmod(0o644);target.write_bytes(changed);target.chmod(0o444);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='REVIEWED_FIXED_REPORT64_FOUR_INTEGER_DELTA',source_sha256=row['sha256'],sha256=sha(target),integer_changes=[40,64,60000,100000])
                if profile in SWARM:
                    closed=None
                    if str(relative) in GATEWAY_COPIES:closed=('gateway',swarm_gateway_bytes)
                    elif str(relative) in RUN_COPIES:closed=('run',swarm_run_bytes)
                    elif str(relative) in STARTUP_COPIES:closed=('startup',swarm_startup_bytes)
                    if closed:
                        target=work/relative;changed=closed[1](target.read_bytes(),profile);target.chmod(0o644);target.write_bytes(changed);target.chmod(0o444);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='REVIEWED_CLOSED_SWARM_INTEGER_DELTA',file=closed[0],source_sha256=row['sha256'],sha256=sha(target))
                    if str(relative)==SELECTOR_REL:
                        target=work/relative;changed=swarm_selector_bytes(target.read_bytes(),profile);target.chmod(0o644);target.write_bytes(changed);target.chmod(0o444);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='SWARM_FULL_PARTICIPATION_THREE_RHS_AND_TWO_CONCURRENCY_INTEGERS',source_sha256=row['sha256'],sha256=sha(target))
                    if str(relative) in SCENARIO_COPIES or str(relative)==SCENARIO_SEAL_REL:
                        scenario,seal=swarm_scenario(profile);target=work/relative;mode=target.stat().st_mode&0o777;target.chmod(0o644);target.write_bytes(seal if str(relative)==SCENARIO_SEAL_REL else scenario);target.chmod(mode);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='SWARM_INVENTED_SCENARIO_REPLACES_QUALIFICATION_INPUT',source_sha256=row['sha256'],sha256=sha(target))
                if profile=='report64_coverage' and str(relative)==SELECTOR_REL:
                    target=work/relative;changed=coverage_selector_bytes(target.read_bytes());target.chmod(0o644);target.write_bytes(changed);target.chmod(0o444);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='EXPLICIT_BOUNDED_NATIVE_COVERAGE_QUALIFICATION_STIMULUS_THREE_RHS',source_sha256=row['sha256'],sha256=sha(target))
        else:pins.append(copy.deepcopy(row))
    scaffold(work)  # Explicit empty runtime directories; never copy historical logs/state.
    native=helpers['mirofish_native_run'];native.graph_configuration(work,manifest['graph_port'])
    generated={'builder/observer.sb':native.observer_policy(work,manifest['proxy_port'],manifest['graph_port']),'builder/graph.sb':native.graph_policy(work,manifest['graph_port']),'builder/worker.sb':native.worker_policy(work/'owned-source/producer',work/'owned-source/worker-entry',manifest['proxy_port'],manifest['graph_port'])}
    for relative,text in generated.items():(work/relative).write_text(text)
    for relative in sorted(REGENERATED):
        target=work/relative;require(str(W16) not in target.read_text(),'stale W16 policy/config path');pins.append({'path':str(target),'sha256':sha(target)});ancestry.append({'source':str(W16/relative),'target':str(target),'sha256':sha(target),'kind':'REGENERATED_BY_EXACT_ACCEPTED_FUNCTION'})
    evidence=work/'builder/metadata-qualification';evidence.mkdir();evidence_files={'base-candidate-manifest.json':base_raw,'metadata-result.json':receipt_raw,'root-metadata-acceptance.json':acceptance_raw,'supported-controls.json':controls}
    for row,raw in zip(receipt['observations'],observations):evidence_files[row['case']+'-actual-sdk.json']=raw
    sizing=snapshot(RESUME/'recon/prep-length-cause-and-contract.json');require(hashlib.sha256(sizing).hexdigest()=='619a1360b19353f30a6628142f040c43313162cddcd87d79da15fb3fa29bc854','sizing contract changed');evidence_files['compact-ontology-sizing-contract.json']=sizing
    for name,raw in evidence_files.items():target=evidence/name;target.write_bytes(raw);pins.append({'path':str(target),'sha256':sha(target)})
    # Preserve copied proof manifests as historical ancestry; never rewrite their embedded old paths.
    staging_source=work/'builder/staging-source';staging_source.mkdir()
    for name in ['mirofish_native_stage.py','test_mirofish_native_stage.py','test_mirofish_event_boundary.py','mirofish_acknowledged_revisions.json']:
        target=staging_source/name;target.write_bytes(Path(__file__).with_name(name).read_bytes());pins.append({'path':str(target),'sha256':sha(target)})
    owned_count=sum(1 for row in ancestry if Path(row['source']).is_relative_to(W16/'owned-source'));exact_owned=sum(1 for row in ancestry if row['kind']=='EXACT_ACCEPTED_W16_BYTES' and Path(row['source']).is_relative_to(W16/'owned-source'));owned_gateway=sum(1 for row in ancestry if row['kind']=='REVIEWED_FIXED_REPORT64_FOUR_INTEGER_DELTA' and Path(row['source']).is_relative_to(W16/'owned-source'));gateway_deltas=sum(row['kind']=='REVIEWED_FIXED_REPORT64_FOUR_INTEGER_DELTA' for row in ancestry);swarm_closed=[row for row in ancestry if row['kind']=='REVIEWED_CLOSED_SWARM_INTEGER_DELTA'];swarm_scenario_rows=sum(row['kind']=='SWARM_INVENTED_SCENARIO_REPLACES_QUALIFICATION_INPUT' for row in ancestry);swarm_selector=sum(row['kind']=='SWARM_FULL_PARTICIPATION_THREE_RHS_AND_TWO_CONCURRENCY_INTEGERS' for row in ancestry)
    if profile in SWARM:require(owned_count==72 and exact_owned==62 and sorted(row['file'] for row in swarm_closed)==['gateway']*3+['run']*3+['startup']*3 and swarm_selector==1 and swarm_scenario_rows==3 and gateway_deltas==0,'swarm source ancestry changed')
    else:require(owned_count==72 and exact_owned==({'compact40':70,'report64':68,'report64_coverage':67}[profile]) and owned_gateway==(0 if profile=='compact40' else 2) and gateway_deltas==(0 if profile=='compact40' else 3) and not swarm_closed and not swarm_scenario_rows and not swarm_selector,'profile source ancestry changed');require(len({sha(work/relative) for relative in GATEWAY_COPIES})==1,'gateway copies inconsistent')
    lineage={'manifest_pinned_owned_source_files':owned_count,'exact_owned_source_files':exact_owned,'reviewed_prompt_only_deltas':1,'profile':profile,'gateway_budget_deltas_total':gateway_deltas,'gateway_budget_deltas_owned':owned_gateway,'qualification_selector_deltas':sum(row['kind']=='EXPLICIT_BOUNDED_NATIVE_COVERAGE_QUALIFICATION_STIMULUS_THREE_RHS' for row in ancestry),'swarm_integer_delta_files':len(swarm_closed),'swarm_selector_deltas':swarm_selector,'swarm_scenario_files_replaced':swarm_scenario_rows,'runtime_scaffolds':EMPTY_SCAFFOLDS,'historical_runtime_log_excluded':'owned-source/producer/backend/logs/2026-11-07.log (not manifest-pinned source)','base_manifest_sha256':BASE_SHA,'metadata_receipt_sha256':METADATA_SHA,'source_copies':ancestry,'canonical_revision_witness':canonical_revision_witness(EXTRACTOR_CANONICAL,EXTRACTOR_PARENT_SHA),'historical_proof_policy':'Copied old manifests remain exact historical proof, not new target claims. Operational file paths in this derived manifest and all four generated policies/configs point at new work.','original713_and318':'713 selected pins unchanged; the one canonical extractor row in318 uses the exact closed witness; '+str(len(ACKNOWLEDGED_WITNESSES))+' other original files carry an owner-acknowledged later revision that is NOT copied into this candidate (the reviewed W16 bytes are); the remaining original pins are unchanged','owner_acknowledged_canonical_revisions':{'list_sha256':ACKNOWLEDGED_SHA,'count':len(ACKNOWLEDGED_WITNESSES),'copied_into_candidate':False,'witnesses':list(ACKNOWLEDGED_WITNESSES)},'no_model_graph_worker_paper_or_broker_actions':True};lineage_path=work/'builder/staging-ancestry.json';write(lineage_path,lineage);pins.append({'path':str(lineage_path),'sha256':sha(lineage_path)})
    derived=copy.deepcopy(manifest);derived['staging_profile']={'name':profile,'chat_attempts':PROFILES[profile][0],'requested_output_total':PROFILES[profile][1],'caller_output':1536,'native_probe_output':512,'global_output_per_call':4096,'context':32768,'coverage':'expected finite report coverage, not guaranteed; full report semantics unchanged','participation_stimulus':'EXPLICIT_BOUNDED_CARDINALITY_COVERAGE_QUALIFICATION_ONLY_NOT_NATURALISTIC_FORECAST' if profile=='report64_coverage' else 'FULL_PARTICIPATION_EVERY_ROUND_SWARM_LOAD_NOT_NATURALISTIC_FORECAST' if profile in SWARM else 'UNCHANGED_STOCHASTIC_SCHEDULE'};derived['files']=pins;derived['task_caller_budget']['global_gateway']=[PROFILES[profile][0],PROFILES[profile][1],4096];derived['task_caller_budget']['maximum_reserved']=2*512+(PROFILES[profile][0]-2)*1536;derived['known_blockers']=[];derived['launch_available']=True;
    if profile in SWARM:
        p=SWARM[profile];derived['uniform_policy']=dict(derived.get('uniform_policy') or {},wall_seconds=p['wall'])
        derived['swarm_profile']=dict(p,name=profile,owner_decision='2026-11-11 go-ahead for a larger swarm profile',what_changed='Closed integers only in the gateway limits, the supervisor and host deadlines, the participant bound and the round count; three selector right-hand sides so every configured agent takes part in every round; two concurrency integers; the scenario input and its seal. Per-call limits (175 s arrival to completion, 170 s queue, 1536/512 output caps, 32768 context), the one dispatch slot, the poison-on-unknown rule, confinement and the paper-only handoff are unchanged.',what_it_is_not='The scenario and its 48 participants are invented, and every agent is made to take part in every round: this shows the pipeline at scale. It is not a forecast, not a naturalistic schedule and not evidence about any market.')
    derived['launch_authorized']=False;derived['source_status']='W16_COMPACT_ONTOLOGY_PLUS_CLOSED_'+profile.upper()+'_FOR_ROOT_GATED_QUALIFICATION';derived['intentional_canonical_source_revision']=canonical_revision_witness(EXTRACTOR_CANONICAL,EXTRACTOR_PARENT_SHA);derived['old_authority_policy']='Old v1 receipts/stores/hash3c053002 remain unchanged. New source extraction/replays are versioned derived artifacts, not new live proof or rewritten authority.';derived['qualification_stimulus']={'enabled':profile=='report64_coverage','scope':'Only three selector RHS: min(2,N) configured valid agents per platform, all24 hours, probability1.0; random.sample identities/order unchanged. Generated configs/prompts/agent content unchanged. No forced action, answer, direction, trade or weakening of >=2 actual completed200 native forwards. Naturalistic forecasting is not claimed.','selector_original_sha256':SELECTOR_ORIGINAL_SHA,'selector_staged_sha256':sha(work/SELECTOR_REL)};derived['metadata_reuse_impact']={'evidence':METADATA_SHA,'scope':'Format conformance reused only: same roles, tools, schemas, LLMClient, SDK/model/tokenizer; only declared ontology prompt content, opt-in four gateway budget integers and optional explicit qualification participation schedule differ; gateway format/guards unchanged, and every actual request still requires actual SDK/frozen comparison. No new model quality or prep success claimed.','ontology_change':{'source_sha256':ONTOLOGY_ORIGINAL_SHA,'staged_sha256':sha(work/ONTOLOGY_REL),'constant':'ONTOLOGY_SYSTEM_PROMPT','functions_and_other_constants':'AST_IDENTICAL'}};derived['derived_from']={'base_candidate_sha256':BASE_SHA,'metadata_receipt_sha256':METADATA_SHA,'removed_metadata_prerequisite':'ACTUAL_SUPPORTED_THREE_CASE_METADATA_ACCEPTED','moved_from_runtime_blockers_to_post_run':'NO_COMPLETED_GENUINE_NATIVE_AGENT_FORWARD_EVIDENCE_FOR_REPAIRED_CODE'};derived['qualification_status']={'native_forward':'PENDING_POST_RUN_ALL_NATIVE_CHATS_COMPLETED_DISPATCHED_200','fresh_report':'PENDING_POST_RUN_EXACT_CAPTURE_AND_VALIDATE','local_paper':'PENDING_GENUINE_EXTRACTED_SIGNAL_MAY_REFUSE_FIXED_BUY','assistant_final_continuation':'UNSUPPORTED','launch_available_means':'eligible for qualification only; separate expiring one-use root receipt required','model_selected_execution_or_profit':'NOT_CLAIMED'}
    if profile in SWARM:derived['metadata_reuse_impact']['scope']+=' SWARM PROFILE: in addition, the closed integers, selector expressions and scenario listed under swarm_profile differ; request formats, roles, tools and schemas do not.'
    write(work/'builder/candidate-manifest.json',derived)
    assert_staged(work,derived)
    return {'status':'STAGED_NOT_AUTHORIZED_NO_SERVICES','work':str(work),'candidate_sha256':sha(work/'builder/candidate-manifest.json'),'base_sha256':BASE_SHA,'metadata_sha256':METADATA_SHA,'pins':len(pins),'source_ancestry':str(lineage_path)}
def assert_staged(work,manifest):
    work=Path(work)
    for relative in EMPTY_SCAFFOLDS:require((work/relative).is_dir() and not any((work/relative).iterdir()),'required runtime scaffold missing/nonempty: '+relative)
    require(not (work/'builder/one-attempt.claim').exists() and not (work/'run1').exists() and not (work/'builder/native-run-receipt.json').exists(),'stale native state/claim copied')
    require(not list(work.rglob('*.sqlite3')) and not list(work.rglob('*.db')),'stale journal/database copied')
    for relative in REGENERATED:require(str(W16) not in (work/relative).read_text(),'stale policy path')
    for row in manifest['files']:
        require(not Path(row['path']).is_relative_to(W16),'old operational pin path');require(sha(row['path'])==row['sha256'],'derived pin mismatch')
    require(manifest['launch_authorized'] is False and manifest['qualification_status']['native_forward'].startswith('PENDING'),'fabricated launch/native qualification')
def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--stage',action='store_true');parser.add_argument('--profile',choices=tuple(PROFILES),default='compact40');parser.add_argument('--work',type=Path);args=parser.parse_args(argv);storage_guard();os.environ['TMPDIR']=str(BASE/'project/tmp');data=inputs()
    selected_work=args.work or {'compact40':WORK,'report64':REPORT64_WORK,'report64_coverage':COVERAGE_WORK,'swarm48':SWARM_WORK}[args.profile];result=stage(selected_work,data,args.profile) if args.stage else {'status':'INPUTS_VERIFIED_NO_STAGE_NO_LOAD','base_sha256':BASE_SHA,'metadata_sha256':METADATA_SHA};print(json.dumps(result));return 0
if __name__=='__main__':raise SystemExit(main())
