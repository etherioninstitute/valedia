"""Stage exact accepted W16 sources for one later root-gated native qualification. Never launch."""
from __future__ import annotations
import argparse,ast,copy,hashlib,importlib.util,json,os,shutil,stat,sys
from pathlib import Path
BASE=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space');CREW=BASE/'project/work/sol-foreman-qwen'
W16=CREW/'valedia-mirofish-fresh-20261106-caller-budget';RESUME=CREW/'valedia-resume-20261108'
WORK=CREW/'valedia-native-20261108-compact-ontology'
REPORT64_WORK=CREW/'valedia-native-20261108-report64'
COVERAGE_WORK=CREW/'valedia-native-20261108-report64-coverage'
def _tools_module(name):
    spec=importlib.util.spec_from_file_location('stage_'+name,Path(__file__).with_name(name+'.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
LIMITS=_tools_module('mirofish_swarm_limits')  # TMF2: every swarm cap and time limit is derived there, from measured constants
# Swarm profiles (owner go-ahead 2026-11-11; the 2,048-token agent cap removed at the owner's instruction of 2026-11-15). Only the
# inputs are closed here; none is a command-line option. Every derived number (model calls, output budget, time limits, wall clock,
# host history) comes from mirofish_swarm_limits.derive(), and staging refuses a profile that breaks a coupling inequality. Every
# configured agent acts in every round on both platforms, one request in flight per platform (dispatch is serial; more in flight
# only adds waiting). Reply caps: a reply cut off at its cap is fatal for the whole attempt, so the caller cap is 4,096 (2,560 was 96%
# used) and an agent turn's 1,024 (longest seen 738). The agent memory is the reviewed whole-unit memory (MEMORY_* below).
# swarm8 is the proving profile: its 4,096-token memory is deliberately tight so whole units drop in a real run. Its participant
# bound is 12 (addendum 2, R3): ceilings and the wall are taken at the bound, and at 12 the ceiling with its margin stays inside
# the 2-hour wall; a graph that builds more than 12 participants is refused by the run's own participant check in its first minutes.
# swarm48's 12,288 is PROVISIONAL until the proving run's memory readings have been read.
SWARM={'swarm48':{'actors':48,'agents_bound':64,'rounds':3,'memory_budget':12288,'embedding_attempts':1024,'embedding_texts':4096,'embedding_bytes':2097152,
                  'scenario':'mirofish_swarm48_scenario.txt','scenario_sha256':'999e56774f34df31ceef8fa344f061a22e704e9c2ee607faf0087f8446121357','scenario_recorded_utc':'2026-11-11T00:00:00+00:00'},
       'swarm8':{'actors':8,'agents_bound':12,'rounds':3,'memory_budget':4096,'embedding_attempts':1024,'embedding_texts':4096,'embedding_bytes':2097152,
                 'scenario':'mirofish_swarm8_scenario.txt','scenario_sha256':'641690f62eb67dd2175a45041b19278a22d07d892693b761da6a067de4f1a15e','scenario_recorded_utc':'2026-11-15T00:00:00+00:00'}}
def swarm_numbers(profile,model=None):
    """The profile's closed inputs and every number derived from them for one model (default: the 27B); refuses a profile that
    breaks any coupling inequality."""
    model=model or LIMITS.DEFAULT_MODEL;inputs=SWARM[profile];derived=LIMITS.derive(inputs,model);broken=LIMITS.violations(inputs,derived,model);require(not broken,'swarm profile breaks: '+'; '.join(broken))
    return dict(inputs,**derived,caller_output=LIMITS.CALLER_REPLY_CAP,native_output=LIMITS.AGENT_REPLY_CAP)
SWARM_WORK=CREW/'valedia-native-20261111-swarm48';SWARM8_WORK=CREW/'valedia-native-20261115-swarm8'
RUN_ORIGINAL_SHA='5fd72f92b52330c689f62ae35ee90c36ec4f0608d925c66f77d6b10548883650'
STARTUP_ORIGINAL_SHA='45ad8faf47457f6d851fcfadc207cb24ee71d90c4602ab1723a3fe01782154fd'
RUN_COPIES={'builder/review-source/mirofish_native_run.py','owned-source/observer-entry/mirofish_native_run.py','owned-source/worker-entry/mirofish_native_run.py'}
STARTUP_COPIES={'builder/review-source/mirofish_native_startup.py','owned-source/observer-entry/mirofish_native_startup.py','owned-source/worker-entry/mirofish_native_startup.py'}
SCENARIO_COPIES={'scenario-input.txt','owned-source/producer/scenario-input.txt'}
# The producer's own four-participant, one-round caps (found by the first swarm attempt, which stopped itself at 'Owned synthetic
# entity cap violated'). Each is one integer in a pinned file: {relative path: (original sha256, [(fragment, replacement template)])}.
SWARM_PRODUCER={
 'owned-source/producer/backend/app/services/simulation_manager.py':('ab0ec6a1b7fd9de72f048d77e09512a730184a43db0fcfa0aaf0670f0df0af53',[('len(filtered.entities) <= 4:','len(filtered.entities) <= {agents}:')]),
 'owned-source/producer/backend/app/services/simulation_config_generator.py':('6faccfe744022e2ea996118f5111b2adf80cf0de17928792ee6d52b8fdc0034b',[('len(entities) <= 4:','len(entities) <= {agents}:')]),
 'owned-source/producer/backend/app/services/simulation_runner.py':('d08c5b346db26ce6ba88e8db2376d3c965ce747e1c507079ddd5f455bf659938',[('or max_rounds != 1 or enable_graph_memory_update','or max_rounds != {rounds} or enable_graph_memory_update'),("len(config['agent_configs']) <= 4):","len(config['agent_configs']) <= {agents}):")]),
 'owned-source/producer/backend/app/owned_caller_budget.py':('1ac9addd7cca1c3d32dbd3f21f47cc0aece7c90fb19bb5621d88d68116cb5e07',[('CALLER_OUTPUT_LIMIT = 1536','CALLER_OUTPUT_LIMIT = {caller}')]),
 # The embedding client sits behind the same gateway arrival-to-done limit, so it outlasts that limit by 30 s like every other client.
 'owned-source/producer/backend/app/storage/embedding_service.py':('04612aee8b2a5b86981793e25ea07cdac1f149bd9fb464284223bd3930ebc460',[('        timeout: int = 180,','        timeout: int = {embedding_client},')]),
 # The final report check accepts a run only if its simulation began less than max_age_seconds earlier, and refuses a policy above
 # one hour. A swarm's simulation alone runs longer than that, so the ceiling follows the profile's wall clock. The paper step stays
 # bound to NVDA only, which this scenario never mentions: no paper action can follow from it.
 'owned-source/paper_app/src/mirofish_provenance.py':('e9b2da75bb59662a18c3a0e808801a1cc326bdd826919eaa7738550da1b34bf4',[('<= self.max_age_seconds <= 3600:','<= self.max_age_seconds <= {wall}:')]),
}
# The agent memory (TMF2 addendum 1; a change of logic in a pinned producer file, decided by the lead under the owner's instruction
# of 2026-11-15 that the cap is to go). For a swarm profile the canonical owned_context_budget.py is checked against its pinned hash,
# replaced by the reviewed whole-unit memory in tools/ (pinned by its own hash), and only then are the profile's integers set on it by
# closed integer replacement. report64_coverage and the other profiles keep the canonical bytes.
MEMORY_REL='owned-source/producer/backend/scripts/owned_context_budget.py'
MEMORY_CANONICAL_SHA='e68444293006bf759c71f78884e07630120ee9dbb4c2d4d38fcdc0cb6a50ddb3'
MEMORY_FILE=Path(__file__).with_name('mirofish_owned_agent_memory.py')
MEMORY_REPLACEMENT_SHA='276add7e12d83b50948afd1f061b747fe156189c6488e17d7ddfa1a1d0c7d589'  # addendum 2: R2 tool-call text counted, R6 refusal line
MEMORY_EDITS=[('len(agents) <= 4:','len(agents) <= {agents}:'),('OUTPUT_COMPLETION_TOKENS = 512','OUTPUT_COMPLETION_TOKENS = {native}'),('INPUT_CONTEXT_TOKENS = 2048','INPUT_CONTEXT_TOKENS = {budget}')]
MEMORY_WHAT_CHANGES=('The canonical file gave every agent the library\'s score-based context at 2,048 tokens; with a 1,100 to 1,700-token '
    'persona inside it the library cut each feed into one-token chunks and kept only the last few dozen, so agents no longer read '
    'their input, and a cut that left two assistant messages in a row was refused by the template admission. The replacement keeps '
    'every guard of the canonical file (library versions, isolated runner, platform, agent count, model shape, memory type, the '
    'provider reply cap) and gives each agent a whole-unit memory: no message is ever sliced; the model is given the system message '
    'and the newest whole units (a unit is one user message and everything after it up to the next), older units dropped whole, '
    'oldest first, with one INFO line; never two assistant messages in a row and never a tool result without its call; only if the '
    'newest unit alone cannot fit is the newest user message trimmed from its end, with a marker line and one WARNING line; the '
    'total never exceeds the profile\'s budget, counting tool-call arguments, which the library itself counts as nothing; every '
    'refusal writes one WARNING line with a fixed phrase and the newest record\'s role.')
SCENARIO_SEAL_REL='scenario-input-seal.json'
# The model server's internal SDK port. The reviewed sources fix the value seen on 2026-11-08; the server picks a new one each time
# its app restarts, so a candidate staged later must carry the live port or it cannot connect at all. One integer, same closed check.
MODEL_ORIGINAL_SHA='6e116603d4b3dee43bbf226bce4fe0a2d393958a84f9e85504f5fa953366c677'
MODEL_COPIES={'builder/review-source/mirofish_native_model.py','owned-source/observer-entry/mirofish_native_model.py'}
ORIGINAL_SDK_PORT=62298;SERVER_PORT=1235
# TMF3 (addendum 1, 2026-11-15): staging WITH a model named (--model) takes the src/ revisions of the model module and the run
# script, each pinned here by its own hash exactly as the memory replacement is (a changed byte in src/ refuses staging until the
# pin is deliberately moved). They are the revisions of commits ab08400 and b5a1797. Their copies read the checkpoint from a
# selector file written beside the model module. Staging WITHOUT --model copies the W16 bytes as before, with no selector.
SRC=BASE/'valedia-trader/src'
MODEL_SRC_SHA='ea0676a6409a0915cd564bc954790937d8c9fbcb8be2a35d51c77ebda730d103'
RUN_SRC_SHA='21c4418452a7c19c16ab7164f3c24f60feac097e46bb70219c896c7029be06eb'
SRC_REVISION_NOTE=('These two files (mirofish_native_model.py, mirofish_native_run.py) are the src/ revisions of commits ab08400 and '
    'b5a1797, reviewed on 2026-11-15 (V134), not the W16 bytes. Every other file is staged as without a model.')
MODEL_SELECTOR='mirofish_model_profile.json'
SELECTOR_COPIES={'builder/review-source/'+MODEL_SELECTOR,'owned-source/observer-entry/'+MODEL_SELECTOR}  # beside each staged model copy
MODEL_REGISTRY=SRC/'mirofish_model_registry.json'  # read only to name a registry-only profile in its refusal; never staged
REGISTRY_NAME='mirofish_model_registry.json'
# Addendum 2, C1: --model accepts exactly the two built-in profiles of the pinned module's MODEL_PROFILES literal; a registered
# profile can name its own paths and pins, so no registry ever reaches a candidate and no registered name is staged.
BUILTIN_MODELS=('qwen3.8-27b','qwen3-8b')
# The reviewed tokenizer-origin hashes of each checkpoint other than the 27B (the 27B's are the base manifest's model_tokenizer).
TOKENIZER_PINS={'qwen3-8b':{'config.json':'f7c4eadfbbf522470667b797a3c89be2524832d2d599797248dc304fff447c30','tokenizer.json':'aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4',
    'tokenizer_config.json':'d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101','chat_template.jinja':'a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8',
    'model.safetensors.index.json':'f9fdbcb91c23971c13ec5d5f2573d2349e8f61f2f049371ec699281748fdb1bc'}}
START_FRAGMENT='(65 if pre else 30)'  # mirofish_native_run.py:380, the free-memory start rule (30 is the stop floor, unchanged)
SDK_HELPER_REL='builder/sdk_native.mjs'  # the generated helper script; the launcher refuses unless it equals what the model source generates
# The SSD's device number, which the launcher checks the prompt-cache target against. The reviewed startup source fixes the value
# seen on 2026-11-07; macOS numbers a volume when it mounts it, so a reboot or remount can change it. Staging carries the live number
# of the mounted SSD itself, read as the launcher reads it, or refuses. One integer, same closed check.
ORIGINAL_CACHE_DEVICE=16777246;SSD_MOUNT=Path('/Volumes/NIMROD_SSD');CACHE_TARGET=BASE/'cache/bionic/prompt-cache'
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
PROFILES={'compact40':(40,60000),'report64':(64,100000),'report64_coverage':(64,100000)}
PROFILES.update({name:(swarm_numbers(name)['chats'],swarm_numbers(name)['output']) for name in SWARM})  # derived, never typed in
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
def pin_scope(rows,source=W16,model_paths=None):
    if model_paths is None:model_paths={BASE/'models/Qwen3.8-27B-MLX-4bit'/name for name in ['config.json','tokenizer.json','tokenizer_config.json','chat_template.jinja','model.safetensors.index.json']}|{BASE/'models/ggml-org/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf'}
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

def live_sdk_port():
    """The one loopback port, other than the public server port, on which the local model server accepts an SDK connection."""
    import base64,socket,subprocess
    listing=subprocess.run(['/usr/sbin/lsof','-nP','-iTCP@127.0.0.1','-sTCP:LISTEN','-Fpcn'],env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=5).stdout
    ports=set();command=''
    for line in listing.splitlines():
        if line.startswith('c'):command=line[1:].lower()
        elif line.startswith('n') and ('bionic' in command or 'lm studio' in command or 'lmstudio' in command):
            port=line.rsplit(':',1)[-1]
            if port.isdigit() and int(port)!=SERVER_PORT:ports.add(int(port))
    answering=[]
    for port in sorted(ports):
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=3) as s:
                s.sendall(('GET /llm HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n'%(port,base64.b64encode(os.urandom(16)).decode())).encode())
                if s.recv(64).startswith(b'HTTP/1.1 101'):answering.append(port)
        except OSError:pass
    require(len(answering)==1,'exactly one live local model-server SDK port required, found '+str(answering));return answering[0]
def sdk_port_bytes(raw,port,original_sha=MODEL_ORIGINAL_SHA):
    require(type(port) is int and 1024<=port<=65535 and port!=SERVER_PORT and port!=ORIGINAL_SDK_PORT,'SDK port delta needs a different valid loopback port')
    return closed_integer_delta(raw,original_sha,[(f'SDK_PORT = {ORIGINAL_SDK_PORT}',f'SDK_PORT = {port}',1,1)])
def sdk_port_helper(text,port):
    old=f'ws://127.0.0.1:{ORIGINAL_SDK_PORT}';require(text.count(old)==1 and text.count(str(ORIGINAL_SDK_PORT))==1,'SDK helper port text missing or ambiguous');return text.replace(old,f'ws://127.0.0.1:{port}')
def staged_helper_matches_model(work,port,model=None):
    """Load the staged model source (it starts nothing on import) and require that the helper it generates is the staged helper
    (and, staged with a model, that it reads the chosen profile from its selector)."""
    entry=Path(work)/'owned-source/observer-entry';sys.path.insert(0,str(entry))
    try:
        spec=importlib.util.spec_from_file_location('staged_native_model_check',entry/'mirofish_native_model.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    finally:sys.path.remove(str(entry))
    require(module.SDK_PORT==port and (Path(work)/SDK_HELPER_REL).read_text()==module.SDK_SCRIPT,'staged SDK helper does not match what the staged model source generates')
    require(model is None or getattr(module,'ACTIVE_MODEL_PROFILE',None)==model,'staged model source does not read the chosen profile from its selector')
    return module
def sdk_port_policy(text,port):
    old=f'(allow network-outbound (remote ip "localhost:{ORIGINAL_SDK_PORT}"))';new=f'(allow network-outbound (remote ip "localhost:{port}"))';require(text.count(old)==1 and new not in text,'observer policy SDK port rule missing or ambiguous')
    return text.replace(old,new)
def live_ssd_device(target=CACHE_TARGET,ssd=BASE,mount=SSD_MOUNT):
    """The prompt-cache target's device number, read as the launcher reads it (Path.stat().st_dev), and only when it is the mounted SSD's."""
    require(target.is_dir() and not target.is_symlink() and os.path.ismount(mount),'prompt cache target missing or the SSD is not mounted')
    device=target.stat().st_dev;require(device==ssd.stat().st_dev==mount.stat().st_dev,'prompt cache target is not on the SSD: device %d, SSD %d'%(device,mount.stat().st_dev));return device
def ssd_device_bytes(raw,device,original_sha=STARTUP_ORIGINAL_SHA):
    require(type(device) is int and device>0 and device!=ORIGINAL_CACHE_DEVICE,'SSD device delta needs a different valid device number')
    return closed_integer_delta(raw,original_sha,[(f'CACHE_DEVICE={ORIGINAL_CACHE_DEVICE}',f'CACHE_DEVICE={device}',1,1)])
def integer_literals(source):return [n.value for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Constant) and type(n.value) is int]
def exact_constant_spans(raw,changed):
    """Exact to the byte (TMF2 addendum 2, R4): `changed` must be `raw` with only the changed constants' own source spans
    replaced, each by the same text with its digits replaced, so no comment, space, quote or other byte can change beside them."""
    def starts(source):
        offsets=[0]
        for line in source.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
        return offsets
    def constants(source):return [n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Constant)]
    before,after,lines_before,lines_after=constants(raw),constants(changed),starts(raw),starts(changed)
    require(len(before)==len(after),'source changed outside the approved constants')
    span=lambda node,lines:(lines[node.lineno-1]+node.col_offset,lines[node.end_lineno-1]+node.end_col_offset)
    pieces=[];cursor=0
    for old,new in sorted(((o,n) for o,n in zip(before,after) if o.value!=n.value or type(o.value) is not type(n.value)),key=lambda pair:span(pair[0],lines_before)):
        (start,end),(new_start,new_end)=span(old,lines_before),span(new,lines_after)
        old_text,new_text,old_digits,new_digits=raw[start:end],changed[new_start:new_end],str(old.value).encode(),str(new.value).encode()
        require(start>=cursor and old_text.count(old_digits)==1 and new_text==old_text.replace(old_digits,new_digits),'source changed outside the approved constants')
        pieces+=[raw[cursor:start],new_text];cursor=end
    require(b''.join(pieces+[raw[cursor:]])==changed,'source changed outside the approved constants')
def closed_integer_delta(raw,original_sha,edits):
    """Replace exact source fragments, each of which must occur exactly the stated number of times, and prove by the syntax tree
    that the result differs from the original in integer literals only and in exactly as many of them as the edits say."""
    require(hashlib.sha256(raw).hexdigest()==original_sha,'original source drift before a closed integer delta');changed=raw;expected=0
    for old,new,count,literals in edits:
        old,new=old.encode(),new.encode();require((old!=new or literals==0) and changed.count(old)==count,'closed delta fragment missing or repeated: '+old.decode());changed=changed.replace(old,new);expected+=count*literals
    def masked(source):
        tree=ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node,ast.Constant) and type(node.value) is int:node.value=0
        return ast.dump(tree,include_attributes=False)
    require(masked(raw)==masked(changed),'source changed outside integer literals')
    before,after=integer_literals(raw),integer_literals(changed);require(len(before)==len(after) and sum(a!=b for a,b in zip(before,after))==expected,'number of changed integer literals differs from the closed delta')
    exact_constant_spans(raw,changed)
    return changed
def closed(edits):
    """A fragment whose derived value equals the reviewed one (for example a wall of exactly 3,600 s against the reviewed one-hour
    freshness cap) is kept as a presence check: it must still occur exactly as often as stated, and it changes nothing."""
    return [(old,new,count,0 if old==new else literals) for old,new,count,literals in edits]
def swarm_gateway_bytes(raw,profile,model=None):
    p=swarm_numbers(profile,model);pairs=[('chat_attempts',40,p['chats']),('output_total',60000,p['output']),('embedding_attempts',64,p['embedding_attempts']),('embedding_texts',256,p['embedding_texts']),('embedding_total_bytes',131072,p['embedding_bytes']),('wall_seconds',2400,p['wall'])]
    edits=[(f'{name}: int = {old}',f'{name}: int = {new}',1,1) for name,old,new in pairs]+[(f'"{name}": {old},',f'"{name}": {new},',1,1) for name,old,new in pairs]
    # TMF2: the arrival-to-done and queue limits (default and reviewed maximum each) and the request byte limit follow the derivation.
    arrival,queue=p['gateway_arrival_s'],p['gateway_queue_s']
    edits+=[('client_seconds: int = 175',f'client_seconds: int = {arrival}',1,1),('"client_seconds": 175,',f'"client_seconds": {arrival},',1,1),
            ('queue_seconds: int = 170',f'queue_seconds: int = {queue}',1,1),('"queue_seconds": 170}',f'"queue_seconds": {queue}}}',1,1),
            ('request_bytes: int = 131072',f'request_bytes: int = {p["request_bytes"]}',1,1)]
    changed=closed_integer_delta(raw,GATEWAY_ORIGINAL_SHA,closed(edits));nodes=gateway_budget_nodes(ast.parse(changed));require([n.value for n in nodes]==[p['chats'],p['output'],p['chats'],p['output']],'swarm gateway budget mismatch');return changed
def swarm_run_bytes(raw,profile,model=None,original_sha=RUN_ORIGINAL_SHA,start_threshold=None):
    """The swarm's closed run-script numbers. Staged with a model, the original is the pinned src/ revision and the free-memory
    start threshold is the chosen model's (TMF3 outcome 5); 65 stays for the 27B, a presence check that changes nothing."""
    p=swarm_numbers(profile,model);wall=p['wall'];caller=p['caller_client_s']
    threshold=[] if start_threshold is None else [(START_FRAGMENT,f'({start_threshold} if pre else 30)',1,1)]
    # TMF2 addendum 1, ruling 1: the two caller timeouts are digit strings in the worker environment; closed_digit_delta proves that
    # only those two strings and the stated integers differ.
    # Addendum 2, R1: the observer's worker event buffer limit, derived from the largest wait event the profile can produce.
    return closed_digit_delta(raw,original_sha,closed(threshold+[('time.monotonic() + 2280',f'time.monotonic() + {p["worker_deadline_s"]}',1,1),
        ('self.buffer += data; require(len(self.buffer) <= 131072, "worker event byte limit")',f'self.buffer += data; require(len(self.buffer) <= {p["worker_event_bytes"]}, "worker event byte limit")',1,1),
        ('"MIROFISH_PREP_LLM_TIMEOUT": "180"',f'"MIROFISH_PREP_LLM_TIMEOUT": "{caller}"',1,1),('"MIROFISH_REPORT_LLM_TIMEOUT": "180"',f'"MIROFISH_REPORT_LLM_TIMEOUT": "{caller}"',1,1),
        ("'caller_timeout':180,'gateway_arrival_seconds':175,'queue_seconds':170",f"'caller_timeout':{caller},'gateway_arrival_seconds':{p['gateway_arrival_s']},'queue_seconds':{p['gateway_queue_s']}",1,3),
        ('<= 4, "actual generated participant count outside bound"',f'<= {p["agents_bound"]}, "actual generated participant count outside bound"',1,1),
        ('"max_rounds": 1,',f'"max_rounds": {p["rounds"]},',1,1),("'wall_seconds':2400}",f"'wall_seconds':{wall}}}",1,1),
        ('deadline=start+2400',f'deadline=start+{wall}',1,1),("receipt['wall_seconds']<=2400",f"receipt['wall_seconds']<={wall}",1,1),
        ('research_only=False,max_age_seconds=3600)',f'research_only=False,max_age_seconds={wall})',1,1)]))
def swarm_startup_bytes(raw,profile,model=None):
    p=swarm_numbers(profile,model)
    return closed_integer_delta(raw,STARTUP_ORIGINAL_SHA,closed([('(30 if preflight else 2400)',f'(30 if preflight else {p["wall"]})',1,1),
        ('MAX_RECORDS=6001',f'MAX_RECORDS={p["history_records"]}',1,1),('MAX_BYTES=16*1024*1024',f'MAX_BYTES={p["history_mib"]}*1024*1024',1,1)]))
def swarm_producer_bytes(relative,raw,profile,model=None):
    p=swarm_numbers(profile,model);original,edits=SWARM_PRODUCER[relative]
    return closed_integer_delta(raw,original,closed([(old,new.format(agents=p['agents_bound'],rounds=p['rounds'],wall=p['wall'],caller=p['caller_output'],native=p['native_output'],embedding_client=p['embedding_client_s']),1,1) for old,new in edits]))
def swarm_memory_bytes(raw,profile):
    """The reviewed whole-unit memory in place of the canonical owned_context_budget.py, with the profile's three integers."""
    p=swarm_numbers(profile);require(hashlib.sha256(raw).hexdigest()==MEMORY_CANONICAL_SHA,'original source drift: canonical owned_context_budget.py')
    reviewed=snapshot(MEMORY_FILE);require(hashlib.sha256(reviewed).hexdigest()==MEMORY_REPLACEMENT_SHA,'reviewed memory replacement changed')
    return closed_integer_delta(reviewed,MEMORY_REPLACEMENT_SHA,[(old,new.format(agents=p['agents_bound'],native=p['native_output'],budget=p['memory_budget']),1,1) for old,new in MEMORY_EDITS])
DIGIT_STRING_KEYS=('MIROFISH_PREP_LLM_TIMEOUT','MIROFISH_REPORT_LLM_TIMEOUT')
def closed_digit_delta(raw,original_sha,edits,string_keys=DIGIT_STRING_KEYS):
    """closed_integer_delta, plus the two caller timeout values, which the worker environment holds as digit strings (TMF2
    addendum 1). Same exact-fragment rule; then the two syntax trees must be identical except for constants, every changed constant
    must be decimal digits before and after (an integer stays an integer, a string stays a string), a changed string must be the
    value of one of `string_keys` in a dict literal, and exactly as many constants may change as the edits say."""
    require(hashlib.sha256(raw).hexdigest()==original_sha,'original source drift before a closed digit delta');changed=raw;expected=0
    for old,new,count,literals in edits:
        old,new=old.encode(),new.encode();require((old!=new or literals==0) and changed.count(old)==count,'closed delta fragment missing or repeated: '+old.decode());changed=changed.replace(old,new);expected+=count*literals
    before,after=ast.parse(raw),ast.parse(changed)
    def masked(tree):
        tree=copy.deepcopy(tree)
        for node in ast.walk(tree):
            if isinstance(node,ast.Constant):node.value='<'+type(node.value).__name__+'>'
        return ast.dump(tree,include_attributes=False)
    require(masked(before)==masked(after),'source changed outside constants, or a constant changed type')
    keys={}
    for node in ast.walk(after):
        if isinstance(node,ast.Dict):
            for key,value in zip(node.keys,node.values):
                if isinstance(key,ast.Constant) and isinstance(value,ast.Constant):keys[id(value)]=key.value
    digits=lambda value:type(value) is int or (type(value) is str and value.isascii() and value.isdigit())
    differing=0
    for old,new in zip([n for n in ast.walk(before) if isinstance(n,ast.Constant)],[n for n in ast.walk(after) if isinstance(n,ast.Constant)]):
        if old.value==new.value:continue
        require(digits(old.value) and digits(new.value),'a changed constant is not decimal digits before and after')
        require(type(new.value) is int or keys.get(id(new)) in string_keys,'a changed digit string is not one of the two caller timeouts')
        differing+=1
    require(differing==expected,'number of changed constants differs from the closed delta')
    exact_constant_spans(raw,changed)
    return changed
def swarm_scenario(profile):
    p=SWARM[profile];raw=snapshot(Path(__file__).with_name(p['scenario']));require(hashlib.sha256(raw).hexdigest()==p['scenario_sha256'],'swarm scenario changed')
    text=raw.decode();require(text.startswith('HYPOTHETICAL RESEARCH SCENARIO, NOT CURRENT MARKET NEWS.') and sum(line.startswith('- ') for line in text.splitlines())==p['actors'],'swarm scenario must be labelled hypothetical and name exactly the closed number of participants')
    seal={'recorded_at_utc':p['scenario_recorded_utc'],'sha256':p['scenario_sha256'],'bytes':len(raw),'kind':'Pre-generation hypothetical public qualification input, not market observations','actor_count':p['actors'],'actual_model_calls_so_far':0}
    return raw,(json.dumps(seal,indent=2)+'\n').encode()

SELECTOR_REL='owned-source/producer/backend/scripts/run_parallel_simulation.py'
SELECTOR_ORIGINAL_SHA='8738ca61335ca7f4f206a0672c062bead8734ef1bf1bc17a601000e728538c40'
def selector_assignments(tree):
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='get_active_agents_for_round');result=[]
    for name in ['target_count','active_hours','activity_level']:
        matches=[n for n in ast.walk(function) if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id==name];require(len(matches)==1,'exact selector assignment missing');result.extend(matches)
    return result

def swarm_selector_bytes(raw,profile,model=None):
    """Every configured agent takes part in every round (three selector right-hand sides), and at most `semaphore` requests are in
    flight per platform (two integers); the child's own round and participant caps follow the profile (two integers). Agents'
    profiles, prompts and what they say stay the model's own."""
    step=coverage_selector_bytes(raw,['len(agent_configs)','list(range(24))','1.0'])
    p=swarm_numbers(profile,model)
    return closed_integer_delta(step,hashlib.sha256(step).hexdigest(),closed([('semaphore=30,',f'semaphore={p["semaphore"]},',2,1),
        ('timeout=180,',f'timeout={p["agent_client_s"]},',1,1),  # the agent client outlasts the gateway arrival-to-done limit by 30 s
        ('or args.max_rounds != 1 or args.twitter_only',f'or args.max_rounds != {p["rounds"]} or args.twitter_only',1,1),
        ("len(config['agent_configs']) <= 4):",f"len(config['agent_configs']) <= {p['agents_bound']}):",1,1),
        ("'max_tokens': 512,",f"'max_tokens': {p['native_output']},",1,1)]))
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

def src_revision(name,expected):
    """One pinned src/ revision (TMF3 addendum 1): its exact bytes, or a refusal naming the file whose pin was not moved."""
    path=SRC/name;raw=snapshot(path);require(hashlib.sha256(raw).hexdigest()==expected,'src revision changed since it was pinned (move the pin deliberately): '+str(path));return raw
def model_profiles(raw=None):
    """The built-in checkpoint profiles, read from the pinned module's own source text (never retyped): its MODEL_PROFILES
    entries, TOKENIZER_FILES and DEFAULT_MODEL_PROFILE. Only constants, dicts, None, Path('...') and `root / '...'` evaluate."""
    raw=raw if raw is not None else src_revision('mirofish_native_model.py',MODEL_SRC_SHA);tree=ast.parse(raw);names={}
    def value(node):
        if isinstance(node,ast.Constant):return node.value
        if isinstance(node,ast.Dict):return {value(k):value(v) for k,v in zip(node.keys,node.values)}
        if isinstance(node,ast.Tuple):return tuple(value(x) for x in node.elts)
        if isinstance(node,ast.Name) and node.id in names:return names[node.id]
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='Path' and len(node.args)==1 and not node.keywords:return Path(value(node.args[0]))
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Div):
            left=value(node.left);right=value(node.right);require(isinstance(left,Path) and isinstance(right,str),'unexpected path expression in the model profiles');return left/right
        raise RuntimeError('unexpected expression in the model profiles: '+ast.unparse(node))
    for node in tree.body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('_MODEL_ROOT','MODEL_PROFILES','DEFAULT_MODEL_PROFILE','TOKENIZER_FILES'):
            require(node.targets[0].id not in names,'model profile constant assigned twice');names[node.targets[0].id]=value(node.value)
    require(set(names)=={'_MODEL_ROOT','MODEL_PROFILES','DEFAULT_MODEL_PROFILE','TOKENIZER_FILES'},'model profile constants missing from the pinned module')
    return names['MODEL_PROFILES'],names['TOKENIZER_FILES'],names['DEFAULT_MODEL_PROFILE']
def model_choice(name,base_manifest,registry=None):
    """The chosen built-in profile and its pins, derived from the pinned module (names and paths) and the reviewed hashes (the
    base manifest's for the 27B, TOKENIZER_PINS otherwise). Refuses, in one line, an unknown name and a registry-only profile."""
    profiles,files,default=model_profiles();require(tuple(profiles)==BUILTIN_MODELS and default==BUILTIN_MODELS[0],'the built-in profiles of the pinned module changed')
    if name not in BUILTIN_MODELS:
        registry=MODEL_REGISTRY if registry is None else registry;rows=json.loads(Path(registry).read_text()).get('profiles',{}) if Path(registry).is_file() else {}
        require(name not in rows or rows[name].get('native'),'model profile %r is registered for HTTP backends only; the native pipeline needs a pinned GGUF checkpoint'%name)
        require(name not in rows,'model profile %r is registry-only; only the built-in profiles of the pinned src/ module can be staged'%name)
        raise RuntimeError('unknown model profile %r; expected one of %s'%(name,sorted(profiles)))
    entry=profiles[name];base_rows=base_manifest['model_files'];require(len(base_rows)==len(files)+1,'base model pin count changed')
    pins=dict(base_manifest['model_tokenizer']) if name==default else TOKENIZER_PINS.get(name)
    require(pins is not None and set(pins)==set(files),'no reviewed tokenizer pins for model profile %r'%name)
    rows=[{'bytes':None,'path':str(entry['tokenizer_root']/f),'role':base_rows[0]['role'],'sha256':pins[f]} for f in files]
    rows.append({'bytes':entry['weight_bytes'],'path':str(entry['weight_file']),'role':base_rows[-1]['role'],'sha256':entry['weight_sha256']})
    require(entry['gguf_selection']['maxContextLength']>=LIMITS.WINDOW and entry['gguf_selection']['sizeBytes']==entry['weight_bytes'] and entry['gguf_selection']['sourceSha256']==entry['weight_sha256'],'model profile %r does not support the 32,768 window or its selection disagrees with its pins'%name)
    return {'name':name,'default':name==default,'entry':entry,'tokenizer_files':files,'model_files':rows,'model_tokenizer':{f:pins[f] for f in files},
            'paths':{Path(row['path']) for row in rows},'start_threshold':LIMITS.start_threshold(name,entry['weight_bytes'])}
def verify_model_files(choice,verified=()):
    """Every pinned model file is a regular file with its pinned hash (and the GGUF its pinned size); a file inputs() already
    verified against the same pin is not hashed twice. The tokenizer origin's config names the profile's architecture and a
    context of at least the window. Fills in the tokenizer files' sizes."""
    done={(row['path'],row['sha256']) for row in verified}
    for row in choice['model_files']:
        p=Path(row['path']);require(p.is_file() and not p.is_symlink(),'model file missing: '+str(p))
        if row['bytes'] is not None:require(p.stat().st_size==row['bytes'],'model file does not match its pinned size: '+str(p))
        require((row['path'],row['sha256']) in done or sha(p)==row['sha256'],'model file does not match its pin: '+str(p))
        if row['bytes'] is None:row['bytes']=p.stat().st_size
    config=json.loads((choice['entry']['tokenizer_root']/'config.json').read_bytes())
    require(config.get('model_type')==choice['entry']['tokenizer_model_type'] and config.get('max_position_embeddings',0)>=LIMITS.WINDOW,'model tokenizer origin does not name the profile architecture or the 32,768 window: '+choice['name'])
    return choice
def selector_bytes(name):
    """The selector, written by this tool as exact bytes (addendum 2, C1), never copied from src/."""
    require(name in BUILTIN_MODELS,'the selector names a built-in profile only');return ('{"profile": "'+name+'"}\n').encode()
def model_policy(text,choice,reference):
    """The observer policy for the chosen checkpoint: the reviewed policy with its two checkpoint rules (the tokenizer origin
    folder and the GGUF file) naming the chosen model's paths, each exactly once; nothing else changes."""
    out=text
    for old,new in [(reference['entry']['tokenizer_root'],choice['entry']['tokenizer_root']),(reference['entry']['weight_file'],choice['entry']['weight_file'])]:
        old_rule='"'+str(old)+'"';require(out.count(old_rule)==1,'observer policy checkpoint rule missing or ambiguous: '+str(old));out=out.replace(old_rule,'"'+str(new)+'"')
    return out
def src_observer_policy(work,proxy_port,graph_port):
    """The observer policy as the staged src/ run script itself generates it, in a fresh interpreter whose only staged modules
    are the observer entry's (this process holds the W16 modules under the same names)."""
    import subprocess
    entry=Path(work)/'owned-source/observer-entry';code='import sys;sys.path.insert(0,sys.argv[1]);import mirofish_native_run as r;sys.stdout.write(r.observer_policy(sys.argv[2],int(sys.argv[3]),int(sys.argv[4])))'
    done=subprocess.run([sys.executable,'-B','-c',code,str(entry),str(work),str(proxy_port),str(graph_port)],cwd=str(entry),env={'PATH':'/usr/bin:/bin','TMPDIR':os.environ.get('TMPDIR','/tmp')},capture_output=True,text=True,timeout=120)
    require(done.returncode==0,'staged src run script could not generate the observer policy: '+done.stderr.strip()[-400:]);return done.stdout
def model_helper_text(text,choice,reference):
    """The SDK helper for the chosen checkpoint: the reviewed helper with its checkpoint selection, weight size and weight hash
    naming the chosen model's, each exactly once; the staged model module must generate exactly this text."""
    out=text
    for old,new in [(json.dumps(reference['entry']['gguf_selection'],sort_keys=True),json.dumps(choice['entry']['gguf_selection'],sort_keys=True)),
                    ('weightSize=%d,'%reference['entry']['weight_bytes'],'weightSize=%d,'%choice['entry']['weight_bytes']),("weightSha='%s'"%reference['entry']['weight_sha256'],"weightSha='%s'"%choice['entry']['weight_sha256'])]:
        require(out.count(old)==1,'SDK helper checkpoint text missing or ambiguous');out=out.replace(old,new)
    return out

def scaffold(work):
    for relative in EMPTY_SCAFFOLDS:
        path=Path(work)/relative;path.mkdir(parents=True,exist_ok=True);require(not any(path.iterdir()),'runtime scaffold must be empty: '+relative)

def stage(work,data,profile='compact40',sdk_port=ORIGINAL_SDK_PORT,ssd_device=ORIGINAL_CACHE_DEVICE,model=None):
    require(profile in PROFILES,'unknown closed staging profile');moved=sdk_port!=ORIGINAL_SDK_PORT;remounted=ssd_device!=ORIGINAL_CACHE_DEVICE
    # model: None (the W16 bytes, no selector) or a verified model_choice() (the pinned src/ revisions plus the selector).
    require(model is None or (profile in SWARM and isinstance(model,dict) and all(row['bytes'] is not None for row in model['model_files'])),'a model is staged only with a swarm profile and verified pins')
    model_name=model['name'] if model else None
    if model:src_model,src_run=src_revision('mirofish_native_model.py',MODEL_SRC_SHA),src_revision('mirofish_native_run.py',RUN_SRC_SHA)
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
                if model and str(relative) in MODEL_COPIES:
                    target=work/relative;changed=sdk_port_bytes(src_model,sdk_port,MODEL_SRC_SHA) if moved else src_model;target.chmod(0o644);target.write_bytes(changed);target.chmod(0o444);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='PINNED_SRC_REVISION_MODEL_MODULE',source_sha256=row['sha256'],src_path=str(SRC/'mirofish_native_model.py'),src_sha256=MODEL_SRC_SHA,sha256=sha(target),integer_change=[ORIGINAL_SDK_PORT,sdk_port] if moved else [])
                if moved and not model and str(relative) in MODEL_COPIES:
                    target=work/relative;changed=sdk_port_bytes(target.read_bytes(),sdk_port);target.chmod(0o644);target.write_bytes(changed);target.chmod(0o444);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='CLOSED_LIVE_SDK_PORT_ONE_INTEGER',source_sha256=row['sha256'],sha256=sha(target),integer_change=[ORIGINAL_SDK_PORT,sdk_port])
                if moved and not model and str(relative)==SDK_HELPER_REL:
                    target=work/relative;mode=target.stat().st_mode&0o777;changed=sdk_port_helper(target.read_text(),sdk_port);target.chmod(0o644);target.write_text(changed);target.chmod(mode);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='CLOSED_LIVE_SDK_PORT_HELPER_TEXT',source_sha256=row['sha256'],sha256=sha(target))
                if profile in SWARM:
                    closed=None
                    if str(relative) in GATEWAY_COPIES:closed=('gateway',lambda raw,p:swarm_gateway_bytes(raw,p,model_name))
                    elif str(relative) in RUN_COPIES:closed=('run',lambda raw,p:swarm_run_bytes(src_run,p,model_name,RUN_SRC_SHA,model['start_threshold']) if model else swarm_run_bytes(raw,p))
                    elif str(relative) in STARTUP_COPIES:closed=('startup',lambda raw,p:swarm_startup_bytes(raw,p,model_name))
                    elif str(relative) in SWARM_PRODUCER:closed=('producer:'+Path(str(relative)).name,lambda raw,p,key=str(relative):swarm_producer_bytes(key,raw,p,model_name))
                    if closed:
                        target=work/relative;changed=closed[1](target.read_bytes(),profile);target.chmod(0o644);target.write_bytes(changed);target.chmod(0o444);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='REVIEWED_CLOSED_SWARM_INTEGER_DELTA',file=closed[0],source_sha256=row['sha256'],sha256=sha(target))
                        if model and closed[0]=='run':ancestry[-1].update(src_path=str(SRC/'mirofish_native_run.py'),src_sha256=RUN_SRC_SHA,start_threshold=[65,model['start_threshold']])
                    if str(relative)==MEMORY_REL:
                        target=work/relative;changed=swarm_memory_bytes(target.read_bytes(),profile);target.chmod(0o644);target.write_bytes(changed);target.chmod(0o444);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='REVIEWED_MEMORY_REPLACEMENT_THEN_CLOSED_INTEGERS',canonical_sha256=MEMORY_CANONICAL_SHA,replacement_path=str(MEMORY_FILE),replacement_sha256=MEMORY_REPLACEMENT_SHA,source_sha256=row['sha256'],sha256=sha(target))
                    if str(relative)==SELECTOR_REL:
                        target=work/relative;changed=swarm_selector_bytes(target.read_bytes(),profile,model_name);target.chmod(0o644);target.write_bytes(changed);target.chmod(0o444);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='SWARM_FULL_PARTICIPATION_THREE_RHS_AND_SIX_CLOSED_INTEGERS',source_sha256=row['sha256'],sha256=sha(target))
                    if str(relative) in SCENARIO_COPIES or str(relative)==SCENARIO_SEAL_REL:
                        scenario,seal=swarm_scenario(profile);target=work/relative;mode=target.stat().st_mode&0o777;target.chmod(0o644);target.write_bytes(seal if str(relative)==SCENARIO_SEAL_REL else scenario);target.chmod(mode);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='SWARM_INVENTED_SCENARIO_REPLACES_QUALIFICATION_INPUT',source_sha256=row['sha256'],sha256=sha(target))
                if profile=='report64_coverage' and str(relative)==SELECTOR_REL:
                    target=work/relative;changed=coverage_selector_bytes(target.read_bytes());target.chmod(0o644);target.write_bytes(changed);target.chmod(0o444);pins[-1]['sha256']=sha(target);ancestry[-1].update(kind='EXPLICIT_BOUNDED_NATIVE_COVERAGE_QUALIFICATION_STIMULUS_THREE_RHS',source_sha256=row['sha256'],sha256=sha(target))
                if remounted and str(relative) in STARTUP_COPIES:  # after any swarm delta of the same copy; a swarm row keeps its kind
                    target=work/relative;raw=target.read_bytes();changed=ssd_device_bytes(raw,ssd_device,STARTUP_ORIGINAL_SHA if profile not in SWARM else hashlib.sha256(raw).hexdigest());target.chmod(0o644);target.write_bytes(changed);target.chmod(0o444);pins[-1]['sha256']=sha(target)
                    if ancestry[-1]['kind']=='EXACT_ACCEPTED_W16_BYTES':ancestry[-1].update(kind='CLOSED_LIVE_SSD_DEVICE_ONE_INTEGER',source_sha256=row['sha256'])
                    ancestry[-1].update(sha256=sha(target),ssd_device_change=[ORIGINAL_CACHE_DEVICE,ssd_device])
        else:pins.append(copy.deepcopy(row))
    require(sum(row['kind']=='CLOSED_LIVE_SDK_PORT_HELPER_TEXT' for row in ancestry)==(1 if moved and not model else 0),'SDK helper port delta must touch exactly the one helper script')
    selector_pins=[]
    if model:
        reference=model_choice(LIMITS.DEFAULT_MODEL,manifest)
        for relative in sorted(SELECTOR_COPIES):
            target=work/relative;require(target.parent.is_dir() and not target.exists() and (target.parent/'mirofish_native_model.py').is_file(),'selector must sit beside a staged model copy: '+relative);target.write_bytes(selector_bytes(model_name));target.chmod(0o444);selector_pins.append({'path':str(target),'sha256':sha(target)})
        helper=work/SDK_HELPER_REL;reviewed=helper.read_text();reviewed=sdk_port_helper(reviewed,sdk_port) if moved else reviewed;helper.chmod(0o644);helper.write_text(model_helper_text(reviewed,model,reference));helper.chmod(0o444)
        rows=[row for row in ancestry if row['target']==str(helper)];require(len(rows)==1,'SDK helper ancestry');rows[0].update(kind='PINNED_SRC_REVISION_MODEL_HELPER',sha256=sha(helper))
        pins[[i for i,row in enumerate(pins) if row['path']==str(helper)][0]]['sha256']=sha(helper)
        staged_helper_matches_model(work,sdk_port,model_name);pins.extend(selector_pins)
    elif moved:staged_helper_matches_model(work,sdk_port)
    scaffold(work)  # Explicit empty runtime directories; never copy historical logs/state.
    native=helpers['mirofish_native_run'];native.graph_configuration(work,manifest['graph_port'])
    generated={'builder/observer.sb':native.observer_policy(work,manifest['proxy_port'],manifest['graph_port']),'builder/graph.sb':native.graph_policy(work,manifest['graph_port']),'builder/worker.sb':native.worker_policy(work/'owned-source/producer',work/'owned-source/worker-entry',manifest['proxy_port'],manifest['graph_port'])}
    if moved:generated['builder/observer.sb']=sdk_port_policy(generated['builder/observer.sb'],sdk_port)
    if model:
        generated['builder/observer.sb']=model_policy(generated['builder/observer.sb'],model,reference)
        require(src_observer_policy(work,manifest['proxy_port'],manifest['graph_port'])==generated['builder/observer.sb'],'observer policy differs from what the staged src/ run script generates')
    for relative,text in generated.items():(work/relative).write_text(text)
    for relative in sorted(REGENERATED):
        target=work/relative;require(str(W16) not in target.read_text(),'stale W16 policy/config path');pins.append({'path':str(target),'sha256':sha(target)});ancestry.append({'source':str(W16/relative),'target':str(target),'sha256':sha(target),'kind':'REGENERATED_BY_EXACT_ACCEPTED_FUNCTION'})
    evidence=work/'builder/metadata-qualification';evidence.mkdir();evidence_files={'base-candidate-manifest.json':base_raw,'metadata-result.json':receipt_raw,'root-metadata-acceptance.json':acceptance_raw,'supported-controls.json':controls}
    for row,raw in zip(receipt['observations'],observations):evidence_files[row['case']+'-actual-sdk.json']=raw
    sizing=snapshot(RESUME/'recon/prep-length-cause-and-contract.json');require(hashlib.sha256(sizing).hexdigest()=='619a1360b19353f30a6628142f040c43313162cddcd87d79da15fb3fa29bc854','sizing contract changed');evidence_files['compact-ontology-sizing-contract.json']=sizing
    for name,raw in evidence_files.items():target=evidence/name;target.write_bytes(raw);pins.append({'path':str(target),'sha256':sha(target)})
    # Preserve copied proof manifests as historical ancestry; never rewrite their embedded old paths.
    staging_source=work/'builder/staging-source';staging_source.mkdir()
    for name in ['mirofish_native_stage.py','test_mirofish_native_stage.py','test_mirofish_event_boundary.py','mirofish_acknowledged_revisions.json']+(['mirofish_swarm_limits.py','test_mirofish_swarm_limits.py','mirofish_owned_agent_memory.py','test_mirofish_owned_agent_memory.py'] if profile in SWARM else []):
        target=staging_source/name;target.write_bytes(Path(__file__).with_name(name).read_bytes());pins.append({'path':str(target),'sha256':sha(target)})
    owned_count=sum(1 for row in ancestry if Path(row['source']).is_relative_to(W16/'owned-source'));exact_owned=sum(1 for row in ancestry if row['kind']=='EXACT_ACCEPTED_W16_BYTES' and Path(row['source']).is_relative_to(W16/'owned-source'));owned_gateway=sum(1 for row in ancestry if row['kind']=='REVIEWED_FIXED_REPORT64_FOUR_INTEGER_DELTA' and Path(row['source']).is_relative_to(W16/'owned-source'));gateway_deltas=sum(row['kind']=='REVIEWED_FIXED_REPORT64_FOUR_INTEGER_DELTA' for row in ancestry);swarm_closed=[row for row in ancestry if row['kind']=='REVIEWED_CLOSED_SWARM_INTEGER_DELTA'];swarm_scenario_rows=sum(row['kind']=='SWARM_INVENTED_SCENARIO_REPLACES_QUALIFICATION_INPUT' for row in ancestry);swarm_selector=sum(row['kind']=='SWARM_FULL_PARTICIPATION_THREE_RHS_AND_SIX_CLOSED_INTEGERS' for row in ancestry);memory_rows=sum(row['kind']=='REVIEWED_MEMORY_REPLACEMENT_THEN_CLOSED_INTEGERS' for row in ancestry)
    port_rows=[row for row in ancestry if row['kind']=='CLOSED_LIVE_SDK_PORT_ONE_INTEGER'];require(len(port_rows)==(2 if moved and not model else 0),'SDK port delta must touch exactly the two model copies');port_owned=sum(Path(row['source']).is_relative_to(W16/'owned-source') for row in port_rows)
    src_model_rows=[row for row in ancestry if row['kind']=='PINNED_SRC_REVISION_MODEL_MODULE'];require(len(src_model_rows)==(len(MODEL_COPIES) if model else 0),'the src/ model revision must replace exactly the model copies');src_owned=sum(Path(row['source']).is_relative_to(W16/'owned-source') for row in src_model_rows)
    src_run_rows=[row for row in ancestry if 'src_sha256' in row and row['src_sha256']==RUN_SRC_SHA];require(len(src_run_rows)==(len(RUN_COPIES) if model else 0),'the src/ run revision must replace exactly the run copies')
    device_rows=[row for row in ancestry if 'ssd_device_change' in row];require(len(device_rows)==(len(STARTUP_COPIES) if remounted else 0),'SSD device delta must touch exactly the three startup copies');device_owned=sum(row['kind']=='CLOSED_LIVE_SSD_DEVICE_ONE_INTEGER' and Path(row['source']).is_relative_to(W16/'owned-source') for row in device_rows)
    if profile in SWARM:require(owned_count==72 and exact_owned+port_owned+device_owned+src_owned==55 and sorted(row['file'] for row in swarm_closed)==sorted(['gateway']*3+['run']*3+['startup']*3+['producer:'+Path(k).name for k in SWARM_PRODUCER]) and swarm_selector==1 and memory_rows==1 and swarm_scenario_rows==3 and gateway_deltas==0,'swarm source ancestry changed')
    else:require(owned_count==72 and exact_owned+port_owned+device_owned==({'compact40':70,'report64':68,'report64_coverage':67}[profile]) and owned_gateway==(0 if profile=='compact40' else 2) and gateway_deltas==(0 if profile=='compact40' else 3) and not swarm_closed and not swarm_scenario_rows and not swarm_selector and not memory_rows,'profile source ancestry changed');require(len({sha(work/relative) for relative in GATEWAY_COPIES})==1,'gateway copies inconsistent')
    lineage={'manifest_pinned_owned_source_files':owned_count,'exact_owned_source_files':exact_owned,'reviewed_prompt_only_deltas':1,'profile':profile,'gateway_budget_deltas_total':gateway_deltas,'gateway_budget_deltas_owned':owned_gateway,'qualification_selector_deltas':sum(row['kind']=='EXPLICIT_BOUNDED_NATIVE_COVERAGE_QUALIFICATION_STIMULUS_THREE_RHS' for row in ancestry),'swarm_integer_delta_files':len(swarm_closed),'sdk_port':{'reviewed':ORIGINAL_SDK_PORT,'staged':sdk_port,'model_copies_changed':len(port_rows)+(len(src_model_rows) if moved else 0),'helper_script_changed':moved,'policy_rule_changed':moved,'why':'the local model server chooses a new internal SDK port each time its app restarts; a candidate is usable only while the server that was running at staging keeps running'},'ssd_device':{'reviewed':ORIGINAL_CACHE_DEVICE,'staged':ssd_device,'startup_copies_changed':len(device_rows),'route_record_changed':False,'why':'macOS numbers a volume when it mounts it, so a reboot or remount can change the SSD device number the launcher checks the prompt-cache target against; staging read the live number of the mounted SSD itself. builder/shared-cache-storage-route.json is the dated record of the 2026-11-07 routing and nothing reads it, so it keeps its 2026-11-07 number'},'swarm_selector_deltas':swarm_selector,'swarm_scenario_files_replaced':swarm_scenario_rows,'runtime_scaffolds':EMPTY_SCAFFOLDS,'historical_runtime_log_excluded':'owned-source/producer/backend/logs/2026-11-07.log (not manifest-pinned source)','base_manifest_sha256':BASE_SHA,'metadata_receipt_sha256':METADATA_SHA,'source_copies':ancestry,'canonical_revision_witness':canonical_revision_witness(EXTRACTOR_CANONICAL,EXTRACTOR_PARENT_SHA),'historical_proof_policy':'Copied old manifests remain exact historical proof, not new target claims. Operational file paths in this derived manifest and all four generated policies/configs point at new work.','original713_and318':'713 selected pins unchanged; the one canonical extractor row in318 uses the exact closed witness; '+str(len(ACKNOWLEDGED_WITNESSES))+' other original files carry an owner-acknowledged later revision that is NOT copied into this candidate (the reviewed W16 bytes are); the remaining original pins are unchanged','owner_acknowledged_canonical_revisions':{'list_sha256':ACKNOWLEDGED_SHA,'count':len(ACKNOWLEDGED_WITNESSES),'copied_into_candidate':False,'witnesses':list(ACKNOWLEDGED_WITNESSES)},'no_model_graph_worker_paper_or_broker_actions':True}
    if model:lineage['model_profile']={'name':model_name,'src_revisions':SRC_REVISION_NOTE,'model_copies_from_src':len(src_model_rows),'run_copies_from_src':len(src_run_rows),'selector_files':selector_pins}
    lineage_path=work/'builder/staging-ancestry.json';write(lineage_path,lineage);pins.append({'path':str(lineage_path),'sha256':sha(lineage_path)})
    derived=copy.deepcopy(manifest);derived['staging_profile']={'name':profile,'chat_attempts':PROFILES[profile][0],'requested_output_total':PROFILES[profile][1],'caller_output':1536,'native_probe_output':512,'global_output_per_call':4096,'context':32768,'coverage':'expected finite report coverage, not guaranteed; full report semantics unchanged','participation_stimulus':'EXPLICIT_BOUNDED_CARDINALITY_COVERAGE_QUALIFICATION_ONLY_NOT_NATURALISTIC_FORECAST' if profile=='report64_coverage' else 'FULL_PARTICIPATION_EVERY_ROUND_SWARM_LOAD_NOT_NATURALISTIC_FORECAST' if profile in SWARM else 'UNCHANGED_STOCHASTIC_SCHEDULE'};derived['files']=pins;derived['task_caller_budget']['global_gateway']=[PROFILES[profile][0],PROFILES[profile][1],4096];derived['task_caller_budget']['maximum_reserved']=2*512+(PROFILES[profile][0]-2)*1536;derived['known_blockers']=[];derived['launch_available']=True;
    if profile in SWARM:
        p=swarm_numbers(profile,model_name);derived['uniform_policy']=dict(derived.get('uniform_policy') or {},wall_seconds=p['wall'],caller_timeout=p['caller_client_s'],gateway_arrival_seconds=p['gateway_arrival_s'],queue_seconds=p['gateway_queue_s'])
        derived['staging_profile'].update(caller_output=p['caller_output'],native_output=p['native_output'],agent_memory_tokens=p['memory_budget']);derived['task_caller_budget']['limit']=p['caller_output'];derived['task_caller_budget']['maximum_reserved']=2*512+(p['chats']-2)*p['caller_output']
        derived['swarm_profile']=dict(p,name=profile,
            owner_decision='2026-11-11 go-ahead for a larger swarm profile; on 2026-11-15 the owner asked for the 2,048-token agent cap to go. The numbers are the lead\'s, derived from the measurements of the stopped run valedia-native-20261114-swarm48-b. Launching remains the owner\'s decision.',
            memory_replacement={'path':MEMORY_REL,'canonical_sha256':MEMORY_CANONICAL_SHA,'replacement':str(MEMORY_FILE),'replacement_sha256':MEMORY_REPLACEMENT_SHA,'staged_sha256':sha(work/MEMORY_REL),
                                'what_changes':MEMORY_WHAT_CHANGES,'decision':'Owner\'s instruction of 2026-11-15 that the cap is to go; the design and the numbers are the lead\'s (TMF2 addendum 1).',
                                'log_phrases':{'dropped_whole_units_info':'Owned memory dropped whole units','trimmed_newest_user_message_warning':'Owned memory trimmed the newest user message','refused_context_warning':'Owned memory refused the context','must_stay_zero':['Slicing into smaller chunks','Context truncation performed']}},
            budget_note=('PROVISIONAL: memory use at large prompts has never been measured on this Mac; this budget is to be set from the proving run (swarm8) readings, and swarm48 is not to be launched before they have been read.' if profile=='swarm48' else
                         'The tight 4,096-token budget is the point: it makes whole units drop in a real run and shows that such prompts pass the template admission, before a multi-hour run is spent.'),
            derivation={'source':'tools/mirofish_swarm_limits.py','window':'%.2f x memory budget + 1750 + 1024 <= 32768 ('%LIMITS.RATIO_ASSUMED+str(p['window_need'])+'); largest allowed '+str(p['largest_budget']),
                        'worst_single_call':'(32768 - reply cap)/188 + reply cap/23.6 + 27: agent '+str(p['agent_worst_s'])+' s, caller '+str(p['caller_worst_s'])+' s',
                        'call_slot':'worst single call x 1.10, rounded up to 30 s: agent '+str(p['agent_slot_s'])+' s, caller '+str(p['caller_slot_s'])+' s',
                        'gateway_arrival':'max(2 agent calls in flight x agent slot, 1 caller call x caller slot) = '+str(p['gateway_arrival_s'])+' s',
                        'gateway_queue':'(calls in flight - 1) x agent slot = '+str(p['gateway_queue_s'])+' s',
                        'clients':'gateway arrival + 30 s = '+str(p['agent_client_s'])+' s (agent, persona/config, report and embedding clients)',
                        'request_bytes':'32768 tokens x 5.28 bytes per token (largest measured) = '+str(p['request_bytes_need'])+' <= '+str(p['request_bytes'])+' <= SDK helper cap '+str(p['helper_request_cap'])+' - 4096',
                        'worker_events':'largest wait event = 8192 + 50 recent actions x (14336 text tokens x 144 bytes per token + 512) = '+str(p['wait_event_bound'])+'; limit = 1.1 x (that + 1), rounded up to a MiB = '+str(p['worker_event_bytes']),
                        'chats':'max((setup '+str(p['setup_chats'])+' + simulation '+str(p['sim_chats'])+' + report '+str(p['report_chats'])+') x 1.5, every call at the participant bound '+str(p['bound_chats'])+'), rounded up to 32 = '+str(p['chats']),
                        'output':'2 x 512 + (chats - 2) x 4096 = '+str(p['reserve_need'])+', rounded up to 100,000',
                        'wall':'('+p['model']+' speeds: '+LIMITS.MODELS[p['model']]['speed_source']+') 1.2 x (start 15 s + setup at the participant bound '+str(p['ceiling_setup_s'])+' s + simulation at the bound and full memory '+str(p['ceiling_sim_s'])+' s + report '+str(p['report_s'])+' s + cleanup 120 s), rounded up to 1800 s = '+str(p['wall'])+' s; expected '+str(p['expected_total_s'])+' s for '+str(p['expected_agents'])+' agents (named actors x 52/48)'},
            what_changed='Closed integers in the gateway limits (model calls, output, embeddings, wall clock, arrival-to-done '+str(p['gateway_arrival_s'])+' s, queue '+str(p['gateway_queue_s'])+' s, request bytes '+str(p['request_bytes'])+'), the observer\'s worker event limit ('+str(p['worker_event_bytes'])+' bytes), the supervisor and host deadlines and the host history, the participant bound and round count wherever the worker and the producer check them, the report check\'s freshness window (it spans the run), the agent client timeout and concurrency (one request in flight per platform), the embedding client timeout; two digit strings, the persona/config and report client timeouts ('+str(p['caller_client_s'])+' s, proven by closed_digit_delta); three selector right-hand sides so every configured agent acts in every round; the scenario input and its seal. Reply caps: 4,096 for personas, ontology, config and report text, 1,024 for an agent turn. The agent memory is the reviewed whole-unit memory with a budget of '+str(p['memory_budget'])+' library tokens (see memory_replacement). Why: every simulation agent\'s whole context had been 2,048 tokens, so the library shredded each feed into one-token chunks and the run of 2026-11-15 was stopped after five template refusals; every limit that had been sized for 3,000-token prompts is now derived from one block of measured constants. Unchanged: the 32,768 context, the 4,096 per-call output ceiling, the one dispatch slot, the 10 s context admission, the template admission, the poison-on-unknown rule, confinement and the paper-only handoff.',
            what_it_is_not='The scenario and its participants are invented, and every agent is made to take part in every round: this shows the pipeline at scale. It is not a forecast, not a naturalistic schedule and not evidence about any market.')
    derived['launch_authorized']=False;derived['source_status']='W16_COMPACT_ONTOLOGY_PLUS_CLOSED_'+profile.upper()+'_FOR_ROOT_GATED_QUALIFICATION';derived['intentional_canonical_source_revision']=canonical_revision_witness(EXTRACTOR_CANONICAL,EXTRACTOR_PARENT_SHA);derived['old_authority_policy']='Old v1 receipts/stores/hash3c053002 remain unchanged. New source extraction/replays are versioned derived artifacts, not new live proof or rewritten authority.';derived['qualification_stimulus']={'enabled':profile=='report64_coverage','scope':'Only three selector RHS: min(2,N) configured valid agents per platform, all24 hours, probability1.0; random.sample identities/order unchanged. Generated configs/prompts/agent content unchanged. No forced action, answer, direction, trade or weakening of >=2 actual completed200 native forwards. Naturalistic forecasting is not claimed.','selector_original_sha256':SELECTOR_ORIGINAL_SHA,'selector_staged_sha256':sha(work/SELECTOR_REL)};derived['metadata_reuse_impact']={'evidence':METADATA_SHA,'scope':'Format conformance reused only: same roles, tools, schemas, LLMClient, SDK/model/tokenizer; only declared ontology prompt content, opt-in four gateway budget integers and optional explicit qualification participation schedule differ; gateway format/guards unchanged, and every actual request still requires actual SDK/frozen comparison. No new model quality or prep success claimed.','ontology_change':{'source_sha256':ONTOLOGY_ORIGINAL_SHA,'staged_sha256':sha(work/ONTOLOGY_REL),'constant':'ONTOLOGY_SYSTEM_PROMPT','functions_and_other_constants':'AST_IDENTICAL'}};derived['derived_from']={'base_candidate_sha256':BASE_SHA,'metadata_receipt_sha256':METADATA_SHA,'removed_metadata_prerequisite':'ACTUAL_SUPPORTED_THREE_CASE_METADATA_ACCEPTED','moved_from_runtime_blockers_to_post_run':'NO_COMPLETED_GENUINE_NATIVE_AGENT_FORWARD_EVIDENCE_FOR_REPAIRED_CODE'};derived['qualification_status']={'native_forward':'PENDING_POST_RUN_ALL_NATIVE_CHATS_COMPLETED_DISPATCHED_200','fresh_report':'PENDING_POST_RUN_EXACT_CAPTURE_AND_VALIDATE','local_paper':'PENDING_GENUINE_EXTRACTED_SIGNAL_MAY_REFUSE_FIXED_BUY','assistant_final_continuation':'UNSUPPORTED','launch_available_means':'eligible for qualification only; separate expiring one-use root receipt required','model_selected_execution_or_profit':'NOT_CLAIMED'}
    derived['sdk_port_revision']={'reviewed':ORIGINAL_SDK_PORT,'staged':sdk_port}
    if model:
        derived['model_files']=copy.deepcopy(model['model_files']);derived['model_tokenizer']=dict(model['model_tokenizer']);pin_scope(derived['model_files'],model_paths=model['paths'])
        derived['model_profile']={'name':model_name,'source_files':SRC_REVISION_NOTE,
            'src_revisions':{'mirofish_native_model.py':{'path':str(SRC/'mirofish_native_model.py'),'sha256':MODEL_SRC_SHA,'copies':sorted(MODEL_COPIES)},'mirofish_native_run.py':{'path':str(SRC/'mirofish_native_run.py'),'sha256':RUN_SRC_SHA,'copies':sorted(RUN_COPIES)}},
            'selector':{'content':selector_bytes(model_name).decode(),'files':selector_pins,'registry_carried':False,'why_no_registry':'only the built-in profiles of the pinned module are staged; without the registry beside it the staged module knows exactly those'},
            'model_files':'derived from MODEL_PROFILES['+repr(model_name)+'] in the pinned module text: the five tokenizer-origin files under '+str(model['entry']['tokenizer_root'])+' and the GGUF '+str(model['entry']['weight_file'])+'; hashes from '+('the base manifest' if model['default'] else 'TOKENIZER_PINS in this tool and the profile\'s weight_sha256'),
            'sdk_helper':'the reviewed helper with its checkpoint selection, weight size and weight hash naming this model (each exactly once), equal to what the staged model module generates',
            'observer_policy':'the reviewed policy with its tokenizer-origin folder rule and GGUF file rule naming this model (each exactly once), equal to what the staged src/ run script generates',
            'start_threshold':{'percent_free':model['start_threshold'],'reviewed':65,'stop_floor':30,'rss_bound_gib':40,
                'rule':'30 + ceil(100 x (weight bytes + 6 GiB) / installed memory %d)'%LIMITS.INSTALLED_MEMORY_BYTES,
                'why':('The reviewed 65 stays for the 27B (the rule gives 67 for it).' if model['default'] else 'The reviewed 65 was sized for the 19 GB 27B checkpoint; for this '+str(model['entry']['weight_bytes'])+'-byte checkpoint the rule gives '+str(model['start_threshold'])+'. ')+' This is a re-derivation of a rule that was sized for another model, decided by the lead (TMF3); the 30% stop floor and the 40 GiB resident-memory bound are unchanged.'},
            'model_source_record':'builder/model-source-manifest.json is the W16 record of the 27B and is not read by the launcher; this candidate\'s checkpoint pins are model_files and model_tokenizer above.',
            'expected_s':p['expected_total_s'],'wall_s':p['wall'],'speeds':LIMITS.MODELS[model_name]['speed_source']}
        derived['staging_profile']['model']=model_name
        if not model['default']:
            # Addendum 2, C3: no borrowed evidence. The metadata receipt and the format-conformance claims rest on the 27B.
            derived['metadata_reuse_impact'].update(applies_to='qwen3.8-27b only',
                scope='NOT REUSED for '+model_name+'. Format conformance of this checkpoint with LM Studio\'s own rendering has NOT been shown; the first run is that test, and the run fails closed (template admission refusal) if the rendering differs. The metadata receipt and builder/metadata-qualification/ are the 27B\'s evidence, kept as ancestry only.')
            derived['derived_from']=dict(derived['derived_from'],removed_metadata_prerequisite='NOT_SHOWN_FOR_'+model_name.upper()+': the accepted three-case metadata is the 27B\'s; for this checkpoint the first run\'s template admission is the conformance test')
            source_txt=model['entry']['tokenizer_root']/'SOURCE.txt'
            derived['model_profile']['provenance']={'known_wrong':'mirofish_native_run.py near line 520 writes the 27B\'s revision identifiers (same_base_revision, GGUF_source_revision) into model-source-manifest.json for any profile; for '+model_name+' that provenance field is known to be wrong. A run on '+model_name+' is a pipeline proof, not evidence about that field.',
                'correct_source':{'tokenizer_origin':str(source_txt),'tokenizer_origin_text':source_txt.read_text() if source_txt.is_file() else None,'gguf':str(model['entry']['weight_file']),'gguf_bytes':model['entry']['weight_bytes'],'gguf_sha256':model['entry']['weight_sha256']}}
        require(not any(work.rglob(REGISTRY_NAME)),'a model registry reached the staged folder')
    derived['ssd_device_revision']={'reviewed':ORIGINAL_CACHE_DEVICE,'staged':ssd_device}
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
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--stage',action='store_true');parser.add_argument('--profile',choices=tuple(PROFILES),default='compact40');parser.add_argument('--work',type=Path);parser.add_argument('--model',help='stage on this built-in checkpoint profile (the pinned src/ revisions plus a selector); omit for the W16 bytes');args=parser.parse_args(argv);storage_guard();os.environ['TMPDIR']=str(BASE/'project/tmp')
    choice=None
    if args.model is not None:
        try:
            require(args.profile in SWARM,'--model applies to the swarm profiles only');choice=model_choice(args.model,decoded(W16/'builder/candidate-manifest.json',BASE_SHA)[0])
        except RuntimeError as refused:print('refused: '+str(refused),file=sys.stderr);return 2
    data=inputs()
    if choice is not None:
        try:verify_model_files(choice,data[0]['model_files'])
        except RuntimeError as refused:print('refused: '+str(refused),file=sys.stderr);return 2
    selected_work=args.work or {'compact40':WORK,'report64':REPORT64_WORK,'report64_coverage':COVERAGE_WORK,'swarm48':SWARM_WORK,'swarm8':SWARM8_WORK}[args.profile];result=stage(selected_work,data,args.profile,live_sdk_port(),live_ssd_device(),choice) if args.stage else {'status':'INPUTS_VERIFIED_NO_STAGE_NO_LOAD','base_sha256':BASE_SHA,'metadata_sha256':METADATA_SHA};print(json.dumps(result));return 0
if __name__=='__main__':raise SystemExit(main())
