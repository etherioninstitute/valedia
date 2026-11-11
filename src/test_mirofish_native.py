"""Offline boundary tests. Synthetic transport replies never become producer reports."""
import dataclasses
import ast
import copy
import json
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from mirofish_native_gateway import Gateway, Limits, NativeRefused, encoded
from mirofish_native_run import OwnedGroup, PYTHON, worker_policy, verify_launch, graph_environment, strict_probe_success


ROOT = Path(os.environ.get("NATIVE_TEST_ROOT", "/Volumes/NIMROD_SSD/Qwen-Weight-Space/project/work/sol-foreman-qwen/valedia-mirofish-fresh-20261106/builder/tests"))
ROOT.mkdir(parents=True, exist_ok=True)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name); self.calls = []
        self.secret = "a" * 64
        def admit(kind, body, timeout):
            self.calls.append((kind, body))
            return {"verified": True, "model": body["model"], "tokens": 10, "proof": "offline-test-only"}
        self.admit = admit
    def tearDown(self):
        self.temp.cleanup()
    def gateway(self, limits=None, dispatch=None, **kw):
        return Gateway(limits or Limits("owned-test-model"), self.secret, self.root / (str(time.time_ns()) + ".json"), self.admit, dispatch=dispatch or (lambda *args: (200, encoded({"choices": [{"message": {"role": "assistant", "content": "offline fixture"}, "finish_reason": "stop"}]}))), **kw)
    def body(self, **kw):
        return dict(model="owned-test-model", messages=[{"role": "user", "content": "offline test only"}], max_tokens=8, **kw)
    def send(self, gateway, body=None, **kw):
        return gateway.handle("/v1/chat/completions", body or self.body(), "Bearer " + self.secret, **kw)
    def test_closed_auth_model_routes_fields(self):
        gateway = self.gateway()
        for route, body, key in [("/v1/chat/completions", self.body(), "wrong"), ("https://elsewhere/chat", self.body(), "Bearer " + self.secret), ("/v1/chat/completions", dict(self.body(), model="other"), "Bearer " + self.secret), ("/v1/chat/completions", dict(self.body(), arbitrary_url="https://elsewhere"), "Bearer " + self.secret)]:
            with self.assertRaises(NativeRefused): gateway.handle(route, body, key)
        self.assertEqual(gateway.state["chat_attempts"], 0)
    def test_no_refund_on_unknown_failure(self):
        def fail(*args): raise TimeoutError("offline fixture")
        gateway = self.gateway(Limits("owned-test-model", chat_attempts=1), fail)
        with self.assertRaises(TimeoutError): self.send(gateway)
        self.assertEqual(gateway.state["calls"][0]["state"], "unknown")
        self.assertIsNone(gateway.state["calls"][0]["usage"])
        self.assertEqual(gateway.state["reserved_output"], 8)
        with self.assertRaises(NativeRefused): self.send(gateway)
    def test_messages_and_tools_preserved_and_uniform_no_thinking(self):
        tools = [{"type": "function", "function": {"name": "no_effect", "parameters": {"type": "object", "properties": {}}}}]
        body = self.body(tools=tools, tool_choice="required", parallel_tool_calls=False)
        gateway = self.gateway(); self.send(gateway, body)
        admitted = self.calls[0][1]
        self.assertEqual(admitted["tools"], tools); self.assertEqual(admitted["messages"], body["messages"])
        self.assertEqual(admitted["reasoning_effort"], "none")
        self.assertIsNone(gateway.state["calls"][0]["usage"])
    def test_context_refusal_charged_without_forward(self):
        gateway = self.gateway(); gateway.admit_context = lambda kind, body, timeout: {"verified": True, "model": body["model"], "tokens": 32768}
        with self.assertRaises(NativeRefused): self.send(gateway)
        self.assertEqual(gateway.state["chat_attempts"], 1)
        self.assertFalse(gateway.state["calls"][0]["dispatched"])
    def test_cancellation_and_expired_arrival_never_dispatch(self):
        dispatched = []
        gateway = self.gateway(dispatch=lambda *args: dispatched.append(1))
        with self.assertRaises(NativeRefused): self.send(gateway, arrived=gateway.clock()-200)
        self.assertFalse(dispatched)
        def admit(kind, body, timeout):
            gateway.cancel(); return {"verified": True, "model": body["model"], "tokens": 2}
        gateway.admit_context = admit
        with self.assertRaises(NativeRefused): self.send(gateway)
        self.assertFalse(dispatched)
    def test_shared_concurrency_chat_and_embedding(self):
        active = 0; maximum = 0; mutex = threading.Lock(); errors = []
        def dispatch(route, body, timeout):
            nonlocal active, maximum
            with mutex: active += 1; maximum = max(maximum, active)
            time.sleep(.05)
            with mutex: active -= 1
            if route.endswith("embeddings"):
                return 200, encoded({"data": [{"index": 0, "embedding": [0.5] * 768}]})
            return 200, encoded({"choices": [{"message": {"content": "offline fixture"}, "finish_reason":"stop"}]})
        gateway = self.gateway(dispatch=dispatch)
        def call(embedding):
            try:
                if embedding: gateway.handle("/v1/embeddings", {"model": "ym-research-embedding", "input": ["offline text"]}, "Bearer " + self.secret)
                else: self.send(gateway)
            except BaseException as exc: errors.append(exc)
        threads = [threading.Thread(target=call, args=(kind,)) for kind in [False, True, False]]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3)
        self.assertFalse(errors); self.assertEqual(maximum, 1)
        self.assertEqual(gateway.state["chat_attempts"], 2); self.assertEqual(gateway.state["embedding_attempts"], 1)
    def test_private_capture_keeps_actual_bytes_without_auth(self):
        response=b'{ "choices" : [{"message":{"content":"offline bytes"},"finish_reason":"stop"}] }'
        gateway=self.gateway(dispatch=lambda *args:(200,response),calls_path=self.root/'calls')
        self.assertEqual(self.send(gateway),response)
        path=self.root/'calls'/gateway.state['calls'][0]['id']
        self.assertEqual((path/'response.bin').read_bytes(),response)
        self.assertNotIn(self.secret.encode(),(path/'request.json').read_bytes())
        self.assertEqual(json.loads((path/'request.json').read_bytes())['reasoning_effort'],'none')
    def test_queued_cancel_is_charged_and_never_forwarded(self):
        entered=threading.Event();release=threading.Event();cancel=threading.Event();sent=[];failures=[]
        def dispatch(*args):
            sent.append(1);entered.set();release.wait(2)
            return 200,encoded({'choices':[{'message':{'content':'offline fixture'},'finish_reason':'stop'}]})
        gateway=self.gateway(dispatch=dispatch)
        def call(disconnected):
            try:self.send(gateway,disconnected=disconnected)
            except NativeRefused as exc:failures.append(str(exc))
        first=threading.Thread(target=call,args=(lambda:False,));first.start();self.assertTrue(entered.wait(1))
        second=threading.Thread(target=call,args=(cancel.is_set,));second.start()
        end=time.monotonic()+1
        while gateway.state['chat_attempts']<2 and time.monotonic()<end:time.sleep(.001)
        cancel.set();release.set();first.join(3);second.join(3)
        self.assertEqual(len(sent),1);self.assertEqual(gateway.state['chat_attempts'],2)
        self.assertEqual(gateway.state['reserved_output'],16);self.assertEqual(len(failures),1)
    def test_queued_global_expiry_never_posts_after_deadline(self):
        now=[100.0];entered=threading.Event();release=threading.Event();sent=[];failures=[]
        def dispatch(*args):
            sent.append(1);entered.set();release.wait(2)
            return 200,encoded({'choices':[{'message':{'content':'offline fixture'},'finish_reason':'stop'}]})
        gateway=self.gateway(Limits('owned-test-model',wall_seconds=121),dispatch,clock=lambda:now[0])
        def call():
            try:self.send(gateway)
            except NativeRefused as exc:failures.append(str(exc))
        first=threading.Thread(target=call);first.start();self.assertTrue(entered.wait(1))
        second=threading.Thread(target=call);second.start();end=time.monotonic()+1
        while gateway.state['chat_attempts']<2 and time.monotonic()<end:time.sleep(.001)
        now[0]=102.0;release.set();first.join(3);second.join(3)
        self.assertEqual(len(sent),1);self.assertEqual(gateway.state['chat_attempts'],2);self.assertEqual(len(failures),2)

    def test_vectors_cardinality_and_finite_dimensions(self):
        gateway = self.gateway(dispatch=lambda *args: (200, encoded({"data": [{"index": 0, "embedding": [0.0] * 767}]})))
        with self.assertRaises(NativeRefused): gateway.handle("/v1/embeddings", {"model": "ym-research-embedding", "input": ["offline"]}, "Bearer " + self.secret)
        self.assertEqual(gateway.state["embedding_attempts"], 1)
        self.assertEqual(gateway.state["calls"][0]["state"], "unknown")
        self.assertTrue(gateway.cancelled.is_set())


class RealHTTPDeadlineTests(unittest.TestCase):
    """Only the fixed destination is redirected to an owned local dummy peer."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.root=Path(self.temp.name)
        self.secret='d'*64;self.results=[]
    def tearDown(self):self.temp.cleanup()
    def exercise(self,mode,embedding=False,context_delay=0):
        import mirofish_native_gateway as module
        stats={'active':0,'maximum':0,'requests':0};lock=threading.Lock();entered=threading.Event();finished=threading.Event()
        payload=encoded({'data':[{'index':0,'embedding':[0.25]*768}]} if embedding else {'choices':[{'message':{'content':'offline real-peer fixture'},'finish_reason':'stop'}]})
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                with lock:
                    stats['requests']+=1;stats['active']+=1;stats['maximum']=max(stats['maximum'],stats['active'])
                entered.set()
                try:
                    if mode=='unknown':time.sleep(1.8)
                    if mode=='headers':
                        self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(payload)).encode()+b'\r\nX-Drip: ');self.wfile.flush()
                        for _ in range(60):self.wfile.write(b'x');self.wfile.flush();time.sleep(.04)
                        self.wfile.write(b'\r\n\r\n'+payload);self.wfile.flush()
                    else:
                        self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers()
                        if mode=='body':
                            for value in payload:self.wfile.write(bytes([value]));self.wfile.flush();time.sleep(.035)
                        else:self.wfile.write(payload);self.wfile.flush()
                except OSError:pass
                finally:
                    with lock:stats['active']-=1
                    finished.set()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
        thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.01));thread.start()
        original=module.http.client.HTTPConnection;port=server.server_address[1]
        def redirect(host,port_arg,timeout):
            self.assertEqual((host,port_arg),('127.0.0.1',1235))
            return original('127.0.0.1',port,timeout=timeout)
        module.http.client.HTTPConnection=redirect
        route='/v1/embeddings' if embedding else '/v1/chat/completions'
        body={'model':'ym-research-embedding','input':['offline fixture']} if embedding else {'model':'owned-test-model','messages':[{'role':'user','content':'offline fixture'}],'max_tokens':8}
        context_ends=[]
        def admit(kind,body,deadline):
            context_ends.append(deadline)
            if context_delay:time.sleep(context_delay)
            return {'verified':True,'model':body['model'],'tokens':3}
        gateway=Gateway(Limits('owned-test-model',client_seconds=1),self.secret,self.root/(mode+str(embedding)+'.json'),admit)
        errors=[];times=[]
        def call():
            before=time.monotonic()
            try:gateway.handle(route,body,'Bearer '+self.secret)
            except NativeRefused as exc:errors.append(type(exc).__name__)
            times.append(time.monotonic()-before)
        try:
            if mode=='normal':
                before=time.monotonic();actual=gateway.handle(route,body,'Bearer '+self.secret)
                self.assertEqual(actual,payload);self.assertFalse(gateway.cancelled.is_set())
                times.append(time.monotonic()-before)
            elif mode=='unknown':
                first=threading.Thread(target=call);first.start();self.assertTrue(entered.wait(1))
                second=threading.Thread(target=call);second.start()
                end=time.monotonic()+.5
                while len(gateway.state['calls'])<2 and time.monotonic()<end:time.sleep(.001)
                first.join(2);second.join(2)
                self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
                self.assertEqual(len(gateway.state['calls']),2)
                self.assertEqual(gateway.state['calls'][0]['state'],'unknown')
                self.assertEqual(gateway.state['calls'][1]['state'],'refused_before_forward')
                self.assertEqual(gateway.state['embedding_attempts'] if embedding else gateway.state['chat_attempts'],2)
                self.assertEqual(gateway.state['reserved_output'],0 if embedding else 16)
            else:
                call();self.assertEqual(len(errors),1)
                # Declared scheduling tolerance: deadline1s plus at most0.4s.
                self.assertGreaterEqual(times[0],.8);self.assertLess(times[0],1.4,times)
            if mode!='normal':
                self.assertTrue(gateway.cancelled.is_set());self.assertTrue(gateway.state['cancelled'])
                with self.assertRaises(NativeRefused):gateway.handle(route,body,'Bearer '+self.secret)
                self.assertIsNone(gateway.state['calls'][0]['usage'])
            self.assertEqual(stats['requests'],1);self.assertEqual(stats['maximum'],1)
            self.assertFalse(any(t.is_alive() and t.name.startswith('mirofish-http-deadline-') for t in threading.enumerate()))
            self.assertTrue(finished.wait(3),'owned peer did not finish')
            self.results.append({'mode':mode,'embedding':embedding,'elapsed':times,'stats':dict(stats),'states':[c['state'] for c in gateway.state['calls']],'cancelled':gateway.state['cancelled'],'context_deadlines':context_ends})
        finally:
            module.http.client.HTTPConnection=original;server.shutdown();server.server_close();thread.join(1)
            self.assertFalse(thread.is_alive())
        (self.root/'real-peer-results.json').write_text(json.dumps(self.results,indent=2))
        evidence=ROOT/'repair-real-peer-results.jsonl'
        with evidence.open('a') as stream:
            for result in self.results:stream.write(json.dumps(result)+'\n')
    def test_unknown_chat_poison_prevents_queued_overlap(self):self.exercise('unknown')
    def test_unknown_embedding_poison_prevents_queued_overlap(self):self.exercise('unknown',True)
    def test_absolute_deadline_interrupts_drip_headers(self):self.exercise('headers')
    def test_absolute_deadline_interrupts_drip_body(self):self.exercise('body')
    def test_absolute_deadline_includes_context(self):self.exercise('body',context_delay=.3)
    def test_absolute_embedding_deadline_interrupts_drip_body(self):self.exercise('body',True)
    def test_real_chat_positive(self):self.exercise('normal')
    def test_real_embedding_positive(self):self.exercise('normal',True)


class GraphPrerequisiteTests(unittest.TestCase):
    def rows(self):
        constraints=[{'name':name,'type':'UNIQUENESS','entityType':'NODE','labelsOrTypes':[label],'properties':[prop]} for name,label,prop in [('graph_uuid','Graph','graph_id'),('entity_uuid','Entity','uuid'),('episode_uuid','Episode','uuid')]]
        indexes=[{'name':name,'type':kind,'state':'ONLINE','entityType':entity,'labelsOrTypes':[label],'properties':props,'options':{'indexConfig':{'vector.dimensions':768,'vector.similarity_function':'cosine'}} if kind=='VECTOR' else {}} for name,kind,entity,label,props in [('entity_embedding','VECTOR','NODE','Entity',['embedding']),('fact_embedding','VECTOR','RELATIONSHIP','RELATION',['fact_embedding']),('entity_fulltext','FULLTEXT','NODE','Entity',['name','summary']),('fact_fulltext','FULLTEXT','RELATIONSHIP','RELATION',['fact','name'])]]
        indexes.append({'name':'builtin_lookup','type':'LOOKUP','state':'ONLINE','entityType':'NODE','labelsOrTypes':None,'properties':None,'options':{}})
        return constraints,indexes
    def fixtures(self,fail=False):
        path=Path('/Volumes/NIMROD_SSD/backups/2026-10-22/home/mirofish-offline/backend/app/storage/neo4j_schema.py');values={}
        for n in ast.parse(path.read_text()).body:
            if isinstance(n,ast.Assign):
                if isinstance(n.value,ast.Constant):values[n.targets[0].id]=n.value.value
                elif isinstance(n.value,ast.List):values[n.targets[0].id]=[values[x.id] for x in n.value.elts]
        constraints,indexes=self.rows();outer=self
        class Result:
            def __init__(self,rows):self.rows=rows
            def consume(self):return None
            def data(self):return copy.deepcopy(self.rows)
        class Driver:
            def __init__(self):self.queries=[];self.constraints=constraints;self.indexes=indexes
            def session(self):return self
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def run(self,query,parameters=None):
                self.queries.append(str(query))
                if fail:raise RuntimeError('owned fixture schema refusal')
                if str(query).startswith('SHOW CONSTRAINTS'):return Result(self.constraints)
                if str(query).startswith('SHOW INDEXES'):return Result(self.indexes)
                return Result([{'hits':0}])
        return Driver(),values['ALL_SCHEMA_QUERIES']
    def test_valid_native_rows_and_builtin_lookup_admit(self):
        from mirofish_native_graph import qualify_graph,EXPECTED_SCHEMA_SHA
        driver,queries=self.fixtures();proof=qualify_graph(driver,queries,EXPECTED_SCHEMA_SHA,[.25]*768,deadline=time.monotonic()+1)
        self.assertTrue(proof['qualified']);self.assertEqual(proof['ddl_consumed'],7);self.assertEqual(len(proof['capability_queries']),4)
    def test_actual_uppercase_cosine_admits_only_known_string_enum(self):
        from mirofish_native_graph import validate_schema,GraphGateRefused
        constraints,indexes=self.rows()
        for row in indexes:
            if row['type']=='VECTOR':row['options']['indexConfig']['vector.similarity_function']='COSINE'
        validate_schema(constraints,indexes)
        for value in ['unknown',None,1,True,['COSINE']]:
            bad=copy.deepcopy(indexes);bad[0]['options']['indexConfig']['vector.similarity_function']=value
            with self.subTest(value=value),self.assertRaises(GraphGateRefused):validate_schema(constraints,bad)
    def test_missing_wrong_dimension_nononline_constraints_and_fulltext_refuse(self):
        from mirofish_native_graph import validate_schema,GraphGateRefused
        for kind in ['missing','dimension','nononline','constraint','fulltext','similarity']:
            constraints,indexes=self.rows()
            if kind=='missing':indexes.pop(0)
            if kind=='dimension':indexes[0]['options']['indexConfig']['vector.dimensions']=1536
            if kind=='nononline':indexes[0]['state']='POPULATING'
            if kind=='constraint':constraints[0]['properties']=['wrong']
            if kind=='fulltext':indexes[2]['entityType']='RELATIONSHIP'
            if kind=='similarity':indexes[0]['options']['indexConfig']['vector.similarity_function']='euclidean'
            with self.subTest(kind=kind),self.assertRaises(GraphGateRefused):validate_schema(constraints,indexes)
    def test_wrong_or_nonfinite_actual_embedding_refuses(self):
        from mirofish_native_graph import vector_dimension,GraphGateRefused
        for vector in [[.2]*767,[float('nan')]*768,[True]*768]:
            with self.assertRaises(GraphGateRefused):vector_dimension(vector)
    def test_query_exception_and_expired_deadline_refuse(self):
        from mirofish_native_graph import qualify_graph,EXPECTED_SCHEMA_SHA,GraphGateRefused
        driver,queries=self.fixtures(True)
        with self.assertRaises(GraphGateRefused) as failure:qualify_graph(driver,queries,EXPECTED_SCHEMA_SHA,[.2]*768,deadline=time.monotonic()+1)
        self.assertFalse(failure.exception.proof['qualified']);self.assertEqual(failure.exception.proof['ddl_consumed'],0)
        driver,queries=self.fixtures()
        with self.assertRaises(GraphGateRefused):qualify_graph(driver,queries,EXPECTED_SCHEMA_SHA,[.2]*768,deadline=time.monotonic()-1)
        self.assertFalse(driver.queries)
    def test_population_wait_is_bounded(self):
        from mirofish_native_graph import qualify_graph,EXPECTED_SCHEMA_SHA,GraphGateRefused
        driver,queries=self.fixtures();driver.indexes[0]['state']='POPULATING';now=[0.0]
        def sleep(delta):now[0]+=delta
        with self.assertRaises(GraphGateRefused):qualify_graph(driver,queries,EXPECTED_SCHEMA_SHA,[.2]*768,deadline=.2,clock=lambda:now[0],sleep=sleep)
        self.assertLessEqual(now[0],.2)
    def test_swallowed_exact_native_bootstrap_cannot_admit_outer_gate(self):
        from mirofish_native_graph import qualify_graph,EXPECTED_SCHEMA_SHA,GraphGateRefused
        driver,queries=self.fixtures(True);warnings=[]
        path=Path('/Volumes/NIMROD_SSD/backups/2026-10-22/home/mirofish-offline/backend/app/storage/neo4j_storage.py');tree=ast.parse(path.read_text());method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_ensure_schema')
        class Logger:
            def warning(self,message):warnings.append(message)
        scope={'neo4j_schema':type('Schema',(),{'ALL_SCHEMA_QUERIES':queries}),'logger':Logger()}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),'owned-bootstrap-fixture','exec'),scope)
        scope['_ensure_schema'](type('Storage',(),{'_driver':driver})())
        self.assertEqual(len(warnings),7)
        ontology=[]
        with self.assertRaises(GraphGateRefused):
            qualify_graph(driver,queries,EXPECTED_SCHEMA_SHA,[.2]*768,deadline=time.monotonic()+1)
            ontology.append('must not run')
        self.assertFalse(ontology)
    def test_actual_worker_and_parent_order_gate_before_prepare(self):
        import mirofish_native_run as native
        tree=ast.parse(Path(native.__file__).read_text());worker=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='native_worker');text=ast.unparse(worker)
        self.assertIn("stage == 'prepare' and graph_qualified",text)
        self.assertLess(text.index('qualify_graph('),text.index('graph_qualified = True'))
        execute=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='execute');text=ast.unparse(execute)
        self.assertLess(text.index("worker.request('graph_gate'"),text.index("save('graph_prerequisite_qualified'"))
        self.assertLess(text.index("save('graph_prerequisite_qualified'"),text.index("worker.request('prepare'"))
    def test_trusted_deadline_can_only_shorten_gateway_budget(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            observed=[]
            gateway=Gateway(Limits('owned-test-model'),'g'*64,Path(tmp)/'ledger.json',lambda kind,body,end:{'verified':True,'model':body['model'],'tokens':2},dispatch=lambda route,body,end:(observed.append(end) or 200,encoded({'choices':[{'message':{'content':'fixture'},'finish_reason':'stop'}]})))
            now=time.monotonic();gateway.handle('/v1/chat/completions',{'model':'owned-test-model','messages':[{'role':'user','content':'fixture'}],'max_tokens':8},'Bearer '+'g'*64,deadline=now+.5)
            self.assertLessEqual(observed[0],now+.5)
            self.assertEqual(gateway.state['chat_attempts'],1)


class CPPProfileTests(unittest.TestCase):
    def test_supported_cpp_profile_and_exact_variant_identity_are_strict(self):
        from mirofish_native_model import SDK_SCRIPT,WEIGHT_SHA256,WEIGHT_BYTES
        function=SDK_SCRIPT[SDK_SCRIPT.index('function matchesSelection('):SDK_SCRIPT.index('async function checkOwner(')]
        fixture={'modelKey':'fixture-variant','path':'fixture/file.gguf','indexedModelIdentifier':'fixture/file.gguf','publisher':'fixture-publisher','format':'gguf','architecture':'qwen35','quantization':{'name':'Q4_K_M','bits':4},'sizeBytes':WEIGHT_BYTES,'sourceSha256':WEIGHT_SHA256}
        script='const selection='+json.dumps(fixture)+';const key=selection.modelKey;const weightSize='+str(WEIGHT_BYTES)+';const weightSha='+json.dumps(WEIGHT_SHA256)+';'+function+"""
const info={...selection,deviceIdentifier:null,identifier:'mf-native-'+'a'.repeat(32),instanceReference:'fixture-owned-reference'};
const cfg={contextLength:32768,maxParallelPredictions:1,keepModelInMemory:false,tryMmap:true,evalBatchSize:128,physicalBatchSize:64,contextCheckpoints:0};
const valid=ownedProfileMatches(info,cfg,info.identifier,info.instanceReference);
const invalid=[{...cfg,keepModelInMemory:true},{...cfg,contextLength:2048},{...cfg,maxParallelPredictions:undefined},{...cfg,maxParallelPredictions:2},{...cfg,tryMmap:false},{...cfg,evalBatchSize:512},{...cfg,physicalBatchSize:undefined},{...cfg,physicalBatchSize:128},{...cfg,contextCheckpoints:32},{...cfg,contextCheckpoints:undefined},{...cfg,contextCheckpoints:false},{...cfg,contextCheckpoints:'0'}].map(x=>ownedProfileMatches(info,x,info.identifier,info.instanceReference));
for(const bad of [{...info,instanceReference:'wrong'},{...info,path:'wrong'},{...info,format:'safetensors'},{...info,deviceIdentifier:'remote'},{...info,quantization:{name:'Q8_0',bits:8}}])invalid.push(ownedProfileMatches(bad,cfg,info.identifier,info.instanceReference));
if(!valid||invalid.some(Boolean))throw Error('CPP profile fixture rejected');
console.log(JSON.stringify({valid,invalid}));
"""
        result=subprocess.run(['/usr/local/bin/node','--input-type=module','-e',script],env={'PATH':'/usr/bin:/bin','NODE_DISABLE_COMPILE_CACHE':'1'},capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr);self.assertTrue(json.loads(result.stdout)['valid'])
        self.assertNotIn('mlxDiskCache:false',SDK_SCRIPT);self.assertIn('keepModelInMemory:false',SDK_SCRIPT)
        self.assertIn("require(selection!==null,'exact GGUF SDK variant selector unresolved; no load')",SDK_SCRIPT)



class SDKViewCounterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures=Path(os.environ['NATIVE_SDKVIEW_FIXTURES'])
    def sdk_view(self,tools):
        from mirofish_native_model import SDK_TOOL_SCHEMA_JS,SDK_FILE
        script="import {createRequire} from 'node:module';const require=createRequire("+json.dumps(str(SDK_FILE))+");const {z}=require('zod');"+SDK_TOOL_SCHEMA_JS+"process.stdout.write(JSON.stringify(z.array(llmToolSchema).parse(JSON.parse(process.argv[1]))));"
        result=subprocess.run(['/usr/local/bin/node','--input-type=module','-e',script,json.dumps(tools or [])],env={'PATH':'/usr/bin:/bin','NODE_DISABLE_COMPILE_CACHE':'1'},capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)
    def counter(self,body,*,bad_format=False,bad_tokens=False,bad_schema=False):
        from jinja2 import Environment,Undefined
        from mirofish_native_model import TemplateAdmission,TOKENIZER_ROOT,TOKENIZER_FILES,SDK_TOOL_SCHEMA_SOURCE_SHA256
        import hashlib
        parsed_tools=self.sdk_view(body.get('tools'))
        actual=Environment(undefined=Undefined,trim_blocks=True,lstrip_blocks=True)
        actual.filters['tojson']=lambda obj,**kw:json.dumps(obj,ensure_ascii=False,**kw)
        actual.globals['raise_exception']=lambda text:(_ for _ in ()).throw(NativeRefused(text))
        template=actual.from_string((self.fixtures/'actual-template.jinja').read_text())
        messages=copy.deepcopy(body['messages'])
        for message in messages:
            for call in message.get('tool_calls') or []:call['function']['arguments']=json.loads(call['function']['arguments'])
        config=json.loads((TOKENIZER_ROOT/'tokenizer_config.json').read_text());tokens={k:config.get(k,'') for k in ['bos_token','eos_token','unk_token','pad_token']}
        formatted=template.render(messages=messages,tools=parsed_tools,add_generation_prompt=True,enable_thinking=True,**tokens)
        class OfflineMetadata:
            def call(inner,action,**values):
                self.assertEqual(action,'chat_context')
                return {'context':32768,'formatted':'not-a-frozen-template' if bad_format else formatted,'tokens':len(counter.tokenizer.encode(formatted,add_special_tokens=False).ids)+(1 if bad_tokens else 0),'sdk_tool_view':parsed_tools,'tool_schema_source_sha256':'wrong' if bad_schema else SDK_TOOL_SCHEMA_SOURCE_SHA256,'instanceReference':'OFFLINE_RECORDED_TEMPLATE_NOT_LIVE_SDK'}
        counter=TemplateAdmission(OfflineMetadata(),body['model'],{name:hashlib.sha256((TOKENIZER_ROOT/name).read_bytes()).hexdigest() for name in TOKENIZER_FILES},embedding_native_semantics=True)
        return counter,parsed_tools,formatted
    def test_hash_matched_native_payload_exact_sdk_view_full_raw_max_and_no_mutation(self):
        import hashlib
        from mirofish_native_gateway import validate_chat
        body=json.loads((self.fixtures/'native-request.json').read_text());body=validate_chat(body,Limits(body['model']))
        self.assertEqual(hashlib.sha256(encoded(body)).hexdigest(),'2ad608cbbe8270e19245327158a8b9ead0e9e3866da82b22df28a05667db77da')
        original=copy.deepcopy(body);counter,parsed,formatted=self.counter(body)
        self.assertTrue(all(t['function']['strict'] is True for t in body['tools']))
        self.assertTrue(all('strict' not in t['function'] for t in parsed))
        candidates=counter.render_candidates(body,parsed)
        self.assertNotIn(formatted,{text for text,count in candidates[:4]})
        self.assertIn(formatted,{text for text,count in candidates})
        result=counter('chat',body,time.monotonic()+10)
        self.assertEqual(result['tokens'],max(count for text,count in candidates));self.assertGreaterEqual(result['tokens'],max(count for text,count in candidates[:4]))
        self.assertEqual(body,original);self.assertEqual(result['candidate_count'],8)
    def test_plain_assistant_missing_optional_tool_calls_and_valid_tool_cycle(self):
        tools=[{'type':'function','function':{'name':'no_effect','description':'offline fixture','strict':True,'parameters':{'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}}}]
        variants=[{'model':'owned-fixture','messages':[{'role':'system','content':'offline fixture'},{'role':'user','content':'offline request'},{'role':'assistant','content':'plain assistant history'}],'max_tokens':512}, {'model':'owned-fixture','messages':[{'role':'system','content':'offline fixture'},{'role':'user','content':'offline request'},{'role':'assistant','content':None,'tool_calls':[{'id':'fixture-call','type':'function','function':{'name':'no_effect','arguments':'{"ok":true}'}}]},{'role':'tool','tool_call_id':'fixture-call','content':'offline tool result'},{'role':'assistant','content':'plain assistant after valid tool cycle'}],'tools':tools,'max_tokens':512}]
        for body in variants:
            with self.subTest(tool_cycle=bool(body.get('tools'))):
                before=copy.deepcopy(body);counter,parsed,formatted=self.counter(body);result=counter('chat',body,time.monotonic()+10)
                self.assertTrue(result['verified']);self.assertEqual(body,before)
    def test_actual_formatter_tokenizer_and_schema_guards_still_fail_closed(self):
        body=json.loads((self.fixtures/'native-request.json').read_text())
        for flag in ['bad_format','bad_tokens','bad_schema']:
            with self.subTest(flag=flag):
                counter,_,_=self.counter(body,**{flag:True})
                with self.assertRaises(NativeRefused):counter('chat',body,time.monotonic()+10)
    def test_pre_admission_failure_request_reason_stage_are_durable_and_credential_safe(self):
        secret='secret-local-token-'+'q'*48
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root=Path(tmp);forwarded=[]
            def refusal(kind,body,end):raise NativeRefused('distinct SDK formatting failure '+secret)
            gateway=Gateway(Limits('owned-model'),secret,root/'ledger.json',refusal,dispatch=lambda *args:forwarded.append(args),calls_path=root/'calls')
            body={'model':'owned-model','messages':[{'role':'user','content':'offline payload '+secret}],'max_tokens':512}
            with self.assertRaises(NativeRefused):gateway.handle('/v1/chat/completions',body,'Bearer '+secret)
            row=gateway.state['calls'][0];outcome=json.loads((root/'calls'/row['id']/'outcome.json').read_text());captured=(root/'calls'/row['id']/'request.json').read_text()
            self.assertEqual(outcome['failure_stage'],'context_admission');self.assertIn('distinct SDK formatting failure',outcome['failure_reason'])
            self.assertNotIn(secret,outcome['failure_reason']);self.assertNotIn(secret,captured);self.assertTrue(outcome['request_capture_redacted'])
            self.assertFalse(outcome['dispatched']);self.assertEqual(outcome['state'],'refused_before_forward');self.assertEqual(outcome['usage_status'],'unknown');self.assertEqual(gateway.state['reserved_output'],512);self.assertEqual(forwarded,[])
    def test_refused_attempts_and_prior_phase_completions_cannot_qualify(self):
        from mirofish_native_run import completed_native_chat_calls
        earlier=[{'id':'prior','kind':'chat','state':'complete','dispatched':True,'upstream_status':200}]
        start=len(earlier);native=[{'id':str(i),'kind':'chat','state':'refused_before_forward','dispatched':False} for i in range(12)]
        self.assertEqual(completed_native_chat_calls((earlier+native)[start:]),[])
        native.extend([{'id':'unknown','kind':'chat','state':'unknown','dispatched':True,'upstream_status':200},{'id':'not-forwarded','kind':'chat','state':'complete','dispatched':False,'upstream_status':200},{'id':'embedding','kind':'embedding','state':'complete','dispatched':True,'upstream_status':200}])
        self.assertEqual(completed_native_chat_calls(native),[])
        for i in range(2):native.append({'id':'complete-'+str(i),'kind':'chat','state':'complete','dispatched':True,'upstream_status':200})
        self.assertEqual([r['id'] for r in completed_native_chat_calls(native)],['complete-0','complete-1'])


class CallerBudgetQualificationTests(unittest.TestCase):
    def test_gateway_never_returns_length_even_when_content_is_valid_json(self):
        for finish in ['length','content_filter',None,'unknown']:
            with self.subTest(finish=finish),tempfile.TemporaryDirectory(dir=ROOT) as tmp:
                root=Path(tmp);secret='gateway-finish-fixture-'+'q'*40
                raw=encoded({'choices':[{'message':{'role':'assistant','content':'{"ok":true}'},'finish_reason':finish}]})
                gateway=Gateway(Limits('owned'),secret,root/'ledger.json',lambda kind,body,end:{'verified':True,'model':'owned','tokens':1},dispatch=lambda *args:(200,raw),calls_path=root/'calls')
                with self.assertRaises(NativeRefused):gateway.handle('/v1/chat/completions',{'model':'owned','messages':[{'role':'user','content':'fixture'}],'max_tokens':1536},'Bearer '+secret)
                row=gateway.state['calls'][0];self.assertEqual(row['state'],'unknown');self.assertTrue(row['dispatched']);self.assertEqual(gateway.state['reserved_output'],1536);self.assertTrue(gateway.cancelled.is_set())
                self.assertEqual((root/'calls'/row['id']/'response.bin').read_bytes(),raw)
                with self.assertRaises(NativeRefused):gateway.handle('/v1/chat/completions',{'model':'owned','messages':[{'role':'user','content':'fixture'}],'max_tokens':512},'Bearer '+secret)
                self.assertEqual(gateway.state['chat_attempts'],1)
    def test_gateway_preserves_valid_stop_and_tool_call_completions(self):
        choices=[{'message':{'role':'assistant','content':'{"ok":true}'},'finish_reason':'stop'},{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'fixture-call','type':'function','function':{'name':'no_effect','arguments':'{"ok":true}'}}]},'finish_reason':'tool_calls'}]
        for choice in choices:
            with self.subTest(finish=choice['finish_reason']),tempfile.TemporaryDirectory(dir=ROOT) as tmp:
                secret='gateway-positive-fixture-'+'q'*40;raw=encoded({'choices':[choice]});gateway=Gateway(Limits('owned'),secret,Path(tmp)/'ledger.json',lambda kind,body,end:{'verified':True,'model':'owned','tokens':1},dispatch=lambda *args:(200,raw))
                self.assertEqual(gateway.handle('/v1/chat/completions',{'model':'owned','messages':[{'role':'user','content':'fixture'}],'max_tokens':512},'Bearer '+secret),raw)
                self.assertEqual(gateway.state['calls'][0]['state'],'complete')

    def test_every_native_chat_must_be_complete_not_only_two_successes(self):
        from mirofish_native_run import qualify_native_chat_calls
        clean=[{'id':str(i),'kind':'chat','state':'complete','dispatched':True,'upstream_status':200} for i in range(2)]
        self.assertEqual(qualify_native_chat_calls(clean),clean)
        for extra in [{'kind':'chat','state':'refused_before_forward','dispatched':False},{'kind':'chat','state':'unknown','dispatched':True,'upstream_status':200},{'kind':'chat','state':'complete','dispatched':True,'upstream_status':409}]:
            with self.subTest(extra=extra),self.assertRaises(NativeRefused):qualify_native_chat_calls(clean+[extra])
        with self.assertRaises(NativeRefused):qualify_native_chat_calls([{'kind':'chat','state':'refused_before_forward','dispatched':False} for _ in range(12)])
        with self.assertRaises(NativeRefused):qualify_native_chat_calls(clean[:1])
    def test_single_declared_caller_limit_lower_preserved_invalid_refused_and_bound(self):
        from mirofish_native_caller_policy import CALLER_OUTPUT_LIMIT,choose_output_limit
        self.assertEqual(CALLER_OUTPUT_LIMIT,1536)
        for requested,expected in [(4096,1536),(2800,1536),(1536,1536),(512,512),(128,128)]:self.assertEqual(choose_output_limit(requested),expected)
        for value in [True,False,None,0,-1,1.5,'512']:
            with self.subTest(value=value),self.assertRaises(ValueError):choose_output_limit(value)
        self.assertEqual(2*512+38*CALLER_OUTPUT_LIMIT,59392);self.assertLess(59392,Limits('owned').output_total)


class FixedHostChildrenTests(unittest.TestCase):
    def adapter(self,root,preflight=True):
        from mirofish_native_children import HostChildren
        return HostChildren(root,None,time.monotonic()+10,None,preflight)
    def test_closed_roles_fields_and_authority_bind(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root=Path(tmp);(root/'builder').mkdir();h=self.adapter(root)
            try:
                for value in [{'op':'exec','role':'worker','argv':['/bin/sh']},{'op':'start','role':'graph'},{'op':'poll','role':'arbitrary'},{'op':'bind','run_id':'x','candidate_sha256':'0'*64}]:
                    with self.assertRaises(NativeRefused):h.handle(value)
                self.assertTrue(h.handle({'op':'bind','run_id':'a'*32,'candidate_sha256':'0'*64})['bound'])
                for value in [{'op':'start','role':'graph','argv':['x']},{'op':'start','role':'worker','model':'unowned','secret':'b'*64,'active_deadline':time.monotonic()+2},{'op':'start','role':'worker','model':'mf-native-'+'a'*32,'secret':'bad','active_deadline':time.monotonic()+2},{'op':'stop','role':'graph','pid':123}]:
                    with self.assertRaises(NativeRefused):h.handle(value)
            finally:
                h.observer.close();os.close(h.command_write);os.close(h.event_read);h.close()
    def test_private_socket_disconnect_cleans_actual_owned_role(self):
        from mirofish_native_children import receive
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root=Path(tmp);(root/'builder').mkdir();h=self.adapter(root)
            p=subprocess.Popen([str(PYTHON),'-B','-c','import time;time.sleep(20)'],start_new_session=True)
            g=OwnedGroup(p);log=open(os.devnull,'w');h.roles['worker']=(p,g,log)
            thread=threading.Thread(target=h.serve);thread.start();h.observer.close();os.close(h.command_write);os.close(h.event_read);thread.join(5)
            self.assertFalse(thread.is_alive());self.assertIsNotNone(p.poll());self.assertEqual(h.events[-1]['host_finally_cleanup']['live_group_members'],[])
    def test_actual_source_uses_only_host_roles_and_inherited_pipe_ends(self):
        import mirofish_native_run as n,mirofish_native_startup as startup
        text=Path(n.__file__).read_text();tree=ast.parse(text)
        worker=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='WorkerChannel');execute=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='execute')
        self.assertNotIn('subprocess.Popen',ast.unparse(worker));self.assertNotIn('subprocess.Popen',ast.unparse(execute))
        self.assertIn('pass_fds=children.observer_fds()',Path(startup.__file__).read_text())
        self.assertIn("client().call('bind'",text)
        self.assertIn('/bin/bash',n.observer_policy(ROOT));self.assertIn('/bin/bash',n.graph_policy(ROOT));self.assertNotIn('/bin/bash',worker_policy(ROOT,ROOT,62471,62472))


class HostResourceCapsuleTests(unittest.TestCase):
    def sample(self):
        from mirofish_native_startup import FIELDS,EXPECTED_PS_SHA
        value={'schema':1,'outcome':'VALEDIA-MIROFISH-FRESH-1','nonce':'b'*32,'host_pid':123,'host_started':'fixed own host start','source_sha256':'c'*64,'sequence':1,'monotonic':100.0,'utc':'2026-11-07T00:00:00+00:00','free_percent':80,'all_process_rss_bytes':34*1024**3,'rss_rows':1000,'rss_positive_rows':1000,'ps_sha256':EXPECTED_PS_SHA,'ps_mode':0o4755,'host_deadline':200.0,'owned_scope_rss_bytes':4*1024**3,'scope_rows':100,'excluded_rows':900,'baseline_source_sha256':'a'*64,'scope_roots_sha256':'b'*64,'tracked_identities_sha256':'c'*64};value['prompt_cache_route']={'source':'/Users/burtonchatelain/.lmstudio/.internal/prompt-cache','target':'/Volumes/NIMROD_SSD/Qwen-Weight-Space/cache/bionic/prompt-cache','device':16777246,'ssd_device':16777246,'source_symlink':True,'target_directory':True};assert set(value)==FIELDS;return value
    def test_strict_fresh_signed_host_binding_and_rejections(self):
        import hashlib,hmac
        from datetime import datetime,timezone
        from mirofish_native_startup import validate_capsule,_LAST
        key='k'*64;host={'pid':123,'started':'fixed own host start'};utc=datetime(2026,11,7,tzinfo=timezone.utc)
        def envelope(sample):return {'sample':sample,'mac':hmac.new(key.encode(),encoded(sample),hashlib.sha256).hexdigest()}
        sample=self.sample();_LAST.clear();self.assertEqual(validate_capsule(envelope(sample),key,'b'*32,host,'c'*64,now=100,utc=utc)['all_process_rss_bytes'],34*1024**3)
        for field,value in [('monotonic',96),('nonce','wrong'),('host_pid',999),('ps_mode',0o555),('schema',True),('rss_positive_rows',0),('sequence',0)]:
            bad=dict(sample);bad[field]=value
            with self.subTest(field=field),self.assertRaises(NativeRefused):validate_capsule(envelope(bad),key,'b'*32,host,'c'*64,now=100,utc=utc)
        bad=envelope(sample);bad['mac']='0'*64
        with self.assertRaises(NativeRefused):validate_capsule(bad,key,'b'*32,host,'c'*64,now=100,utc=utc)
    def test_cache_route_rejects_changed_pointer_or_device_without_contents(self):
        from mirofish_native_startup import validate_cache_route
        route=self.sample()['prompt_cache_route'];self.assertEqual(validate_cache_route(route),route)
        for field,value in [('target','/tmp/prompt-cache'),('device',0),('ssd_device',0),('source_symlink',False),('target_directory',False)]:
            bad=dict(route);bad[field]=value
            with self.subTest(field=field),self.assertRaises(NativeRefused):validate_cache_route(bad)
    def test_owned_scope_exclusion_family_orphans_reparent_and_pid_reuse(self):
        from mirofish_native_startup import OwnedResourceScope
        def row(pid,ppid,start,rss,comm):return {'pid':pid,'ppid':ppid,'pgid':pid,'start':start,'rss':rss,'comm':comm}
        baseline=[row(1,0,'one',100,'/sbin/launchd'),row(10,1,'bio',200,'/Applications/Bionic.app/Bionic'),row(11,10,'engine',300,'engine'),row(20,1,'old',90000,'unrelated-big-app')]
        scope=OwnedResourceScope(baseline,{10});first=scope.measure(baseline)
        self.assertEqual(first['owned_scope_rss_bytes'],500);self.assertEqual(first['excluded_rows'],2)
        current=[row(1,0,'one',100,'/sbin/launchd'),row(10,1,'bio',200,'/Applications/Bionic.app/Bionic'),row(11,1,'engine',300,'engine'),row(20,1,'old',90000,'unrelated-big-app'),row(30,1,'orphan',400,'new-unrelated')]
        self.assertEqual(scope.measure(current)['owned_scope_rss_bytes'],900)
        current[3]=row(20,1,'reused',700,'unrelated-big-app')
        self.assertEqual(scope.measure(current)['owned_scope_rss_bytes'],1600)
    def test_producer_environment_never_receives_capsule_authority(self):
        from mirofish_native_run import worker_environment
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root=Path(tmp);(root/'tmp').mkdir()
            env=worker_environment(root,'http://127.0.0.1:62471/v1','owned','p'*64,62472,0)
            self.assertFalse(any(k.startswith('MIROFISH_RESOURCE') for k in env));self.assertNotIn('MIROFISH_IDENTITY_PS',env)


class StreamingDigestTests(unittest.TestCase):
    def test_known_sha_and_large_file_uses_only_bounded_stream_reads(self):
        import hashlib
        from unittest.mock import patch
        from mirofish_native_run import digest
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root=Path(tmp);known=root/'known';known.write_bytes(b'abc')
            self.assertEqual(digest(known),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
            chunk=b'0123456789abcdef'*65536;tail=b'fixed-tail';large=root/'large'
            with large.open('wb') as stream:
                for _ in range(5):stream.write(chunk)
                stream.write(tail)
            expected=hashlib.sha256(chunk*5+tail).hexdigest();original=Path.open;reads=[]
            class Reader:
                def __init__(inner,stream):inner.stream=stream
                def __enter__(inner):return inner
                def __exit__(inner,*args):inner.stream.close()
                def read(inner,size):
                    self.assertGreater(size,0);self.assertLessEqual(size,1024*1024);reads.append(size)
                    return inner.stream.read(size)
            def bounded(path,*args,**kwargs):return Reader(original(path,*args,**kwargs))
            with patch.object(Path,'open',bounded),patch.object(Path,'read_bytes',side_effect=AssertionError('whole file read forbidden')):
                self.assertEqual(digest(large),expected)
            self.assertGreater(len(reads),5);self.assertEqual(max(reads),1024*1024)


class ResourceObservabilityTests(unittest.TestCase):
    def test_exact_bad_signed_measurement_persists_before_cancel(self):
        from mirofish_native_gateway import decode,atomic
        import hashlib,hmac
        from datetime import datetime,timezone
        from unittest.mock import patch
        import mirofish_native_startup as startup
        import mirofish_native_run as native
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root=Path(tmp);sample=HostResourceCapsuleTests().sample();sample.update(free_percent=29,monotonic=time.monotonic(),utc=datetime.now(timezone.utc).isoformat(),host_deadline=time.monotonic()+20,source_sha256=startup.digest(startup.__file__))
            key='k'*64;envelope={'sample':sample,'mac':hmac.new(key.encode(),encoded(sample),hashlib.sha256).hexdigest()}
            history=startup.ResourceHistory(root)
            try:history.publish(envelope)
            finally:history.close()
            self.assertEqual((root/'samples.jsonl').stat().st_mode&0o777,0o600)
            self.assertEqual(decode((root/'samples.jsonl').read_bytes().strip()),envelope)
            receipt={};target=root/'receipt.json';aborted=threading.Event();errors=[];observed=[]
            class Gateway:
                def cancel(inner):
                    self.assertTrue(aborted.is_set())
                    actual=decode(target.read_bytes())['resource_failure_samples'][0]['sample']
                    self.assertEqual(actual,sample);self.assertEqual(actual['free_percent'],29)
                    observed.append('cancel-after-durable-measurement')
            env={'MIROFISH_RESOURCE_CAPSULE':str(root/'sample.json'),'MIROFISH_RESOURCE_KEY':key,'MIROFISH_RESOURCE_NONCE':sample['nonce'],'MIROFISH_RESOURCE_HOST':json.dumps({'pid':sample['host_pid'],'started':sample['host_started']})}
            startup._LAST.clear()
            with patch.dict(os.environ,env):
                self.assertFalse(native.monitor_resource_once(receipt,target,aborted,Gateway(),errors))
            self.assertEqual(observed,['cancel-after-durable-measurement']);self.assertEqual(errors,['NativeRefused:memory free threshold'])
            stale=dict(sample);stale['monotonic']=time.monotonic()-4
            bad={'sample':stale,'mac':hmac.new(key.encode(),encoded(stale),hashlib.sha256).hexdigest()};atomic(root/'sample.json',bad)
            with patch.dict(os.environ,env),self.assertRaises(NativeRefused):native.resource_sample(False)
            bad['mac']='0'*64;atomic(root/'sample.json',bad)
            with patch.dict(os.environ,env),self.assertRaises(NativeRefused):native.resource_sample(False)
    def test_history_bound_refuses_before_replacing_current_capsule(self):
        from mirofish_native_startup import ResourceHistory
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root=Path(tmp);history=ResourceHistory(root);sample=HostResourceCapsuleTests().sample();value={'sample':sample,'mac':'m'*64}
            try:
                history.publish(value);before=(root/'sample.json').read_bytes();history.records=history.MAX_RECORDS
                with self.assertRaises(NativeRefused):history.publish(value)
                self.assertEqual((root/'sample.json').read_bytes(),before)
            finally:history.close()


class NativeBoundaries(unittest.TestCase):
    def test_actual_probe_helper_requires_exact_json_boolean(self):
        self.assertTrue(strict_probe_success({"ok":True}))
        invalid=[{"ok":1},{"ok":1.0},{"ok":"true"},{"ok":"True"},{"ok":False},{"ok":0},{"ok":None},{"ok":True,"extra":1},{},[True],None,True]
        for value in invalid:
            with self.subTest(value=value):self.assertFalse(strict_probe_success(value))

    def test_sdk_pipe_write_uses_absolute_deadline_and_poison(self):
        from mirofish_native_model import SDKBridge
        process=subprocess.Popen([str(PYTHON),'-B','-c','import time;time.sleep(10)'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
        group=OwnedGroup(process)
        try:
            os.set_blocking(process.stdin.fileno(),False)
            bridge=object.__new__(SDKBridge);bridge.process=process;bridge.poisoned=False;bridge.sequence=0;bridge.buffer=b'';bridge.deadline=time.monotonic()+3
            before=time.monotonic()
            with self.assertRaises(NativeRefused):bridge.call('embed',deadline=before+.2,body={'input':'x'*200000})
            self.assertLess(time.monotonic()-before,.6)
            self.assertTrue(bridge.poisoned)
            with self.assertRaises(NativeRefused):bridge.call('embed',deadline=time.monotonic()+1,body={})
        finally:
            group.stop(time.monotonic()+4);process.stdin.close();process.stdout.close()

    def test_jvm_preserves_original_home_only_and_owned_cwd_paths(self):
        graph=ROOT/'graph-contract'
        self.assertNotIn('HOME',graph_environment(graph,None))
        env=graph_environment(graph,'/original-home-sentinel')
        self.assertEqual(env['HOME'],'/original-home-sentinel')
        self.assertEqual(env['NEO4J_CONF'],str(graph/'conf'))
        self.assertNotIn('OPENAI_API_KEY',env)

    def test_real_group_cleanup_preserves_supervisor_and_unrelated(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            marker = Path(tmp) / "child.json"
            code = "import subprocess,sys,time,json;from pathlib import Path;p=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(120)']);Path(sys.argv[1]).write_text(json.dumps({'child':p.pid}));time.sleep(120)"
            unrelated = subprocess.Popen([str(PYTHON), "-B", "-c", "import time;time.sleep(120)"], start_new_session=True)
            process = subprocess.Popen([str(PYTHON), "-B", "-c", code, str(marker)], start_new_session=True)
            group = OwnedGroup(process)
            try:
                end = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < end: time.sleep(.02)
                self.assertTrue(marker.exists())
                receipt = group.stop(time.monotonic()+7)
                self.assertTrue(receipt["stopped"]); self.assertIsNone(unrelated.poll())
                self.assertNotEqual(receipt["owner"]["pgid"], os.getpgrp())
            finally:
                if process.poll() is None: group.stop(time.monotonic()+7)
                unrelated.terminate(); unrelated.wait(3)
    @unittest.skipUnless(sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists(), "macOS confinement required")
    def test_actual_os_files_and_network_boundary(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root = Path(tmp); producer = root / "producer"; entry = root / "entry"
            for path in [producer / "backend/uploads", producer / "backend/log", producer / "backend/logs", producer / "tmp", entry]: path.mkdir(parents=True)
            readonly = producer / "source.txt"; readonly.write_text("source sentinel")
            authority = root / "observer-authority.fixture"; authority.write_text("nonsecret offline sentinel")
            listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(1); port = listener.getsockname()[1]
            spare = socket.socket(); spare.bind(("127.0.0.1", 0)); graph = spare.getsockname()[1]; spare.close()
            policy = root / "worker.sb"; policy.write_text(worker_policy(producer, entry, port, graph))
            probe = entry / "probe.py"
            probe.write_text("import socket,json,sys\nfrom pathlib import Path\np=Path(sys.argv[1]);a=Path(sys.argv[2]);results={}\nfor k,fn in [('source_write',lambda:p.write_text('changed')),('observer_read',lambda:a.read_text()),('home_read',lambda:open('/Users','rb'))]:\n try:fn();results[k]=False\n except PermissionError:results[k]=True\nPath(sys.argv[3]).write_text('owned write allowed')\nfor k,host,port in [('direct_upstream','127.0.0.1',1235),('external','198.51.100.1',9),('owned_proxy','127.0.0.1',int(sys.argv[4]))]:\n s=socket.socket();s.settimeout(1)\n try:s.connect((host,port));results[k]=(k=='owned_proxy')\n except PermissionError:results[k]=(k!='owned_proxy')\n except OSError:results[k]=False\n finally:s.close()\nprint(json.dumps(results))\n")
            command = ["/usr/bin/sandbox-exec", "-f", str(policy), str(PYTHON), "-B", str(probe), str(readonly), str(authority), str(producer / "backend/uploads/positive.txt"), str(port)]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=10, env={"PATH": "/usr/bin:/bin", "TMPDIR": str(producer / "tmp"), "PYTHONDONTWRITEBYTECODE": "1"})
                self.assertEqual(result.returncode, 0, result.stderr)
                values = json.loads(result.stdout); self.assertTrue(all(values.values()), values)
                self.assertEqual(readonly.read_text(), "source sentinel")
                self.assertEqual((producer / "backend/uploads/positive.txt").read_text(), "owned write allowed")
            finally: listener.close()


if __name__ == "__main__": unittest.main()
