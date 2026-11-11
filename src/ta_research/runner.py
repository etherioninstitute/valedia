"""Pinned TradingAgents execution boundary. Import of upstream occurs ONLY in the sandboxed child.

No upstream install or import is performed by preflight, doctor, or tests. An explicit
later action-time approval is required before invoking run from the CLI.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from urllib.parse import urlparse

if __package__ in (None, ''):
    # -I excludes script directory: explicitly allow only this owned integration.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ta_research.adapter import PIN, TAError, validate_request
    from ta_research.bridge import TOOLS, capture, extract, upstream_config
    from ta_research.owned_runs import OwnedRunRegistry, read_regular
    from ta_research.review_controls import _bounded_child, ModelBudget, verify_local_model, _backend_json
else:
    from .adapter import PIN, TAError, validate_request
    from .bridge import TOOLS, capture, extract, upstream_config
    from .owned_runs import OwnedRunRegistry, read_regular
    from .review_controls import _bounded_child, ModelBudget, verify_local_model, _backend_json

ARCHIVE_SHA = 'fc3428f4002bc128cbbd0ba0070a9aae1e7c6c6a1286aa00e0594b061a7f96c5'
PREFIX = 'TradingAgents_TauricResearch-' + PIN + '/'
TOOL_MODULES = {
    'market': ('market_analyst', ('get_stock_data', 'get_indicators', 'get_verified_market_snapshot')),
    'social': ('sentiment_analyst', ('get_news',)),
    'news': ('news_analyst', ('get_news', 'get_global_news', 'get_insider_transactions',
                            'get_macro_indicators', 'get_prediction_markets')),
    'fundamentals': ('fundamentals_analyst', ('get_fundamentals', 'get_balance_sheet',
                                           'get_cashflow', 'get_income_statement')),
}
# Source anchors are checked in the pinned archive and against every installed .py.
ANCHORS = {
    'tradingagents/graph/trading_graph.py': ('class TradingAgentsGraph:', 'return final_state, self.process_signal(',
                                             'self._resolve_pending_entries(company_name)', 'resolve_instrument_identity(ticker)',
                                             'llm_kwargs["callbacks"] = self.callbacks'),
    'tradingagents/llm_clients/openai_client.py': ('"api_key", "callbacks"', 'llm_kwargs[key] = self.kwargs[key]',
                                                   'def get_llm(self)'),
    'tradingagents/agents/utils/agent_utils.py': ('def resolve_instrument_identity(', 'yf.Ticker('),
    'tradingagents/dataflows/interface.py': ('def route_to_vendor(', 'VENDOR_METHODS ='),
    'tradingagents/graph/setup.py': ('class GraphSetup:', 'def setup_graph('),
}


def _sha(raw): return hashlib.sha256(raw).hexdigest()


def _regular(path: Path, *, directory=False):
    if path.is_symlink() or not (path.is_dir() if directory else path.is_file()):
        raise TAError('required regular path missing or symlink: ' + str(path))
    for parent in path.parents:
        if parent.is_symlink(): raise TAError('symlink parent rejected')
    return path.resolve()


def source_preflight(archive: Path, venv: Path) -> dict:
    """Inspect bytes only; no package import, install, or interpreter execution."""
    archive = _regular(archive); venv = _regular(venv, directory=True)
    if not (venv / 'pyvenv.cfg').is_file(): raise TAError('isolated venv missing')
    python = _regular(venv / 'bin/python3')  # venv must be created with --copies
    if not python.is_relative_to(venv): raise TAError('venv python escaped')
    if archive.stat().st_size > 20_000_000 or _sha(archive.read_bytes()) != ARCHIVE_SHA:
        raise TAError('upstream archive digest/size mismatch')
    roots = list((venv / 'lib').glob('python*/site-packages/tradingagents'))
    if len(roots) != 1: raise TAError('exactly one pinned installed package required')
    package = _regular(roots[0], directory=True)
    with tarfile.open(archive, 'r:gz') as tar:
        source = {}
        for member in tar:
            # The verified GitHub archive has one root directory entry without
            # the trailing slash; do not weaken the path gate for any other member.
            if member.isdir() and member.name == PREFIX.rstrip('/'): continue
            if not member.name.startswith(PREFIX): raise TAError('archive revision/path mismatch')
            if not member.isfile() or not member.name.endswith('.py'): continue
            if member.size > 2_000_000: raise TAError('oversized upstream source')
            relative = member.name[len(PREFIX):]
            if relative.startswith('tradingagents/'):
                stream = tar.extractfile(member)
                if stream is None: raise TAError('unreadable archive member')
                source[relative] = stream.read()
        if not source: raise TAError('empty upstream source')
    for name, anchors in ANCHORS.items():
        if name not in source or any(a not in source[name].decode() for a in anchors):
            raise TAError('pinned graph API/source drift: ' + name)
    expected = {name[len('tradingagents/'):]: raw for name, raw in source.items()}
    installed = {str(p.relative_to(package)): p for p in package.rglob('*.py')}
    if set(expected) != set(installed): raise TAError('installed package source inventory mismatch')
    for name, raw in expected.items():
        p = _regular(installed[name]);
        if not p.is_relative_to(package) or p.stat().st_size != len(raw) or _sha(p.read_bytes()) != _sha(raw):
            raise TAError('installed source differs from pinned archive: ' + name)
    return {'archive_sha256': ARCHIVE_SHA, 'revision': PIN, 'source_files': len(expected),
            'package': str(package), 'python': str(python)}


def _private_work(path: Path):
    path = _regular(path, directory=True)
    if path.stat().st_mode & 0o077: raise TAError('work directory must be private (0700)')
    scratch = Path.home() / '.hermes/profiles/valedia/cache/scratch/ta-1/runs'
    if not path.is_relative_to(scratch) or path == scratch: raise TAError('work must be an isolated TA-1 scratch run')
    return path


def _profile(venv: Path, work: Path, port: int, archive: Path) -> str:
    """macOS seatbelt: local endpoint only, no other network or home writes/reads."""
    if not 1 <= port <= 65535: raise TAError('invalid port')
    q = json.dumps
    home_path = Path.home()
    home = str(home_path)
    runner_dir = Path(__file__).resolve().parent
    # realpath() of the copied venv interpreter traverses each home ancestor.
    # Allow metadata on only those exact ancestor directories; never their data
    # or siblings. The existing home read deny remains in force.
    ancestors = set()
    for permitted in (venv, work, archive, runner_dir, runner_dir.parent / 'ta_research_cli.py'):
        parent = permitted.parent
        while parent.is_relative_to(home_path):
            ancestors.add(parent)
            if parent == home_path: break
            parent = parent.parent
    ancestor_rules = tuple(f'(allow file-read-metadata (literal {q(str(p))}))'
                           for p in sorted(ancestors, key=str))
    # Specific allows override the general home read denial; no ambient ~/.ssh,
    # trading files, broker secrets, or other profile data are visible to child.
    return '\n'.join((
        '(version 1)', '(allow default)',
        '(deny network*)', f'(allow network-outbound (remote ip "localhost:{port}"))',
        '(deny file-write*)', f'(allow file-write* (subpath {q(str(work))}))',
        f'(deny file-read* (subpath {q(home)}))',
        *ancestor_rules,
        f'(allow file-read* (subpath {q(str(venv))}))',
        f'(allow file-read* (subpath {q(str(work))}))',
        f'(allow file-read* (literal {q(str(archive))}))',
        # This runner is read from its installed workspace path; only its package,
        # not sibling broker modules or workspace data, is readable.
        f'(allow file-read* (subpath {q(str(Path(__file__).resolve().parent))}))',
        f'(allow file-read* (literal {q(str(Path(__file__).resolve().parent.parent / "ta_research_cli.py"))}))',
    ))


def _child_env(work: Path):
    env = {'HOME': str(work), 'TMPDIR': str(work), 'TMP': str(work),
           'XDG_CACHE_HOME': str(work), 'XDG_CONFIG_HOME': str(work),
           'XDG_DATA_HOME': str(work), 'PYTHONDONTWRITEBYTECODE': '1',
           'OPENAI_API_KEY': 'local-no-cloud-key', 'NO_PROXY': '127.0.0.1,localhost',
           'no_proxy': '127.0.0.1,localhost', 'HTTP_PROXY': '', 'HTTPS_PROXY': '', 'ALL_PROXY': '',
           'PATH': '/usr/bin:/bin'}
    return env


def run_isolated(req: dict, snapshot_path: str, *, archive: str, venv: str,
                 work: str, timeout: int = 180, receipt_path: str | None = None) -> dict:
    """Launch ONLY after external action-time approval, never from doctor/preflight."""
    validate_request(req)
    if type(timeout) is not int or not 10 <= timeout <= 300: raise TAError('timeout out of bounds')
    frozen_bytes=read_regular(Path(snapshot_path),2_000_000)
    verified = capture(req, snapshot_path,verified_bytes=frozen_bytes)
    if req['asset_type'] == 'polymarket': raise TAError('upstream polymarket identity unsupported; no launch')
    if not isinstance(verified['snapshot'].get('identity'),dict): raise TAError('frozen instrument identity required')
    if 'social' in req['analysts'] and 'social' not in verified['snapshot']:
        raise TAError('frozen social prefetch required')
    required = set().union(*(TOOL_MODULES[a][1] for a in req['analysts']))
    if not required <= set(verified['snapshot']['tools']):
        raise TAError('selected analysts lack complete snapshot tool coverage: ' + ','.join(sorted(required-set(verified['snapshot']['tools']))))
    if any(not verified['snapshot']['tools'][name] or any('query' not in r for r in verified['snapshot']['tools'][name]) for name in required):
        raise TAError('live graph requires exact frozen query/response pairs for all selected tools')
    work_path = _private_work(Path(work)); venv_path = _regular(Path(venv), directory=True)
    preflight = source_preflight(Path(archive), venv_path)
    destination = Path(receipt_path).absolute() if receipt_path is not None else None
    if destination is None or destination.parent != work_path or destination.exists() or destination.is_symlink():
        raise TAError('new receipt path must be inside private owned work')
    if _sha(read_regular(Path(snapshot_path),2_000_000))!=verified['sha256']:
        raise TAError('snapshot path changed since capture')
    locality=verify_local_model(req)
    owned_files=[Path(__file__),Path(__file__).with_name('review_controls.py'),
                 Path(__file__).with_name('owned_runs.py'),Path(__file__).with_name('bridge.py'),
                 Path(__file__).with_name('adapter.py'),Path(__file__).with_name('intake.py'),
                 Path(__file__).resolve().parent.parent/'ta_research_cli.py']
    code_hashes={str(p.name):_sha(read_regular(p,1_000_000)) for p in owned_files}
    code_digest=_sha(json.dumps(code_hashes,sort_keys=True).encode())
    registry=OwnedRunRegistry()
    run_id=registry.allocate(req,verified['sha256'],preflight,code_digest,locality,_sha(frozen_bytes))
    port = urlparse(req['model_base_url']).port
    instruction = {'req': req, 'snapshot': verified['snapshot'], 'digest': verified['sha256'],
                   'archive': str(_regular(Path(archive))), 'venv': str(venv_path), 'work': str(work_path),
                   'run_id':run_id}
    payload = json.dumps(instruction, allow_nan=False).encode()
    if len(payload) > 2_100_000: raise TAError('child input too large')
    profile = _profile(venv_path, work_path, port, _regular(Path(archive)))
    result_path = work_path / 'result.json'
    if result_path.exists() or result_path.is_symlink(): raise TAError('result path already exists')
    returncode=_bounded_child(['/usr/bin/sandbox-exec','-p',profile,preflight['python'],'-I',
                               str(Path(__file__).resolve()),'--child'],payload,work_path,timeout,
                              work_path/'child.log',env=_child_env(work_path))
    if returncode:raise TAError('upstream child failed/refused ('+str(returncode)+'); no receipt')
    receipt=json.loads(read_regular(result_path,300_000))
    if receipt.get('snapshot_sha256') != verified['sha256'] or receipt.get('upstream_commit') != PIN:
        raise TAError('child receipt mismatch')
    budget=receipt.get('model_budget')
    if (not isinstance(budget,dict) or type(budget.get('model_calls')) is not int or
        not 1<=budget['model_calls']<=24 or type(budget.get('output_tokens')) is not int or
        not 0<=budget['output_tokens']<=32768 or budget.get('call_limit')!=24 or
        budget.get('output_limit')!=32768 or budget.get('per_call_limit')!=2048):
        raise TAError('child aggregate model budget not verified')
    if verify_local_model(req)!=locality:raise TAError('model metadata changed during owned run')
    if 'run_id' in receipt:raise TAError('child supplied run identity')
    receipt['run_id']=run_id
    raw=json.dumps(receipt,sort_keys=True,allow_nan=False,separators=(',',':')).encode()
    if len(raw)>300_000:raise TAError('upstream result too large')
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    digest=_sha(raw)
    registry.complete(run_id,destination,digest,exit_code=returncode)
    return receipt


def _frozen_tool_response(snapshot, name, kwargs, req):
    from datetime import date
    if name not in TOOLS or name not in snapshot['tools']: raise TAError('uncaptured snapshot tool')
    if 'symbol' in kwargs and kwargs['symbol'] != req['symbol']: raise TAError('snapshot ticker mismatch')
    if 'ticker' in kwargs and kwargs['ticker'] != req['symbol']: raise TAError('snapshot ticker mismatch')
    for key in ('start_date','end_date','curr_date','trade_date'):
        value = kwargs.get(key)
        if value is not None:
            try: day = date.fromisoformat(value)
            except (ValueError,TypeError): raise TAError('snapshot tool date invalid')
            if day > date.fromisoformat(req['trade_date']): raise TAError('future tool query')
    matches = [r for r in snapshot['tools'][name] if r.get('query') == kwargs]
    if len(matches) != 1: raise TAError('uncaptured or ambiguous snapshot tool query: ' + name)
    value = matches[0]['value']
    return value if isinstance(value,str) else json.dumps(value,sort_keys=True,allow_nan=False)


def _scoped_snapshot_shim(req, snapshot, budget_callback):
    """Patch only the child graph/analyst module references; fail on unknown tool drift."""
    from langchain_core.tools import StructuredTool
    import tradingagents.graph.trading_graph as graph_mod
    from tradingagents.llm_clients import create_llm_client
    import tradingagents.agents.utils.agent_utils as utilities
    from tradingagents.agents.analysts import market_analyst, sentiment_analyst, news_analyst, fundamentals_analyst
    modules = {'market': market_analyst, 'social': sentiment_analyst,
               'news': news_analyst, 'fundamentals': fundamentals_analyst}
    all_names = set().union(*(names for _, names in TOOL_MODULES.values()))
    if all_names != TOOLS: raise TAError('tool inventory drift')
    def make_tool(name, original):
        if getattr(original, 'name', None) != name or not getattr(original, 'args_schema', None):
            raise TAError('upstream tool schema drift: ' + name)
        def frozen(*args, **kwargs):
            fields = list(original.args_schema.model_fields)
            if len(args) > len(fields): raise TAError('unexpected positional tool arguments')
            kwargs = dict(zip(fields, args)) | kwargs
            return _frozen_tool_response(snapshot,name,kwargs,req)
        return StructuredTool.from_function(func=frozen, name=name, description=original.description,
                                             args_schema=original.args_schema)
    tool_map = {}
    for name in all_names:
        original = getattr(utilities, name, None)
        tool_map[name] = make_tool(name, original)
        if getattr(graph_mod, name, None) is not original: raise TAError('graph tool reference drift')
        setattr(graph_mod, name, tool_map[name])
    for analyst, (module_name, names) in TOOL_MODULES.items():
        mod = modules[analyst]
        if mod.__name__.rsplit('.', 1)[-1] != module_name: raise TAError('analyst module drift')
        for name in names:
            # This pinned news analyst never imports the insider tool; the
            # graph itself imports and references it, already checked above.
            if analyst == 'news' and name == 'get_insider_transactions' and not hasattr(mod, name):
                continue
            if getattr(mod, name, None) is not getattr(utilities, name):
                raise TAError('analyst tool reference drift: ' + name)
            setattr(mod, name, tool_map[name])
    identity = snapshot['identity']
    if not isinstance(identity, dict): raise TAError('snapshot identity missing')
    class FrozenTicker:
        def __init__(self, ticker):
            if ticker != req['symbol']: raise TAError('direct yfinance ticker not in snapshot')
        @property
        def info(self): return dict(identity)
        def history(self, **kwargs): raise TAError('pending-outcome yfinance prohibited')
    graph_mod.yf = SimpleNamespace(Ticker=FrozenTicker)
    utilities.yf = SimpleNamespace(Ticker=FrozenTicker)
    if graph_mod.resolve_instrument_identity is not utilities.resolve_instrument_identity:
        raise TAError('direct identity reference drift')
    if graph_mod.create_llm_client is not create_llm_client:
        raise TAError('model client reference drift')
    def bounded_client(*args, **kwargs):
        if kwargs.get('base_url') != req['model_base_url'] or kwargs.get('provider') != 'openai':
            raise TAError('model endpoint/provider mismatch')
        if kwargs.get('callbacks') != [budget_callback]:
            raise TAError('pinned graph did not attach model budget callback')
        kwargs.update(timeout=30, max_retries=0)
        client = create_llm_client(*args, **kwargs)
        get_llm = client.get_llm
        def bounded_llm():
            llm = get_llm()
            if getattr(llm, 'openai_api_base', None) is None and getattr(llm, 'base_url', None) is None:
                raise TAError('local model base URL not configured')
            llm.max_tokens = 2048
            if llm.max_tokens != 2048: raise TAError('model token cap not applied')
            if budget_callback not in (getattr(llm,'callbacks',None) or []):
                raise TAError('pinned model did not retain budget callback')
            return llm
        client.get_llm = bounded_llm
        return client
    graph_mod.create_llm_client = bounded_client
    if 'social' in req['analysts']:
        blocks = snapshot.get('social')
        if not isinstance(blocks, dict) or set(blocks) != {'stocktwits','reddit'}:
            raise TAError('snapshot missing social prefetch blocks')
        sentiment_analyst.fetch_stocktwits_messages = lambda ticker, **kw: (
            blocks['stocktwits']['value'] if ticker == req['symbol'] else (_ for _ in ()).throw(TAError('snapshot ticker mismatch')))
        sentiment_analyst.fetch_reddit_posts = lambda ticker, **kw: (
            blocks['reddit']['value'] if ticker == req['symbol'] else (_ for _ in ()).throw(TAError('snapshot ticker mismatch')))
    return graph_mod.TradingAgentsGraph


def child_main():
    # Called ONLY inside sandbox after parent preflight and user's action-time approval.
    if len(sys.argv) != 2 or sys.argv[1] != '--child': raise TAError('child mode required')
    data = sys.stdin.buffer.read(2_100_001)
    if len(data) > 2_100_000: raise TAError('child payload too large')
    instruction = json.loads(data)
    if set(instruction) != {'req','snapshot','digest','archive','venv','work','run_id'}: raise TAError('child schema mismatch')
    req = instruction['req']; validate_request(req)
    work = _private_work(Path(instruction['work']))
    source_preflight(Path(instruction['archive']), Path(instruction['venv']))
    # Frozen snapshot is passed as bytes from verified parent; child independently
    # checks its digest against the request's original source identity.
    snapshot = instruction['snapshot']
    if snapshot.get('schema') != 'valedia.ta1.snapshot.v1' or not isinstance(snapshot.get('identity'), dict):
        raise TAError('invalid frozen snapshot')
    if instruction['digest'] != req['source_sha256']: raise TAError('child digest mismatch')
    from langchain_core.callbacks import BaseCallbackHandler
    class BudgetCallback(ModelBudget,BaseCallbackHandler):
        raise_error=True  # LangChain callback failures must abort, not log-and-continue.
    budget=BudgetCallback(call_limit=24,output_limit=32768,per_call_limit=2048)
    graph_type = _scoped_snapshot_shim(req, snapshot,budget)
    config = upstream_config(req, str(work))
    config['max_recur_limit'] = 32
    graph = graph_type(selected_analysts=req['analysts'], debug=False, config=config, callbacks=[budget])
    if graph.memory_log.get_pending_entries(): raise TAError('pending outcomes forbidden')
    outcome = graph.propagate(req['symbol'], req['trade_date'], asset_type=req['asset_type'])
    receipt = extract(req, outcome, instruction['digest'])
    receipt['model_budget']=budget.assert_finished()
    target = work / 'result.json'
    with target.open('x', encoding='utf8') as stream:
        json.dump(receipt, stream, sort_keys=True, allow_nan=False)

if __name__ == '__main__':
    child_main()
