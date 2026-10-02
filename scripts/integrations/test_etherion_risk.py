"""Offline tests: python -m unittest -v test_etherion_risk (or pytest -q).
No prices, network, API credentials, or trading accounts are required.
"""
import ast
from dataclasses import asdict, replace, FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from etherion_risk import (EREConfig, EREResult, Baseline, assess, update_baseline,
                          mult_for, score_series, level_for, failure, utc)


def dates(n, start="2000-01-01T00:00:00+00:00"):
    t = datetime.fromisoformat(start)
    return [(t+timedelta(days=i)).isoformat() for i in range(n)]


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = EREConfig(assets=("A","B","C"))
        rng = np.random.default_rng(730177)
        cls.br = rng.normal(0,.01,(4096,3))
        cls.bd = dates(len(cls.br))
        cls.b = update_baseline(None, cls.br, cls.bd, cls.cfg)
        cls.r = rng.normal(0,.01,(300,3))
        cls.d = dates(300, "2020-01-01T00:00:00+00:00")
        cls.w = np.array([.6,.3,.1])

    def check(self, r=None, d=None, w=None, b=None, cfg=None, **kw):
        return assess(self.r if r is None else r, self.d if d is None else d,
                      self.w if w is None else w, self.b if b is None else b,
                      self.cfg if cfg is None else cfg, **kw)

    def test_deterministic_and_immutable_baseline(self):
        before=asdict(self.b)
        a=self.check();b=self.check()
        self.assertEqual(a,b);self.assertEqual(a.data_quality,"OK")
        self.assertEqual(before,asdict(self.b))
        with self.assertRaises(FrozenInstanceError):self.cfg.cut_q=.99
        with self.assertRaises(TypeError):self.b.returns[0][0]=0

    def test_scale_and_history_length_invariance(self):
        b=update_baseline(None,self.br*.1,self.bd,self.cfg)
        a=self.check();v=self.check(r=self.r*.1,b=b)
        self.assertAlmostEqual(a.multiplier,v.multiplier,places=12)
        self.assertAlmostEqual(a.components["downside_mean"]["value"],v.components["downside_mean"]["value"],places=12)
        short=self.cfg.short+self.cfg.reference
        self.assertEqual(a,self.check(r=self.r[-short:],d=self.d[-short:]))

    def test_single_asset(self):
        cfg=EREConfig()
        b=update_baseline(None,self.br[:,0],self.bd,cfg)
        z=assess(self.r[:,0],self.d,baseline=b,cfg=cfg)
        self.assertEqual(z.data_quality,"OK")

    def test_required_weights_and_ids(self):
        a=assess(self.r,self.d,baseline=self.b,cfg=self.cfg)
        self.assertTrue(a.halt);self.assertEqual(a.data_quality,"INSUFFICIENT")
        self.assertTrue(assess(self.r,self.d,self.w,self.b).halt)
        for w in ([0,0,0],[1,1,1],[np.nan,0,1],[1,0]):
            self.assertEqual(self.check(w=w).multiplier,0)

    def test_asset_permutation_must_match_baseline(self):
        self.assertTrue(self.check(cfg=replace(self.cfg,assets=("B","A","C"))).halt)
        perm=[2,0,1]
        cfg=replace(self.cfg,assets=tuple(self.cfg.assets[i] for i in perm))
        b=update_baseline(None,self.br[:,perm],self.bd,cfg)
        v=self.check(r=self.r[:,perm],w=self.w[perm],b=b,cfg=cfg)
        self.assertAlmostEqual(v.multiplier,self.check().multiplier,places=12)

    def test_nan_inf_and_impossible_return_fail_closed(self):
        for value in (np.nan,np.inf,-np.inf,-1.1):
            r=self.r.copy();r[-1,0]=value
            z=self.check(r=r)
            self.assertTrue(z.halt);self.assertNotEqual(z.data_quality,"OK")
            self.assertEqual(z.multiplier,0)

    def test_misalignment_not_dropped(self):
        ds=np.repeat(np.array(self.d,dtype=object)[:,None],3,axis=1)
        self.assertEqual(self.check(d=ds).data_quality,"OK")
        ds[-1,1]=self.d[-2]
        self.assertEqual(self.check(d=ds).data_quality,"MISALIGNED")
        for d in (self.d[:-1],self.d[::-1],[self.d[0]]*len(self.d)):
            self.assertEqual(self.check(d=d).data_quality,"MISALIGNED")
        self.assertEqual(self.check(d=[x[:10] for x in self.d]).data_quality,"MISALIGNED")

    def test_no_self_baseline(self):
        b=update_baseline(None,self.r,self.d,self.cfg)
        z=self.check(b=b)
        self.assertEqual(z.data_quality,"MISALIGNED");self.assertTrue(z.halt)
        z=assess(self.r,self.d,self.w,None,self.cfg)
        self.assertTrue(z.halt);self.assertEqual(z.data_quality,"INSUFFICIENT")

    def test_zero_volatility_and_short_data(self):
        self.assertTrue(self.check(r=np.zeros_like(self.r)).halt)
        self.assertTrue(self.check(r=self.r[:50],d=self.d[:50]).halt)
        b=update_baseline(None,np.zeros_like(self.br),self.bd,self.cfg)
        self.assertTrue(self.check(b=b).halt)

    def test_baseline_append_idempotent_revision_guard(self):
        self.assertEqual(update_baseline(self.b,self.br[-10:],self.bd[-10:],self.cfg),self.b)
        revised=self.br[-10:].copy();revised[-1,0]+=.01
        with self.assertRaises(ValueError):update_baseline(self.b,revised,self.bd[-10:],self.cfg)
        b=update_baseline(self.b,self.r,self.d,self.cfg)
        self.assertEqual(len(b.returns),self.cfg.max_history)
        self.assertEqual(b.dates[-1],self.d[-1].replace("+00:00","Z"))
        # The new baseline cannot assess the data it just incorporated.
        self.assertTrue(self.check(b=b).halt)

    def test_stale_future_and_mult_for_contract(self):
        a=self.check();end=datetime.fromisoformat(self.d[-1]);now=end+timedelta(hours=1)
        self.assertEqual(mult_for(a,7200,now=now,cfg=self.cfg),a.multiplier)
        self.assertEqual(mult_for(a,1,now=now,cfg=self.cfg),0)
        self.assertEqual(mult_for(a,7200,now=end-timedelta(seconds=1),cfg=self.cfg),0)
        self.assertEqual(self.check(now=end+timedelta(days=5)).data_quality,"STALE")
        self.assertEqual(mult_for(None,7200,now=now),0)
        for key,value in [("schema","ere/1"),("config_id","wrong-config"),("multiplier",np.nan),("multiplier",True),
                           ("multiplier",1.1),("halt",1),("level","HIGH"),("risk_level","HIGH"),
                           ("data_quality","STALE"),("asof","bad")]:
            d=asdict(a);d[key]=value
            self.assertEqual(mult_for(d,7200,now=now,cfg=self.cfg),0,(key,value))

    def test_correlated_crash_within_five_bars(self):
        rng=np.random.default_rng(512709)
        r=self.r.copy();ds=list(self.d)
        first=None
        for j in range(1,11):
            crash=-.04+rng.normal(0,.001,(1,1))+rng.normal(0,.0002,(1,3))
            r=np.concatenate([r,crash]);ds=dates(len(r),"2020-01-01T00:00:00+00:00")
            z=self.check(r=r,d=ds)
            if z.multiplier<1 and first is None:first=j
        self.assertIsNotNone(first);self.assertLessEqual(first,5)
        print("\nSYNTHETIC correlated crash first cut:",first,"bar(s)")

    def test_volatility_shift_severity_monotone(self):
        # A pathwise time-monotonic assertion would be false for noisy returns.
        # The correct property is monotonicity in severity on a fixed path and
        # fixed independent reference. No baseline updates inside the shock.
        vals=[];ms=[]
        for factor in (1.,1.5,2.,3.,4.,6.):
            r=self.r.copy();r[-self.cfg.short:]*=factor
            z=self.check(r=r);vals.append(z.components["downside_mean"]["value"]);ms.append(z.multiplier)
        self.assertTrue(np.all(np.diff(vals)>0));self.assertTrue(np.all(np.diff(ms)<=0))
        self.assertLess(ms[-1],ms[0])
        print("SYNTHETIC volatility severity scores:",vals,"multipliers:",ms)

    def test_continuous_monotone_bounded_mapping(self):
        m=[]
        for severity in np.linspace(0,5,101):
            r=self.r.copy();r[-self.cfg.short:]=-severity*.01
            z=self.check(r=r);m.append(z.multiplier)
            self.assertTrue(0<=z.multiplier<=1)
            self.assertEqual(z.level,level_for(z.multiplier,self.cfg))
        self.assertTrue(np.all(np.diff(m)<=1e-12))
        self.assertEqual(m[0],1);self.assertEqual(m[-1],0)

    def test_weights_measure_the_held_book(self):
        r=self.r.copy();r[-10:,0]=-.04;r[-10:,1:]=.001
        risky=self.check(r=r,w=[.9,.05,.05]);protected=self.check(r=r,w=[.01,.495,.495])
        self.assertLess(risky.multiplier,protected.multiplier)

    def test_no_future_leakage_in_score_series(self):
        a=score_series(self.r,self.w,self.cfg)
        r=self.r.copy();r[-10:]=-.05
        b=score_series(r,self.w,self.cfg)
        self.assertTrue(np.array_equal(a[:-10],b[:-10]))

    def test_independent_noise_budget(self):
        # Validation seed NOT used for parameter selection. Thresholds come only
        # from each past baseline, never from the draws being evaluated.
        rng=np.random.default_rng(20261002)
        cfg=EREConfig()
        report=[]
        for distribution in ("Gaussian","Student_t3"):
            cuts=halts=n=0
            for replication in range(20):
                if distribution=="Gaussian":
                    b=rng.normal(0,.001,4096);r=rng.normal(0,.001,5000)
                else:
                    b=rng.standard_t(3,4096)*.001;r=rng.standard_t(3,5000)*.001
                self.assertTrue(np.all(b>=-1) and np.all(r>=-1))
                past=score_series(b,[1.],cfg);test=score_series(r,[1.],cfg)
                lo,hi=np.quantile(past,[cfg.cut_q,cfg.halt_q])
                cuts+=int(np.sum(test>lo));halts+=int(np.sum(test>=hi));n+=len(test)
                # Public assess parity at multiple full, genuinely out-of-sample windows.
                bb=update_baseline(None,b,dates(len(b)),cfg)
                rd=dates(len(r),"2020-01-01T00:00:00+00:00")
                for t in (500,1500,4999):
                    res=assess(r[t-129:t+1],rd[t-129:t+1],baseline=bb,cfg=cfg)
                    expected=float(np.clip((hi-test[t-129])/(hi-lo),0,1))
                    self.assertEqual(res.data_quality,"OK")
                    self.assertAlmostEqual(res.multiplier,expected,places=12)
            self.assertLessEqual(cuts/n,.10);self.assertLessEqual(halts/n,.01)
            row={"distribution":distribution,"observations":n,"cuts":cuts,"halts":halts,
                 "cut_rate":cuts/n,"halt_rate":halts/n}
            report.append(row)
        print("\nNULL VALIDATION",json.dumps(report,sort_keys=True))
        # Windows overlap: do NOT report binomial confidence intervals as if
        # these n observations were independent. The pass is an empirical test.


class LedgerTests(unittest.TestCase):
    def test_zero_fee_flat_price_ledger(self):
        from validate_ere import Plan,simulate,metrics
        import pandas as pd
        plan=replace(Plan(),min_order=0)
        pan={"dates":pd.date_range("2020-01-01",periods=20),"open":np.ones((20,2))*100,
             "weights":np.array([.7,.3]),"rates":np.zeros(2)}
        m=np.ones(20);r=simulate(pan,m,"2020-01-01","2021-01-01",plan)
        self.assertTrue(np.allclose(r.net_return,0));self.assertAlmostEqual(r.ending_equity.iloc[-1],500)
        self.assertGreater(r.turnover.sum(),1.9)  # entry AND exit

    def test_fees_and_halt_are_booked(self):
        from validate_ere import Plan,simulate
        import pandas as pd
        pan={"dates":pd.date_range("2020-01-01",periods=20),"open":np.ones((20,1))*100,
             "weights":np.array([1.]),"rates":np.array([.0015])}
        m=np.ones(20);m[5:]=0
        r=simulate(pan,m,"2020-01-01","2021-01-01",Plan())
        self.assertGreater(r.cost_dollars.sum(),1)
        self.assertAlmostEqual(500-r.ending_equity.iloc[-1],r.cost_dollars.sum(),places=8)
        self.assertTrue(np.allclose(r.net_return.iloc[6:],0))

    def test_next_open_no_same_close_execution(self):
        from validate_ere import Plan,simulate
        import pandas as pd
        op=np.ones((12,1))*100;op[4:]=200
        pan={"dates":pd.date_range("2020-01-01",periods=12),"open":op,
             "weights":np.array([1.]),"rates":np.zeros(1)}
        m=np.zeros(12);m[4:]=1  # learns of gap only after it happened
        r=simulate(pan,m,"2020-01-01","2021-01-01",Plan())
        self.assertTrue(np.allclose(r.net_return,0))



class RuntimeTests(unittest.TestCase):
    def setUp(self):
        import ere_runtime as rt
        self.rt=rt; self.tmp=tempfile.TemporaryDirectory();self.models=Path(self.tmp.name)
        self.now=datetime(2026,10,1,12,tzinfo=timezone.utc)
        self.policy={"mode":"HEALTH_ONLY","all_risk_increasing_readers_migrated":True,
                     "universe":["BTC-USD"],"core":{}}
        self.pos={"schema":"ere-positions/1","asof":self.now.isoformat(),"complete":True,
                  "position_version":"test-only-v1","nav":500.,"weights":{"BTC-USD":.5},
                  "binary_positions":[]}
        self.write("ere_runtime_config.json",self.policy);self.write("ere_positions.json",self.pos)
        self.cfg=EREConfig()
        self.good=rt.result_dict(1., "2026-10-01T00:00:00+00:00", "test fixture",self.cfg)
        self.state={**self.good,"results":{"BOOK":dict(self.good),"BTC-USD":dict(self.good)},
                    "generated_at":self.now.isoformat(),
                    "expires_at":(self.now+timedelta(seconds=self.cfg.heartbeat_ttl_s)).isoformat(),
                    "risk_level":"NORMAL","heartbeat":{"status":"OK"},
                    "config_digest":rt.digest(self.policy),"holdings_digest":rt.digest(self.pos),
                    "core_sha256":rt.core_digest(),"mode":"HEALTH_ONLY"}
        self.write("ere_state.json",self.state)

    def tearDown(self):self.tmp.cleanup()
    def write(self,name,data):self.rt.atomic_write(self.models/name,data)
    def read(self):return self.rt.read_gate("BTC-USD",self.models,now=self.now)
    def closed(self):
        with self.assertLogs("ere",level="ERROR"):
            value=self.read()
        self.assertEqual(value["multiplier"],0);self.assertTrue(value["halt"])

    def test_runtime_good_and_missing(self):
        self.assertEqual(self.read()["multiplier"],1.)
        (self.models/"ere_state.json").unlink();self.closed()

    def test_runtime_invalid_json_and_nonfinite(self):
        (self.models/"ere_state.json").write_text("{broken");self.closed()
        (self.models/"ere_state.json").write_text('{"multiplier":NaN}');self.closed()

    def test_runtime_heartbeat_is_not_price_freshness(self):
        self.state["results"]["BTC-USD"]["asof"]="2025-01-01T00:00:00Z"
        self.write("ere_state.json",self.state);self.closed()

    def test_runtime_position_revision_and_config_revision(self):
        self.pos["position_version"]="new-fill";self.write("ere_positions.json",self.pos);self.closed()
        self.pos["position_version"]="test-only-v1";self.write("ere_positions.json",self.pos)
        self.policy["core"]={"cut_q":.975};self.write("ere_runtime_config.json",self.policy);self.closed()

    def test_runtime_heartbeat_expiry_future_and_bad_alias(self):
        self.state["generated_at"]=(self.now+timedelta(seconds=1)).isoformat()
        self.write("ere_state.json",self.state);self.closed()
        self.state["generated_at"]=(self.now-timedelta(hours=2)).isoformat()
        self.write("ere_state.json",self.state);self.closed()
        self.state["generated_at"]=self.now.isoformat();self.state["risk_level"]="HIGH"
        self.write("ere_state.json",self.state);self.closed()

    def test_runtime_unknown_scope_and_no_statistical_live_mode(self):
        with self.assertLogs("ere",level="ERROR"):
            v=self.rt.read_gate("KALSHI",self.models,now=self.now)
        self.assertEqual(v["multiplier"],0)
        self.policy["mode"]="LIVE";self.write("ere_runtime_config.json",self.policy);self.closed()

    def test_calendar_completed_crypto_weekend_and_us_early_close(self):
        import pandas as pd
        import exchange_calendars as xc
        ds=pd.date_range("2025-01-01","2025-11-30",freq="D")
        df=pd.DataFrame({"date":ds,"ticker":"BTC-USD","adj_close":np.arange(len(ds))+100.})
        cfg=replace(self.cfg,reference=2,short=2,min_scores=2)
        r,ends,_=self.rt.completed_inputs(df,["BTC-USD"],cfg,"2025-11-30T12:00:00Z")
        self.assertEqual(utc(ends[-1]),utc("2025-11-30T00:00:00Z"))
        cal=xc.get_calendar("XNYS",start="2025-01-01",end="2025-11-30")
        df=pd.DataFrame({"date":cal.sessions,"ticker":"SPY","adj_close":np.arange(len(cal.sessions))+100.})
        r,ends,_=self.rt.completed_inputs(df,["SPY"],cfg,"2025-11-28T18:30:00Z")
        self.assertEqual(utc(ends[-1]),utc("2025-11-28T18:00:00Z"))
        # Missing expected completed price cannot be repaired with a forward fill.
        with self.assertRaises(ValueError):
            self.rt.completed_inputs(df.iloc[:-1],["SPY"],cfg,"2025-11-28T18:30:00Z")

    def test_runtime_paper_writer_and_error_publication(self):
        import pandas as pd
        ds=pd.date_range("2017-01-01","2026-09-30",freq="D")
        prices=100*np.exp(np.cumsum(np.random.default_rng(9321).normal(0,.01,len(ds))))
        df=pd.DataFrame({"date":ds,"ticker":"BTC-USD","adj_close":prices,
                "open":prices,"high":prices,"low":prices,"close":prices,"volume":100})
        csv=self.models/"prices.csv";df.to_csv(csv,index=False)
        self.policy["mode"]="PAPER_LOCKED";self.write("ere_runtime_config.json",self.policy)
        a=self.rt.publish(self.models,csv,now=self.now)
        self.assertEqual(a["heartbeat"]["status"],"OK")
        self.assertEqual(a["multiplier"],0.)
        self.assertEqual(a["candidate_results"]["BTC-USD"]["data_quality"],"OK")
        before=(self.models/"ere_baselines.json").read_text()
        self.rt.publish(self.models,csv,now=self.now)
        self.assertEqual(before,(self.models/"ere_baselines.json").read_text())
        self.pos["complete"]=False;self.write("ere_positions.json",self.pos)
        with self.assertLogs("ere",level="ERROR"):
            b=self.rt.publish(self.models,csv,now=self.now)
        self.assertEqual(b["heartbeat"]["status"],"ERROR");self.assertEqual(b["multiplier"],0.)

    def test_core_configuration_integer_and_identity_validation(self):
        for kw in ({"short":10.5},{"assets":["A"]},{"assets":("",)},
                   {"heartbeat_ttl_s":float("nan")},{"baseline_embargo":1}):
            with self.assertRaises(ValueError):EREConfig(**kw)


if __name__=="__main__":
    unittest.main(verbosity=2)
