"""Fixed host resource sampler and read-only confined observer capsule handoff.

The host uses original setuid /bin/ps for RSS outside Seatbelt. The private0555
copy is ONLY for owned lifecycle metadata, never resource qualification.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from mirofish_native_gateway import NativeRefused,atomic,decode,encoded

FIELDS={'schema','outcome','nonce','host_pid','host_started','source_sha256','sequence','monotonic','utc','free_percent','all_process_rss_bytes','rss_rows','rss_positive_rows','ps_sha256','ps_mode','host_deadline','owned_scope_rss_bytes','scope_rows','excluded_rows','baseline_source_sha256','scope_roots_sha256','tracked_identities_sha256','prompt_cache_route',
        'pressure_level','swap_used_bytes','co_resident_rss_bytes','co_resident_rows'}
EXPECTED_PS_SHA='d0e7b30250a9b1bd2afd1d6462a10d8a2d10ca448d9ce76be067db8a9bf459e0'
_LAST={}

# TMF5 addendum 1 (b): the declared co-resident, the order-book collector's decision model. The same string as FILE in
# valedia_trader/core/decision_model.py (a test fails if they differ); staging writes it into the staged copy. A process is the
# co-resident when its executable is LM Studio's llama-server and its --model argument, resolved with realpath, EQUALS this file
# resolved the same way. Its row is left out of the run's owned memory and recorded beside it; two such rows stop the run.
CO_RESIDENT_FILE='~/.lmstudio/models/debackerl/elephant-1-Q8_0-GGUF/elephant-1.Q8_0.gguf'

CACHE_SOURCE=Path('/Users/burtonchatelain/.lmstudio/.internal/prompt-cache')
CACHE_TARGET=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space/cache/bionic/prompt-cache')
CACHE_SSD=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space')
CACHE_DEVICE=16777246

def validate_cache_route(route):
    require(type(route) is dict and set(route)=={'source','target','device','ssd_device','source_symlink','target_directory'},'prompt cache route schema')
    require(route['source']==str(CACHE_SOURCE) and route['target']==str(CACHE_TARGET) and route['source_symlink'] is True and route['target_directory'] is True and type(route['device']) is int and type(route['ssd_device']) is int and route['device']==route['ssd_device']==CACHE_DEVICE,'prompt cache SSD route changed/detached')
    return route

def cache_route_metadata():
    # Exact approved pointer/volume metadata only; never inspect cache contents.
    require(CACHE_SOURCE.is_symlink() and CACHE_TARGET.is_dir() and not CACHE_TARGET.is_symlink() and CACHE_SOURCE.resolve(strict=True)==CACHE_TARGET and CACHE_TARGET.resolve(strict=True)==CACHE_TARGET,'prompt cache pointer changed')
    return validate_cache_route({'source':str(CACHE_SOURCE),'target':str(CACHE_TARGET),'device':CACHE_TARGET.stat().st_dev,'ssd_device':CACHE_SSD.stat().st_dev,'source_symlink':True,'target_directory':True})


def require(ok,why):
    if not ok:raise NativeRefused(why)

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def validate_capsule(envelope,key,nonce,host,source_sha,*,now=None,utc=None):
    now=time.monotonic() if now is None else now;utc=datetime.now(timezone.utc) if utc is None else utc
    require(type(envelope) is dict and set(envelope)=={'sample','mac'},'resource capsule envelope')
    sample=envelope['sample'];require(type(sample) is dict and set(sample)==FIELDS,'resource capsule fields')
    require(type(envelope['mac']) is str and hmac.compare_digest(envelope['mac'],hmac.new(key.encode(),encoded(sample),hashlib.sha256).hexdigest()),'resource capsule signature')
    require(type(sample['schema']) is int and sample['schema']==1 and sample['outcome']=='VALEDIA-MIROFISH-FRESH-1' and sample['nonce']==nonce and sample['host_pid']==host['pid'] and sample['host_started']==host['started'] and sample['source_sha256']==source_sha,'resource sampler identity')
    for field in ['host_pid','sequence','free_percent','all_process_rss_bytes','rss_rows','rss_positive_rows','ps_mode','owned_scope_rss_bytes','scope_rows','excluded_rows','co_resident_rss_bytes','co_resident_rows']:
        require(type(sample[field]) is int,'resource integer field')
    # The pressure level is recorded as read; whether it is a valid reading is for the run's stop rule to judge (TMF5 d).
    require(sample['co_resident_rss_bytes']>=0 and sample['co_resident_rows']>=0 and (sample['co_resident_rows']>0 or sample['co_resident_rss_bytes']==0),'co-resident record shape')
    require(sample['swap_used_bytes'] is None or (type(sample['swap_used_bytes']) is int and sample['swap_used_bytes']>=0),'swap record shape')
    require(0<=sample['free_percent']<=100 and sample['all_process_rss_bytes']>0 and 0<sample['rss_positive_rows']<=sample['rss_rows'] and sample['ps_mode']==0o4755 and sample['ps_sha256']==EXPECTED_PS_SHA,'complete privileged resource sample required')
    require(0<sample['scope_rows']<=sample['rss_rows'] and sample['scope_rows']+sample['excluded_rows']==sample['rss_rows'] and 0<sample['owned_scope_rss_bytes']<=sample['all_process_rss_bytes'] and all(type(sample[k]) is str and len(sample[k])==64 for k in ['baseline_source_sha256','scope_roots_sha256','tracked_identities_sha256']),'owned resource scope proof')
    require(type(sample['monotonic']) in (int,float) and type(sample['host_deadline']) in (int,float) and -.1<=now-sample['monotonic']<=3 and now<sample['host_deadline'],'resource capsule stale/future/expired')
    stamp=datetime.fromisoformat(sample['utc']);require(stamp.tzinfo is not None and -.1<=(utc-stamp).total_seconds()<=3,'resource UTC stale/future')
    validate_cache_route(sample['prompt_cache_route'])
    old=_LAST.get(nonce)
    require(sample['sequence']>=1 and (old is None or sample['sequence']>=old[0] and sample['monotonic']>=old[1]),'resource sequence rollback')
    _LAST[nonce]=(sample['sequence'],sample['monotonic'])
    return sample

def resource_from_capsule():
    path=Path(os.environ.get('MIROFISH_RESOURCE_CAPSULE',''))
    key=os.environ.get('MIROFISH_RESOURCE_KEY','');nonce=os.environ.get('MIROFISH_RESOURCE_NONCE','')
    require(path.is_absolute() and path.name=='sample.json' and not path.is_symlink() and len(key)==64 and len(nonce)==32,'host resource binding missing')
    host=decode(os.environ.get('MIROFISH_RESOURCE_HOST','{}'))
    raw=path.read_bytes();require(len(raw)<=16384,'resource capsule bytes')
    return validate_capsule(decode(raw),key,nonce,host,digest(__file__))

def process_info_tool():
    value=os.environ.get('MIROFISH_IDENTITY_PS')
    if value is None:return '/bin/ps'  # Host-only/unit-test path; observer execution requires a capsule first.
    path=Path(value)
    require(path.is_absolute() and path.is_file() and not path.is_symlink() and path.stat().st_mode&0o7777==0o555 and digest(path)==EXPECTED_PS_SHA,'owned identity-only ps copy changed')
    return str(path)


def process_rows():
    result=subprocess.run(['/bin/ps','-axo','pid=,ppid=,pgid=,lstart=,rss=,comm='],env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=1.2,check=True)
    rows=[]
    for line in result.stdout.splitlines():
        values=line.split(None,9)
        require(len(values)==10 and all(values[i].isdigit() for i in [0,1,2,8]),'fixed host process metadata malformed')
        rows.append({'pid':int(values[0]),'ppid':int(values[1]),'pgid':int(values[2]),'start':' '.join(values[3:8]),'rss':int(values[8])*1024,'comm':values[9]})
    require(rows and len({r['pid'] for r in rows})==len(rows),'complete host identity rows')
    return rows

class OwnedResourceScope:
    def __init__(self,baseline,listener_pids):
        self.baseline={(r['pid'],r['start']) for r in baseline}
        self.baseline_sha=hashlib.sha256(encoded(baseline)).hexdigest()
        self.tracked={(r['pid'],r['start']) for r in baseline if self.bionic(r['comm']) or r['pid'] in listener_pids}
        require(self.tracked,'baseline Bionic/listener family unavailable')
        self.expand(baseline);self.roots_sha=hashlib.sha256(encoded(sorted(self.tracked))).hexdigest()
    @staticmethod
    def bionic(comm):
        value=comm.lower()
        return 'bionic' in value or '/.lmstudio/' in value or '/lm studio.app/' in value or '/lm-studio/' in value
    def expand(self,rows):
        while True:
            parents={r['pid'] for r in rows if (r['pid'],r['start']) in self.tracked}
            extra={(r['pid'],r['start']) for r in rows if r['ppid'] in parents}
            old=len(self.tracked);self.tracked.update(extra)
            if len(self.tracked)==old:break
    def measure(self,rows,co_resident=frozenset()):
        """co_resident: the (pid, start) identities of this sample that serve the declared co-resident's file. They are left
        out of the owned figure (counted among the excluded rows) and their memory is returned beside it."""
        self.tracked.update((r['pid'],r['start']) for r in rows if (r['pid'],r['start']) not in self.baseline or self.bionic(r['comm']))
        self.expand(rows)
        co=[r for r in rows if (r['pid'],r['start']) in co_resident]
        selected=[r for r in rows if (r['pid'],r['start']) in self.tracked and (r['pid'],r['start']) not in co_resident]
        return {'owned_scope_rss_bytes':sum(r['rss'] for r in selected),'scope_rows':len(selected),'excluded_rows':len(rows)-len(selected),'baseline_source_sha256':self.baseline_sha,'scope_roots_sha256':self.roots_sha,'tracked_identities_sha256':hashlib.sha256(encoded(sorted(self.tracked))).hexdigest(),
                'co_resident_rss_bytes':sum(r['rss'] for r in co),'co_resident_rows':len(co)}


def declared_co_resident(value=CO_RESIDENT_FILE):
    """The declared file made absolute (home expanded) and resolved with realpath."""
    expanded=os.path.expanduser(value)
    require(os.path.isabs(expanded),'declared co-resident file cannot be made absolute')
    return os.path.realpath(expanded)

def llama_server(comm):
    """LM Studio's llama-server executable: the only rows whose argument list is ever read."""
    return type(comm) is str and Path(comm).name=='llama-server' and '/.lmstudio/' in comm

def model_argument(argv):
    """The single value given to --model (`--model <path>` or `--model=<path>`), or None. Nothing else of argv is kept."""
    if type(argv) is not list or not all(type(a) is str for a in argv):return None
    found=[argv[i+1] for i,a in enumerate(argv[:-1]) if a=='--model']+[a[len('--model='):] for a in argv if a.startswith('--model=')]
    return found[0] if len(found)==1 and found[0] else None

def _process_arguments(pid):
    """One process's argument vector through sysctl KERN_PROCARGS2 (exact, no shell joining), or None. Called only for
    llama-server rows, whose caller keeps only the --model value. No argument text is ever logged, written or raised."""
    try:
        import ctypes
        libc=ctypes.CDLL(None,use_errno=True);mib=(ctypes.c_int*3)(1,49,int(pid));size=ctypes.c_size_t(0)
        if libc.sysctl(mib,3,None,ctypes.byref(size),None,0)!=0 or not 4<size.value<=4*1024*1024:return None
        buffer=ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib,3,buffer,ctypes.byref(size),None,0)!=0:return None
        raw=buffer.raw[:size.value];count=int.from_bytes(raw[:4],'little');rest=raw[4:];end=rest.find(b'\0')
        if end<0:return None
        while end<len(rest) and rest[end]==0:end+=1
        parts=rest[end:].split(b'\0')
        return [part.decode('utf-8','surrogateescape') for part in parts[:count]] if len(parts)>=count else None
    except Exception:
        return None

def resolved_model(pid,arguments=_process_arguments):
    """The --model value of one llama-server process, resolved with realpath, or None (absent, relative, ambiguous, or ending
    in a slash, which names a folder and never the file: realpath would drop the slash and make it look equal)."""
    value=model_argument(arguments(pid))  # the argument vector is dropped in this same expression
    return os.path.realpath(value) if value is not None and os.path.isabs(value) and not value.endswith('/') else None

def co_resident_identities(rows,declared,resolve=resolved_model):
    """The (pid, start) identities whose executable is llama-server and whose resolved --model equals the declared file."""
    return frozenset((r['pid'],r['start']) for r in rows if llama_server(r['comm']) and resolve(r['pid'])==declared)

def _sysctl_bytes(name,size):
    try:
        import ctypes
        libc=ctypes.CDLL(None,use_errno=True);buffer=ctypes.create_string_buffer(size);length=ctypes.c_size_t(size)
        if libc.sysctlbyname(name.encode(),buffer,ctypes.byref(length),None,0)!=0 or length.value!=size:return None
        return buffer.raw[:size]
    except Exception:
        return None

def pressure_level():
    """kern.memorystatus_vm_pressure_level as read (1 normal, 2 warning, 4 critical), or None when it cannot be read."""
    raw=_sysctl_bytes('kern.memorystatus_vm_pressure_level',4)
    return None if raw is None else int.from_bytes(raw,'little',signed=True)

def swap_used_bytes():
    """vm.swapusage's used bytes (struct xsw_usage: total, avail, used as 64-bit integers, then page size and the encrypted
    flag), or None when it cannot be read. Recorded only (TMF5 g)."""
    raw=_sysctl_bytes('vm.swapusage',32)
    return None if raw is None else int.from_bytes(raw[16:24],'little')

def sample_host(nonce,host,sequence,deadline,scope,declared=None,resolve=resolved_model):
    require(Path('/bin/ps').stat().st_mode&0o7777==0o4755 and digest('/bin/ps')==EXPECTED_PS_SHA,'original privileged ps identity changed')
    pressure=subprocess.run(['/usr/bin/memory_pressure'],env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=1.2,check=True)
    match=re.search(r'System-wide memory free percentage:\s*(\d+)%',pressure.stdout);require(match,'host memory measurement')
    rows=process_rows();co=co_resident_identities(rows,declared,resolve) if declared is not None else frozenset();measurement=scope.measure(rows,co)
    return {'schema':1,'outcome':'VALEDIA-MIROFISH-FRESH-1','nonce':nonce,'host_pid':host['pid'],'host_started':host['started'],'source_sha256':digest(__file__),'sequence':sequence,'monotonic':time.monotonic(),'utc':datetime.now(timezone.utc).isoformat(),'free_percent':int(match[1]),'all_process_rss_bytes':sum(r['rss'] for r in rows),'rss_rows':len(rows),'rss_positive_rows':sum(r['rss']>0 for r in rows),'ps_sha256':digest('/bin/ps'),'ps_mode':0o4755,'host_deadline':deadline,'prompt_cache_route':cache_route_metadata(),
            'pressure_level':pressure_level(),'swap_used_bytes':swap_used_bytes(),**measurement}

def write_observer_identity(work,owner):
    """TMF5 (operator command): the observer's identity (pid, ps start string, process group), written right after it is
    spawned, before anything else."""
    require(type(owner) is dict and type(owner.get('pid')) is int and type(owner.get('pgid')) is int and type(owner.get('start')) is str and owner['start'],'observer identity shape')
    value={'pid':owner['pid'],'start':owner['start'],'pgid':owner['pgid'],'written_utc':datetime.now(timezone.utc).isoformat()}
    atomic(Path(work)/'builder/observer-identity.json',value)
    return value

class ResourceHistory:
    """Private bounded signed snapshots, durable before capsule publication."""
    MAX_RECORDS=6001
    MAX_BYTES=16*1024*1024
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'samples.jsonl'
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        self.stream=os.fdopen(fd,'wb');self.records=0;self.bytes=0
    def publish(self,envelope):
        require(type(envelope) is dict and set(envelope)=={'sample','mac'} and type(envelope['sample']) is dict and set(envelope['sample'])==FIELDS,'resource history schema')
        raw=encoded(envelope)+b'\n'
        require(len(raw)<=16385 and self.records<self.MAX_RECORDS and self.bytes+len(raw)<=self.MAX_BYTES,'bounded resource history exhausted')
        self.stream.write(raw);self.stream.flush();os.fsync(self.stream.fileno())
        self.records+=1;self.bytes+=len(raw)
        atomic(self.root/'sample.json',envelope)
    def close(self):self.stream.close()


def launch(work,gate=None,preflight=False):
    from mirofish_native_run import PYTHON,OwnedGroup
    from mirofish_native_model import SDK_PORT
    work=Path(work);base=work/'host-resource';base.mkdir(mode=0o700,exist_ok=True)
    nonce=uuid.uuid4().hex;root=base/nonce;root.mkdir(mode=0o700);key=uuid.uuid4().hex+uuid.uuid4().hex
    started=subprocess.run(['/bin/ps','-p',str(os.getpid()),'-o','lstart='],capture_output=True,text=True,timeout=2,check=True).stdout.strip();host={'pid':os.getpid(),'started':started}
    deadline=time.monotonic()+(30 if preflight else 2400);stop=threading.Event();errors=[]
    path=root/'sample.json';history=ResourceHistory(root)
    baseline=process_rows()
    listeners=subprocess.run(['/usr/sbin/lsof','-nP',f'-iTCP@127.0.0.1:{SDK_PORT}','-sTCP:LISTEN','-Fp'],env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=1.2,check=True)
    listener_pids={int(line[1:]) for line in listeners.stdout.splitlines() if line.startswith('p') and line[1:].isdigit()}
    scope=OwnedResourceScope(baseline,listener_pids);declared=declared_co_resident()
    atomic(root/'baseline.json',{'rows':baseline,'listener_pids':sorted(listener_pids),'baseline_source_sha256':scope.baseline_sha,'scope_roots_sha256':scope.roots_sha,'co_resident_file':CO_RESIDENT_FILE})
    def update(sequence):
        sample=sample_host(nonce,host,sequence,deadline,scope,declared);history.publish({'sample':sample,'mac':hmac.new(key.encode(),encoded(sample),hashlib.sha256).hexdigest()})
    update(1)
    def loop():
        sequence=1
        while not stop.wait(.4):
            try:sequence+=1;update(sequence)
            except BaseException as exc:errors.append(type(exc).__name__);return
    thread=threading.Thread(target=loop,name='owned-host-resource-sampler',daemon=True);thread.start()
    entry=work/'owned-source/observer-entry';env={name:os.environ[name] for name in ['HOME'] if name in os.environ}
    env.update(PATH='/usr/bin:/bin',TMPDIR=str(work/'tmp'),PYTHONDONTWRITEBYTECODE='1',MIROFISH_RESOURCE_CAPSULE=str(path),MIROFISH_RESOURCE_KEY=key,MIROFISH_RESOURCE_NONCE=nonce,MIROFISH_RESOURCE_HOST=json.dumps(host),MIROFISH_HOST_DEADLINE=str(deadline),MIROFISH_IDENTITY_PS=str(work/'builder/ps-identity'))
    command=['/usr/bin/sandbox-exec','-f',str(work/'builder/observer.sb'),str(PYTHON),'-B',str(entry/('mirofish_startup_preflight.py' if preflight else 'mirofish_native_run.py'))]
    if not preflight:require(gate is not None,'explicit lead gate required');command+=['--execute','--work',str(work),'--gate',str(gate)]
    from mirofish_native_children import HostChildren
    children=HostChildren(work,gate,deadline,os.environ.get('HOME'),preflight)
    env.update(children.observer_env())
    output=work/'builder'/('startup-preflight.json' if preflight else 'host-launch-result.json')
    process=None
    try:
        process=subprocess.Popen(command,env=env,cwd=work,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,pass_fds=children.observer_fds())
        # Host-side metadata is also fixed-field; do not collect command lines. The observer's identity is written before
        # anything else (TMF5: the operator command's handle on the run), and the group is owned from that moment.
        group=OwnedGroup(process);write_observer_identity(work,group.owner)
        children.after_observer_spawn()
        child_thread=threading.Thread(target=children.serve,name="fixed-host-children",daemon=True);child_thread.start()
        out,err=process.communicate(timeout=max(.1,deadline-time.monotonic()))
        record={'command':command,'exit':process.returncode,'stdout':out,'stderr':err,'host_sampler_errors':errors,'preflight':preflight,'capsule_path':str(path),'resource_history_path':str(history.path),'resource_history_records':history.records,'resource_history_bytes':history.bytes,'host':host,'key_recorded':False}
    except BaseException:
        if process is not None and process.poll() is None:group.stop(time.monotonic()+4)
        raise
    finally:
        stop.set();thread.join(3)
        if "child_thread" in locals():
            try:children.host.shutdown(2)
            except OSError:pass
            child_thread.join(6)
            require(not child_thread.is_alive(),"host child cleanup thread did not finish")
        children.close();history.close()
    require(not thread.is_alive(),'host sampler thread cleanup')
    atomic(output,record);return record

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--work',type=Path,required=True);parser.add_argument('--gate',type=Path);parser.add_argument('--preflight',action='store_true');args=parser.parse_args()
    result=launch(args.work,args.gate,args.preflight);print(json.dumps({'exit':result['exit'],'preflight':args.preflight}));return result['exit']

if __name__=='__main__':raise SystemExit(main())
