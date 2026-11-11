"""TA-1 pure, stdlib execution controls; imported without the third-party graph."""
from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time
from urllib.parse import urlparse
from urllib.request import build_opener, ProxyHandler, Request, HTTPRedirectHandler

from .adapter import TAError

LOG_LIMIT=65_536


def _bounded_child(command, payload: bytes, work: Path, timeout: float, log: Path, *, env=None):
    """Bound pipes and wall clock; terminate ONLY the spawned process group, then reap."""
    work=Path(work); log=Path(log)
    input_path=work/('child-input-'+os.urandom(8).hex()+'.json')
    fd=os.open(input_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as stream: stream.write(payload);stream.flush();os.fsync(stream.fileno())
        child_input=input_path.open('rb')
        log_fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            with child_input,os.fdopen(log_fd,'wb') as saved:
                proc=subprocess.Popen(command,stdin=child_input,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                      cwd=work,env=env,start_new_session=True,close_fds=True)
                selector=selectors.DefaultSelector()
                total=0; deadline=time.monotonic()+timeout;reason=None
                try:
                    for stream in (proc.stdout,proc.stderr):
                        os.set_blocking(stream.fileno(),False)
                        selector.register(stream,selectors.EVENT_READ)
                    while selector.get_map():
                        remaining=deadline-time.monotonic()
                        if remaining<=0:
                            reason='upstream child timed out; no receipt';break
                        for key,_ in selector.select(min(remaining,0.1)):
                            try: data=os.read(key.fileobj.fileno(),4096)
                            except BlockingIOError: continue
                            if not data:
                                selector.unregister(key.fileobj);key.fileobj.close();continue
                            available=LOG_LIMIT-total
                            if available:
                                saved.write(data[:available]);total+=min(len(data),available)
                            if len(data)>available:
                                reason='upstream child output limit exceeded; no receipt';break
                        if reason:break
                    if not reason and proc.poll() is None:
                        remaining=deadline-time.monotonic()
                        if remaining<=0:reason='upstream child timed out; no receipt'
                        else:
                            try:proc.wait(timeout=remaining)
                            except subprocess.TimeoutExpired:reason='upstream child timed out; no receipt'
                finally:
                    selector.close()
                    # Even when the leader exited, stop any remaining process in OUR group.
                    # No global signal, no unrelated process group or daemon touched.
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    try:proc.wait(timeout=3)
                    except subprocess.TimeoutExpired as exc:raise TAError('owned subprocess not reaped') from exc
                    for stream in (proc.stdout,proc.stderr):
                        if not stream.closed:stream.close()
                    saved.flush();os.fsync(saved.fileno())
                if reason:raise TAError(reason)
                return proc.returncode
        finally: pass
    finally: input_path.unlink(missing_ok=True)


class ModelBudget:
    """LangChain on_llm/on_chat_model callbacks: reserve before sending, count after.

    Per-call max_tokens is a request cap, *not* a total. Reserve the full call cap
    until metered usage returns; unmetered or over-limit responses fail closed.
    """
    def __init__(self,*,call_limit=24,output_limit=32768,per_call_limit=2048):
        if any(type(value) is not int or value<=0 for value in (call_limit,output_limit,per_call_limit)) or per_call_limit>output_limit:
            raise TAError('invalid model budget limits')
        self.call_limit=call_limit;self.output_limit=output_limit;self.per_call_limit=per_call_limit
        self.calls=0;self.output_tokens=0;self._active=set();self._lock=threading.Lock();self._poisoned=None
    def _fail(self,reason):
        self._poisoned=reason
        raise TAError(reason)
    def _check(self):
        if self._poisoned is not None:raise TAError('model budget poisoned: '+self._poisoned)
        if (type(self.calls) is not int or not 0<=self.calls<=self.call_limit or
            type(self.output_tokens) is not int or not 0<=self.output_tokens<=self.output_limit or
            not isinstance(self._active,set) or len(self._active)>self.calls):
            self._fail('invalid model budget counters')
    def _start(self,run_id):
        with self._lock:
            self._check()
            try: invalid_id=run_id is None or run_id in self._active
            except TypeError:invalid_id=True
            if invalid_id:self._fail('model callback identity missing/duplicate')
            if self.calls>=self.call_limit:self._fail('model call limit exceeded')
            if self.output_tokens+(len(self._active)+1)*self.per_call_limit>self.output_limit:
                self._fail('aggregate output budget exceeded before model call')
            self.calls+=1;self._active.add(run_id)
    def on_llm_start(self,serialized,prompts,*,run_id=None,**kwargs):self._start(run_id)
    def on_chat_model_start(self,serialized,messages,*,run_id=None,**kwargs):self._start(run_id)
    def on_llm_end(self,response,*,run_id=None,**kwargs):
        with self._lock:
            self._check()
            try: active=run_id in self._active
            except TypeError:active=False
            if not active:self._fail('model callback end without start')
            try:
                output=getattr(response,'llm_output',None)
                if output is None:output={}
                if not isinstance(output,dict):self._fail('invalid output token usage')
                usage=output.get('token_usage',output.get('usage'))
                if usage is not None:
                    if not isinstance(usage,dict):self._fail('invalid output token usage')
                    count=usage.get('completion_tokens',usage.get('output_tokens'))
                else:
                    generations=getattr(response,'generations',None) or []
                    counts=[getattr(getattr(g,'message',None),'usage_metadata',None)
                            for group in generations for g in group]
                    if not counts or any(not isinstance(u,dict) or type(u.get('output_tokens')) is not int or u['output_tokens']<0 for u in counts):
                        self._fail('missing output token usage; model run refused')
                    count=sum(u['output_tokens'] for u in counts if isinstance(u,dict))
            except TAError:raise
            except Exception as exc:
                self._fail('invalid output token usage: '+type(exc).__name__)
            if type(count) is not int or count<0:self._fail('missing output token usage; model run refused')
            if count>self.per_call_limit or self.output_tokens+count+(len(self._active)-1)*self.per_call_limit>self.output_limit:
                self._fail('aggregate output budget exceeded')
            self._active.remove(run_id)
            self.output_tokens+=count
    def on_llm_error(self,error,*,run_id=None,**kwargs):
        with self._lock:
            self._check()
            self._poisoned='model callback error'
    def assert_finished(self):
        with self._lock:
            self._check()
            if self._active or not self.calls:self._fail('model callbacks incomplete or no model calls')
            return {'model_calls':self.calls,'output_tokens':self.output_tokens,
                    'call_limit':self.call_limit,'output_limit':self.output_limit,
                    'per_call_limit':self.per_call_limit}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise TAError('model metadata redirect refused')

def _backend_json(url):
    opener=build_opener(ProxyHandler({}),_NoRedirect())
    try:
        with opener.open(Request(url,method='GET'),timeout=3) as response:
            raw=response.read(100_001)
            if len(raw)>100_000:raise TAError('oversized model metadata')
            return json.loads(raw)
    except (OSError,ValueError) as exc:raise TAError('local model metadata unavailable') from exc


def verify_local_model(req):
    """Positive backend-reported on-device residency, not loopback/listing alone.

    Trusts the selected LOCAL server's own metadata. It cannot attest the OS
    process against a malicious local server; no model prompt or load occurs.
    """
    name=req['model_name'];parsed=urlparse(req['model_base_url']);port=parsed.port
    if name.endswith(':cloud') or 'cloud-proxy' in name.lower(): raise TAError('cloud proxy model refused')
    if parsed.hostname not in ('localhost','127.0.0.1') or parsed.path.rstrip('/')!='/v1':
        raise TAError('unsupported local model backend')
    base=f'http://127.0.0.1:{port}'
    def rows(document):
        if not isinstance(document,dict) or not isinstance(document.get('models'),list):
            raise TAError('local model metadata unknown')
        return document['models']
    def remote(record):
        if not isinstance(record,dict):return True
        return (any(field in record for field in ('remote_host','remote_model')) or
                any(field in record and record[field] is not False for field in ('cloud','is_cloud','is_remote')))
    if port==1234:
        match=[r for r in rows(_backend_json(base+'/api/v1/models')) if isinstance(r,dict) and r.get('key')==name]
        if len(match)!=1 or match[0].get('type')!='llm' or match[0].get('format') not in ('gguf','mlx'):
            raise TAError('local model metadata unknown or cloud proxy')
        row=match[0]
        instances=row.get('loaded_instances')
        if remote(row):raise TAError('cloud/remote model refused')
        if not isinstance(instances,list) or not instances:
            raise TAError('local model not loaded')
        if any(remote(instance) or not isinstance(instance.get('id'),str) or not instance['id'].strip()
               for instance in instances):
            raise TAError('local model loaded instance identity unknown or remote')
        backend='lmstudio'
    elif port==11434:
        listed=rows(_backend_json(base+'/api/tags'))
        loaded=rows(_backend_json(base+'/api/ps'))
        matches=[r for r in listed if isinstance(r,dict) and r.get('name')==name]
        active=[r for r in loaded if isinstance(r,dict) and r.get('name',r.get('model'))==name]
        if len(matches)!=1:raise TAError('local model metadata unknown or cloud proxy')
        listing=matches[0];details=listing.get('details')
        if (remote(listing) or not isinstance(details,dict) or remote(details) or details.get('format')!='gguf' or
            type(listing.get('size')) is not int or listing['size']<10_000_000):
            raise TAError('local model metadata unknown or cloud proxy')
        listed_digest=listing.get('digest')
        if not isinstance(listed_digest,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',listed_digest):
            raise TAError('local model artifact digest missing or malformed')
        if len(active)!=1:raise TAError('local model not loaded or resident')
        resident=active[0];resident_details=resident.get('details')
        if remote(resident) or not isinstance(resident_details,dict) or remote(resident_details) or resident_details.get('format')!='gguf':
            raise TAError('cloud/remote or unknown resident model format refused')
        resident_digest=resident.get('digest')
        if not isinstance(resident_digest,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',resident_digest) or resident_digest!=listed_digest:
            raise TAError('resident model artifact digest missing, malformed or mismatched')
        row={'listing':listing,'resident':resident};backend='ollama'
    else:raise TAError('unsupported local model backend')
    digest=hashlib.sha256(json.dumps(row,sort_keys=True,default=str).encode()).hexdigest()
    return {'backend':backend,'model':name,'endpoint':req['model_base_url'],
            'local':True,'loaded':True,'metadata_sha256':digest}
