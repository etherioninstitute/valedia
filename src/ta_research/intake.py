"""TA advisory evidence in the existing research intake schema; no candidate promotion."""
import hashlib
import json
import os
from pathlib import Path
import uuid

from .adapter import TAError, _canonical, validate_request
from .bridge import capture
from .owned_runs import OwnedRunRegistry, DEFAULT_REGISTRY, read_regular


def intake_advisory(db_path, receipt_path, req, snapshot_path, *, registry_root=DEFAULT_REGISTRY):
    """Accept only exact bytes registered by this parent after owned child success."""
    from poly_research_intake import EvidenceDB
    validate_request(req)
    if Path(db_path).is_symlink() or not Path(db_path).is_file():
        raise TAError('canonical existing research database required; do not create a parallel ledger')
    source_raw=read_regular(Path(snapshot_path),2_000_000)
    captured = capture(req, snapshot_path,verified_bytes=source_raw)
    if hashlib.sha256(read_regular(Path(snapshot_path),2_000_000)).hexdigest()!=captured['sha256']:
        raise TAError('snapshot path changed during intake')
    path = Path(receipt_path).absolute()
    raw = read_regular(path,300_000)
    receipt = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        if str(uuid.UUID(receipt.get('run_id'))) != receipt['run_id']:raise ValueError()
    except (ValueError,TypeError,KeyError) as exc:raise TAError('invalid owned run identity') from exc
    registry=OwnedRunRegistry(registry_root,create=False)
    run=registry.load(receipt.get('run_id'))
    if run.get('state')!='COMPLETED':raise TAError('owned completion absent or incomplete')
    if (run.get('receipt_path')!=str(path) or run.get('receipt_sha256')!=digest):
        raise TAError('receipt hash/path not registered by owned runner')
    if (run.get('request_sha256')!=hashlib.sha256(_canonical(req)).hexdigest() or
        run.get('snapshot_sha256')!=captured['sha256'] or
        run.get('snapshot_file_sha256')!=hashlib.sha256(source_raw).hexdigest() or
        run.get('source',{}).get('revision')!='01477f9afb7a47b849ed4c9259d3a9a4738d9fda' or
        run.get('source',{}).get('archive_sha256')!='fc3428f4002bc128cbbd0ba0070a9aae1e7c6c6a1286aa00e0594b061a7f96c5' or
        not isinstance(run.get('code_sha256'),str) or len(run['code_sha256'])!=64 or
        run.get('model_evidence',{}).get('loaded') is not True or
        run.get('model_evidence',{}).get('local') is not True or
        run.get('model_evidence',{}).get('model')!=req['model_name'] or
        run.get('model_evidence',{}).get('endpoint')!=req['model_base_url'] or
        hashlib.sha256(_canonical(run.get('model_evidence'))).hexdigest()!=run.get('model_evidence_sha256')):
        raise TAError('owned completion provenance mismatch')
    if (receipt.get('schema') != 'valedia.ta1.advisory.v1' or
        receipt.get('request_sha256') != hashlib.sha256(_canonical(req)).hexdigest() or
        receipt.get('run_id') != run['run_id'] or
        receipt.get('snapshot_sha256') != captured['sha256'] or
        receipt.get('identity') != {k: req[k] for k in ('asset_type','symbol','as_of','trade_date','source','source_sha256','captured_at','model_provider','model_name','model_base_url','data_mode','analysts')} or
        receipt.get('performance_eligible') is not False or receipt.get('order_link') is not False or
        receipt.get('ownership') != 'UNOWNED' or receipt.get('status') != 'ADVISORY_ONLY' or
        not isinstance(receipt.get('reports'),dict) or not receipt['reports']):
        raise TAError('receipt provenance or safety mismatch')
    # No fabricated market, owner, fill, candidate, or observed trade outcome.
    if read_regular(path,300_000)!=raw:raise TAError('receipt changed during intake')
    row = {'receipt_path':str(path), 'receipt_sha256':digest,
           'owned_run_id':run['run_id'],'runner_code_sha256':run['code_sha256'],
           'model_evidence_sha256':run['model_evidence_sha256'],
           'snapshot_sha256':captured['sha256'], 'request_sha256':receipt['request_sha256'],
           'upstream_commit':receipt['upstream_commit'], 'symbol':req['symbol'],
           'asset_type':req['asset_type'], 'reports':receipt['reports'],
           'debates':receipt.get('debates'), 'decision_advisory':receipt.get('decision_advisory'),
           'ownership':'UNOWNED','provenance':'GRAPH_TEXT_UNVERIFIED',
           'is_fill':False,'candidate_sha256':None,'performance_eligible':False,'order_link':False}
    if receipt['upstream_commit'] != '01477f9afb7a47b849ed4c9259d3a9a4738d9fda':
        raise TAError('upstream revision mismatch')
    ledger = EvidenceDB(Path(db_path))
    try:
        with ledger.db:
            inserted = ledger.db.execute('''INSERT OR IGNORE INTO source_snapshots
              (source_path,file_sha256,line_no,line_sha256,captured_at,kind,market_id,slug,event_id,
               candidate_sha256,ownership,provenance,is_fill,snapshot)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
               (str(path),digest,0,digest,req['captured_at'],'ta-advisory',None,None,None,
                None,'UNOWNED','GRAPH_TEXT_UNVERIFIED',0,json.dumps(row,sort_keys=True,allow_nan=False))).rowcount
            found = ledger.db.execute('''SELECT file_sha256,ownership,candidate_sha256,is_fill,snapshot
              FROM source_snapshots WHERE source_path=? AND file_sha256=? AND line_no=0''',
              (str(path),digest)).fetchone()
            if not found or found[0] != digest or found[1] != 'UNOWNED' or found[2] is not None or found[3] != 0 or json.loads(found[4]) != row:
                raise TAError('intake readback failed')
        return {'status':'ADVISORY_INTAKE_VERIFIED','inserted':bool(inserted),
                'receipt_sha256':digest,'ownership':'UNOWNED','performance_eligible':False}
    finally:
        ledger.close()
