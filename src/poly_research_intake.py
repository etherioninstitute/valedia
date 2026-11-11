"""Offline bounded intake of existing paper desk and captured public-source bytes.

Separate evidence tables in the canonical research DB; NEVER candidate performance or
broker execution. No imports from the broker, no journal writes, no network by default.
"""
import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from .poly_research_sources import read_receipt
except ImportError:
    from poly_research_sources import read_receipt

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_DB=ROOT/'data/poly-research/research.sqlite'
DEFAULT_JOURNAL=ROOT/'data/poly-loop/ledger.jsonl'
MAX_BYTES=2_000_000
MAX_LINES=500


def stamp(value):
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.tzinfo is None: raise ValueError('timezone required')
        return dt.astimezone(timezone.utc).isoformat()
    except (AttributeError,TypeError,OverflowError) as exc:
        raise ValueError('invalid timestamp') from exc


def digest(raw): return hashlib.sha256(raw).hexdigest()


def bounded(path, max_bytes=MAX_BYTES):
    p=Path(path)
    if p.stat().st_size>max_bytes: raise ValueError('bounded file byte limit exceeded')
    with p.open('rb') as stream:
        raw=stream.read(max_bytes+1)
    if len(raw)>max_bytes: raise ValueError('bounded file byte limit exceeded')
    return raw


class EvidenceDB:
    def __init__(self,path=DEFAULT_DB):
        path=Path(path)
        self.path=path
        path.parent.mkdir(parents=True,exist_ok=True)
        try:
            from .poly_research_loop import Ledger
        except ImportError:
            from poly_research_loop import Ledger
        owner=Ledger(path)
        owner.close()
        self.db=sqlite3.connect(path)
        self.db.execute('PRAGMA busy_timeout=3000')
        self.db.execute('''CREATE TABLE IF NOT EXISTS source_snapshots (
            id INTEGER PRIMARY KEY, source_path TEXT NOT NULL, file_sha256 TEXT NOT NULL,
            line_no INTEGER NOT NULL, line_sha256 TEXT NOT NULL, captured_at TEXT NOT NULL,
            kind TEXT NOT NULL, market_id TEXT, slug TEXT, event_id TEXT,
            candidate_sha256 TEXT, ownership TEXT NOT NULL, provenance TEXT NOT NULL,
            is_fill INTEGER NOT NULL CHECK(is_fill=0), snapshot TEXT NOT NULL,
            UNIQUE(source_path,file_sha256,line_no))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS intake_refusals (
            id INTEGER PRIMARY KEY, source_path TEXT NOT NULL, file_sha256 TEXT NOT NULL,
            line_no INTEGER NOT NULL, reason TEXT NOT NULL, captured_at TEXT NOT NULL,
            UNIQUE(source_path,file_sha256,line_no))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS diagnostic_weeks (
            week TEXT PRIMARY KEY, report TEXT NOT NULL)''')
        for table in ('source_snapshots','intake_refusals','diagnostic_weeks'):
            for operation in ('UPDATE','DELETE'):
                self.db.execute(f'''CREATE TRIGGER IF NOT EXISTS frozen_{table}_{operation}
                    BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable'); END''')
        self.db.commit()
    def close(self): self.db.close()
    def rows(self):
        self.db.row_factory=sqlite3.Row
        return [dict(r) for r in self.db.execute('SELECT * FROM source_snapshots ORDER BY id')]
    def status(self):
        count=lambda table:self.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        return {'source_snapshots':count('source_snapshots'),'refusals':count('intake_refusals'),
                'candidate_owned_performance_ready':False,'fills':0,
                'state':'UNVERIFIED_DIAGNOSTIC_ONLY','canonical_db':'local SQLite; no cross-DB attestation'}


def snapshot_journal(ledger,path=DEFAULT_JOURNAL,*,max_lines=MAX_LINES,max_bytes=MAX_BYTES):
    raw=bounded(path,max_bytes)
    lines=raw.splitlines()
    if len(lines)>max_lines: raise ValueError('bounded journal line limit exceeded')
    source=str(Path(path).resolve()); file_hash=digest(raw)
    now=datetime.now(timezone.utc).isoformat()
    inserted=refused=0
    with ledger.db:
        for index,line in enumerate(lines,1):
            try:
                row=json.loads(line)
                if not isinstance(row,dict): raise ValueError('row must be object')
                if row.get('status') not in ('DRY/SHADOW','BLOCKED'): raise ValueError('unsupported paper status')
                if row.get('create_order') not in (None,False): raise ValueError('order flag present')
                observed=stamp(row.get('ts'))
                slug=row.get('slug'); market=row.get('market_id'); event=row.get('event_id')
                if not any(isinstance(x,str) and x for x in (slug,market,event)):
                    raise ValueError('market/event identity missing')
                # Existing desk does NOT emit candidate attribution: ignore caller-added
                # ownership assertions, never infer a candidate from a matching label.
                snapshot={'observed_at':observed,'status':row['status'],
                          'market_id':market if isinstance(market,str) else None,
                          'slug':slug if isinstance(slug,str) else None,
                          'event_id':event if isinstance(event,str) else None,
                          'fv':row.get('fv'),'mid':row.get('mid'),
                          'candidate_sha256':None,'ownership':'UNOWNED',
                          'provenance':'UNVERIFIED','is_fill':False}
                if ledger.db.execute('''INSERT OR IGNORE INTO source_snapshots
                  (source_path,file_sha256,line_no,line_sha256,captured_at,kind,market_id,slug,event_id,
                   candidate_sha256,ownership,provenance,is_fill,snapshot)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (source,file_hash,index,digest(line),now,row['status'],snapshot['market_id'],
                   snapshot['slug'],snapshot['event_id'],None,'UNOWNED','UNVERIFIED',0,
                   json.dumps(snapshot,sort_keys=True,allow_nan=False))).rowcount: inserted+=1
            except (ValueError,TypeError,json.JSONDecodeError) as exc:
                if ledger.db.execute('''INSERT OR IGNORE INTO intake_refusals
                  (source_path,file_sha256,line_no,reason,captured_at) VALUES (?,?,?,?,?)''',
                  (source,file_hash,index,str(exc)[:200],now)).rowcount: refused+=1
    return {'source_path':source,'file_sha256':file_hash,'lines':len(lines),
            'inserted':inserted,'refused':refused,'ownership':'UNOWNED',
            'performance_eligible':False}


def snapshot_source(ledger,path,kind,slug,*,max_bytes=MAX_BYTES):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',slug): raise ValueError('invalid slug')
    raw=bounded(path,max_bytes)
    receipt=read_receipt(path,kind,slug)
    source=str(Path(path).resolve()); file_hash=digest(raw)
    receipt.update({'captured_at':datetime.now(timezone.utc).isoformat(),
                    'ownership':'UNOWNED','provenance':'UNVERIFIED',
                    'is_fill':False,'performance_eligible':False})
    with ledger.db:
        inserted=ledger.db.execute('''INSERT OR IGNORE INTO source_snapshots
          (source_path,file_sha256,line_no,line_sha256,captured_at,kind,market_id,slug,event_id,
           candidate_sha256,ownership,provenance,is_fill,snapshot)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (source,file_hash,0,file_hash,receipt['captured_at'],kind,None,slug,None,None,
           'UNOWNED','UNVERIFIED',0,json.dumps(receipt,sort_keys=True,allow_nan=False))).rowcount
    return {'source_path':source,'file_sha256':file_hash,'inserted':inserted,
            'ownership':'UNOWNED','performance_eligible':False}


def snapshot_capture(ledger,receipt_path,body_path):
    try:
        from .poly_research_capture import url_for
    except ImportError:
        from poly_research_capture import url_for
    receipt=json.loads(bounded(receipt_path))
    if not isinstance(receipt,dict) or type(receipt.get('status')) is not int or receipt['status']!=200:
        raise ValueError('capture HTTP status not successful')
    kind=receipt.get('kind'); slug=receipt.get('market_slug')
    if type(kind) is not str: raise ValueError('invalid capture kind')
    if receipt.get('url')!=url_for(kind,slug): raise ValueError('capture URL not allowlisted')
    body=bounded(body_path)
    if type(receipt.get('bytes')) is not int or receipt['bytes']!=len(body):
        raise ValueError('capture byte count mismatch')
    if digest(body)!=receipt.get('sha256'): raise ValueError('capture body hash mismatch')
    # Older fixed-name receipts omitted body_path; they still require exact bytes/hash.
    declared=receipt.get('body_path')
    if declared is not None and (type(declared) is not str or not declared or
                                 Path(declared).resolve()!=Path(body_path).resolve()):
        raise ValueError('capture body path mismatch')
    requested=stamp(receipt.get('requested_at_utc')) if 'requested_at_utc' in receipt else None
    now=stamp(receipt.get('received_at_utc'))
    if requested is not None and requested>now: raise ValueError('capture time order invalid')
    source=str(Path(receipt_path).resolve()); raw=bounded(receipt_path)
    snapshot={'kind':kind,'url':receipt['url'],'body_path':str(Path(body_path).resolve()),
              'body_sha256':digest(body),'receipt_path':source,'receipt_sha256':digest(raw),
              'received_at_utc':now,'ownership':'UNOWNED','provenance':'UNVERIFIED',
              'is_fill':False,'candidate_sha256':None,'performance_eligible':False}
    with ledger.db:
        inserted=ledger.db.execute('''INSERT OR IGNORE INTO source_snapshots
          (source_path,file_sha256,line_no,line_sha256,captured_at,kind,market_id,slug,event_id,
           candidate_sha256,ownership,provenance,is_fill,snapshot)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (source,digest(raw),0,digest(body),now,'capture-'+kind,None,slug,None,None,
           'UNOWNED','UNVERIFIED',0,json.dumps(snapshot,sort_keys=True))).rowcount
    return {'source_path':source,'body_sha256':digest(body),'inserted':inserted,
            'ownership':'UNOWNED','performance_eligible':False}


def weekly(ledger,week):
    week=stamp(week)
    previous=ledger.db.execute('SELECT report FROM diagnostic_weeks WHERE week=?',(week,)).fetchone()
    if previous: return json.loads(previous[0])
    candidates=ledger.db.execute('SELECT COUNT(*) FROM hypotheses').fetchone()[0]
    strict=None
    if candidates:
        try:
            from .poly_research_loop import Ledger
        except ImportError:
            from poly_research_loop import Ledger
        # Native strict candidate-owned critic is the ONLY performance comparison.
        existing=ledger.db.execute('SELECT report FROM critiques WHERE week=?',(week,)).fetchone()
        if existing: strict=json.loads(existing[0])
        else:
            owner=Ledger(ledger.path)
            try: strict=owner.weekly_critic(week)
            finally: owner.close()
    report={'week':week,'status':strict['status'] if strict else 'INSUFFICIENT_CANDIDATE_OWNED_PAPER',
            'source_snapshots':ledger.status()['source_snapshots'],
            'candidate_owned_paper_n':sum(f['paper_n'] for f in strict['findings']) if strict else 0,
            'live_promotions':0,'strict_candidate_critic':strict,
            'reason':'unowned/unverified snapshots excluded; fees/books/settlement/DSR remain independent promotion gates'}
    with ledger.db:
        ledger.db.execute('INSERT OR IGNORE INTO diagnostic_weeks(week,report) VALUES (?,?)',
                          (week,json.dumps(report,sort_keys=True)))
        return json.loads(ledger.db.execute('SELECT report FROM diagnostic_weeks WHERE week=?',(week,)).fetchone()[0])


def main(argv=None):
    parser=argparse.ArgumentParser(description='Offline research evidence intake; never broker orders')
    parser.add_argument('command',choices=('intake-evidence','status','weekly-critic'))
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    parser.add_argument('--journal',type=Path,default=DEFAULT_JOURNAL)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--receipt',type=Path)
    parser.add_argument('--body',type=Path)
    parser.add_argument('--kind',choices=('history','settlement'))
    parser.add_argument('--slug')
    parser.add_argument('--week')
    args=parser.parse_args(argv)
    ledger=EvidenceDB(args.db)
    try:
        if args.command=='intake-evidence':
            if args.receipt:
                if not args.body or args.source: parser.error('--receipt requires --body and excludes --source')
                result=snapshot_capture(ledger,args.receipt,args.body)
            elif args.source:
                if args.body or not args.kind or not args.slug: parser.error('--source requires --kind and --slug, excludes --body')
                result=snapshot_source(ledger,args.source,args.kind,args.slug)
            else:
                if args.body: parser.error('--body requires --receipt')
                result=snapshot_journal(ledger,args.journal)
        elif args.command=='weekly-critic':
            if not args.week: parser.error('--week required')
            result=weekly(ledger,args.week)
        else: result=ledger.status()
    finally: ledger.close()
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__': main()
