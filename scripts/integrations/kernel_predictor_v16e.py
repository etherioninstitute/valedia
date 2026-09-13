"""Kernel Predictor v16e — Fixed Signal Extraction
KL gap extraction REMOVED (markets don't have spectral gaps).
Wedderburn SVD used ADAPTIVELY:
  SVD SNR > 1.5 → clean signal (trend assets like GLD, SPY)
  SVD SNR < 1.0 → raw signal (noise assets like BTC, crypto)
  Between → blend

ALL 11 v9 frameworks + Lyapunov channels + Rosetta stationarity.
(c) GOATWORTHY Post LLC.
"""
import numpy as np

# Canonical MARKET manuscript math (NS/RH/BSD/YM/Hodge/Rosetta/Lyapunov functionals).
# Bump on any manuscript update; the manifest (MANUSCRIPT_MANIFEST.md) maps this to consumers,
# and valedia_consistency_check.py fails if a second divergent copy of these functionals appears.
MANUSCRIPT_VERSION = "UGFT-v32.9 (market functionals, impl v16e)"
from scipy.signal import welch, butter, filtfilt, lfilter, lfilter_zi
from scipy import stats
import warnings; warnings.filterwarnings('ignore')

# ══ ADAPTIVE SVD: clean when profitable, raw when not ════════════════
def adaptive_svd(ret):
    """SVD decomposition with adaptive component selection.
    Returns cleaned signal + SNR. High SNR = use cleaned. Low = use raw."""
    if len(ret)<40: return ret, 0.5, ret
    w=min(15,len(ret)//4); cols=len(ret)-w
    H=np.zeros((w,cols))
    for i in range(w): H[i]=ret[i:i+cols]
    try:
        U,S,Vt=np.linalg.svd(H,full_matrices=False)
        total=np.sum(S**2)
        # Adaptive n_sig based on singular value spectrum
        # Find elbow: where does marginal explained variance drop below 5%?
        explained = S**2 / total
        n_sig = 1
        for i in range(1, min(len(S), 8)):
            if explained[i] > 0.05: n_sig = i+1
            else: break
        n_sig = max(2, min(n_sig, 5))
        sig_pow = np.sum(S[:n_sig]**2)
        snr = sig_pow / (total - sig_pow + 1e-20)
        # Reconstruct
        Sc = S.copy(); Sc[n_sig:] *= 0.05
        Hc = U @ np.diag(Sc[:U.shape[1]]) @ Vt[:len(Sc)]
        clean = Hc[0]
        # Blend: high SNR → use clean. Low SNR → use raw.
        blend = np.clip((snr - 0.5) / 2.0, 0, 1)  # 0 at snr=0.5, 1 at snr=2.5
        raw_tail = ret[-len(clean):] if len(ret)>=len(clean) else ret
        if len(raw_tail) != len(clean):
            raw_tail = raw_tail[:len(clean)]
        blended = clean * blend + raw_tail * (1 - blend)
        return blended, float(snr), clean
    except: return ret, 0.5, ret

# == EXPONENTIAL CLUSTERING: adaptive lookback ==
def clustering_horizon(ret, max_lag=25):
    if len(ret) < max_lag*2: return 15, 0.0
    r = ret - np.mean(ret)
    ac = np.array([np.mean(r[:len(r)-lag]*r[lag:]) for lag in range(max_lag)])
    ac_norm = ac / (ac[0] + 1e-20)
    ac_abs = np.abs(ac_norm[1:])
    valid = ac_abs > 0.005
    if np.sum(valid) < 4: return 15, 0.0
    lags = np.arange(1, max_lag)[valid]
    try:
        coeffs = np.polyfit(lags, np.log(ac_abs[valid] + 1e-20), 1)
        kappa = max(-coeffs[0], 0.02)
        xi = min(1.0/kappa, 60)
        predicted = coeffs[0]*lags + coeffs[1]
        ss_res = np.sum((np.log(ac_abs[valid]+1e-20) - predicted)**2)
        ss_tot = np.sum((np.log(ac_abs[valid]+1e-20) - np.mean(np.log(ac_abs[valid]+1e-20)))**2) + 1e-20
        r2 = max(1.0 - ss_res/ss_tot, 0)
        return float(xi), float(r2)
    except: return 15, 0.0


# ══ ALL 11 v9 FRAMEWORKS ═════════════════════════════════════════════
def ns_trend_noise(ret, cutoff=12):
    if len(ret)<cutoff*3: return np.zeros_like(ret), ret, 0
    b,a=butter(3,min(1.0/cutoff/0.5,0.99),btype='low')
    tr=filtfilt(b,a,ret); zi=lfilter_zi(b,a)*ret[0]; tc,_=lfilter(b,a,ret,zi=zi)
    bp=max(0,len(ret)-cutoff); tr[bp:]=tc[bp:]
    ns=ret-tr; return tr, ns, np.var(tr)/(np.var(ns)+1e-20)

def ns_cascade(ret, w=30):
    if len(ret)<w: return True
    r=ret[-w:]; L2=np.var(r)
    rv=np.array([np.var(r[max(0,i-10):i]) for i in range(10,len(r))])
    return np.var(rv)<100*L2 if L2>1e-12 else True

def rh_D(ret, w=60):
    if len(ret)<w: return 0.0
    rec=ret[-w//2:]; old=ret[-w:-w//2]; nps=min(len(rec)//2,24)
    if nps<8: return 0.0
    _,p1=welch(rec,nperseg=nps,noverlap=nps//2); _,p0=welch(old,nperseg=nps,noverlap=nps//2)
    mn=min(len(p1),len(p0)); return float(np.sum(p1[:mn]/(p0[:mn]+1e-20)-1))

def rh_se(ret, nps=12):
    if len(ret)<nps*2: return 0.5
    _,p=welch(ret,nperseg=nps,noverlap=nps//2)
    p=p/(np.sum(p)+1e-20); p=p[p>1e-20]
    return float(np.clip(-np.sum(p*np.log2(p))/(np.log2(len(p))+1e-20),0,1))

def rh_conv(ret, nps=12):
    if len(ret)<nps*2: return 0.0
    f,psd=welch(ret,nperseg=nps,noverlap=nps//2)
    if len(psd)<2: return 0.0
    g=max(f[np.argmax(psd[1:])+1]*2*np.pi,0.01)
    return max(float(8*(3*g**2-0.25)/(0.25+g**2)**3),0.0)

def rh_prod(ret, nps=12):
    if len(ret)<nps*2: return 0.0
    _,psd=welch(ret,nperseg=nps,noverlap=nps//2)
    ref=np.mean(psd)+1e-20; return float(np.sum(np.log(np.clip(psd/ref,1e-10,None))))

def bsd_S2(ret, w=40):
    if len(ret)<20: return 0
    c,_=np.histogram(ret[-w:],bins=20); p=c/(np.sum(c)+1e-20); p=p[p>0]
    return float(np.sum(p**2))

def bsd_flow(ret):
    if len(ret)<40: return 0.0
    return bsd_S2(ret[-20:])-bsd_S2(ret[-40:-20])

def ym_gap(ret, nl=15):
    if len(ret)<40: return 0
    r=ret[-40:]; C=np.zeros((nl,nl))
    for i in range(nl):
        for j in range(nl):
            lag=abs(i-j); C[i,j]=np.mean(r[:len(r)-lag]*r[lag:]) if 0<lag<len(r) else np.var(r)
    try:
        ev=np.sort(np.abs(np.linalg.eigvalsh(C)))[::-1]; ev=ev[ev>1e-15]
        v=float((ev[0]-ev[1])/(np.sum(ev)+1e-20)) if len(ev)>=2 else 0
        return v if np.isfinite(v) else 0
    except: return 0

def hodge_coh(ret):
    dirs=[]
    for s in [5,10,20,40]:
        if len(ret)>=s: dirs.append(np.sign(np.sum(ret[-s:])))
    if len(dirs)<2: return 0.0
    return float(np.mean([1 if d==np.sign(np.sum(dirs)) else 0 for d in dirs]))

def mom_mt(ret, xi=15):
    if xi > 20: lookbacks = [5,10,20,40]; weights = [0.15,0.25,0.35,0.25]
    elif xi > 10: lookbacks = [3,5,10,20]; weights = [0.2,0.3,0.3,0.2]
    elif xi > 5: lookbacks = [2,3,5,10]; weights = [0.3,0.3,0.25,0.15]
    else: lookbacks = [1,2,3,5]; weights = [0.4,0.3,0.2,0.1]
    m=[]
    for lb in lookbacks:
        if len(ret)>=lb: m.append(np.sum(ret[-lb:])/(np.std(ret[-lb:])*np.sqrt(lb)+1e-20))
    return float(np.average(m,weights=weights[:len(m)])) if m else 0

# ══ META: Lyapunov Channels + Rosetta Stationarity ═══════════════════
def lyapunov_dom(r):
    if len(r)<30: return "MOM"
    r=r[-30:]
    ac1=np.mean(r[:-1]*r[1:])/(np.var(r)+1e-20)
    ac10=np.mean(r[:-10]*r[10:])/(np.var(r)+1e-20) if len(r)>10 else 0
    vol_r=np.std(r[-10:])/(np.std(r[:20])+1e-20)
    rates={"MOM":max(ac1,0),"REV":max(-ac10,0),"VOL":max(1-vol_r,0)}
    return max(rates, key=rates.get)

def rosetta_stab(ret, w=20):
    if len(ret)<w*3: return 1.0
    nps=min(w//2,12)
    if nps<4: return 1.0
    def se(r):
        _,p=welch(r,nperseg=nps,noverlap=nps//2)
        p=p/(np.sum(p)+1e-20); p=p[p>1e-20]
        return float(-np.sum(p*np.log2(p))/(np.log2(len(p))+1e-20))
    psi_dot=abs(se(ret[-w:])-se(ret[-w*2:-w]))+abs(np.log(np.var(ret[-w:])+1e-20)-np.log(np.var(ret[-w*2:-w])+1e-20))*0.3
    return float(np.clip(1.0-psi_dot*2, 0.3, 1.0))

# ══════════════════════════════════════════════════════════════════════
# MASTER PREDICTOR v16e — Adaptive SVD + Full 11 Frameworks
# ══════════════════════════════════════════════════════════════════════
class KernelPredictor:
    def __init__(self, ac="crypto"):
        self.ac=ac; self.prev=0

    def predict(self, prices):
        if len(prices)<80: return {"size":0,"score":50,"direction":"HOLD"}
        raw_ret=np.diff(np.log(prices))

        # ═══ ADAPTIVE SVD PRE-PROCESSING ═══
        blended, svd_snr, clean = adaptive_svd(raw_ret)

        # ═══ ALL 11 FRAMEWORKS — on BLENDED signal ═══
        # (high SVD SNR → mostly clean, low → mostly raw)
        tr, ns, snr = ns_trend_noise(blended, 12)
        cas = ns_cascade(raw_ret, 30)  # cascade on RAW (detects real vol breaks)
        D = rh_D(blended, 60)
        se = rh_se(blended[-40:], 12)
        conv = rh_conv(blended[-40:], 12)
        prod = rh_prod(raw_ret[-40:], 12)  # product health on RAW
        s2 = bsd_S2(blended)
        info = bsd_flow(blended)
        gap_s = ym_gap(blended)
        coh = hodge_coh(blended)
        
        # Momentum on SVD-blended signal
        xi, xi_r2 = clustering_horizon(raw_ret)
        mom = mom_mt(blended, xi)
        td = np.mean(tr[-5:])/(np.std(blended)+1e-20) if len(tr)>5 else 0
        nz = ns[-1]/(np.std(ns)+1e-20) if len(ns)>1 and np.std(ns)>1e-10 else 0
        vs = np.std(raw_ret[-10:])*np.sqrt(252) if len(raw_ret)>=10 else 0
        vl = np.std(raw_ret[-30:])*np.sqrt(252) if len(raw_ret)>=30 else vs
        vr = vs/(vl+1e-20)

        # RSI on blended
        if len(blended)>=14:
            r14=blended[-14:]; g=r14[r14>0]; l=-r14[r14<0]
            rsi=100-100/(1+(np.mean(g) if len(g)>0 else 1e-10)/(np.mean(l) if len(l)>0 else 1e-10))
        else: rsi=50

        # ═══ CIRCUIT BREAKERS ═══
        if not cas or abs(prod)>6:
            self.prev=0
            return {"size":0,"score":50,"direction":"HALT","dom":"HALT","svd":round(svd_snr,2)}

        # ═══ META ═══
        dom = lyapunov_dom(blended)
        stab = rosetta_stab(raw_ret)

        # ═══ v9 SCORING ENGINE (proven, on blended signal) ═══
        score = 50.0
        mom_boost = 1.3 if dom=="MOM" else 1.0
        if mom*td > 0:
            score += np.clip(mom*14*mom_boost, -30, 30)
            score += np.clip(td*8, -12, 12)
            if coh > 0.75: score = 50+(score-50)*1.25
        else:
            score += np.clip(mom*5, -8, 8)

        # ASYMMETRIC CRASH DETECTION
        crash_sig = 0
        if vr > 1.3: crash_sig += 1
        if coh < 0.25: crash_sig += 1
        if se > 0.9: crash_sig += 1
        if nz < -2.0: crash_sig += 1
        if crash_sig >= 3 and score < 50:
            score = 50 + (score-50)*1.8

        # RALLY CONFIRMATION (needs more evidence)
        rally_sig = 0
        if vr < 0.85: rally_sig += 1
        if coh > 0.75: rally_sig += 1
        if se < 0.7: rally_sig += 1
        if mom > 0.5 and td > 0: rally_sig += 1
        if rally_sig >= 3 and score > 55:
            score = 50 + (score-50)*1.3

        if snr > 0.3: score = 50+(score-50)*1.3
        elif snr < 0.05 and abs(nz)>2.0: score -= nz*4
        if abs(D)>3.0: score = 50+(score-50)*1.2
        elif abs(D)<0.5 and abs(mom)<0.3: score = 50+(score-50)*0.5
        if se < 0.45: score = 50+(score-50)*1.25
        elif se > 0.88: score = 50+(score-50)*0.55
        if s2 > 0.15: score = 50+(score-50)*1.1
        if abs(info)>0.02: score += 3*np.sign(info)*np.sign(mom)
        if gap_s > 0.25: score = 50+(score-50)*1.15
        elif gap_s < 0.05: score = 50+(score-50)*0.7
        if vr > 1.4: score = 50+(score-50)*0.5
        elif vr < 0.8: score = 50+(score-50)*1.15
        if rsi < 25: score += 6
        elif rsi > 75: score -= 6

        # Mean-reversion (boosted when REV channel dominates)
        rev_boost = 1.5 if dom=="REV" else 1.0
        if self.ac=="crypto" and abs(nz)>2.5 and snr<0.1: score -= nz*3*rev_boost
        if self.ac=="equity": score=max(score,35); score+=3

        # ═══ DYNAMIC CONFIDENCE: 11-framework vote ═══
        votes = 0; tv = 0
        if snr > 0.15: votes += 2
        elif snr > 0.05: votes += 1
        tv += 2
        if coh > 0.75: votes += 2
        elif coh > 0.5: votes += 1
        tv += 2
        if se < 0.5: votes += 2
        elif se < 0.75: votes += 1
        tv += 2
        if gap_s > 0.15: votes += 2
        elif gap_s > 0.05: votes += 1
        tv += 2
        if vr < 0.85: votes += 1
        elif vr > 1.3: votes -= 1
        tv += 1
        if svd_snr > 2.0: votes += 2
        elif svd_snr > 0.8: votes += 1
        tv += 2
        if 2 < abs(D) < 50: votes += 1
        tv += 1
        if mom * td > 0 and abs(mom) > 0.3: votes += 2
        elif mom * td > 0: votes += 1
        tv += 2
        if stab > 0.7: votes += 1
        tv += 1
        if s2 > 0.12: votes += 1
        tv += 1
        vote_frac = votes / (tv + 1e-10)
        conf = 0.15 + vote_frac * 0.85
        if xi_r2 > 0.4: conf *= 1.15
        elif xi_r2 > 0.25: conf *= 1.05
        elif xi_r2 < 0.1: conf *= 0.85
        conf = float(np.clip(conf, 0.15, 1.3))

        score = 50 + (score-50)*conf
        score = float(np.clip(score, 5, 95))

        # ═══ SIZING ═══
        conv_scale = np.clip(1+conv*50, 0.5, 2.0)
        raw = (score-50)/50
        hyst = 0.06 + (1.0-conf)*0.08
        if abs(raw)<hyst: size = self.prev*0.95
        else: size = raw * conv_scale
        if self.ac=="equity": size=max(size,0)
        size = float(np.clip(size,-1,1))
        if not np.isfinite(size): size=0
        self.prev = size
        d = "BUY" if size>0.10 else "SELL" if size<-0.06 else "HOLD"
        return {"score":round(score,1),"direction":d,"size":round(size,3),
                "conf":round(conf,3),"xi":round(xi,1),"xiR2":round(xi_r2,3),"votes":votes,"svd":round(svd_snr,2),"stab":round(stab,3),
                "dom":dom,"mom":round(mom,3),"snr":round(float(snr),3),
                "se":round(se,3),"D":round(D,2),"coh":round(coh,2),
                "gap_s":round(gap_s,3),"vr":round(float(vr),3),"rsi":round(rsi,1)}

# ══════════════════════════════════════════════════════════════════════
def backtest(prices, ac="crypto", warmup=80, fee=0.0008):
    pred=KernelPredictor(ac); ret=np.diff(np.log(prices))
    n=len(ret); eq=[1.0]; pos=0; w=l=t=0
    for i in range(warmup,n):
        sig=pred.predict(prices[:i+1]); tgt=sig["size"]
        chg=abs(tgt-pos)
        if chg>0.05: t+=1; f=chg*fee
        else: f=0
        eq.append(eq[-1]*(1+pos*ret[i]-f))
        if abs(pos)>0.01:
            if pos*ret[i]>0: w+=1
            else: l+=1
        pos=tgt
    eq=np.array(eq); dr=np.diff(np.log(np.maximum(eq,1e-10)))
    bh=prices[warmup:]/prices[warmup]
    tr=(eq[-1]/eq[0]-1)*100; br=(bh[-1]/bh[0]-1)*100 if len(bh)>0 else 0
    sh=np.mean(dr)/(np.std(dr)+1e-20)*np.sqrt(252) if len(dr)>1 else 0
    dd=np.max(np.maximum.accumulate(eq)-eq)/(np.max(eq)+1e-20)*100
    hr=w/(w+l)*100 if (w+l)>0 else 0
    return {"ret":tr,"bh":br,"alpha":tr-br,"sh":sh,"dd":dd,"hit":hr,"trades":t}

if __name__=="__main__":
    import yfinance as yf
    tickers={
        'BTC':'BTC-USD','ETH':'ETH-USD','SOL':'SOL-USD','XRP':'XRP-USD',
        'ADA':'ADA-USD','AVAX':'AVAX-USD','DOGE':'DOGE-USD','LINK':'LINK-USD',
        'SPY':'SPY','QQQ':'QQQ','NVDA':'NVDA','GOOGL':'GOOGL','GLD':'GLD',
        'AAPL':'AAPL','TSLA':'TSLA','COIN':'COIN','AMD':'AMD','META':'META'}
    print('='*100)
    print('  KERNEL v16e — ADAPTIVE SVD + FULL 11 FRAMEWORKS (OOS 2024-2026)')
    print('  KL gap REMOVED. SVD blend: high SNR→clean, low SNR→raw.')
    print('='*100)
    ca=[]; ea=[]
    for nm,tk in tickers.items():
        try:
            full=yf.Ticker(tk).history(start='2020-01-01',end='2026-03-11')
            p=full['Close'].values
            if len(p)<500: print(f'  {nm:6s} skip'); continue
            test=p[int(len(p)*0.70):]
            ac='equity' if '-' not in tk else 'crypto'
            r=backtest(test,ac,warmup=80)
            kp=KernelPredictor(ac); sig=kp.predict(test)
            a='+' if r['alpha']>0 else ''
            print(f'  {nm:6s} Ret={r["ret"]:>7.1f}% B&H={r["bh"]:>7.1f}% '
                  f'A={a}{r["alpha"]:.1f}% Sh={r["sh"]:>6.3f} '
                  f'DD={r["dd"]:.1f}% Hit={r["hit"]:.1f}% T={r["trades"]} '
                  f'dom={sig["dom"]} svd={sig["svd"]:.1f} '
                  f'conf={sig["conf"]:.2f} D={sig["D"]:+.1f} se={sig["se"]:.3f}')
            if ac=='crypto': ca.append(r['alpha'])
            else: ea.append(r['alpha'])
        except Exception as e: print(f'  {nm:6s} err: {e}')
    print('='*100)
    if ca: print(f'  CRYPTO: Alpha={np.mean(ca):+.1f}% W={sum(1 for a in ca if a>0)}/{len(ca)}')
    if ea: print(f'  EQUITY: Alpha={np.mean(ea):+.1f}% W={sum(1 for a in ea if a>0)}/{len(ea)}')
    all_a=ca+ea
    print(f'  TOTAL:  Alpha={np.mean(all_a):+.1f}% W={sum(1 for a in all_a if a>0)}/{len(all_a)}')
    print('='*100)
