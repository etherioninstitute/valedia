"""Strict offline event intake and six-role advisory review."""
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

ROLES = ('scanner', 'setup', 'news', 'insider', 'risk_context', 'synthesis')
STATES = ('available', 'unavailable', 'not_applicable')
TOKEN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$')
HEX = re.compile(r'^[0-9a-f]{64}$')


class DeskError(ValueError):
    pass


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def utc(s):
    if not isinstance(s, str) or len(s) > 40:
        raise DeskError('UTC timestamp required')
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if d.tzinfo is None or d.utcoffset().total_seconds() != 0:
            raise ValueError()
        return d.astimezone(timezone.utc)
    except (ValueError, OverflowError, AttributeError) as exc:
        raise DeskError('UTC timestamp required') from exc


def num(v, *, positive=False, nonnegative=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise DeskError('finite number required')
    if positive and v <= 0 or nonnegative and v < 0:
        raise DeskError('number outside allowed range')
    return float(v)


def _parts(path):
    p=Path(path).absolute()
    if '..' in p.parts:raise DeskError('parent traversal in path')
    parts=list(p.parts[1:])
    # Only these fixed macOS root aliases may be normalized. No caller path is resolved.
    if parts and parts[0] in ('tmp','var'):
        alias=Path('/')/parts[0]
        target={'tmp':Path('/private/tmp'),'var':Path('/private/var')}[parts[0]]
        if alias.is_symlink():
            if alias.resolve()!=target:raise DeskError('unexpected OS root alias')
            parts=['private',parts[0]]+parts[1:]
    return p,parts


def open_directory(path):
    """Walk every directory component relative to a held, no-follow descriptor."""
    if not getattr(os,'O_NOFOLLOW',0) or not getattr(os,'O_DIRECTORY',0) or os.open not in os.supports_dir_fd:
        raise DeskError('descriptor-safe directory open unavailable')
    _,parts=_parts(path)
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    try:fd=os.open('/',flags)
    except OSError as exc:raise DeskError('root directory open failed') from exc
    try:
        for part in parts:
            next_fd=os.open(part,flags,dir_fd=fd)
            os.close(fd);fd=next_fd
        return fd
    except (OSError,ValueError) as exc:
        os.close(fd)
        raise DeskError('regular non-symlink directory required') from exc


def _read_json_at(parent_fd,leaf,limit,*,with_stat=False):
    if not getattr(os,'O_NOFOLLOW',0) or not getattr(os,'O_NONBLOCK',0) or os.open not in os.supports_dir_fd:
        raise DeskError('descriptor-safe input open unavailable')
    try:fd=os.open(leaf,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent_fd)
    except OSError as exc:raise DeskError('regular bounded input required') from exc
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:
            raise DeskError('regular bounded input required')
        with os.fdopen(fd,'rb') as f:
            fd=-1;raw=f.read(limit+1)
            return (raw,info) if with_stat else raw
    finally:
        if fd>=0:os.close(fd)


def regular_bytes(path,limit=300_000,*,with_stat=False):
    p,_=_parts(path)
    parent_fd=open_directory(p.parent)
    try:
        result=_read_json_at(parent_fd,p.name,limit,with_stat=with_stat)
    finally:os.close(parent_fd)
    raw=result[0] if with_stat else result
    if len(raw) > limit:
        raise DeskError('input byte limit')
    return result


def regular_json(path, limit=300_000):
    raw=regular_bytes(path,limit)
    try:
        def unique(pairs):
            out={}
            for key,value in pairs:
                if key in out: raise DeskError('duplicate JSON key')
                out[key]=value
            return out
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(DeskError('nonfinite JSON')))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DeskError('invalid JSON') from exc


def verify_report(receipt):
    """Check receipt-bound bytes and advisory structure; source authenticity is unverified."""
    if not isinstance(receipt,dict):receipt=regular_json(receipt,20_000)
    _fields(receipt,('schema','event_id','artifacts'))
    if receipt['schema']!='valedia.research-desk.receipt.v1' or not isinstance(receipt['event_id'],str) or not HEX.fullmatch(receipt['event_id']):
        raise DeskError('invalid receipt schema or event identity')
    artifacts=receipt['artifacts']
    if not isinstance(artifacts,dict) or 'report' not in artifacts or set(artifacts)-{'report','evaluation','csv','svg','pine'}:
        raise DeskError('invalid receipt artifact map')
    if set(artifacts)&{'csv','svg','pine'} not in (set(),{'csv','svg','pine'}):
        raise DeskError('incomplete forecast receipt')
    paths=[]
    for item in artifacts.values():
        _fields(item,('path','sha256','size','dev','ino'))
        if (not isinstance(item['path'],str) or not Path(item['path']).is_absolute() or
            not isinstance(item['sha256'],str) or not HEX.fullmatch(item['sha256']) or
            any(isinstance(item[k],bool) or not isinstance(item[k],int) or item[k]<0 for k in ('size','dev','ino')) or
            item['size']>2_000_000):
            raise DeskError('invalid artifact metadata')
        paths.append(item['path'])
    if len(set(paths))!=len(paths) or artifacts['report']['size']>1_000_000:
        raise DeskError('duplicate or oversized report artifact')
    item=artifacts['report']
    raw,info=regular_bytes(item['path'],1_000_000,with_stat=True)
    if (len(raw)!=item['size'] or hashlib.sha256(raw).hexdigest()!=item['sha256'] or
        info.st_dev!=item['dev'] or info.st_ino!=item['ino']):
        raise DeskError('report artifact identity mismatch')
    try:report=json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(DeskError('nonfinite report JSON')))
    except (UnicodeError,json.JSONDecodeError) as exc:raise DeskError('invalid report JSON') from exc
    if not isinstance(report,dict) or report.get('event_id')!=receipt['event_id']:
        raise DeskError('report event mismatch')
    if review(report.get('event'),report.get('roles'),digest=report.get('cached_scenario'))!=report:
        raise DeskError('report advisory reconstruction failed')
    return report


def _fields(d, expected):
    if not isinstance(d, dict) or set(d) != set(expected):
        raise DeskError('unexpected or missing fields')


def validate_event(event):
    _fields(event, ('venue', 'symbol', 'market_type', 'interval_seconds', 'decision_at', 'published_at',
                    'available_at', 'expires_at', 'source_sha256', 'source_uri', 'candles'))
    if event['market_type'] not in ('continuous','event','sports','weather','other'):
        raise DeskError('invalid market type')
    for key in ('venue', 'symbol'):
        if not isinstance(event[key], str) or not TOKEN.fullmatch(event[key]):
            raise DeskError('invalid venue or symbol')
    step = event['interval_seconds']
    if isinstance(step, bool) or not isinstance(step, int) or not 1 <= step <= 86400:
        raise DeskError('invalid interval')
    if not isinstance(event['source_sha256'], str) or not HEX.fullmatch(event['source_sha256']):
        raise DeskError('source hash required')
    uri = event['source_uri']
    if not isinstance(uri, str) or len(uri) > 512 or not (uri.startswith('https://') or uri.startswith('fixture:')):
        raise DeskError('invalid source URI')
    decision, published, available, expires = (utc(event[k]) for k in
        ('decision_at', 'published_at', 'available_at', 'expires_at'))
    if not published <= available <= decision < expires:
        raise DeskError('future, expired or inconsistent source chronology')
    bars = event['candles']
    if not isinstance(bars, list) or len(bars) not in (0,) and not 2 <= len(bars) <= 512:
        raise DeskError('zero or 2..512 candles required')
    prev = None
    for bar in bars:
        _fields(bar, ('at', 'available_at', 'open', 'high', 'low', 'close', 'volume'))
        at, seen = utc(bar['at']), utc(bar['available_at'])
        if seen > decision or at > seen or (prev and (at-prev).total_seconds() != step):
            raise DeskError('future, duplicate, nonmonotone or gapped candle')
        o, h, l, c = (num(bar[k], positive=True) for k in ('open', 'high', 'low', 'close'))
        num(bar['volume'], nonnegative=True)
        if l > min(o, c) or h < max(o, c) or l > h:
            raise DeskError('invalid OHLC')
        prev = at
    return event


def _source(s, decision, role):
    _fields(s, ('uri', 'published_at', 'available_at', 'expires_at', 'sha256'))
    if not isinstance(s['uri'], str) or len(s['uri']) > 512 or not (s['uri'].startswith('https://') or
        role not in ('news','insider') and (s['uri'].startswith('repo:') or s['uri'].startswith('fixture:'))):
        raise DeskError('claim source URI invalid')
    if not isinstance(s['sha256'], str) or not HEX.fullmatch(s['sha256']):
        raise DeskError('claim source hash required')
    if not utc(s['published_at']) <= utc(s['available_at']) <= decision < utc(s['expires_at']):
        raise DeskError('stale or future claim')


def _validate_role(name,r,decision):
        _fields(r, ('status', 'observed_at', 'summary', 'sources')+
                (('relevance',) if name == 'scanner' else ()))
        if name == 'scanner' and (r['relevance'] not in ('relevant','noise','unknown') or
           (r['status']=='available') != (r['relevance']!='unknown')):
            raise DeskError('scanner relevance required')
        if r['status'] not in STATES or not isinstance(r['summary'], str) or len(r['summary']) > 500:
            raise DeskError('invalid role report')
        if r['observed_at'] is not None and utc(r['observed_at']) > decision:
            raise DeskError('future role report')
        if not isinstance(r['sources'], list) or len(r['sources']) > 8:
            raise DeskError('bounded source list required')
        for s in r['sources']:
            _source(s, decision, name)
            if r['observed_at'] is not None and utc(s['available_at']) > utc(r['observed_at']):
                raise DeskError('claim became available after role observation')
        if r['status'] == 'available' and (r['observed_at'] is None or
           name in ('news', 'insider') and not r['sources']):
            raise DeskError('available report lacks dated evidence')
        if r['status'] != 'available' and r['sources']:
            raise DeskError('unavailable report cannot contain claims')
        if r['status']=='not_applicable' and not r['summary'].strip():
            raise DeskError('not applicable requires a reason')


def validate_roles(roles, decision):
    if not isinstance(roles, dict) or set(roles) != set(ROLES):
        raise DeskError('six role reports required')
    for name,r in roles.items():
        _validate_role(name,r,decision)
    return roles


def review(event, roles=None, providers=None, digest=None):
    validate_event(event)
    if roles is not None and providers is not None:
        raise DeskError('choose supplied reports or injected providers')
    if providers is not None:
        if not isinstance(providers, dict) or any(k not in ROLES for k in providers):
            raise DeskError('invalid provider map')
        def call(name):
            unavailable={'status': 'unavailable', 'observed_at': None, 'summary': '', 'sources': []}
            if name == 'scanner': unavailable['relevance']='unknown'
            if name not in providers:return unavailable
            try:return providers[name](event)
            except Exception:return unavailable
        scanner=call('scanner')
        try:_validate_role('scanner',scanner,utc(event['decision_at']))
        except (DeskError,TypeError,KeyError):
            scanner={'status':'unavailable','relevance':'unknown','observed_at':None,
                     'summary':'malformed scanner report','sources':[]}
        roles={'scanner':scanner}
        short=(isinstance(scanner,dict) and
               (scanner.get('status')!='available' or scanner.get('relevance')=='noise'))
        for name in ROLES[1:]:
            roles[name]=({'status':'not_applicable' if scanner.get('relevance')=='noise' else 'unavailable',
                          'observed_at':None,'summary':'scanner short circuit','sources':[]}
                         if short else call(name))
    if roles is None:
        roles = {name: {'status': 'unavailable', 'observed_at': None, 'summary': '', 'sources': []}
                 for name in ROLES}
        roles['scanner']['relevance']='unknown'
    validate_roles(roles, utc(event['decision_at']))
    if digest is not None:
        _fields(digest, ('kind', 'generated_at', 'expires_at', 'summary', 'source_sha256'))
        if digest['kind'] != 'cached_scenario' or not isinstance(digest['summary'], str) or len(digest['summary']) > 500:
            raise DeskError('invalid cached digest')
        if not isinstance(digest['source_sha256'], str) or not HEX.fullmatch(digest['source_sha256']):
            raise DeskError('invalid digest hash')
        if not utc(digest['generated_at']) <= utc(event['decision_at']) < utc(digest['expires_at']):
            raise DeskError('stale or future digest')
    identity = sha({'schema': 'valedia.research-desk.event.v1', 'venue': event['venue'],
                    'market_type':event['market_type'],
                    'symbol': event['symbol'], 'interval_seconds': event['interval_seconds'],
                    'decision_at': event['decision_at'], 'published_at': event['published_at'],
                    'available_at': event['available_at'], 'source_sha256': event['source_sha256']})
    required=('scanner','setup','risk_context','synthesis')
    substantial=lambda r:len(r['summary'].strip())>=12 and bool(r['sources'])
    status = ('RESEARCH_ONLY' if roles['scanner']['relevance']=='relevant' and
              all(roles[n]['status']=='available' and substantial(roles[n]) for n in required) and
              all(roles[n]['status'] in ('available','not_applicable') for n in ('news','insider'))
              else 'HOLD')
    report = {'schema': 'valedia.research-desk.report.v1', 'event_id': identity,
              'event': event, 'roles': roles, 'cached_scenario': digest,
              'decision': status, 'reason': 'all role records present' if status == 'RESEARCH_ONLY' else
              ('scanner marked alert as noise' if roles['scanner']['relevance']=='noise' else 'one or more role records unavailable'),
              'ownership': 'UNOWNED', 'provenance': 'UNVERIFIED', 'is_fill': False,
              'candidate_sha256': None, 'performance_eligible': False, 'order_link': False}
    report['report_sha256'] = sha(report)
    return report


def _check_canonical_schema(db):
    expected=('id','source_path','file_sha256','line_no','line_sha256','captured_at','kind',
              'market_id','slug','event_id','candidate_sha256','ownership','provenance','is_fill','snapshot')
    columns=tuple(row[1] for row in db.execute('PRAGMA table_info(source_snapshots)'))
    if columns!=expected:
        raise DeskError('canonical source_snapshots schema required')
    unique=False
    for index in db.execute('PRAGMA index_list(source_snapshots)'):
        name=index[1].replace('"','""')
        if index[2] and tuple(r[2] for r in db.execute(f'PRAGMA index_info("{name}")'))==('source_path','file_sha256','line_no'):
            unique=True
    if not unique:
        raise DeskError('canonical source identity key required')
    triggers={row[0]:row[1] for row in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='source_snapshots'")}
    for operation in ('UPDATE','DELETE'):
        sql=triggers.get('frozen_source_snapshots_'+operation,'') or ''
        if 'RAISE(ABORT' not in sql or 'immutable' not in sql:
            raise DeskError('immutable source snapshot trigger required')
    if not {'intake_refusals','diagnostic_weeks'}.issubset({row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}):
        raise DeskError('canonical EvidenceDB companion tables required')


def persist_advisory(db, report):
    """Append via an already open, owner-managed canonical EvidenceDB connection."""
    if not isinstance(db,sqlite3.Connection):raise DeskError('owner SQLite connection required')
    if db.in_transaction:raise DeskError('active owner transaction refused')
    if report.get('ownership') != 'UNOWNED' or report.get('provenance') != 'UNVERIFIED' or report.get('is_fill') is not False or report.get('candidate_sha256') is not None or report.get('performance_eligible') is not False or report.get('order_link') is not False:
        raise DeskError('nonadvisory report')
    if sha({k: v for k, v in report.items() if k != 'report_sha256'}) != report.get('report_sha256'):
        raise DeskError('report hash mismatch')
    if review(report.get('event'),report.get('roles'),digest=report.get('cached_scenario')) != report:
        raise DeskError('report fails canonical advisory reconstruction')
    event_id, digest = report['event_id'], report['report_sha256']
    source_path = 'research-desk:' + event_id
    snapshot = canonical(report).decode()
    try:
        db.execute('BEGIN IMMEDIATE')
        _check_canonical_schema(db)
        inserted = db.execute('''INSERT OR IGNORE INTO source_snapshots
              (source_path,file_sha256,line_no,line_sha256,captured_at,kind,market_id,slug,event_id,
               candidate_sha256,ownership,provenance,is_fill,snapshot)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (source_path,digest,0,digest,report['event']['decision_at'],'research-desk-advisory',
               None,None,event_id,None,'UNOWNED','UNVERIFIED',0,snapshot)).rowcount
        row = db.execute('''SELECT snapshot,ownership,provenance,is_fill,candidate_sha256
              FROM source_snapshots WHERE source_path=? AND file_sha256=? AND line_no=0''',
              (source_path,digest)).fetchone()
        if row is None or tuple(row) != (snapshot,'UNOWNED','UNVERIFIED',0,None):
            raise DeskError('snapshot readback mismatch')
        db.commit()
        return {'inserted': bool(inserted), 'event_id': event_id, 'report_sha256': digest}
    except (DeskError,sqlite3.Error):
        db.rollback()
        raise
