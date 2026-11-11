"""Owned process-local OASIS follower DB lookup routing; no package/env writes."""
from contextvars import ContextVar
from pathlib import Path
from functools import wraps
import hashlib,importlib,inspect,os,sys

OASIS_ROOT=Path('/Volumes/NIMROD_SSD/Qwen-Weight-Space/runtime/mirofish-local-20261106/venv-py311/lib/python3.11/site-packages/oasis')
PINS={'oasis.social_platform.database': 'b5ec7ea0faa2453f626af84e4ec750769eb40bbed339f6dfef27307407c61ede', 'oasis.social_agent.agent_environment': '94f703f443a686469245a2e5c5a51474db86a3ce62f9d92720dd3a898b4b69e7'}
_ACTIVE=ContextVar('owned_oasis_database_path',default=None)
_ROOT=None
class OwnedDBPathRefused(RuntimeError):pass

def require(ok,why):
    if not ok:raise OwnedDBPathRefused(why)

def absolute_clean(value):
    path=Path(value)
    require(path.is_absolute() and not any(p.is_symlink() for p in (path,*path.parents)),'absolute nonsymlink owned path required')
    return path.resolve(strict=False)

def database_path(root,simulation_dir,platform):
    root=absolute_clean(root);simulation_dir=absolute_clean(simulation_dir)
    require(root.is_dir() and simulation_dir.is_dir() and simulation_dir.parent==root,'simulation directory must be a direct owned child')
    require(platform in {'twitter','reddit'},'fixed native platform required')
    path=absolute_clean(simulation_dir/(platform+'_simulation.db'))
    require(not path.exists() or path.is_file(),'database must be regular or not yet created')
    return path

def lookup():
    value=_ACTIVE.get();require(value is not None,'unbound OASIS database context refused')
    root,simulation_dir,platform,path=value
    require(_ROOT==root and database_path(root,simulation_dir,platform)==path,'OASIS database context changed')
    return str(path)

def install(root):
    global _ROOT
    root=absolute_clean(root);require(root.is_dir(),'owned simulations root required')
    require(os.environ.get('MIROFISH_TEST_ISOLATED')=='1' and os.environ.get('MIROFISH_TEST_ROUTE')=='native-local' and root==absolute_clean(Path(os.environ['MIROFISH_TEST_ROOT'])/'backend/uploads/simulations'),'exact owned native simulations root required')
    require(not os.environ.get('OASIS_DB_PATH'),'process-global OASIS_DB_PATH is incompatible with native parallel routing')
    for name,sha in PINS.items():
        path=OASIS_ROOT/('social_platform/database.py' if name.endswith('.database') else 'social_agent/agent_environment.py')
        require(hashlib.sha256(path.read_bytes()).hexdigest()==sha,'pinned OASIS source changed: '+name)
    database=importlib.import_module('oasis.social_platform.database');environment=importlib.import_module('oasis.social_agent.agent_environment')
    require(Path(database.__file__)==OASIS_ROOT/'social_platform/database.py' and Path(environment.__file__)==OASIS_ROOT/'social_agent/agent_environment.py','installed OASIS module origin changed')
    if _ROOT is not None:
        require(_ROOT==root and database.get_db_path is lookup and environment.get_db_path is lookup,'OASIS adapter binding changed')
        return
    original=database.get_db_path
    require(environment.get_db_path is original and original.__module__=='oasis.social_platform.database','original OASIS lookup identity changed')
    bindings=sorted(name for name,module in tuple(sys.modules.items()) if name.startswith('oasis.') and getattr(module,'get_db_path',None) is original)
    require(bindings==sorted(PINS),'unexpected OASIS get_db_path alias; refuse partial routing')
    _ROOT=root;database.get_db_path=lookup;environment.get_db_path=lookup

def wrap_platforms(twitter,reddit,root):
    root=absolute_clean(root);install(root)
    def bind(function,platform):
        require(inspect.iscoroutinefunction(function),'native platform must remain async')
        signature=inspect.signature(function);require('simulation_dir' in signature.parameters,'native simulation_dir seam changed')
        @wraps(function)
        async def wrapped(*args,**kwargs):
            arguments=signature.bind(*args,**kwargs).arguments
            simulation_dir=absolute_clean(arguments['simulation_dir']);path=database_path(root,simulation_dir,platform)
            token=_ACTIVE.set((root,simulation_dir,platform,path))
            try:return await function(*args,**kwargs)
            finally:_ACTIVE.reset(token)
        return wrapped
    return bind(twitter,'twitter'),bind(reddit,'reddit')
