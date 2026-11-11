"""TA-1 snapshot/request builder, offline preflight, approved sandbox runner and intake."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.request import build_opener, ProxyHandler, Request, HTTPRedirectHandler

from ta_research.adapter import TAError, validate_request
from ta_research.bridge import capture, upstream_config
from ta_research.runner import run_isolated, source_preflight, TOOL_MODULES
from ta_research.owned_runs import read_regular


def _read(path, size=2_000_000):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size>size:
        raise TAError('invalid input file')
    raw=p.read_bytes()
    if len(raw)>size: raise TAError('input oversized')
    return json.loads(raw)


def _new(path, obj):
    raw=json.dumps(obj,sort_keys=True,allow_nan=False,separators=(',',':')).encode()
    p=Path(path)
    fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as f: f.write(raw)
    except BaseException:
        p.unlink(missing_ok=True)
        raise
    return hashlib.sha256(raw).hexdigest()


def _health(endpoint):
    from urllib.parse import urlparse
    url=urlparse(endpoint)
    if url.scheme!='http' or url.hostname not in ('localhost','127.0.0.1') or url.path not in ('/v1','/v1/') or url.username or url.password or url.query or url.fragment or not url.port:
        raise TAError('local /v1 endpoint required')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise TAError('model endpoint redirect rejected')
    try:
        opener=build_opener(ProxyHandler({}),NoRedirect())
        with opener.open(Request(endpoint.rstrip('/')+'/models',method='GET'),timeout=3) as response:
            models=json.loads(response.read(100_001)).get('data',[])
            return {'reachable':True,'listed_models':[m.get('id') for m in models if isinstance(m,dict)][:30],
                    'loaded_model_verified':False,'note':'GET /models does not prove a model is loaded'}
    except (OSError,ValueError) as exc:
        return {'reachable':False,'reason':str(exc)[:200]}


def main(argv=None):
    ap=argparse.ArgumentParser(description='TradingAgents advisory research; no trade or promotion')
    sp=ap.add_subparsers(dest='action',required=True)
    doctor=sp.add_parser('doctor'); doctor.add_argument('--endpoint',default='http://127.0.0.1:11434/v1')
    snap=sp.add_parser('snapshot',help='freeze pre-captured tool/identity/social JSON; NO fetching')
    for flag in ('asset-type','symbol','as-of','captured-at','tools-json','identity-json','output'):
        snap.add_argument('--'+flag,required=True)
    snap.add_argument('--social-json')
    request=sp.add_parser('request')
    for flag in ('snapshot','source','model-name','endpoint','analysts','output'):
        request.add_argument('--'+flag,required=True)
    for name in ('dry-run','run','intake'):
        cmd=sp.add_parser(name)
        cmd.add_argument('--request',required=True);cmd.add_argument('--snapshot',required=True)
        if name in ('dry-run','run'):
            cmd.add_argument('--archive');cmd.add_argument('--venv')
        if name=='run':
            cmd.add_argument('--work',required=True);cmd.add_argument('--receipt',required=True)
            cmd.add_argument('--timeout',type=int,default=180)
            cmd.add_argument('--ack-approved-upstream-execution',action='store_true')
        if name=='intake':
            cmd.add_argument('--db',required=True);cmd.add_argument('--receipt',required=True)
    args=ap.parse_args(argv)
    try:
        if args.action=='doctor':
            print(json.dumps({'upstream_imported':False,'upstream_execution_approved':False,
                              'local_endpoint':_health(args.endpoint),'graph_run':'REQUIRES_ACTION_TIME_APPROVAL'}));return 0
        if args.action=='snapshot':
            body={'schema':'valedia.ta1.snapshot.v1','asset_type':args.asset_type,'symbol':args.symbol,
                  'as_of':args.as_of,'captured_at':args.captured_at,
                  'identity':_read(args.identity_json),'tools':_read(args.tools_json)}
            if args.social_json: body['social']=_read(args.social_json)
            digest=_new(args.output,body)
            # Validate after writing using matching request below? Validate structure first
            # on the request command; never claim a snapshot was vetted here.
            print(json.dumps({'status':'SNAPSHOT_FROZEN_NOT_VALIDATED','sha256':digest,'path':args.output}));return 0
        if args.action=='request':
            body=_read(args.snapshot); raw=Path(args.snapshot).read_bytes()
            req={'asset_type':body['asset_type'],'symbol':body['symbol'],'as_of':body['as_of'],
                 'trade_date':datetime.fromisoformat(body['as_of'].replace('Z','+00:00')).date().isoformat(),
                 'source':args.source,'source_sha256':hashlib.sha256(raw).hexdigest(),
                 'captured_at':body['captured_at'],'model_provider':'local-openai-compatible',
                 'model_name':args.model_name,'model_base_url':args.endpoint,
                 'data_mode':'approved-snapshot','max_cost_usd':0,
                 'analysts':args.analysts.split(','),'max_age_seconds':86400}
            capture(req,args.snapshot)
            _new(args.output,req)
            print(json.dumps({'status':'VALIDATED_REQUEST_CREATED','path':args.output,'snapshot_sha256':req['source_sha256']}));return 0
        req=_read(args.request,16384)
        verified=capture(req,args.snapshot)
        if args.action=='dry-run':
            result={'status':'VALIDATED_NO_UPSTREAM_EXECUTION','snapshot_sha256':verified['sha256'],
                    'config':upstream_config(req,str(Path(args.request).parent))}
            if args.archive and args.venv: result['pinned_source']=source_preflight(Path(args.archive),Path(args.venv))
            print(json.dumps(result,sort_keys=True));return 0
        if args.action=='run':
            if not args.ack_approved_upstream_execution:
                raise TAError('action-time approval required; ack flag only after human approval')
            if not args.archive or not args.venv: raise TAError('pinned archive and isolated venv required')
            receipt=run_isolated(req,args.snapshot,archive=args.archive,venv=args.venv,
                                 work=args.work,timeout=args.timeout,receipt_path=args.receipt)
            digest=hashlib.sha256(read_regular(Path(args.receipt),300_000)).hexdigest()
            print(json.dumps({'status':'ADVISORY_RECEIPT_CREATED','receipt_sha256':digest,'path':args.receipt,
                              'performance_eligible':False,'order_link':False}));return 0
        from ta_research.intake import intake_advisory
        canonical_db=Path(__file__).resolve().parents[2]/'data/poly-research/research.sqlite'
        if Path(args.db).absolute()!=canonical_db:raise TAError('canonical research DB path required')
        print(json.dumps(intake_advisory(args.db,args.receipt,req,args.snapshot),sort_keys=True));return 0
    except (ValueError,TypeError,OSError,KeyError,RuntimeError) as exc:
        print(json.dumps({'status':'REFUSED','reason':str(exc)}),file=sys.stderr);return 2

if __name__=='__main__': raise SystemExit(main())
