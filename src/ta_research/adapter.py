"""Typed, fail-closed boundary for a separately approved TradingAgents graph.

No TradingAgents import, network, orders, live ledger or ambient provider credentials.
The injected graph is a research graph only. Its decision is deliberately discarded.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

PIN='01477f9afb7a47b849ed4c9259d3a9a4738d9fda'
REPORT_KEYS=frozenset(('market_report','sentiment_report','news_report','fundamentals_report',
                       'investment_plan','trader_investment_plan','final_trade_decision'))
ANALYSTS=frozenset(('market','social','news','fundamentals'))
FIELDS=frozenset(('asset_type','symbol','as_of','trade_date','source','source_sha256',
                  'captured_at','model_provider','model_name','model_base_url',
                  'data_mode','max_cost_usd','analysts','max_age_seconds'))

class TAError(ValueError):
    """Refused research request or integrity failure."""


def _time(value: Any) -> datetime:
    if not isinstance(value,str) or not (value.endswith('Z') or re.search(r'[+-]\d\d:\d\d$',value)):
        raise TAError('timezone-aware timestamp required')
    try: return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)
    except ValueError as exc: raise TAError('invalid timestamp') from exc


def _canonical(value: Any) -> bytes:
    return json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()


def validate_request(req: Mapping[str,Any]) -> None:
    if not isinstance(req,dict) or set(req)-FIELDS or FIELDS-{'max_age_seconds'}-set(req):
        raise TAError('request schema mismatch')
    if req['asset_type'] not in ('stock','crypto','polymarket'):
        raise TAError('unsupported asset type')
    if not isinstance(req['symbol'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}',req['symbol']):
        raise TAError('invalid asset identity')
    asof=_time(req['as_of']); captured=_time(req['captured_at'])
    if captured>asof or asof>datetime.now(timezone.utc): raise TAError('future observation')
    age=req.get('max_age_seconds',86400)
    if type(age) is not int or not 0<age<=86400 or (asof-captured).total_seconds()>age:
        raise TAError('stale observation')
    try: date=datetime.strptime(req['trade_date'],'%Y-%m-%d').date()
    except (TypeError,ValueError) as exc: raise TAError('invalid trade date') from exc
    if date!=asof.date(): raise TAError('trade date/as-of mismatch')
    if not isinstance(req['source'],str) or not 0<len(req['source'])<=256:
        raise TAError('source provenance missing')
    if not isinstance(req['source_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',req['source_sha256']):
        raise TAError('source digest required')
    if req['data_mode']!='approved-snapshot': raise TAError('no ambient vendor fallback')
    if req['model_provider']!='local-openai-compatible': raise TAError('provider must be explicit local')
    if not isinstance(req['model_name'],str) or not re.fullmatch(r'[\w./:-]{1,100}',req['model_name']):
        raise TAError('invalid model name')
    url=urlparse(req['model_base_url']) if isinstance(req['model_base_url'],str) else None
    if url is None or url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost') or url.username or url.password or url.query or url.fragment or not url.port or url.path not in ('/v1','/v1/'):
        raise TAError('local endpoint required')
    if type(req['max_cost_usd']) not in (int,float) or req['max_cost_usd']!=0:
        raise TAError('paid budget not authorized')
    analysts=req['analysts']
    if not isinstance(analysts,list) or not analysts or len(analysts)>4 or len(set(map(str,analysts)))!=len(analysts) or not set(analysts)<=ANALYSTS:
        raise TAError('invalid analysts')
    _canonical(req)


def graph_config(req: Mapping[str,Any], results_dir: str) -> dict[str,Any]:
    validate_request(req)
    return {'provider':'local-openai-compatible','model':req['model_name'],
            'backend_url':req['model_base_url'],'data_mode':req['data_mode'],
            'results_dir':str(Path(results_dir).resolve()),'max_cost_usd':0,
            'upstream_commit':PIN,'allow_order':False,'allow_vendor_network':False}


def make_receipt(req: Mapping[str,Any], result: Mapping[str,Any]) -> dict[str,Any]:
    validate_request(req)
    if not isinstance(result,dict) or not set(result)<=REPORT_KEYS:
        raise TAError('graph result schema mismatch')
    reports={}
    for key,value in result.items():
        if not isinstance(value,str) or len(value)>32768: raise TAError('report schema mismatch')
        if key!='final_trade_decision': reports[key]=value
    if not reports: raise TAError('no research report')
    identity={key:req[key] for key in ('asset_type','symbol','as_of','trade_date','source','source_sha256','captured_at','model_provider','model_name','model_base_url','data_mode','analysts')}
    return {'schema':'valedia.ta1.advisory.v1','request_sha256':sha256(_canonical(req)).hexdigest(),
            'upstream_commit':PIN,'identity':identity,'reports':reports,
            'status':'ADVISORY_ONLY','ownership':'UNOWNED','provenance':'SOURCE_DECLARED_NOT_ATTESTED',
            'holdout':'NOT_EVALUATED','performance_eligible':False,'paper_intake':'NOT_CONNECTED',
            'order_link':False,'cost_cap_usd':0}


def run_with_graph(req: dict[str,Any], factory: Callable[[list[str],dict[str,Any]],Callable[...,dict[str,Any]]], *, results_dir: str='.') -> dict[str,Any]:
    """Pure orchestration seam; caller supplies approved graph+data snapshot at action time."""
    config=graph_config(req,results_dir)
    graph=factory(list(req['analysts']),config)
    if not callable(graph): raise TAError('graph factory returned noncallable')
    return make_receipt(req,graph(req['symbol'],req['trade_date'],req['asset_type']))


class ReceiptStore:
    """Separate TA1 advisory receipts, NOT a trading/performance or second memory store."""
    def __init__(self,path: Path):
        self.db=sqlite3.connect(path,timeout=5)
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.execute('CREATE TABLE IF NOT EXISTS advisory_receipts (request_sha256 TEXT PRIMARY KEY, receipt_sha256 TEXT NOT NULL, body TEXT NOT NULL)')
        self.db.commit()
    def save(self,receipt: dict[str,Any]) -> dict[str,Any]:
        if receipt.get('schema')!='valedia.ta1.advisory.v1' or receipt.get('performance_eligible') is not False or receipt.get('order_link') is not False:
            raise TAError('not an advisory receipt')
        raw=_canonical(receipt); digest=sha256(raw).hexdigest(); key=receipt['request_sha256']
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO advisory_receipts VALUES (?,?,?)',(key,digest,raw.decode()))
            row=self.db.execute('SELECT receipt_sha256 FROM advisory_receipts WHERE request_sha256=?',(key,)).fetchone()
            if row[0]!=digest: raise TAError('concurrent or duplicate request conflict')
        return {'request_sha256':key,'receipt_sha256':digest}
    def close(self): self.db.close()
