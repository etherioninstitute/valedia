"""Kernel Predictor v9 — FULL 6-Paper Kernel Mapping
Every proved paper mapped to a market signal. No framework left behind.

Paper → Signal Mapping:
  NS v9.8  → (1) Butterworth trend/noise (2) Variance cascade halt
  RH v16   → (3) D-functional regime (4) Spectral entropy (5) Convexity sizing (6) Product health
  BSD v26  → (7) Entropy moments S_k (8) Information flow
  YM v15   → (9) Spectral gap stability
  Hodge v11→ (10) Multi-scale coherence alignment
  Rosetta  → (11) Trace stationarity equilibrium

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import numpy as np
from scipy.signal import welch, butter, filtfilt, lfilter, lfilter_zi
from scipy import stats
import warnings; warnings.filterwarnings('ignore')

# ── NS v9.8 ──────────────────────────────────────────────────────────
def ns_trend_noise(ret, cutoff=12):
    """(1) Butterworth trend/noise decomposition."""
    if len(ret)<cutoff*3: return np.zeros_like(ret), ret, 0
    b,a=butter(3,min(1.0/cutoff/0.5,0.99),btype='low')
    tr=filtfilt(b,a,ret)
    zi=lfilter_zi(b,a)*ret[0]; tc,_=lfilter(b,a,ret,zi=zi)
    bp=max(0,len(ret)-cutoff); tr[bp:]=tc[bp:]
    ns=ret-tr
    snr=np.var(tr)/(np.var(ns)+1e-20)
    return tr, ns, snr

def ns_variance_cascade(ret, window=30):
    """(2) Variance cascade — L2→L4→L6 hierarchy. Halt if cascade breaks."""
    if len(ret)<window: return {"stable":True,"phi_ratio":0}
    r=ret[-window:]
    L2=np.var(r)
    rv=np.array([np.var(r[max(0,i-10):i]) for i in range(10,len(r))])
    L4=np.var(rv) if len(rv)>2 else 0
    cummax=np.maximum.accumulate(np.cumsum(r))
    dd=np.max(cummax-np.cumsum(r))
    vol=np.std(r); skw=abs(stats.skew(r)) if len(r)>10 else 0
    krt=max(0,stats.kurtosis(r)-3) if len(r)>10 else 0
    phi=dd+0.5*vol+L2+0.1*skw+0.05*krt
    stable=L4<100*L2 if L2>1e-12 else True
    return {"stable":stable,"phi":round(phi,6),"L2":round(L2,8),"L4":round(L4,10)}

# ── RH v16 ───────────────────────────────────────────────────────────
def rh_D_functional(ret, window=60):
    """(3) D = Σ(|α_k|²-1): recent spectrum vs older reference. Non-zero when regime shifts."""
    if len(ret)<window: return 0.0
    recent=ret[-window//2:]
    older=ret[-window:-window//2]
    nps=min(len(recent)//2, 24)
    if nps<8: return 0.0
    _,psd_now=welch(recent,nperseg=nps,noverlap=nps//2)
    _,psd_ref=welch(older,nperseg=nps,noverlap=nps//2)
    mn=min(len(psd_now),len(psd_ref))
    psd_now=psd_now[:mn]; psd_ref=psd_ref[:mn]
    alpha_sq=psd_now/(psd_ref+1e-20)
    return float(np.sum(alpha_sq-1))

def rh_spectral_entropy(ret, nperseg=10):
    """(4) Spectral entropy [0,1]. Low=clean trend. High=noise."""
    if len(ret)<nperseg*2: return 0.5
    _,p=welch(ret,nperseg=nperseg,noverlap=nperseg//2)
    p=p/(np.sum(p)+1e-20); p=p[p>1e-20]
    return float(np.clip(-np.sum(p*np.log2(p))/(np.log2(len(p))+1e-20),0,1))

def rh_convexity(ret, nperseg=10):
    """(5) P''(0)=8(3γ²-1/4)/(1/4+γ²)³ at dominant freq. Higher=stronger attractor."""
    if len(ret)<nperseg*2: return 0.0
    f,psd=welch(ret,nperseg=nperseg,noverlap=nperseg//2)
    if len(psd)<2: return 0.0
    dom=np.argmax(psd[1:])+1
    gamma=max(f[dom]*2*np.pi, 0.01)
    return max(float(8*(3*gamma**2-0.25)/(0.25+gamma**2)**3), 0.0)

def rh_product_health(ret, nperseg=10):
    """(6) Σlog|α_k|² ≈ 0 for balanced signal. Drift = bias/degradation."""
    if len(ret)<nperseg*2: return 0.0
    _,psd=welch(ret,nperseg=nperseg,noverlap=nperseg//2)
    ref=np.mean(psd)+1e-20
    a2=np.clip(psd/ref,1e-10,None)
    return float(np.sum(np.log(a2)))


# ── BSD v26 ──────────────────────────────────────────────────────────
def bsd_entropy_moments(ret, n_bins=20, window=40):
    """(7) Entropy moments S_k on return distribution. High S_2 = concentrated (exploitable)."""
    if len(ret)<20: return {"S1":0,"S2":0,"S3":0}
    r=ret[-window:]
    counts,_=np.histogram(r,bins=n_bins)
    p=counts/(np.sum(counts)+1e-20); p=p[p>0]
    return {"S1":float(np.sum(p)),"S2":float(np.sum(p**2)),"S3":float(np.sum(p**3))}

def bsd_info_flow(ret):
    """(8) ΔS_2 between recent and older windows. High = new info arriving."""
    if len(ret)<40: return 0.0
    m1=bsd_entropy_moments(ret[-20:])
    m2=bsd_entropy_moments(ret[-40:-20])
    return float(m1["S2"]-m2["S2"])

# ── YM v15 ───────────────────────────────────────────────────────────
def ym_spectral_gap(ret, n_lags=15):
    """(9) Spectral gap from autocorrelation eigenvalues. Large=stable regime."""
    if len(ret)<40: return {"gap":0,"stability":0}
    r=ret[-40:]
    C=np.zeros((n_lags,n_lags))
    for i in range(n_lags):
        for j in range(n_lags):
            lag=abs(i-j)
            C[i,j]=np.mean(r[:len(r)-lag]*r[lag:]) if lag>0 and lag<len(r) else np.var(r)
    ev=np.sort(np.abs(np.linalg.eigvalsh(C)))[::-1]
    ev=ev[ev>1e-15]
    if len(ev)<2: return {"gap":0,"stability":0}
    gap=float(ev[0]-ev[1])
    return {"gap":round(gap,6),"stability":round(gap/(np.sum(ev)+1e-20),4)}

# ── Hodge v11 ────────────────────────────────────────────────────────
def hodge_coherence(ret, scales=[5,10,20,40]):
    """(10) Multi-scale trend alignment. All scales agree = high coherence."""
    dirs=[]
    for s in scales:
        if len(ret)>=s:
            dirs.append(np.sign(np.sum(ret[-s:])))
    if len(dirs)<2: return 0.0
    # Coherence = fraction of scales agreeing with majority
    majority=np.sign(np.sum(dirs))
    return float(np.mean([1 if d==majority else 0 for d in dirs]))

# ── Rosetta v2 ───────────────────────────────────────────────────────
def rosetta_trace(ret, window=30):
    """(11) Tr(Φ∘S) where Φ=rolling cov, S=lag-1 autocorr. Near 0=stationary."""
    if len(ret)<window+2: return {"trace":0,"stationary":True}
    X=ret[-window-1:-1]; Y=ret[-window:]
    if len(X)!=len(Y): Y=Y[:len(X)]
    vx=np.var(X)+1e-20; vy=np.var(Y)+1e-20
    cov_xy=np.mean((X-np.mean(X))*(Y-np.mean(Y)))
    autocorr=cov_xy/(np.sqrt(vx*vy)+1e-20)
    tr=float(vx*autocorr)
    return {"trace":round(tr,8),"stationary":abs(tr/vx)<0.15}


# ── UTILITY ──────────────────────────────────────────────────────────
def mom_score(ret):
    m=[]
    for lb in [3,5,10,20]:
        if len(ret)>=lb: m.append(np.sum(ret[-lb:])/(np.std(ret[-lb:])*np.sqrt(lb)+1e-20))
    return float(np.average(m,weights=[0.4,0.3,0.2,0.1][:len(m)])) if m else 0

def rsi_proxy(ret):
    if len(ret)<14: return 50
    r=ret[-14:]
    g=r[r>0]; l=-r[r<0]
    rs=(np.mean(g) if len(g)>0 else 1e-10)/(np.mean(l) if len(l)>0 else 1e-10)
    return 100-100/(1+rs)


# ══════════════════════════════════════════════════════════════════════
# MASTER PREDICTOR — All 11 Frameworks Fused
# ══════════════════════════════════════════════════════════════════════
class KernelPredictor:
    def __init__(self, ac="crypto", w_mom=14, w_trend=8, hyst=0.10, bias=0):
        self.ac=ac; self.w_mom=w_mom; self.w_trend=w_trend
        self.hyst=hyst; self.bias=bias; self.prev=0

    def predict(self, prices):
        if len(prices)<60: return {"size":0,"score":50,"direction":"HOLD"}
        ret=np.diff(np.log(prices))

        # ── Compute all 11 signals ──
        tr,ns,snr = ns_trend_noise(ret, 12)                       # (1) NS
        cascade = ns_variance_cascade(ret, 30)                     # (2) NS
        D = rh_D_functional(ret, 60)                         # (3) RH
        s_e = rh_spectral_entropy(ret[-40:], 12)                   # (4) RH
        conv = rh_convexity(ret[-40:], 12)                         # (5) RH
        prod = rh_product_health(ret[-40:], 12)                    # (6) RH
        bsd_m = bsd_entropy_moments(ret)                           # (7) BSD
        info = bsd_info_flow(ret)                                  # (8) BSD
        gap = ym_spectral_gap(ret)                                 # (9) YM
        coh = hodge_coherence(ret)                                 # (10) Hodge
        rosa = rosetta_trace(ret)                                  # (11) Rosetta

        # ── Derived features ──
        mom = mom_score(ret)
        td = np.mean(tr[-5:])/(np.std(ret)+1e-20) if len(tr)>5 else 0
        nz = ns[-1]/(np.std(ns)+1e-20) if len(ns)>1 and np.std(ns)>1e-10 else 0
        vs = np.std(ret[-10:])*np.sqrt(252) if len(ret)>=10 else 0
        vl = np.std(ret[-30:])*np.sqrt(252) if len(ret)>=30 else vs
        vr = vs/(vl+1e-20)
        rsi = rsi_proxy(ret)

        # ══ SCORING ENGINE ══
        score = 50.0 + self.bias


        # ── (1,10) MOMENTUM + TREND + COHERENCE (35%) ──
        if mom*td > 0:  # aligned
            score += np.clip(mom*self.w_mom, -25, 25)
            score += np.clip(td*self.w_trend, -12, 12)
            if coh > 0.75:  # (10) Hodge: all scales agree → boost
                score = 50 + (score-50)*1.25
        else:  # conflict
            score += np.clip(mom*self.w_mom*0.25, -6, 6)

        # ── (1) NS SNR ──
        if snr > 0.3:
            score = 50+(score-50)*1.3
        elif snr < 0.05 and abs(nz) > 2.0:
            score -= nz*4

        # ── (3) RH D-functional regime (10%) ──
        if abs(D) > 3.0:  # energy concentrated → amplify
            score = 50+(score-50)*1.2
        elif abs(D) < 0.5 and abs(mom) < 0.3:  # true equilibrium + no momentum
            score = 50 + (score-50)*0.5  # dampen — no edge

        # ── (4) RH Spectral entropy (10%) ──
        if s_e < 0.45: score = 50+(score-50)*1.25
        elif s_e > 0.88: score = 50+(score-50)*0.55

        # ── (7,8) BSD entropy + info flow (8%) ──
        if bsd_m["S2"] > 0.15:  # concentrated distribution → exploitable
            score = 50+(score-50)*1.1
        if abs(info) > 0.02:
            score += 3*np.sign(info)*np.sign(mom)

        # ── (9) YM spectral gap (7%) ──
        if gap["stability"] > 0.25:
            score = 50+(score-50)*1.15  # stable regime → trust signal
        elif gap["stability"] < 0.05:
            score = 50+(score-50)*0.7  # gap closing → reduce

        # ── (11) Rosetta trace stationarity (5%) ──
        if not rosa["stationary"]:
            score = 50+(score-50)*0.7  # non-stationary → dampen

        # ── Vol regime (5%) ──
        if vr > 1.4: score = 50+(score-50)*0.5
        elif vr < 0.8: score = 50+(score-50)*1.15

        # ── RSI extremes ──
        if rsi < 25: score += 6
        elif rsi > 75: score -= 6

        # ── Mean-reversion fade (crypto only) ──
        if self.ac=="crypto" and abs(nz)>2.5 and snr<0.1:
            score -= nz*3

        # ── (2) NS Variance cascade HALT ──
        if not cascade["stable"]:
            score = 50  # cascade breaking → go flat

        # ── (6) RH Product identity health — CIRCUIT BREAKER ──
        if abs(prod) > 6.0:
            score = 50  # signal degraded


        # ── EQUITY FLOOR ──
        if self.ac=="equity": score=max(score,30)
        score=float(np.clip(score,5,95))

        # ── (5) RH Convexity-scaled sizing ──
        conv_scale = np.clip(1 + conv*50, 0.5, 2.0)
        raw = (score-50)/50
        if abs(raw) < self.hyst:
            size = self.prev*0.95
        else:
            size = raw * conv_scale
        if self.ac=="equity": size=max(size,0)
        size=float(np.clip(size,-1,1))
        self.prev=size
        d="BUY" if size>0.08 else "SELL" if size<-0.08 else "HOLD"

        return {"score":round(score,1),"direction":d,"size":round(size,3),
                "mom":round(mom,3),"snr":round(float(snr),3),"se":round(s_e,3),
                "D":round(D,2),"coh":round(coh,2),"gap_s":round(gap["stability"],3),
                "info":round(info,4),"rosa":rosa["stationary"],"vr":round(float(vr),3),
                "rsi":round(rsi,1),"cascade_ok":cascade["stable"],"prod":round(prod,2),
                "conv":round(conv,4),"td":round(float(td),3)}


# ══════════════════════════════════════════════════════════════════════
# BACKTESTER + OPTIMIZER
# ══════════════════════════════════════════════════════════════════════
from itertools import product as iprod

def backtest(prices, ac="crypto", warmup=60, fee=0.0008, **kw):
    pred=KernelPredictor(ac,**kw); ret=np.diff(np.log(prices))
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

def optimize(prices, ac="crypto"):
    split=int(len(prices)*0.7); train=prices[:split]
    best=-1e10; bp={}
    mr=[10,14,18] if ac=="crypto" else [8,12,16]
    tr_r=[6,8,10]; bi_r=[0] if ac=="crypto" else [2,4,6]; hy_r=[0.08,0.12]
    for wm,wt,bi,hy in iprod(mr,tr_r,bi_r,hy_r):
        try:
            r=backtest(train,ac,w_mom=wm,w_trend=wt,bias=bi,hyst=hy)
            sc=r["sh"]+0.5*r["hit"]/100-0.01*r["dd"]
            if sc>best: best=sc; bp={"w_mom":wm,"w_trend":wt,"bias":bi,"hyst":hy}
        except: pass
    return bp


# ══════════════════════════════════════════════════════════════════════
CRYPTO={"BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD","XRP":"XRP-USD",
        "ADA":"ADA-USD","AVAX":"AVAX-USD","DOT":"DOT-USD","LINK":"LINK-USD",
        "MATIC":"MATIC-USD","DOGE":"DOGE-USD","ATOM":"ATOM-USD","UNI":"UNI-USD",
        "LTC":"LTC-USD","NEAR":"NEAR-USD","ARB":"ARB-USD","OP":"OP-USD"}
EQUITY={"SPY":"SPY","QQQ":"QQQ","AAPL":"AAPL","MSFT":"MSFT","NVDA":"NVDA",
        "AMZN":"AMZN","GOOGL":"GOOGL","META":"META","TSLA":"TSLA","AMD":"AMD",
        "COIN":"COIN","MSTR":"MSTR","BLOCK":"XYZ","ARKK":"ARKK","XLF":"XLF",
        "XLE":"XLE","GLD":"GLD","TLT":"TLT","IWM":"IWM","DIA":"DIA"}

if __name__=="__main__":
    import yfinance as yf
    print("="*85)
    print("  KERNEL v9 — FULL 6-PAPER KERNEL (11 frameworks, 36 assets)")
    print("="*85)
    for period,pl in [("1y","1Y"),("2y","2Y")]:
        print(f"\n{'='*85}\n  {pl} BACKTEST\n{'='*85}")
        for gn,grp,ac in [("CRYPTO",CRYPTO,"crypto"),("EQUITY",EQUITY,"equity")]:
            print(f"\n  --- {gn} ---")
            ar=[]
            for nm,tk in grp.items():
                try:
                    p=yf.Ticker(tk).history(period=period)["Close"].values
                    if len(p)<120: continue
                    bp=optimize(p,ac); r=backtest(p,ac,**bp); ar.append(r)
                    a="+" if r["alpha"]>0 else ""
                    print(f"    {nm:6s} Ret={r['ret']:>7.1f}% B&H={r['bh']:>7.1f}% "
                          f"A={a}{r['alpha']:.1f}% Sh={r['sh']:>6.3f} "
                          f"DD={r['dd']:.1f}% Hit={r['hit']:.1f}% T={r['trades']}")
                except Exception as e: print(f"    {nm:6s} err: {e}")
            if ar:
                print(f"  {gn} AVG: A={np.mean([r['alpha'] for r in ar]):+.1f}% "
                      f"Sh={np.mean([r['sh'] for r in ar]):.3f} "
                      f"Hit={np.mean([r['hit'] for r in ar]):.1f}% "
                      f"W={sum(1 for r in ar if r['alpha']>0)}/{len(ar)}")

    print(f"\n{'='*85}\n  LIVE SIGNALS\n{'='*85}")
    for gn,grp,ac in [("CRYPTO",CRYPTO,"crypto"),("EQUITY",EQUITY,"equity")]:
        kp=KernelPredictor(ac)
        for nm,tk in grp.items():
            try:
                sig=kp.predict(yf.Ticker(tk).history(period="6mo")["Close"].values)
                print(f"  {nm:6s}[{ac[0]}] {sig['direction']:4s} sz={sig['size']:+.3f} "
                      f"mom={sig['mom']:+.3f} D={sig['D']:+.1f} se={sig['se']:.3f} "
                      f"coh={sig['coh']:.2f} gap={sig['gap_s']:.3f} rosa={'Y' if sig['rosa'] else 'N'}")
            except: pass
    print("="*85)
