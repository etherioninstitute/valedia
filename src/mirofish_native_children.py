"""Private inherited fixed-role host child launch bridge; no execution RPC surface."""
import json,os,re,select,socket,subprocess,threading,time,uuid
from pathlib import Path
from mirofish_native_gateway import NativeRefused,encoded,decode,atomic

def require(ok,why):
    if not ok:raise NativeRefused(why)

def receive(sock,end):
    data=bytearray()
    while b"\n" not in data:
        require(time.monotonic()<end,'host control deadline')
        ready,_,_=select.select([sock],[],[],min(.1,max(0,end-time.monotonic())))
        if ready:
            part=sock.recv(1);require(part,'host control disconnected');data.extend(part)
            require(len(data)<=16384,'host control byte bound')
    return decode(bytes(data[:-1]))

class HostClient:
    def __init__(self):
        self.sock=socket.socket(fileno=int(os.environ['MIROFISH_HOST_CONTROL_FD']));self.lock=threading.Lock()
    def call(self,op,role=None,**fields):
        with self.lock:
            end=min(float(os.environ['MIROFISH_HOST_DEADLINE']),time.monotonic()+8)
            self.sock.settimeout(max(.1,end-time.monotonic()))
            self.sock.sendall(encoded({'op':op,**({'role':role} if role else {}),**fields})+b'\n')
            value=receive(self.sock,end);require(value.get('ok') is True,'fixed host refused: '+str(value.get('reason')))
            return value['data']
_CLIENT=None
def client():
    global _CLIENT
    if _CLIENT is None:_CLIENT=HostClient()
    return _CLIENT

class RemoteProcess:
    def __init__(self,role,info,stdin=None):
        self.role=role;self.pid=info['owner']['pid'];self.stdin=stdin
    def poll(self):return client().call('poll',self.role)['returncode']
class RemoteGroup:
    def __init__(self,role,info):self.role=role;self.owner=info['owner']
    def stop(self,deadline):return client().call('stop',self.role)['cleanup']
def start_graph():
    info=client().call('start','graph');return RemoteProcess('graph',info),RemoteGroup('graph',info)
def start_worker(model,secret,active_deadline):
    info=client().call('start','worker',model=model,secret=secret,active_deadline=active_deadline)
    stream=os.fdopen(int(os.environ['MIROFISH_WORKER_COMMAND_FD']),'wb',buffering=0)
    return RemoteProcess('worker',info,stream),RemoteGroup('worker',info),int(os.environ['MIROFISH_WORKER_EVENT_FD'])

class HostChildren:
    def __init__(self,work,gate,deadline,original_home,preflight=False):
        self.work=Path(work);self.gate=Path(gate) if gate else None;self.deadline=deadline;self.home=original_home;self.preflight=preflight
        self.host,self.observer=socket.socketpair();self.command_read,self.command_write=os.pipe();self.event_read,self.event_write=os.pipe()
        self.roles={};self.used=set();self.bound=None;self.closed=False;self.events=[]
        self.evidence=self.work/'builder'/('host-child-preflight/'+uuid.uuid4().hex) if preflight else self.work/'builder'
        self.evidence.mkdir(parents=True,exist_ok=True)
    def observer_fds(self):return (self.observer.fileno(),self.command_write,self.event_read)
    def observer_env(self):return dict(zip(['MIROFISH_HOST_CONTROL_FD','MIROFISH_WORKER_COMMAND_FD','MIROFISH_WORKER_EVENT_FD'],map(str,self.observer_fds())))
    def after_observer_spawn(self):
        self.observer.close();os.close(self.command_write);os.close(self.event_read)
    def bind(self,value):
        require(set(value)=={'op','run_id','candidate_sha256'} and re.fullmatch('[0-9a-f]{32}',value['run_id']) and self.bound is None,'one fixed run binding required')
        if not self.preflight:
            from mirofish_native_run import digest
            claim=decode((self.work/'builder/one-attempt.claim').read_bytes());gate=decode(self.gate.read_bytes())
            require(claim.get('run_id')==value['run_id'] and claim['gate_sha256']==digest(self.gate) and claim['candidate_sha256']==value['candidate_sha256']==gate['candidate_sha256']==digest(self.work/'builder/candidate-manifest.json'),'host gate/claim/candidate binding')
            require(gate['action']=='one-native-attempt' and gate['outcome']=='VALEDIA-MIROFISH-FRESH-1','host fixed authority')
        self.bound=value['run_id'];return {'bound':True}
    def start(self,role,value):
        from mirofish_native_run import PYTHON,RUNTIME,OwnedGroup,graph_environment,worker_environment
        require(self.bound is not None and role in {'graph','worker'} and role not in self.used,'fixed role start binding/one-use')
        require(set(value)==({'op','role'} if role=='graph' else {'op','role','model','secret','active_deadline'}),'fixed role fields')
        graph=self.work/'builder/graph';entry=self.work/'owned-source/worker-entry';producer=self.work/'owned-source/producer'
        if role=='graph':
            argv=[str(RUNTIME/'neo4j-community-2026.10.1/bin/neo4j'),'--help' if self.preflight else 'console'];env=graph_environment(graph,self.home);cwd=graph;passed=();stdin=subprocess.DEVNULL
        else:
            require(value['model']=='mf-native-'+self.bound and re.fullmatch('[0-9a-f]{64}',value['secret']) and type(value['active_deadline']) in (int,float) and time.monotonic()<value['active_deadline']<=self.deadline,'fixed worker model/token/deadline')
            env=worker_environment(producer,'http://127.0.0.1:62471/v1',value['model'],value['secret'],62472,self.event_write)
            env['MIROFISH_NATIVE_ACTIVE_DEADLINE']=str(value['active_deadline']);cwd=producer/'backend';passed=(self.event_write,);stdin=self.command_read
            argv=[str(PYTHON),'-B',str(entry/('mirofish_native_children.py' if self.preflight else 'mirofish_native_run.py')),'--dummy-worker' if self.preflight else '--worker']
        require(not any(k.startswith(('MIROFISH_RESOURCE_','MIROFISH_HOST_','MIROFISH_WORKER_')) for k in env),'worker/graph authority leak')
        command=['/usr/bin/sandbox-exec','-f',str(self.work/'builder'/f'{role}.sb'),*argv]
        log=(self.evidence/f'{role}-console.log').open('xb');process=subprocess.Popen(command,cwd=cwd,env=env,stdin=stdin,stdout=log,stderr=subprocess.STDOUT,pass_fds=passed,start_new_session=True)
        group=OwnedGroup(process);self.roles[role]=(process,group,log);self.used.add(role)
        if role=='worker':os.close(self.command_read);os.close(self.event_write);self.command_read=self.event_write=None
        row={'role':role,'command':command,'owner':group.owner};self.events.append(row);atomic(self.evidence/f'{role}-owner.json',row)
        return {'owner':group.owner}
    def handle(self,value):
        require(type(value) is dict,'host request shape');op=value.get('op')
        if op=='bind':return self.bind(value)
        role=value.get('role');require(role in {'graph','worker'},'fixed role only')
        if op=='start':return self.start(role,value)
        require(set(value)=={'op','role'} and op in {'poll','stop'} and role in self.roles,'closed status/cleanup protocol')
        process,group,log=self.roles[role]
        if op=='poll':return {'returncode':process.poll()}
        cleanup=group.stop(min(self.deadline,time.monotonic()+5));return {'cleanup':cleanup}
    def serve(self):
        try:
            while time.monotonic()<self.deadline:
                value=receive(self.host,self.deadline)
                try:reply={'ok':True,'data':self.handle(value)}
                except BaseException as exc:reply={'ok':False,'reason':type(exc).__name__+':'+str(exc)[:500]}
                self.host.sendall(encoded(reply)+b'\n')
        except (OSError,ValueError,NativeRefused):pass
        finally:self.close()
    def close(self):
        if self.closed:return
        self.closed=True
        for role,(process,group,log) in self.roles.items():
            try:self.events.append({'role':role,'host_finally_cleanup':group.stop(min(self.deadline,time.monotonic()+5))})
            except BaseException as exc:self.events.append({'role':role,'cleanup_failure':str(exc)})
            log.close()
        for fd in [self.command_read,self.event_write]:
            if fd is not None:
                try:os.close(fd)
                except OSError:pass
        self.host.close();atomic(self.evidence/'host-children-result.json',{'preflight':self.preflight,'events':self.events})
        atomic(self.work/'builder/host-children-result.json',{'preflight':self.preflight,'evidence_directory':str(self.evidence),'events':self.events})

def dummy_worker():
    require(len(os.sys.argv)==2 and os.sys.argv[1]=='--dummy-worker','fixed dummy only')
    forbidden=[k for k in os.environ if k.startswith(('MIROFISH_HOST_','MIROFISH_RESOURCE_','MIROFISH_WORKER_'))]
    sockets=[]
    for fd in range(3,128):
        try:
            import stat
            if stat.S_ISSOCK(os.fstat(fd).st_mode):sockets.append(fd)
        except OSError:pass
    require(not forbidden and not sockets,'dummy inherited host authority')
    line=os.sys.stdin.buffer.readline(1024);require(line==b'fixed-preflight\n','fixed dummy command')
    os.write(int(os.environ['NATIVE_EVENT_FD']),encoded({'ok':True,'forbidden_environment':forbidden,'socket_fds':sockets})+b'\n')
    time.sleep(20)
if __name__=='__main__':dummy_worker()
