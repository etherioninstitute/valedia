"""Trusted, fixed-local SDK ownership and full-template context admission.

No SDK client, model, tokenizer, or service is created at import. The helper is
outside the producer sandbox and supports only the frozen local checkpoint,
already-loaded embedding instance and metadata/tokenization/embedding operations.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import time
import uuid
from mirofish_native_gateway import NativeRefused, atomic, decode, encoded

# Checkpoint profiles. The active profile is named in mirofish_model_profile.json beside this file
# (written by `python -m valedia_trader.agents.mirofish_models use <name>`); without that file the
# checkpoint this pipeline was qualified on is used. A prepared candidate carries its own copy of the
# file, so a candidate can never change checkpoint after preparation. Each profile is pinned by byte
# size and SHA-256 and names the tokenizer origin its template admission is built from.
_MODEL_ROOT = Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space/models')
MODEL_PROFILES = {
    'qwen3.8-27b': {
        'tokenizer_root': _MODEL_ROOT/'Qwen3.8-27B-MLX-4bit',
        'weight_file': _MODEL_ROOT/'ggml-org/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf',
        'weight_bytes': 18973870528,
        'weight_sha256': 'c600de0300ae8a0eb3a6c0b8b5561b8b96f16bd2c863c2a66c42de29d391a747',
        'tokenizer_model_type': 'qwen3_5', 'tokenizer_quant_bits': 4,
        'gguf_selection': {'modelKey': 'qwen3.8-27b', 'path': 'ggml-org/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf', 'indexedModelIdentifier': 'ggml-org/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf', 'format': 'gguf', 'publisher': 'ggml-org', 'architecture': 'qwen35', 'quantization': {'name': 'Q4_K_M', 'bits': 4}, 'sizeBytes': 18973870528, 'deviceIdentifier': None, 'vision': False, 'trainedForToolUse': True, 'maxContextLength': 262144, 'sourceSha256': 'c600de0300ae8a0eb3a6c0b8b5561b8b96f16bd2c863c2a66c42de29d391a747'},
    },
    'qwen3-8b': {
        'tokenizer_root': _MODEL_ROOT/'Qwen3-8B-tokenizer',
        'weight_file': _MODEL_ROOT/'lmstudio-library/Qwen/Qwen3-8B/Qwen3-8B-Q4_K_M.gguf',
        'weight_bytes': 5225374496,
        'weight_sha256': 'a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f',
        'tokenizer_model_type': 'qwen3', 'tokenizer_quant_bits': None,
        'gguf_selection': {'modelKey': 'qwen3-8b', 'path': 'Qwen/Qwen3-8B/Qwen3-8B-Q4_K_M.gguf', 'indexedModelIdentifier': 'Qwen/Qwen3-8B/Qwen3-8B-Q4_K_M.gguf', 'format': 'gguf', 'publisher': 'Qwen', 'architecture': 'qwen3', 'quantization': {'name': 'Q4_K_M', 'bits': 4}, 'sizeBytes': 5225374496, 'deviceIdentifier': None, 'vision': False, 'trainedForToolUse': True, 'maxContextLength': 40960, 'sourceSha256': 'a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f'},
    },
}
DEFAULT_MODEL_PROFILE = 'qwen3.8-27b'
MODEL_PROFILE_FILE = Path(__file__).with_name('mirofish_model_profile.json')


def active_model_profile():
    name = DEFAULT_MODEL_PROFILE
    if MODEL_PROFILE_FILE.exists():
        name = json.loads(MODEL_PROFILE_FILE.read_text()).get('profile')
    if name not in MODEL_PROFILES:
        raise NativeRefused('unknown MiroFish model profile %r; expected one of %s' % (name, sorted(MODEL_PROFILES)))
    return name


ACTIVE_MODEL_PROFILE = active_model_profile()
_PROFILE = MODEL_PROFILES[ACTIVE_MODEL_PROFILE]
TOKENIZER_ROOT = _PROFILE['tokenizer_root']
WEIGHT_FILE = _PROFILE['weight_file']
WEIGHT_BYTES = _PROFILE['weight_bytes']
WEIGHT_SHA256 = _PROFILE['weight_sha256']
TOKENIZER_FILES = ('config.json','tokenizer.json','tokenizer_config.json','chat_template.jinja','model.safetensors.index.json')
# Exact unambiguous public SDK group observed 2026-11-07T14:02:22Z.
SDK_PORT = 62298
GGUF_SELECTION = _PROFILE['gguf_selection']
SDK_FILE = Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space/runtime/flint-lmstudio-sdk-2.1.0/node_modules/@lmstudio/sdk/dist/index.mjs')

SDK_TOOL_SCHEMA_SOURCE_SHA256 = 'd44c6732b8d7abe4061cf42887dd95c537e2b85ad8000b83d25e7ed706a37b66'
SDK_TOOL_SCHEMA_JS = 'const jsonSerializableSchema = z.any().transform((val, ctx) => {\n    try {\n        // Needs a more performant way to do this.\n        return JSON.parse(JSON.stringify(val));\n    }\n    catch (e) {\n        ctx.addIssue({\n            code: z.ZodIssueCode.custom,\n            message: "Not JSON serializable: " + e.message,\n        });\n        return val;\n    }\n});\n\nconst llmToolParametersSchema = z.discriminatedUnion("type", [\n    z.object({\n        type: z.literal("object"),\n        properties: z.record(jsonSerializableSchema),\n        required: z.array(z.string()).optional(),\n        additionalProperties: z.any().optional(),\n        $defs: z.record(jsonSerializableSchema).optional(),\n    }),\n    // add more parameter types here\n    // ...\n]);\nconst llmToolSchema = z.discriminatedUnion("type", [\n    z.object({\n        type: z.literal("function"),\n        function: z.object({\n            name: z.string(),\n            description: z.string().optional(),\n            parameters: llmToolParametersSchema.optional(),\n        }),\n    }),\n    // add more tool types here\n    // ...\n]);\n/**\n * For convenience\n */\nz.array(llmToolSchema);\n'

SDK_SCRIPT = r"""import {LMStudioClient} from '/Volumes/NIMROD_SSD/Qwen-Weight-Space/runtime/flint-lmstudio-sdk-2.1.0/node_modules/@lmstudio/sdk/dist/index.mjs';
import {randomUUID,createHash} from 'node:crypto';
import readline from 'node:readline';
import {createRequire} from 'node:module';
const sdkRequire=createRequire('/Volumes/NIMROD_SSD/Qwen-Weight-Space/runtime/flint-lmstudio-sdk-2.1.0/node_modules/@lmstudio/sdk/dist/index.mjs');const {z}=sdkRequire('zod');
__SDK_TOOL_SCHEMA_JS__
const client=new LMStudioClient({baseUrl:'ws://127.0.0.1:__SDK_PORT__',clientIdentifier:'guest:'+randomUUID(),clientPasskey:randomUUID()});
const require=(ok,why)=>{if(!ok)throw Error(why)};
const selection=__GGUF_SELECTION__;
const key=selection?.modelKey??null, embeddingId='ym-research-embedding';
const weightSize=__WEIGHT_BYTES__,weightSha='__WEIGHT_SHA256__';
let owned=null,ownInfo=null,baselineEmbedding=null;
const loadAbort=new AbortController();process.once('SIGTERM',()=>loadAbort.abort());process.once('SIGINT',()=>loadAbort.abort());
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
async function snapshots(){
 const llm=await client.llm.listLoaded(), embedding=await client.embedding.listLoaded();
 return {llm:await Promise.all(llm.map(m=>m.getModelInfo())),embedding:await Promise.all(embedding.map(m=>m.getModelInfo()))};
}
async function embedding(){
 const model=client.embedding.createDynamicHandle({identifier:embeddingId});
 const info=await model.getModelInfo();
 require(info?.identifier===embeddingId && info.modelKey==='google/embedding-gemma-300m' && typeof info.instanceReference==='string','exact baseline embedding unavailable');
 if(baselineEmbedding)require(info.instanceReference===baselineEmbedding.instanceReference && info.modelKey===baselineEmbedding.modelKey,'baseline embedding identity changed');
 return {model,info};
}
function history(body){
 return {messages:body.messages.map(m=>{
  const content=[];
  if(m.role==='tool')content.push({type:'toolCallResult',content:m.content??'',toolCallId:m.tool_call_id,...(m.name?{name:m.name}:{})});
  else {
   if(m.content!==null && m.content!==undefined)content.push({type:'text',text:m.content});
   for(const t of m.tool_calls??[])content.push({type:'toolCallRequest',toolCallRequest:{type:'function',id:t.id,name:t.function.name,arguments:JSON.parse(t.function.arguments)}});
  }
  return {role:m.role,content};
 })};
}
function matchesSelection(info){
 return selection!==null && selection.sourceSha256===weightSha && selection.sizeBytes===weightSize && info?.deviceIdentifier===null && info.modelKey===selection.modelKey && info.path===selection.path && info.indexedModelIdentifier===selection.indexedModelIdentifier && info.publisher===selection.publisher && info.format==='gguf' && info.format===selection.format && info.architecture===selection.architecture && info.sizeBytes===weightSize && info.quantization?.name===selection.quantization.name && info.quantization?.bits===selection.quantization.bits;
}
function ownedProfileMatches(info,cfg,identifier,reference){
 return matchesSelection(info) && info?.identifier===identifier && info.instanceReference===reference && cfg?.contextLength===32768 && cfg.maxParallelPredictions===1 && cfg.keepModelInMemory===false && cfg.tryMmap===true && cfg.evalBatchSize===128 && cfg.physicalBatchSize===64 && cfg.contextCheckpoints===0;
}
async function checkOwner(identifier){
 require(owned && ownInfo && ownInfo.identifier===identifier,'owned instance missing');
 const info=await owned.getModelInfo(),cfg=await owned.getLoadConfig();
 require(info?.identifier===identifier && matchesSelection(info) && info.instanceReference===ownInfo.instanceReference,'owned model identity changed');
 const all=await snapshots();require(all.llm.every(m=>m.identifier===identifier),'unowned LLM activity appeared');await embedding();
 require(ownedProfileMatches(info,cfg,identifier,ownInfo.instanceReference),'owned CPP load config changed: '+JSON.stringify(cfg));
 return {info,cfg,profile:{runtime:'CPP',MLX_flags:'NOT_APPLICABLE'}};
}
async function action(x){
 if(x.action==='baseline'){
  const s=await snapshots();require(s.llm.length===0,'unowned LLM already active');
  const e=await embedding();baselineEmbedding=e.info;
  return {snapshot:s,embedding:e.info,embeddingConfig:await e.model.getLoadConfig(),embeddingContext:await e.model.getContextLength()};
 }
 if(x.action==='load'){
  require(!owned && /^mf-native-[0-9a-f]{32}$/.test(x.identifier),'fresh exact owned identifier');
  const s=await snapshots();require(s.llm.length===0,'unowned LLM activity appeared');
  require(selection!==null,'exact GGUF SDK variant selector unresolved; no load');
  const rows=await client.system.listDownloadedModels('llm');const matches=rows.filter(m=>m.modelKey===selection.modelKey);
  require(matches.length===1,'exact downloaded checkpoint unavailable');const index=matches[0];
  require(matchesSelection(index),'exact downloaded GGUF variant drift');
  owned=await client.llm.load(key,{identifier:x.identifier,deviceIdentifier:null,ttl:2400,config:{contextLength:32768,maxParallelPredictions:1,keepModelInMemory:false,tryMmap:true,evalBatchSize:128,physicalBatchSize:64,contextCheckpoints:0,gpu:{ratio:1}},verbose:false,signal:loadAbort.signal});
  ownInfo=await owned.getModelInfo();const checked=await checkOwner(x.identifier);
  return {requested:{key,identifier:x.identifier,ttl:2400,context:32768,gpuRatio:1},index,...checked,basePrediction:await owned.getBasePredictionConfig()};
 }
 if(x.action==='chat_context'){
  const a=await checkOwner(x.identifier);
  const sdkToolView=z.array(llmToolSchema).parse(x.body.tools??[]);
  const formatted=await owned.applyPromptTemplate(history(x.body),{toolDefinitions:x.body.tools??[]});
  require(Buffer.byteLength(formatted)<=262144,'formatted prompt cap');
  const tokens=await owned.countTokens(formatted);await checkOwner(x.identifier);
  return {formatted,tokens,sdk_tool_view:sdkToolView,tool_schema_source_sha256:'d44c6732b8d7abe4061cf42887dd95c537e2b85ad8000b83d25e7ed706a37b66',instanceReference:a.info.instanceReference,context:a.cfg.contextLength};
 }
 if(x.action==='embedding_context'){
  const e=await embedding();const texts=typeof x.body.input==='string'?[x.body.input]:x.body.input;
  const tokens=await Promise.all(texts.map(t=>e.model.countTokens(t)));const context=await e.model.getContextLength();await embedding();
  return {tokens,context,instanceReference:e.info.instanceReference,config:await e.model.getLoadConfig(),transport:'SDK embed passes same raw input strings as SDK countTokens; no OpenAI REST task wrapper'};
 }
 if(x.action==='embed'){
  const e=await embedding();const texts=typeof x.body.input==='string'?[x.body.input]:x.body.input;
  const before=e.info.instanceReference;const result=await e.model.embed(texts);await embedding();
  return {data:result.map((v,index)=>({object:'embedding',index,embedding:v.embedding})),model:embeddingId,instanceReference:before,usage:null};
 }
 if(x.action==='unload'){
  const current=(await snapshots()).llm.filter(i=>i.identifier===x.identifier);
  if(current.length===0)return {alreadyAbsent:true,snapshot:await snapshots()};
  require(current.length===1 && matchesSelection(current[0]) && (!ownInfo || current[0].instanceReference===ownInfo.instanceReference),'refuse unloading unowned instance');
  if(x.instanceReference)require(current[0].instanceReference===x.instanceReference,'cleanup instance reference changed');
  if(x.embeddingReference)require((await embedding()).info.instanceReference===x.embeddingReference,'baseline embedding changed before cleanup');
  await client.llm.unload(x.identifier);owned=null;
  const after=await snapshots();require(!after.llm.some(i=>i.identifier===x.identifier),'owned instance remains loaded');await embedding();return {unloaded:x.identifier,snapshot:after};
 }
 if(x.action==='snapshot')return snapshots();
 throw Error('closed SDK operation');
}
const lines=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
try{for await(const line of lines){
 require(Buffer.byteLength(line)<=262144,'SDK request bytes');let id=null;
 try{const x=JSON.parse(line);id=x.id;const data=await action(x);const out=JSON.stringify({id,ok:true,data});require(Buffer.byteLength(out)<=524288,'SDK reply cap');process.stdout.write(out+'\n');}
 catch(e){process.stdout.write(JSON.stringify({id,ok:false,error:String(e).slice(0,1024)})+'\n');}
}}finally{await client[Symbol.asyncDispose]();}
""".replace("__GGUF_SELECTION__",json.dumps(GGUF_SELECTION,sort_keys=True)).replace("__SDK_PORT__",str(SDK_PORT)).replace("__SDK_TOOL_SCHEMA_JS__",SDK_TOOL_SCHEMA_JS).replace("__WEIGHT_BYTES__",str(WEIGHT_BYTES)).replace("__WEIGHT_SHA256__",WEIGHT_SHA256)


def require(ok, reason):
    if not ok:
        raise NativeRefused(reason)


class SDKBridge:
    def __init__(self, work, deadline):
        from mirofish_native_run import OwnedGroup, RUNTIME
        self.work, self.deadline = Path(work), deadline
        self.buffer = b''; self.sequence = 0; self.poisoned=False; self.tag=uuid.uuid4().hex
        require(hashlib.sha256(SDK_FILE.read_bytes()).hexdigest()==SDK_TOOL_SCHEMA_SOURCE_SHA256,'pinned SDK tool-schema source changed')
        script = self.work / 'builder/sdk_native.mjs'
        require(script.read_text() == SDK_SCRIPT, 'frozen SDK helper changed')
        env = {'PATH': '/usr/bin:/bin', 'TMPDIR': str(RUNTIME / 'tmp'), 'NODE_DISABLE_COMPILE_CACHE': '1',
               'XDG_CACHE_HOME': str(RUNTIME / 'cache'), 'XDG_CONFIG_HOME': str(RUNTIME / 'xdg/config')}
        self.log = (self.work / ('builder/sdk-stderr-'+self.tag+'.log')).open('xb')
        self.process = subprocess.Popen(['/usr/local/bin/node', str(script)], cwd=script.parent, env=env,
             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, start_new_session=True)
        self.group = OwnedGroup(self.process)
        os.set_blocking(self.process.stdin.fileno(), False)
        atomic(self.work / ('builder/sdk-process-owner-'+self.tag+'.json'), self.group.owner)

    def call(self, action, timeout=None, *, deadline=None, **values):
        require(not self.poisoned,'uncertain SDK stream cannot be reused')
        self.sequence += 1
        end = min(self.deadline, deadline if deadline is not None else time.monotonic()+timeout)
        require(end > time.monotonic() and self.process.poll() is None, 'SDK process/deadline unavailable')
        request = dict(values, action=action, id=self.sequence)
        payload=encoded(request)+b'\n';offset=0
        while offset<len(payload):
            remaining=end-time.monotonic()
            if remaining<=0:
                self.poisoned=True
                raise NativeRefused('SDK request write absolute deadline')
            _,writable,_=select.select([],[self.process.stdin],[],min(.05,remaining))
            if writable:
                try:offset+=os.write(self.process.stdin.fileno(),payload[offset:])
                except BlockingIOError:pass
        while time.monotonic() < end:
            if b'\n' in self.buffer:
                line,self.buffer=self.buffer.split(b'\n',1); value=decode(line)
                require(value.get('id')==self.sequence and value.get('ok') is True, 'fixed SDK operation refused: '+action+' '+str(value.get('error',''))[:1024])
                return value['data']
            ready,_,_=select.select([self.process.stdout],[],[],min(.05,max(0,end-time.monotonic())))
            if ready:
                data=os.read(self.process.stdout.fileno(),65536)
                require(data,'SDK pipe closed');self.buffer+=data
                require(len(self.buffer)<=524288,'SDK reply bytes')
            require(self.process.poll() is None,'SDK helper exited')
        # An uncertain SDK call is never followed by another operation on this
        # stream: a late reply cannot be mistaken for cleanup or new generation.
        self.poisoned=True
        raise NativeRefused('SDK operation deadline; ownership reconciliation required: '+action)


class TemplateAdmission:
    def __init__(self, sdk, identifier, manifest, *, embedding_native_semantics=False):
        from jinja2 import Environment, StrictUndefined
        from tokenizers import Tokenizer
        self.sdk,self.identifier=sdk,identifier
        self.embedding_native_semantics=embedding_native_semantics
        for name in ['config.json','tokenizer.json','tokenizer_config.json','chat_template.jinja','model.safetensors.index.json']:
            require(hashlib.sha256((TOKENIZER_ROOT/name).read_bytes()).hexdigest()==manifest[name], 'frozen native model/tokenizer file changed')
        config=decode((TOKENIZER_ROOT/'config.json').read_bytes())
        require(config.get('model_type')==_PROFILE['tokenizer_model_type'] and (_PROFILE['tokenizer_quant_bits'] is None or config.get('quantization',{}).get('bits')==_PROFILE['tokenizer_quant_bits']),'frozen same-base tokenizer origin architecture/quantization drift')
        tokenizer_config=decode((TOKENIZER_ROOT/'tokenizer_config.json').read_bytes())
        self.tokenizer=Tokenizer.from_file(str(TOKENIZER_ROOT/'tokenizer.json'))
        self.tokens={key: tokenizer_config.get(key,'') for key in ['bos_token','eos_token','unk_token','pad_token']}
        self.env=Environment(undefined=StrictUndefined,trim_blocks=True,lstrip_blocks=True)
        def raise_exception(message): raise NativeRefused('native template refused: '+str(message))
        self.env.globals['raise_exception']=raise_exception
        self.env.globals['strftime_now']=lambda fmt: (_ for _ in ()).throw(NativeRefused('clock-dependent native template unavailable'))
        self.env.filters['tojson']=lambda obj,**kw: json.dumps(obj,ensure_ascii=False,**kw)
        self.template=self.env.from_string((TOKENIZER_ROOT/'chat_template.jinja').read_text())
        self.template_sha=manifest['chat_template.jinja']

    def __call__(self, kind, body, deadline):
        start=time.monotonic()
        if kind=='embedding':
            require(self.embedding_native_semantics,'SDK raw embedding/countTokens semantics not admitted')
            observation=self.sdk.call('embedding_context',deadline=deadline,body=body)
            require(observation.get('context')==2048 and isinstance(observation.get('tokens'),list) and observation['tokens'] and all(type(x) is int and 0<x<=2048 for x in observation['tokens']),'actual embedding token context unavailable')
            return {'verified':True,'model':body['model'],'tokens':max(observation['tokens']),
                    'instance_reference':observation['instanceReference'],'proof':'same pinned SDK raw input path; countTokens per actual text; no REST task prefix'}
        observation=self.sdk.call('chat_context',deadline=deadline,identifier=self.identifier,body=body)
        require(observation.get('context')==32768 and isinstance(observation.get('formatted'),str),'native SDK formatted context missing')
        require(observation.get('tool_schema_source_sha256')==SDK_TOOL_SCHEMA_SOURCE_SHA256 and type(observation.get('sdk_tool_view')) is list,'exact pinned SDK parsed-tool view unavailable')
        candidates=self.render_candidates(body,observation['sdk_tool_view'])
        require(observation['formatted'] in {text for text,count in candidates}, 'actual SDK template differs from all frozen no-thinking/thinking branches')
        native_tokens=len(self.tokenizer.encode(observation['formatted'],add_special_tokens=False).ids)
        require(native_tokens==observation.get('tokens'),'actual SDK tokenizer differs from frozen native tokenizer')
        maximum=max(count for text,count in candidates)
        require(time.monotonic()<deadline,'context admission deadline')
        return {'verified':True,'model':body['model'],'tokens':maximum,'template_sha256':self.template_sha,
                'instance_reference':observation['instanceReference'],'sdk_tool_schema_source_sha256':SDK_TOOL_SCHEMA_SOURCE_SHA256,'candidate_count':len(candidates),'proof':'maximum raw unchanged and exact SDK-parsed tool views, all thinking and valid typed-argument renderings; actual SDK template/tokenizer equality retained'}

    def render_candidates(self,body,sdk_tool_view):
        candidates=[]
        for tools in (body.get('tools'),sdk_tool_view):
            for parsed in [False,True]:
                messages=json.loads(json.dumps(body['messages']))
                raw_string_view=False
                for message in messages:
                    if message.get('role')=='assistant' and 'tool_calls' not in message:
                        message['tool_calls']=[]  # Optional absence only; never alter forwarded history.
                    for call in message.get('tool_calls') or []:
                        arguments=call['function']['arguments']
                        if isinstance(arguments,str):
                            value=decode(arguments)
                            require(type(value) is dict,'tool arguments must decode to an object')
                            if parsed:call['function']['arguments']=value
                            else:raw_string_view=True
                        else:require(type(arguments) is dict,'tool argument mapping required')
                # The pinned Qwen template uses arguments|items; a string is not
                # a valid renderable view. SDK history always JSON.parses it.
                if raw_string_view:continue
                for thinking in [False,True]:
                    formatted=self.template.render(messages=messages,tools=tools,add_generation_prompt=True,enable_thinking=thinking,**self.tokens)
                    count=len(self.tokenizer.encode(formatted,add_special_tokens=False).ids)
                    candidates.append((formatted,count))
        return candidates
