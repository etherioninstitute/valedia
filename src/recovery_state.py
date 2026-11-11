"""Paper recovery journal. No credentials, network, HOME, or historical ledger access."""
from __future__ import annotations
import contextlib, contextvars, datetime, decimal, hashlib, json, os, pathlib, re, sqlite3

class Refused(RuntimeError): pass
class Unsupported(Refused): pass
_SIZING = contextvars.ContextVar("recovery_sizing", default=None)
def sizing_context(): return _SIZING.get()
@contextlib.contextmanager
def paper_sizing(track, risk):
    vals = [decimal.Decimal(str(x)) for x in (track, risk)]
    if any(not x.is_finite() or x < 0 for x in vals): raise Refused("invalid sizing context")
    token = _SIZING.set({"track": float(vals[0]), "risk": float(vals[1])})
    try: yield
    finally: _SIZING.reset(token)

def require_paper():
    yes = {"1", "true", "yes", "on"}
    no = {"0", "false", "no", "off"}
    if os.environ.get("VALEDIA_LIVE_OPT_IN", "").strip().lower() in yes or os.environ.get("ALPACA_PAPER", "").strip().lower() in no:
        raise Refused("recovery is paper-only; explicit live request refused")

def cents(value):
    try: v = decimal.Decimal(str(value))
    except Exception as exc: raise Refused("invalid money") from exc
    if not v.is_finite() or v <= 0 or v * 100 != (v * 100).to_integral_value(): raise Refused("positive finite cent-exact money required")
    if v > decimal.Decimal("1000000000"): raise Refused("money bound")
    return int(v * 100)
def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", value): raise Refused("explicit stable identifier required")
    return value
def canonical(value):
    try: return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except Exception as exc: raise Refused("invalid immutable payload") from exc
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def utc_day(value):
    if not isinstance(value, str): raise Refused("explicit UTC day required")
    try: day = datetime.date.fromisoformat(value)
    except Exception as exc: raise Refused("invalid UTC day") from exc
    if day.isoformat() != value: raise Refused("invalid UTC day")
    return value

class LazyUnqualified:
    def __getattr__(self, name): raise Unsupported("automatic analysis backend is unqualified in recovery")

class Journal:
    def __init__(self, path, *, account, equity, daily_limit=None, clock=None, reconciler=None):
        self.account = ident(account)
        self.equity = decimal.Decimal(str(equity))
        equity_cents = cents(equity)
        maximum = equity_cents // 5
        self.limit_cents = cents(daily_limit) if daily_limit is not None else maximum
        if self.limit_cents <= 0 or self.limit_cents > maximum: raise Refused("configured daily limit outside equity bound")
        self.clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self.reconciler = reconciler
        root = pathlib.Path(__file__).resolve().parent.parent / "state"
        p = pathlib.Path(path)
        if not p.is_absolute() or ".." in p.parts or not p.is_relative_to(root): raise Refused("state must be inside owned SSD state root")
        if any(q.is_symlink() for q in [p, *p.parents]): raise Refused("symlink state refused")
        p.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(p)
        try:
            with self.tx() as c:
                c.execute("CREATE TABLE IF NOT EXISTS meta(version INTEGER NOT NULL)")
                rows=c.execute("SELECT version FROM meta").fetchall()
                if not rows: c.execute("INSERT INTO meta VALUES(2)")
                elif rows != [(2,)]: raise Refused("unsupported state schema")
                c.execute("CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, account TEXT NOT NULL, day TEXT NOT NULL, request TEXT NOT NULL, request_hash TEXT NOT NULL)")
                c.execute("CREATE TABLE IF NOT EXISTS budgets(account TEXT, day TEXT, limit_cents INTEGER NOT NULL CHECK(limit_cents>0), PRIMARY KEY(account,day))")
                c.execute("CREATE TABLE IF NOT EXISTS intents(id TEXT PRIMARY KEY, decision TEXT NOT NULL, leg TEXT NOT NULL, account TEXT NOT NULL, day TEXT NOT NULL, venue TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL, client_id TEXT UNIQUE NOT NULL, amount INTEGER NOT NULL CHECK(amount>0), status TEXT NOT NULL, broker_id TEXT, evidence TEXT, UNIQUE(decision,leg))")
        except (sqlite3.Error, OSError) as exc: raise Refused("state unavailable; no reset") from exc
    def today(self):
        now = self.clock()
        if not isinstance(now, datetime.datetime) or now.tzinfo is None: raise Refused("trusted UTC-aware clock required")
        return now.astimezone(datetime.timezone.utc).date().isoformat()
    def validate_context(self, account, day):
        if account != self.account or day != self.today(): raise Refused("prepared payload cannot change trusted account/UTC day")
    @contextlib.contextmanager
    def tx(self):
        c=None
        try:
            c=sqlite3.connect(self.path,timeout=5,isolation_level=None)
            c.execute("PRAGMA synchronous=FULL")
            c.execute("PRAGMA busy_timeout=5000")
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.execute("COMMIT")
        except BaseException:
            if c is not None:
                try: c.execute("ROLLBACK")
                except sqlite3.Error: pass
            raise
        finally:
            if c is not None: c.close()
    def decision(self, decision_id, account, day, request=None):
        require_paper();ident(decision_id);ident(account);utc_day(day);self.validate_context(account,day)
        try:
            with self.tx() as c:
                row=c.execute("SELECT account,day,request,request_hash FROM decisions WHERE id=?",(decision_id,)).fetchone()
                if row:
                    if row[0]!=account or row[1]!=day or (request is not None and digest(canonical(request))!=row[3]): raise Refused("decision identity or payload drift")
                    return json.loads(row[2])
                if request is None: raise Refused("unknown decision; explicit new request required")
                body=canonical(request)
                c.execute("INSERT INTO decisions VALUES(?,?,?,?,?)",(decision_id,account,day,body,digest(body)))
                return json.loads(body)
        except (sqlite3.Error, OSError) as exc: raise Refused("decision state unavailable") from exc
    def budget(self, account, day, limit):
        amount=cents(limit)
        self.validate_context(account,day)
        if amount != self.limit_cents: raise Refused("prepared payload cannot change configured daily cap")
        try:
            with self.tx() as c:
                row=c.execute("SELECT limit_cents FROM budgets WHERE account=? AND day=?",(ident(account),utc_day(day))).fetchone()
                if row and row[0]!=amount: raise Refused("daily limit drift refused")
                if not row: c.execute("INSERT INTO budgets VALUES(?,?,?)",(account,day,amount))
        except (sqlite3.Error, OSError) as exc: raise Refused("budget state unavailable") from exc
    def _row(self,c,intent):
        c.row_factory=sqlite3.Row
        r=c.execute("SELECT * FROM intents WHERE id=?",(intent,)).fetchone()
        return dict(r) if r else None
    def submit(self, decision_id, leg, payload, transport):
        require_paper();ident(decision_id);ident(leg)
        if not callable(transport): raise Refused("injected paper transport required")
        if payload.get("venue") not in {"coinbase","alpaca"} or payload.get("side")!="buy": raise Unsupported("unqualified order surface")
        amount=cents(payload["amount"]);body=canonical(payload);ph=digest(body)
        intent=digest(canonical({"namespace":"valedia-recovery-v2","decision":decision_id,"leg":leg}))
        try:
            with self.tx() as c:
                dec=c.execute("SELECT account,day,request FROM decisions WHERE id=?",(decision_id,)).fetchone()
                if not dec: raise Refused("decision must be persisted before intent")
                account,day,request_body=dec
                self.validate_context(account,day)
                legs=json.loads(request_body).get("legs",[])
                matches=[x for x in legs if x.get("leg_id")==leg]
                if len(matches)!=1: raise Refused("leg not in persisted decision")
                planned=matches[0]
                if planned.get("platform")!=payload.get("venue") or planned.get("ticker")!=payload.get("instrument"): raise Refused("decision instrument drift")
                if "allocation" in planned and cents(planned["allocation"])!=amount: raise Refused("decision amount drift")
                if payload.get("account")!=account: raise Refused("account drift")
                existing=self._row(c,intent)
                if existing:
                    if existing["payload_hash"]!=ph: raise Refused("intent payload drift")
                    return {**existing,"attempted":False}
                lim=c.execute("SELECT limit_cents FROM budgets WHERE account=? AND day=?",(account,day)).fetchone()
                if not lim: raise Refused("budget missing")
                spent=c.execute("SELECT COALESCE(SUM(amount),0) FROM intents WHERE account=? AND day=? AND status != 'REJECTED'",(account,day)).fetchone()[0]
                if spent+amount>lim[0]: raise Refused("shared daily budget exceeded")
                client_id="vr1-"+digest(canonical({"namespace":"valedia-recovery-v2","account":account,"venue":payload["venue"],"intent":intent}))[:40]
                c.execute("INSERT INTO intents(id,decision,leg,account,day,venue,payload,payload_hash,client_id,amount,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(intent,decision_id,leg,account,day,payload["venue"],body,ph,client_id,amount,"SUBMITTING"))
            # Durable SUBMITTING and reservation have committed before any transport.
            try:
                response=transport(client_id, json.loads(body))
                if not isinstance(response,dict): response={}
                status="ACKNOWLEDGED" if response.get("outcome")=="accepted" else "REJECTED" if response.get("outcome")=="rejected" and response.get("definite") is True else "UNKNOWN"
                if int(response.get("http_status",200))>=500 or int(response.get("http_status",200))==408: status="UNKNOWN"
                broker_id=response.get("order_id")
            except Exception:
                status="UNKNOWN";broker_id=None
            with self.tx() as c:
                c.execute("UPDATE intents SET status=?,broker_id=? WHERE id=? AND status='SUBMITTING'",(status,str(broker_id) if broker_id else None,intent))
                return {**self._row(c,intent),"attempted":True}
        except (sqlite3.Error,OSError) as exc: raise Refused("journal failed; uncertain submission must not be retried") from exc
    def reconcile(self, intent):
        """Paper evidence may confirm a fill; no uncertain-notional refunds qualified.

        SUBMITTING may represent a live transport or an orphan after a crash.
        Neither can be proven quiescent here, so it remains blocked without even
        invoking lookup. Recovery of that state requires a separate manual gate.
        """
        require_paper()
        if not callable(self.reconciler): raise Refused("trusted reconciler not configured")
        with self.tx() as c:
            before=self._row(c,intent)
            if not before or before["status"] not in {"UNKNOWN","ACKNOWLEDGED"}:
                raise Refused("in-flight/orphan or terminal state cannot be reconciled here")
            snapshot=(before["status"],before["broker_id"],before["evidence"])
        response=self.reconciler(intent)
        if not isinstance(response,dict) or response.get("authoritative") is not True:
            raise Refused("authoritative reconciliation required")
        if response.get("status")!="FILLED" or not isinstance(response.get("evidence"),str) or not response["evidence"].strip():
            raise Refused("absence/cancel/refund reconciliation unqualified; reservation retained")
        with self.tx() as c:
            current=self._row(c,intent)
            if not current or (current["status"],current["broker_id"],current["evidence"])!=snapshot:
                raise Refused("reconciliation state changed; stale evidence refused")
            changed=c.execute("UPDATE intents SET status='FILLED',evidence=? WHERE id=? AND status=?",(response["evidence"],intent,before["status"])).rowcount
            if changed!=1: raise Refused("reconciliation race")
            return self._row(c,intent)
