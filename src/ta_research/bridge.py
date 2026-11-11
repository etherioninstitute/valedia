"""TA-1 research bridge. Upstream is NEVER imported by this module.

The upstream graph has direct yfinance paths in propagate and its constructor;
the separate runner gates graph invocation behind action-time approval, exact
snapshot replacement, scoped identity shim, and a macOS seatbelt child.
Never infer network safety from data_vendors alone.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone
from typing import Mapping

from .adapter import TAError, _time, make_receipt, validate_request

MAX_SNAPSHOT=2_000_000
TOOLS=frozenset(('get_stock_data','get_indicators','get_verified_market_snapshot','get_news','get_global_news','get_insider_transactions','get_macro_indicators','get_prediction_markets','get_fundamentals','get_balance_sheet','get_cashflow','get_income_statement'))
REPORTS=frozenset(('market_report','sentiment_report','news_report','fundamentals_report','investment_plan','trader_investment_plan'))
DEBATES=frozenset(('investment_debate_state','risk_debate_state'))

def capture(req: dict, snapshot_path: str, *, verified_bytes: bytes | None = None) -> dict:
    """Verify actual immutable-at-read bytes; never trust a claimed snapshot hash."""
    validate_request(req)
    if verified_bytes is None:
        p=Path(snapshot_path)
        if p.is_symlink() or not p.is_file(): raise TAError('snapshot must be regular non-symlink file')
        fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            stat=os.fstat(fd)
            if not __import__('stat').S_ISREG(stat.st_mode) or stat.st_size>MAX_SNAPSHOT: raise TAError('invalid snapshot file')
            raw=os.read(fd,MAX_SNAPSHOT+1)
            if len(raw)>MAX_SNAPSHOT or os.fstat(fd).st_size!=len(raw): raise TAError('snapshot changed or oversized')
        finally: os.close(fd)
    else:
        raw=verified_bytes
        if type(raw) is not bytes or len(raw)>MAX_SNAPSHOT:raise TAError('invalid frozen snapshot bytes')
    digest=hashlib.sha256(raw).hexdigest()
    if digest!=req['source_sha256']: raise TAError('snapshot digest mismatch')
    try: body=json.loads(raw)
    except (ValueError,UnicodeDecodeError) as exc: raise TAError('snapshot JSON invalid') from exc
    required={'schema','asset_type','symbol','as_of','captured_at','tools'}
    if not isinstance(body,dict) or not required<=set(body) or set(body)-required-{'identity','social'} or body['schema']!='valedia.ta1.snapshot.v1':
        raise TAError('snapshot schema mismatch')
    if body['asset_type']!=req['asset_type'] or body['symbol']!=req['symbol'] or body['as_of']!=req['as_of'] or body['captured_at']!=req['captured_at']:
        raise TAError('snapshot identity mismatch')
    if not isinstance(body['tools'],dict) or not body['tools'] or not set(body['tools'])<=TOOLS: raise TAError('snapshot tools invalid')
    if 'identity' in body:
        identity=body['identity']
        if not isinstance(identity,dict) or not identity or not set(identity)<={'longName','shortName','sector','industry','exchange','quoteType'} or not all(isinstance(v,str) and 0<len(v)<=200 for v in identity.values()):
            raise TAError('snapshot identity invalid')
    if 'social' in body:
        blocks=body['social']
        if not isinstance(blocks,dict) or set(blocks)!={'stocktwits','reddit'}:
            raise TAError('snapshot social blocks invalid')
        for record in blocks.values():
            if not isinstance(record,dict) or set(record)!={'observed_at','available_at','value'} or not isinstance(record['value'],str) or not 0<len(record['value'])<=30000:
                raise TAError('snapshot social record invalid')
            observed,available=_time(record['observed_at']),_time(record['available_at'])
            if observed>available or available>_time(req['as_of']) or available>_time(req['captured_at']): raise TAError('social look-ahead')
    for name,records in body['tools'].items():
        if not isinstance(records,list) or len(records)>100: raise TAError('snapshot records invalid')
        for record in records:
            if not isinstance(record,dict) or not {'observed_at','available_at','value'}<=set(record) or set(record)-{'observed_at','available_at','value','query'} or not isinstance(record['value'],(str,int,float,dict,list)):
                raise TAError('record schema mismatch')
            if 'query' in record and (not isinstance(record['query'],dict) or not all(isinstance(k,str) and isinstance(v,(str,int,float,bool,type(None))) for k,v in record['query'].items())):
                raise TAError('record query invalid')
            observed,available=_time(record['observed_at']),_time(record['available_at'])
            if observed>available or available>_time(req['as_of']) or available>_time(req['captured_at']): raise TAError('snapshot look-ahead')
    try: json.dumps(body,sort_keys=True,allow_nan=False)
    except (ValueError,TypeError) as exc: raise TAError('snapshot contains non-finite data') from exc
    return {'sha256':digest,'bytes':len(raw),'snapshot':body}

def vendor(snapshot: dict, tool: str) -> str:
    """Fail-closed upstream-compatible tool name to frozen JSON response protocol."""
    if tool not in TOOLS: raise TAError('unknown vendor tool')
    records=snapshot['tools'].get(tool)
    if not records: raise TAError('vendor data unavailable; no fallback')
    return json.dumps(records,sort_keys=True,allow_nan=False)

def upstream_config(req: dict, directory: str) -> dict:
    """Exact keys of pinned graph config; deliberately no provider API key."""
    validate_request(req)
    p=Path(directory)
    if p.is_symlink() or not p.is_dir(): raise TAError('result directory must exist and not be symlink')
    return {'llm_provider':'openai','deep_think_llm':req['model_name'],
            'quick_think_llm':req['model_name'],'backend_url':req['model_base_url'],
            'max_debate_rounds':1,'max_risk_discuss_rounds':1,'llm_max_retries':0,
            'checkpoint_enabled':False,'results_dir':str(p.resolve()),
            'data_cache_dir':str(p.resolve()), 'data_vendors':{},
            'online_tools':False}

def extract(req: dict, outcome: tuple, snapshot_digest: str) -> dict:
    """propagate returns (AgentState, processed_signal); NEVER consume signal."""
    if not isinstance(outcome,tuple) or len(outcome)!=2 or not isinstance(outcome[0],Mapping):
        raise TAError('upstream propagate signature mismatch')
    state=outcome[0]
    if state.get('company_of_interest')!=req['symbol'] or state.get('trade_date')!=req['trade_date'] or state.get('asset_type')!=req['asset_type']:
        raise TAError('graph state identity mismatch')
    reports={}
    for key in REPORTS:
        value=state.get(key)
        if value is not None:
            if not isinstance(value,str) or len(value)>32768: raise TAError('invalid research field')
            reports[key]=value
    receipt=make_receipt(req,reports)
    debates={}
    for key in DEBATES:
        value=state.get(key)
        if value is not None:
            if not isinstance(value,dict) or not set(value)<=frozenset(('history','judge_decision','bull_history','bear_history','aggressive_history','conservative_history','neutral_history','current_response','current_aggressive_response','current_conservative_response','current_neutral_response','latest_speaker','count')):
                raise TAError('invalid debate state')
            debates[key]={k:v for k,v in value.items() if isinstance(v,str) and len(v)<=32768}
    decision=state.get('final_trade_decision')
    if decision is not None and (not isinstance(decision,str) or len(decision)>32768): raise TAError('decision provenance invalid')
    receipt.update({'snapshot_sha256':snapshot_digest,'provenance':'SNAPSHOT_BYTES_VERIFIED; GRAPH_TEXT_UNVERIFIED',
                    'debates':debates,'decision_advisory':decision,'paper_intake':'DIAGNOSTIC_AVAILABLE_UNOWNED'})
    return receipt

def run_mocked(req: dict, path: str, graph_factory, *, results_dir: str) -> dict:
    """Exercise pinned TradingAgentsGraph constructor/propagate signatures with a stub.

    It never imports upstream; the real graph is gated separately by runner.py.
    A mock pass does not demonstrate a successful upstream execution.
    """
    captured=capture(req,path)
    config=upstream_config(req,results_dir)
    graph=graph_factory(selected_analysts=req['analysts'],debug=False,config=config,callbacks=None,
                        snapshot_vendor=lambda name:vendor(captured['snapshot'],name))
    outcome=graph.propagate(req['symbol'],req['trade_date'],asset_type=req['asset_type'])
    return extract(req,outcome,captured['sha256'])
