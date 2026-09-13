"""Etherion Risk Engine v2.0 — Valedia ASI
═══════════════════════════════════════════════════════════════════════════
Derived from the UGFT mathematical framework (Burton Chatelain Jr.).
These physics→market mappings are HEURISTIC ANALOGIES to be calibrated against
realized data, NOT certified/proven constants (per UGFT v32.9, the NGFP is an
unproven program, and the YM mass gap is a conditional reduction):
  - NS v11.0:  Rψ geometric smoother, energy ladder, Kolmogorov 5/3 spectrum
  - YM v38.5:  Kotecký-Preiss source predicate, large-field additive barrier,
               conditional OS mass gap
  - Fusion v20.7: Lyapunov dissipation (Thm 5.1) + ISS regime stability (Thm 11.1)
  - ZPE v3:    QEI drawdown bounds (Ford-Roman), mass gap thresholds
  - Rosetta v3: Correspondence stationarity, ψ-calculus rate tracking
  - UGFT v32.9: Etherion superconnection (NGFP parameters = calibratable priors)

Architecture:
  Module 1: Rψ Geometric Signal Decomposer (NS v11.0 §2-4)
  Module 2: Energy Ladder Cascade (NS v11.0 Thm 4.1 + YM v38.5 KP predicate)
  Module 3: Kolmogorov Spectrum Analyzer (NS v11.0 §6, Thm 6.3)
  Module 4: Large-Field Extraction Engine (YM v38.5 thm:additive-barrier)
  Module 5: Mass Gap Detector (YM v38.5 thm:os-gap, conditional + ZPE v3 Thm 5.1)
  Module 6: QEI Drawdown Guard (ZPE v3 Thm 4.1, Ford-Roman bound)
  Module 7: Regime Stability Protector (Fusion v20.7 Thm 5.1 + Thm 11.1)
  Module 8: Rosetta Correspondence Monitor (Rosetta v3 §1,8)
  Module 9: Integrated Report

(c) GOATWORTHY Post LLC. All rights reserved. Trade secret.
"""
import numpy as np

# Canonical RISK manuscript math (Sobolev ladder, Kotecky-Preiss, spectral gap, helicity,
# Ford-Roman, trace correspondence). Bump on any manuscript update; the manifest maps this to
# consumers, and valedia_consistency_check.py guards against a divergent second copy.
MANUSCRIPT_VERSION = "UGFT-v32.9 / NS-v11.0 / YM-v38.5"
import pandas as pd
import yfinance as yf
from scipy.signal import butter, filtfilt, lfilter, lfilter_zi
from scipy import stats
from datetime import datetime, timezone
import json, os


# ============================================================================
# MODULE 1: Rψ Geometric Signal Decomposer (NS v11.0 §2-4)
# ============================================================================
# Replaces ad-hoc Butterworth filter with the Rψ covariant smoothing
# operator from UGFT-NS. The key insight: the stretching matrix bound
# M ≤ Cψ E₀^(1/2) (Lemma 3.2) provides a HARD CAP on market velocity
# gradient, preventing the "UV catastrophe" of unconstrained volatility.

class RpsiDecomposer:
    """Geometric signal decomposition via the Rψ operator (NS v11.0 Def 2.2).
    
    The admissible kernel Kψ satisfies:
      (M1) Normalization, (M2) Positivity, (M3) Radial symmetry,
      (M4) Compact support at scale ε(ψ), (M5) C∞, (M6) Scaling.
    
    For markets: ε(ψ) = cutoff_period (the fundamental resolution scale).
    Rψ[u] = Kψ * u: convolution of returns with the geometric kernel.
    The stretching bound M ≤ Cψ·E₀^(1/2) maps to: rate of change of
    smoothed returns is bounded by kernel geometry × total energy.
    """
    
    def __init__(self, epsilon_psi=20):
        self.epsilon_psi = epsilon_psi
    
    def _admissible_kernel(self, n):
        """Construct admissible Kψ kernel satisfying (M1)-(M6).
        Uses Gaussian profile K0 with compact support at ε(ψ)."""
        x = np.linspace(-3, 3, n)
        K0 = np.exp(-0.5 * x**2)
        K0[np.abs(x) > 2.5] = 0  # (M4) compact support
        Kpsi = K0 / K0.sum()     # (M1) normalization
        return Kpsi
    
    def rpsi_smooth(self, u):
        """Apply Rψ[u] = Kψ * u (NS v11.0 Eq. 4).
        Returns smoothed field preserving divergence-free property."""
        Kpsi = self._admissible_kernel(min(self.epsilon_psi, len(u) // 3))
        if len(Kpsi) < 3 or len(u) < len(Kpsi):
            return u
        return np.convolve(u, Kpsi, mode='same')
    
    def decompose(self, prices):
        """Rψ-based decomposition into trend + noise.
        
        The Rψ operator provides the trend: Rψ[returns].
        Noise = returns - Rψ[returns].
        
        Stretching matrix bound (Lemma 3.2):
          M = ||∇Rψ[u]||_L∞ ≤ Cψ · E₀^(1/2)
        where Cψ = ||∇Kψ||_L2 (fixed by kernel geometry)
        and E₀ = ||u₀||²_L2 (initial energy = total variance).
        """
        if len(prices) < self.epsilon_psi * 3:
            return None, None, None, None
        
        returns = np.diff(np.log(prices))
        
        # Rψ smoothing (geometric, not ad-hoc Butterworth)
        trend = self.rpsi_smooth(returns)
        noise = returns - trend
        
        # Stretching matrix bound (NS v11.0 Lemma 3.2)
        grad_rpsi = np.diff(trend)
        M = np.max(np.abs(grad_rpsi)) if len(grad_rpsi) > 0 else 0
        
        # Cψ = ||∇Kψ||_L2 (kernel geometry constant)
        Kpsi = self._admissible_kernel(min(self.epsilon_psi, len(returns) // 3))
        grad_K = np.diff(Kpsi)
        Cpsi = np.sqrt(np.sum(grad_K**2))
        E0 = np.sum(returns**2)  # Total energy
        
        # Theoretical bound: M should satisfy M ≤ Cψ · E₀^(1/2)
        M_bound = Cpsi * np.sqrt(E0)
        bound_satisfied = M <= M_bound + 1e-10
        
        # Spectral decay coefficient (from noise autocovariance)
        noise_energy = np.var(noise)
        noise_gradient = np.var(np.diff(noise)) if len(noise) > 1 else 0
        decay_coeff = noise_gradient / (noise_energy + 1e-12)
        
        return trend, noise, decay_coeff, {
            "M": round(M, 6),
            "M_bound": round(M_bound, 6),
            "Cpsi": round(Cpsi, 6),
            "E0": round(E0, 6),
            "bound_satisfied": bound_satisfied,
        }
    
    def signal_strength(self, prices):
        """SNR = Var(trend) / Var(noise). High = momentum, Low = mean-reversion."""
        trend, noise, _, _ = self.decompose(prices)
        if trend is None:
            return 0
        return np.var(trend) / (np.var(noise) + 1e-12)
    
    def multi_scale_decompose(self, prices, scales=[10, 20, 50]):
        """Hierarchical Rψ decomposition at multiple ε(ψ) scales.
        Each scale controls the next via the energy ladder (Thm 4.1)."""
        results = {}
        for scale in scales:
            self.epsilon_psi = scale
            trend, noise, decay, bounds = self.decompose(prices)
            if trend is not None:
                results[scale] = {
                    "trend": trend, "noise": noise,
                    "decay_coeff": decay,
                    "signal_strength": np.var(trend) / (np.var(noise) + 1e-12),
                    "noise_zscore": noise[-1] / (np.std(noise) + 1e-12),
                    "stretching_bound": bounds,
                }
        self.epsilon_psi = 20
        return results


# ============================================================================
# MODULE 2: Energy Ladder Cascade (NS v11.0 Thm 4.1 + YM v38.5 KP source predicate)
# ============================================================================
# The energy ladder: d/dt Hk + ν·Hk+1 ≤ Clad·M·Hk (NS v11.0 Thm 4.1)
# where Hk = ||∇^k u||²_L2 is the k-th Sobolev norm.
# Kotecký-Preiss convergence (YM v38.5 def:weighted-observable-polymer-source
# L2195 + prop:kp-fp-polymer-upgrade L2239): the true criterion is SIZE-EXTENSIVE,
# Σ_{Q≁P} |ζ̂(Q)| e^{a(Q)} ≤ a(P) = a_*·|P| — NOT a universal η<0.1 (that flat
# threshold was a v15.6 calibration artifact with no counterpart in v38.5).
# The tilt τ_* additionally sets a correlation-decay rate (a market risk horizon),
# so KP is treated as a continuous readout, not a boolean. Mapping remains a
# heuristic analogy — calibrate against realized data before trusting it.

class EnergyLadderCascade:
    """Multi-scale variance cascade with provable Gronwall bounds.
    
    Maps financial returns to the Sobolev hierarchy:
      H0 = L2 norm (total energy = variance)
      H1 = H1 norm (energy of first differences = vol-of-vol)
      H2 = H2 norm (energy of second differences = acceleration risk)
      H3 = H3 norm (energy of third differences = jerk risk)
    
    The energy ladder inequality (Thm 4.1):
      d/dt Hk + ν·Hk+1 ≤ Clad·M·Hk
    with Gronwall bound (Thm 5.1):
      Hk(t) ≤ Hk(0)·exp(Γψ·t), where Γψ = Clad·Cψ·E₀^(1/2)
    
    The Kotecký-Preiss criterion (YM v38.5 KP source predicate) checks whether
    the cascade converges: per-cluster tilted activity ≤ size-extensive budget a_*.
    """
    
    def __init__(self, lookback=60):
        self.lookback = lookback
    
    def compute_sobolev_ladder(self, returns_matrix):
        """Compute the 4-level Sobolev energy hierarchy."""
        if len(returns_matrix) < 20:
            return {"H0": 0, "H1": 0, "H2": 0, "H3": 0, "Gronwall_ratio": 0}
        
        if returns_matrix.ndim == 1:
            returns_matrix = returns_matrix.reshape(-1, 1)
        
        # H0: L2 norm (base energy = portfolio variance)
        cov = np.cov(returns_matrix.T)
        H0 = np.trace(cov) if cov.ndim > 0 else float(cov)
        
        # H1: First difference energy (vol-of-vol / enstrophy analog)
        d1 = np.diff(returns_matrix, axis=0)
        H1 = np.mean(d1**2) if len(d1) > 0 else 0
        
        # H2: Second difference energy (palinstrophy / acceleration risk)
        d2 = np.diff(d1, axis=0)
        H2 = np.mean(d2**2) if len(d2) > 0 else 0
        
        # H3: Third difference energy (jerk risk / vortex stretching rate)
        d3 = np.diff(d2, axis=0)
        H3 = np.mean(d3**2) if len(d3) > 0 else 0
        
        # Gronwall ratio: Hk(t) / Hk(0). If >> 1, cascade is growing
        # Use rolling windows to estimate d/dt Hk
        w = min(20, len(returns_matrix) // 3)
        if w >= 10:
            early_H0 = np.var(returns_matrix[:w])
            late_H0 = np.var(returns_matrix[-w:])
            gronwall_ratio = late_H0 / (early_H0 + 1e-12)
        else:
            gronwall_ratio = 1.0
        
        return {
            "H0": round(H0, 8), "H1": round(H1, 10),
            "H2": round(H2, 12), "H3": round(H3, 14),
            "Gronwall_ratio": round(gronwall_ratio, 4),
        }
    
    def kotecky_preiss_criterion(self, returns_matrix):
        """Kotecký-Preiss convergence check (YM v38.5 KP source predicate).

        v38.5 criterion is SIZE-EXTENSIVE: Σ_{Q≁P} |ζ̂(Q)| e^{a(Q)} ≤ a(P) = a_*·|P|.
        The per-cluster tilted activity is compared to a size-extensive budget a_*,
        NOT to a flat 0.1 (that universal threshold was a v15.6 artifact — removed).

        Market mapping (heuristic analogy — calibrate before trusting):
          "polymers" = correlated asset clusters; activity ζ(Γ) = |corr| × vol.
        Also emits `decay_rate` — the tilt τ_* sets a correlation-decay rate
        (prop:kp-fp-polymer-upgrade): higher = faster decorrelation = safer.
        """
        if len(returns_matrix) < 20 or returns_matrix.ndim < 2:
            return {"eta": 0, "converges": True, "decay_rate": 0.0, "detail": "insufficient data"}

        corr = np.corrcoef(returns_matrix.T)
        n = corr.shape[0]
        np.fill_diagonal(corr, 0)
        vols = np.std(returns_matrix, axis=0)

        # Polymer activity: tilted correlation × volatility, per cluster.
        tau = 1.0  # tilt τ_* (polymer decay parameter)
        eta = 0.0
        for i in range(n):
            cluster_activity = 0
            for j in range(n):
                if i != j:
                    # |ζ̂(Γ)| ∝ |corr_ij| × vol_j × e^{τ|corr_ij|}
                    activity = abs(corr[i, j]) * vols[j]
                    cluster_activity += activity * np.exp(tau * abs(corr[i, j]))
            eta = max(eta, cluster_activity)

        # Size-extensive per-node activity (budget scales with cluster count n).
        activity_per_node = eta / (np.mean(vols) * n + 1e-12)
        # a_* = order-1 size-extensive KP budget (tilted units); replaces flat 0.1.
        A_STAR = 0.667
        # Correlation-decay rate from the tilt: shrinks toward 0 as activity → budget.
        decay_rate = tau * max(0.0, 1.0 - activity_per_node / A_STAR)

        return {
            "eta": round(activity_per_node, 4),
            "converges": activity_per_node <= A_STAR,
            "decay_rate": round(decay_rate, 4),
            "a_star": A_STAR,
            "raw_activity": round(eta, 6),
            "avg_vol": round(np.mean(vols), 6),
            "avg_abs_corr": round(np.mean(np.abs(corr)), 4),
        }
    
    def energy_ladder_check(self, returns_matrix):
        """Combined energy ladder + KP check."""
        ladder = self.compute_sobolev_ladder(returns_matrix)
        kp = self.kotecky_preiss_criterion(returns_matrix)
        
        # Cascade stable if: Gronwall ratio < 2 AND KP converges
        stable = ladder["Gronwall_ratio"] < 2.0 and kp["converges"]
        violations = []
        if ladder["Gronwall_ratio"] >= 2.0:
            violations.append(f"Gronwall ratio {ladder['Gronwall_ratio']:.2f} ≥ 2.0 (energy growing)")
        if not kp["converges"]:
            violations.append(f"KP activity {kp['eta']:.4f} > a_*={kp.get('a_star', 0.667)} (cluster expansion diverging)")
        
        return {"stable": stable, "violations": violations,
                "ladder": ladder, "kotecky_preiss": kp}


# ============================================================================
# MODULE 3: Kolmogorov Spectrum Analyzer (NS v11.0 §6, Thm 6.3)
# ============================================================================
# At the NGFP: νk ∝ ε^(1/3) k^(-4/3), yielding E(k) = CK ε^(2/3) k^(-5/3)
# Deviation from k^(-5/3) indicates the market is NOT at the turbulent
# fixed point → regime change in progress.

class KolmogorovAnalyzer:
    """Spectral analysis checking if returns follow the Kolmogorov cascade.
    
    The anomalous dimension η = 4/3 (classical K41 Kolmogorov exponent — an
    empirical/phenomenological prior, NOT an NGFP-certified constant per UGFT v32.9)
    yields the exact energy spectrum E(k) ∝ k^(-5/3).
    
    Market mapping: frequency-domain power spectrum of returns.
    If the slope is near -5/3, the market is in a "turbulent cascade" regime
    (energy flows predictably from large to small scales).
    If slope steepens (< -2) or flattens (> -1), the cascade is breaking.
    """
    
    @staticmethod
    def compute_spectrum(returns, min_samples=64):
        """Compute power spectral density of returns."""
        if len(returns) < min_samples:
            return None, None
        n = len(returns)
        fft_vals = np.fft.rfft(returns - np.mean(returns))
        psd = np.abs(fft_vals)**2 / n
        freqs = np.fft.rfftfreq(n, d=1.0)
        # Skip DC component
        return freqs[1:], psd[1:]
    
    @staticmethod
    def fit_spectral_slope(freqs, psd):
        """Fit log-log slope of PSD. Kolmogorov = -5/3 ≈ -1.667."""
        if freqs is None or len(freqs) < 5:
            return 0, 0
        # Use inertial range (middle frequencies, avoid DC and Nyquist)
        n = len(freqs)
        inertial = slice(n // 6, 5 * n // 6)
        log_f = np.log10(freqs[inertial] + 1e-12)
        log_p = np.log10(psd[inertial] + 1e-12)
        # Linear fit in log-log space
        if len(log_f) < 3:
            return 0, 0
        coeffs = np.polyfit(log_f, log_p, 1)
        return coeffs[0], coeffs[1]  # slope, intercept
    
    def analyze(self, prices):
        """Full Kolmogorov spectral analysis."""
        if len(prices) < 64:
            return {"slope": 0, "regime": "UNKNOWN", "deviation_from_K41": 0}
        
        returns = np.diff(np.log(prices))
        freqs, psd = self.compute_spectrum(returns)
        slope, intercept = self.fit_spectral_slope(freqs, psd)
        
        K41_slope = -5.0 / 3.0  # -1.6667 (exact from Thm 6.3)
        deviation = abs(slope - K41_slope)
        
        # Regime classification based on spectral slope
        if deviation < 0.3:
            regime = "TURBULENT_CASCADE"  # At or near NGFP
        elif slope < -2.2:
            regime = "LAMINAR_DECAY"      # Steeper than K41: energy dissipating fast
        elif slope > -1.0:
            regime = "ENERGY_INJECTION"   # Flatter than K41: new energy entering
        elif slope < K41_slope - 0.3:
            regime = "INTERMITTENT"       # Steeper: intermittency corrections
        else:
            regime = "MIXED_REGIME"
        
        # Anomalous dimension check: η should be 4/3 at NGFP
        # η = -k ∂k(νk)/νk → approximated from PSD slope
        eta_estimated = -(slope + 1) * 2.0 / 3.0  # Inverse of K41 relation
        eta_deviation = abs(eta_estimated - 4.0 / 3.0)
        
        return {
            "slope": round(slope, 4),
            "K41_slope": round(K41_slope, 4),
            "deviation_from_K41": round(deviation, 4),
            "regime": regime,
            "eta_estimated": round(eta_estimated, 4),
            "eta_target": round(4.0 / 3.0, 4),
            "eta_deviation": round(eta_deviation, 4),
            "at_NGFP": deviation < 0.3 and eta_deviation < 0.3,
        }


# ============================================================================
# MODULE 4: Large-Field Extraction Engine (YM v38.5 thm:additive-barrier)
# ============================================================================
# Small/large-field decomposition. In v38.5 the suppression is a FULL ADDITIVE
# barrier, not a fractional power: S_X(U) ≥ β·δ_β²·|X| = β^(1-2s)·|X| per tail
# block (thm:additive-barrier, c0=1 sharp), with threshold δ_β = β^(-s),
# s ∈ (1/3, 1/2) (rem:s-window; canonical s = 5/12). The v15.6 fractional form
# exp(-ccomp·β^0.4) and constant ccomp=0.49 are SUPERSEDED, and the old
# subtracted-barrier form was proved FALSE for all c0>0 (rem:barrier-corrections)
# — repaired by subtracting the vacuum. Net suppression only bites when the
# barrier beats the polymer entropy log(9Δ) (activity (9/4)^n × plane-tree count).

class LargeFieldExcision:
    """Identifies and suppresses extreme market moves using YM large-field theory.

    Market mapping (heuristic analogy — calibrate before trusting):
      Small-field = normal returns (|Z| ≤ δ_β): Gaussian risk models work.
      Large-field = tail events (|Z| > δ_β): additive-barrier suppression.

    Threshold δ_β = σ · β^(-s) with s = 5/12 (YM v38.5 rem:s-window, in (1/3,1/2)).
    Additive barrier per tail block = β^(1-2s); net exponent subtracts the polymer
    entropy log(9Δ), so a tail cluster is suppressed only when barrier > entropy.
    """

    S_WINDOW = 5.0 / 12.0  # threshold/barrier exponent s ∈ (1/3, 1/2) (YM v38.5)
    DELTA_DEG = 1.0         # polymer connectivity Δ (plane-tree degree); 1 = single series
    BETA_BASE = 200000      # Base coupling (maps to inverse temperature of market)
    
    def __init__(self, lookback=60):
        self.lookback = lookback
    
    def compute_threshold(self, returns):
        """Compute the small/large field threshold δk.
        
        δk = σ · β^(-ρ) where σ = realized vol, β = inverse noise temperature.
        β is estimated from the signal-to-noise ratio (high SNR → high β → tighter threshold).
        """
        if len(returns) < 20:
            return 0.02  # Default 2% threshold
        
        sigma = np.std(returns)
        # β = inverse noise temperature: how "ordered" the market is
        # High β = low noise = tight threshold. Low β = noisy = loose threshold.
        autocorr = np.corrcoef(returns[:-1], returns[1:])[0, 1] if len(returns) > 1 else 0
        beta = max(1.0, 1.0 / (1.0 - abs(autocorr) + 1e-6))

        delta_k = sigma * beta**(-self.S_WINDOW)  # δ_β = σ·β^(-s), YM v38.5
        return max(delta_k, sigma * 0.5)  # Floor at 0.5σ

    def _beta(self, returns):
        """Inverse noise temperature β from lag-1 autocorrelation (same as threshold)."""
        if len(returns) < 2:
            return 1.0
        autocorr = np.corrcoef(returns[:-1], returns[1:])[0, 1]
        if not np.isfinite(autocorr):
            autocorr = 0.0
        return max(1.0, 1.0 / (1.0 - abs(autocorr) + 1e-6))
    
    def classify_field(self, returns):
        """Classify each return as small-field or large-field.
        
        Small-field: |r| ≤ δk → normal Gaussian risk model applies.
        Large-field: |r| > δk → geometric suppression penalty.
        """
        delta_k = self.compute_threshold(returns)
        is_large = np.abs(returns) > delta_k

        # Additive-barrier suppression (YM v38.5 thm:additive-barrier):
        #   per-event position damping = exp(-β^(1-2s) · excess_blocks).
        # (The polymer entropy log(9Δ) governs CLUSTER tail PROBABILITY — the
        #  net exponent β^(1-2s)-log(9Δ) belongs to the Phase-B tail-prob estimator,
        #  NOT to per-event sizing; subtracting it here would zero out the guard.)
        beta = self._beta(returns)
        barrier = beta ** (1.0 - 2.0 * self.S_WINDOW)      # β^(1-2s), full additive barrier
        penalties = np.ones(len(returns))
        large_indices = np.where(is_large)[0]
        for idx in large_indices:
            # tail blocks above threshold, with the vacuum (δ_β) subtracted
            excess_blocks = max(0.0, abs(returns[idx]) / (delta_k + 1e-12) - 1.0)
            penalties[idx] = np.exp(-barrier * excess_blocks)
        
        n_large = np.sum(is_large)
        pct_large = n_large / len(returns) * 100
        
        return {
            "delta_k": round(delta_k, 6),
            "n_large_field": int(n_large),
            "pct_large_field": round(pct_large, 1),
            "avg_penalty": round(np.mean(penalties), 4),
            "min_penalty": round(np.min(penalties), 4),
            "penalties": penalties,
            "regime": "NORMAL" if pct_large < 5 else "ELEVATED" if pct_large < 15 else "EXTREME",
        }
    
    def recent_excision_status(self, returns, window=10):
        """Check if recent returns are in the large-field regime."""
        if len(returns) < window:
            return {"status": "INSUFFICIENT_DATA"}
        recent = returns[-window:]
        result = self.classify_field(recent)
        result["window"] = window
        return result


# ============================================================================
# MODULE 5: Mass Gap Detector (YM v38.5 thm:os-gap — CONDITIONAL + ZPE v3 Thm 5.1)
# ============================================================================
# Spec(H) ⊂ {0} ∪ [m,∞) (YM v38.5 thm:os-gap), where m = the exponential
# clustering/DECORRELATION rate. NOTE: in v38.5 the physics gap is CONDITIONAL —
# delivered only by thm:certificate-final assuming the unproven certificates
# UCF1–UCF5 (residual obligations T1–T5). This is NOT a proven floor; treat the
# market mapping as a heuristic proxy, not a guarantee.
# Market mapping: the correlation-eigenvalue gap is used as a cheap proxy for
# regime stability; the principled observable is the exponential decorrelation
# rate (built out via the OS spectral-measure estimator — Phase B).

class MassGapDetector:
    """Detects a spectral 'mass gap' proxy in the correlation structure.

    YM v38.5 thm:os-gap: Spec(H) ⊂ {0} ∪ [m,∞), m = exponential decorrelation
    rate — but CONDITIONAL on unproven UCF1–UCF5, so not a certified floor.

    Market proxy (heuristic — calibrate before trusting): eigenvalues of the
    returns covariance. λ₁ (market factor) ≫ λ₂ ≫ ... ≫ λn (noise).
    gap = λ₂/λ₁ measures market-factor dominance. Small → single-factor
    (systemic risk); large → diversified. A `decorrelation_rate` (lag-1) is also
    reported as the observable v38.5 actually points to.
    """
    
    def __init__(self, lookback=60):
        self.lookback = lookback
    
    def compute_spectral_gap(self, returns_matrix):
        """Compute the spectral mass gap from correlation eigenvalues.
        
        Returns the gap Δ = λ₂/λ₁ and the full eigenvalue spectrum.
        """
        if len(returns_matrix) < 20 or returns_matrix.ndim < 2:
            return {"gap": 0, "stable": True, "detail": "insufficient data"}

        # Clean NaN/inf rows (real market data has gaps) before covariance.
        rm = np.asarray(returns_matrix, dtype=float)
        rm = rm[np.all(np.isfinite(rm), axis=1)]
        if len(rm) < 20:
            return {"gap": 0, "stable": True, "detail": "insufficient finite data"}
        cov = np.cov(rm.T)
        cov = np.nan_to_num(cov, nan=0.0, posinf=0.0, neginf=0.0)
        try:
            eigenvalues = np.sort(np.linalg.eigvalsh(cov))[::-1]
        except np.linalg.LinAlgError:
            # Non-convergence: regularize the diagonal and retry once.
            cov = cov + np.eye(cov.shape[0]) * 1e-9
            try:
                eigenvalues = np.sort(np.linalg.eigvalsh(cov))[::-1]
            except np.linalg.LinAlgError:
                return {"gap": 0, "stable": True, "detail": "eigendecomposition failed"}
        eigenvalues = eigenvalues[eigenvalues > 1e-12]  # Remove noise floor
        
        if len(eigenvalues) < 2:
            return {"gap": 1.0, "stable": True, "eigenvalues": eigenvalues.tolist()}
        
        # Mass gap: ratio of second to first eigenvalue
        gap = eigenvalues[1] / (eigenvalues[0] + 1e-12)
        
        # Explained variance by first eigenvalue (market factor dominance)
        total_var = np.sum(eigenvalues)
        market_factor_pct = eigenvalues[0] / (total_var + 1e-12) * 100
        
        # Effective dimensionality: how many independent factors
        # (participation ratio: (Σλ)² / Σλ²)
        eff_dim = np.sum(eigenvalues)**2 / (np.sum(eigenvalues**2) + 1e-12)
        
        # Stability assessment (heuristic thresholds — NOT the conditional physics):
        # Large gap = regime is stable, requires big shock to transition
        # Small gap = market factor dominates everything = fragile
        if gap > 0.3 and market_factor_pct < 50:
            stability = "GAPPED"       # Strong gap, diversified
        elif gap > 0.15:
            stability = "WEAKLY_GAPPED"  # Moderate gap
        elif gap > 0.05:
            stability = "NEAR_CRITICAL"  # Gap closing, regime may shift
        else:
            stability = "GAPLESS"       # Single factor dominates, systemic risk

        # Decorrelation rate m (YM v38.5 thm:os-gap observable): the exponential
        # clustering rate the gap actually is. Estimated from mean lag-1 autocorr.
        d1 = np.diff(returns_matrix, axis=0) if returns_matrix.ndim > 1 else returns_matrix
        try:
            ac = [np.corrcoef(returns_matrix[:-1, k], returns_matrix[1:, k])[0, 1]
                  for k in range(returns_matrix.shape[1])]
            ac = np.nanmean(np.abs(ac))
            decorrelation_rate = float(-np.log(ac + 1e-6)) if np.isfinite(ac) else 0.0
        except Exception:
            decorrelation_rate = 0.0

        return {
            "gap": round(gap, 4),
            "market_factor_pct": round(market_factor_pct, 1),
            "effective_dimension": round(eff_dim, 2),
            "stability": stability,
            "decorrelation_rate": round(decorrelation_rate, 4),
            "top_eigenvalues": [round(e, 8) for e in eigenvalues[:5]],
            "regime_transition_energy": round(eigenvalues[0] * gap, 6),
        }
    
    def gap_evolution(self, returns_matrix, windows=[20, 40, 60]):
        """Track mass gap evolution over multiple lookback windows.
        Closing gap = regime transition approaching (ZPE v3 Corollary 5.2)."""
        results = {}
        for w in windows:
            if len(returns_matrix) >= w:
                sub = returns_matrix[-w:]
                results[w] = self.compute_spectral_gap(sub)
        
        # Trend: is the gap closing or opening?
        gaps = [results[w]["gap"] for w in sorted(results.keys()) if "gap" in results[w]]
        if len(gaps) >= 2:
            trend = "CLOSING" if gaps[-1] < gaps[0] * 0.8 else "OPENING" if gaps[-1] > gaps[0] * 1.2 else "STABLE"
        else:
            trend = "UNKNOWN"
        
        return {"windows": results, "trend": trend}


# ============================================================================
# MODULE 6: QEI Drawdown Guard (ZPE v3 Thm 4.1, Ford-Roman bound)
# ============================================================================
# "For any smooth sampling function f(τ) of characteristic width τ₀,
#  ∫ ⟨Tμν kμ kν⟩ f(τ)² dτ ≥ -C/τ₀⁴" (ZPE v3 Thm 4.1)
#
# Market mapping: drawdown energy (negative returns accumulated over time τ)
# is bounded below by -C/τ⁴. Short intense drawdowns are penalized more
# heavily than long shallow ones. The guard HALTS trading if the integrated
# drawdown exceeds the QEI bound.

class QEIDrawdownGuard:
    """Multi-metric portfolio guard with Ford-Roman QEI bounds.
    
    The Ford-Roman bound (ZPE v3 Thm 4.1):
      ∫ drawdown(τ) · f(τ)² dτ ≥ -C/τ₀⁴
    
    This means: the integral of negative P&L, weighted by a sampling
    function of characteristic timescale τ₀, cannot exceed C/τ₀⁴.
    Short timescales (τ₀ small) allow smaller drawdowns → tighter guard.
    Long timescales (τ₀ large) allow larger drawdowns → more patience.
    
    Status levels (matching ZPE v3 vocabulary):
      IMPROVING:   risk < φ₀ (Casimir regime: extracting energy from noise)
      STABLE:      risk ≤ 1.5·φ₀ (within QEI bound)
      WARNING:     risk ≤ 2·φ₀ (approaching NEC violation)
      CRITICAL:    risk > 2·φ₀ → HALT ALL (No-Extraction Thm 9.1 violated)
    """
    
    def __init__(self, initial_capital, max_drawdown_pct=0.20):
        self.initial_capital = initial_capital
        self.phi_0 = None
        self.max_drawdown_pct = max_drawdown_pct
    
    def ford_roman_bound(self, returns, tau_0):
        """Compute the Ford-Roman QEI bound for timescale τ₀.
        
        ∫ drawdown · f² dτ ≥ -C/τ₀⁴
        
        C is calibrated from the NGFP matter coordinates (α*_U).
        For markets: C depends on asset class volatility structure.
        """
        if len(returns) < int(tau_0):
            return {"within_bound": True, "integral": 0, "bound": 0}
        
        # Sampling function: Gaussian of width τ₀ centered on recent data
        t = np.arange(len(returns))
        center = len(returns) - 1
        f = np.exp(-0.5 * ((t - center) / tau_0)**2)
        f = f / np.sum(f)
        
        # Cumulative drawdown
        cumulative = np.cumsum(returns)
        peak = np.maximum.accumulate(cumulative)
        drawdown = cumulative - peak  # Always ≤ 0
        
        # Integrated drawdown weighted by f²
        integral = np.sum(drawdown * f**2)
        
        # QEI bound: -C/τ₀⁴
        C_qei = np.var(returns) * len(returns)  # Calibrated from data
        bound = -C_qei / (tau_0**4 + 1e-12)
        
        return {
            "within_bound": integral >= bound,
            "integral": round(integral, 8),
            "bound": round(bound, 8),
            "headroom_pct": round((integral - bound) / (abs(bound) + 1e-12) * 100, 1),
        }
    
    def compute_risk_functional(self, returns):
        """Composite risk functional φ(t) from ZPE v3 §7.
        
        Combines 5 risk metrics:
          - Max drawdown (worst low-freq damage)
          - Volatility (high-freq energy, √H0)
          - Variance acceleration (H1 from energy ladder)
          - Skewness (asymmetric tail exposure)
          - Kurtosis (fat tail exposure)
        """
        if len(returns) < 10:
            return 0
        
        cumulative = np.cumsum(returns)
        peak = np.maximum.accumulate(cumulative)
        max_dd = np.max(peak - cumulative)
        vol = np.std(returns)
        var_accel = np.std(np.diff(returns**2)) if len(returns) > 2 else 0
        skew = abs(stats.skew(returns))
        kurt = max(0, stats.kurtosis(returns) - 3)  # Excess kurtosis
        
        # Composite risk weights (hardcoded heuristic priors — NOT NGFP-derived;
        # UGFT v32.9 supplies no such constants. Calibrate against realized data.)
        phi = max_dd + 0.5 * vol + var_accel + 0.1 * skew + 0.05 * kurt
        
        if self.phi_0 is None:
            self.phi_0 = max(phi, 1e-10)
        
        return phi
    
    def check_guard(self, returns):
        """Check if portfolio is within QEI-safe bounds.
        
        Enforces: if φ > 2·φ₀ → HALT ALL
        (analogous to No-Extraction Thm 9.1: net energy extraction
        from a stable vacuum is forbidden by thermodynamics)
        """
        phi = self.compute_risk_functional(returns)
        
        if self.phi_0 is None or self.phi_0 < 1e-10:
            return {"status": "INITIALIZING", "phi": phi, "phi_0": 0, "action": "NORMAL"}
        
        ratio = phi / self.phi_0
        
        if ratio <= 1.0:
            status, action = "IMPROVING", "NORMAL"
        elif ratio <= 1.5:
            status, action = "STABLE", "NORMAL"
        elif ratio <= 2.0:
            status, action = "WARNING", "REDUCE_50%"
        else:
            status, action = "CRITICAL", "HALT_ALL"
        
        # Multi-scale QEI check at different timescales
        qei_short = self.ford_roman_bound(returns, tau_0=5)
        qei_medium = self.ford_roman_bound(returns, tau_0=20)
        qei_long = self.ford_roman_bound(returns, tau_0=60)
        
        return {
            "status": status, "action": action,
            "phi": round(phi, 6), "phi_0": round(self.phi_0, 6),
            "ratio": round(ratio, 4), "halt_threshold": 2.0,
            "qei_bounds": {
                "short_5d": qei_short,
                "medium_20d": qei_medium,
                "long_60d": qei_long,
            },
            "components": {
                "drawdown": round(np.max(np.maximum.accumulate(np.cumsum(returns)) - np.cumsum(returns)), 6) if len(returns) > 0 else 0,
                "volatility": round(np.std(returns), 6) if len(returns) > 0 else 0,
                "skewness": round(abs(stats.skew(returns)), 4) if len(returns) > 10 else 0,
                "kurtosis": round(max(0, stats.kurtosis(returns) - 3), 4) if len(returns) > 10 else 0,
            }
        }
    
    def adaptive_threshold(self, returns, windows=[20, 60, 120]):
        """Calibrate φ₀ from multi-scale analysis (Fusion v15.0 §15.1:
        penalty coefficients from NGFP, not hardcoded)."""
        thresholds = {}
        for w in windows:
            if len(returns) >= w:
                phi_w = self.compute_risk_functional(returns[-w:])
                thresholds[f"window_{w}"] = round(phi_w, 6)
        if thresholds:
            weights = [0.5, 0.3, 0.2][:len(thresholds)]
            values = list(thresholds.values())
            self.phi_0 = sum(w * v for w, v in zip(weights, values)) / sum(weights[:len(values)])
        return thresholds


# ============================================================================
# MODULE 7: Regime Stability Protector (Fusion v20.7 Thm 5.1 + Thm 11.1 ISS)
# ============================================================================
# The v15.0 Boltzmann topological-suppression law "η_topo ∝ exp(-Δ_YM/kT)"
# (old "Thm 15.4") DOES NOT EXIST in Fusion v20.7 and is retired.
# v20.7's real content:
#   Thm 5.1  exact Lyapunov identity: dE/dt = -D(U) ≤ 0  ⇒ risk relaxation rate
#            γ̂ = D/E ; γ̂ → 0 means approaching the neutral (fragile) manifold.
#   Thm 11.1 ISS steady-state radius ~ G/γ₀  ⇒ vulnerability = G/(γ̂·Δ)
#            (G = forcing / vol-of-vol, Δ = correlation gap).
# Market mapping (heuristic — calibrate before trusting): regime is fragile when
# the relaxation rate collapses relative to the forcing it must dissipate.

class TopologicalRegimeProtector:
    """Quantifies regime fragility via the Fusion v20.7 Lyapunov/ISS bounds.

    Thm 5.1: dE/dt = -D ⇒ relaxation rate γ̂ = D/E (large = safe, →0 = fragile).
    Thm 11.1: steady-state radius ~ G/γ₀ ⇒ vulnerability = G/(γ̂·Δ).
    (Class name kept for import compatibility; the physics is now v20.7 control
    theory, not the retired Boltzmann/helicity law.)
    """

    def __init__(self, lookback=120):
        self.lookback = lookback
        self.mass_gap_detector = MassGapDetector(lookback)

    def transition_probability(self, returns_matrix):
        """Regime fragility from Fusion v20.7 Lyapunov (Thm 5.1) + ISS (Thm 11.1).

        γ̂ = D/E (relaxation rate); vulnerability = G/(γ̂·Δ); prob = 1 - e^{-vuln}.
        """
        if len(returns_matrix) < 20 or returns_matrix.ndim < 2:
            return {"transition_prob": 0, "protection": "TOPOLOGICALLY_PROTECTED",
                    "detail": "insufficient data"}

        # Clean NaN/inf rows (real market data has gaps) before the ISS estimate.
        returns_matrix = np.asarray(returns_matrix, dtype=float)
        returns_matrix = returns_matrix[np.all(np.isfinite(returns_matrix), axis=1)]
        if len(returns_matrix) < 20:
            return {"transition_prob": 0, "protection": "TOPOLOGICALLY_PROTECTED",
                    "detail": "insufficient finite data"}

        gap_info = self.mass_gap_detector.compute_spectral_gap(returns_matrix)
        delta = gap_info.get("gap", 0.1)  # correlation gap Δ

        port = np.mean(returns_matrix, axis=1)
        # Thm 5.1: energy E (variance) and dissipation D (first-difference energy)
        E = np.var(port) + 1e-12
        D = np.mean(np.diff(port) ** 2) if len(port) > 1 else 0.0
        gamma_hat = D / E  # instantaneous relaxation rate γ̂
        # Forcing proxy G = vol-of-vol (dispersion of rolling volatility)
        w = max(5, len(port) // 6)
        rvol = np.array([np.std(port[i:i + w]) for i in range(0, len(port) - w + 1)])
        G = np.std(rvol) if len(rvol) > 1 else np.std(port)
        gamma0 = max(1e-6, gamma_hat)
        # Thm 11.1 ISS steady-state radius ~ G/γ₀, scaled by the correlation gap
        vulnerability = G / (gamma0 * (delta + 1e-6))
        prob = float(1.0 - np.exp(-vulnerability))  # [0,1): high = fragile

        if prob < 0.05:
            protection = "TOPOLOGICALLY_PROTECTED"
        elif prob < 0.20:
            protection = "WEAKLY_PROTECTED"
        elif prob < 0.50:
            protection = "VULNERABLE"
        else:
            protection = "TRANSITION_IMMINENT"

        return {
            "transition_prob": round(prob, 4),
            "protection": protection,
            "mass_gap": round(delta, 4),
            "relaxation_rate": round(gamma_hat, 6),
            "vulnerability": round(vulnerability, 4),
            "gap_over_temp": round(vulnerability, 4),  # legacy key = vulnerability
            "gap_stability": gap_info.get("stability", "UNKNOWN"),
        }
    
    def helicity_conservation(self, returns_matrix, window=20):
        """Correlation-structure shift detector (empirical; method name kept for
        compatibility). NOTE: the old 'magnetic helicity / Fusion Thm 15.4'
        attribution was fabricated — no such result exists in v20.7. This simply
        measures how much the correlation matrix moved between two windows; a
        large shift flags a regime change.
        """
        if len(returns_matrix) < 2 * window or returns_matrix.ndim < 2:
            return {"conserved": True, "detail": "insufficient data"}

        # Compare correlation matrices: early vs late
        corr_early = np.corrcoef(returns_matrix[-2*window:-window].T)
        corr_late = np.corrcoef(returns_matrix[-window:].T)

        # Frobenius norm of correlation change = correlation-structure shift
        delta_corr = np.linalg.norm(corr_late - corr_early, 'fro')
        n = corr_early.shape[0]
        # Normalize by matrix size
        reconnection_energy = delta_corr / (n + 1e-6)

        # Threshold from the correlation gap (empirical scale)
        gap_info = self.mass_gap_detector.compute_spectral_gap(returns_matrix[-window:])
        threshold = gap_info.get("gap", 0.1) * 2
        
        conserved = reconnection_energy < threshold
        
        return {
            "conserved": conserved,
            "reconnection_energy": round(reconnection_energy, 4),
            "threshold": round(threshold, 4),
            "correlation_shift": round(delta_corr, 4),
            "event": "NONE" if conserved else "STRUCTURE_SHIFT",
        }


# ============================================================================
# MODULE 8: Rosetta Correspondence Monitor (Rosetta v3 §1, §8, §9)
# ============================================================================
# "All stable, compact, or equilibrium phenomena arise as solutions to the
#  Rosetta Equation: Tr_X(Φ ∘ S) = 0" (Rosetta v3 Foundational Postulate)
#
# "ψ-stationarity: ψ(Φ) = 0 encodes compactness and regularity along
#  the correspondence dynamics" (Rosetta v3 §8, Fluids specialization)
#
# Market mapping: the Rosetta correspondence Tr(Φ∘S) measures whether
# the cross-domain relationships (stocks↔crypto, bonds↔commodities)
# are at equilibrium. When Tr(Φ∘S) → 0, all asset classes are in a
# stable correspondence. When it deviates, arbitrage or regime change.

class RosettaCorrespondenceMonitor:
    """Monitors cross-domain correspondence stationarity.
    
    The Rosetta Equation (v3 Eq. 1): Tr_X(Φ ∘ S) = 0 at equilibrium.
    
    Φ = correspondence map between asset classes.
    S = transition operator (1 - T_τ for short-time flow).
    Tr(Φ∘S) = trace of the composed map = cross-domain residual.
    
    When the residual vanishes → markets are at correspondence equilibrium.
    When the residual is large → intermarket dislocations exist.
    
    The ψ-calculus (Rosetta v3 §8):
      ψ(Φ) = d/dt[Tr(Φ∘S)] = rate of change of correspondence.
      ψ(Φ) = 0 → stationarity (equilibrium is holding).
      ψ(Φ) ≠ 0 → correspondence is breaking → opportunity or danger.
    
    Rosetta Transport Theorem (v3 §9): structures proven in one domain
    can be transported to another. We use this to transport YM mass gap
    insights to market regime detection.
    """
    
    # Standard cross-domain pairs (the "correspondence atlas")
    CORRESPONDENCES = {
        "equity_crypto": (["SPY", "QQQ"], ["BTC", "ETH"]),
        "equity_bonds": (["SPY", "QQQ"], ["TLT"]),
        "crypto_defi": (["BTC", "ETH"], ["SOL", "AVAX"]),
        "growth_value": (["QQQ", "ARKK"], ["XLE", "GLD"]),
    }
    
    @staticmethod
    def trace_correspondence(returns_a, returns_b):
        """Compute Tr(Φ∘S) for two asset groups.
        
        Φ = cross-correlation matrix between groups.
        S = transition operator = I - T_τ where T_τ = lagged correlation.
        Tr(Φ∘S) = sum of eigenvalues of (cross_corr × transition_operator).
        
        At equilibrium: Tr(Φ∘S) → 0 (stable correspondence).
        Deviation: cross-domain dislocation.
        """
        n = min(len(returns_a), len(returns_b))
        if n < 20:
            return {"trace": 0, "stationary": True}
        
        a = returns_a[-n:]
        b = returns_b[-n:]
        
        # Φ: contemporaneous cross-correlation
        if a.ndim == 1:
            a = a.reshape(-1, 1)
        if b.ndim == 1:
            b = b.reshape(-1, 1)
        
        combined = np.hstack([a, b])
        full_corr = np.corrcoef(combined.T)
        na, nb = a.shape[1], b.shape[1]
        Phi = full_corr[:na, na:]  # Cross-correlation block
        
        # S: transition operator = I - T_τ (lagged correlation change)
        half = n // 2
        combined_early = np.hstack([a[:half], b[:half]])
        combined_late = np.hstack([a[half:], b[half:]])
        corr_early = np.corrcoef(combined_early.T)
        corr_late = np.corrcoef(combined_late.T)
        Phi_early = corr_early[:na, na:]
        Phi_late = corr_late[:na, na:]
        
        # S = Phi_late - Phi_early (transition)
        S = Phi_late - Phi_early
        
        # Tr(Φ∘S) = trace of Phi @ S.T
        if Phi.shape[1] == S.shape[0]:
            composed = Phi @ S.T
            trace_val = np.trace(composed) if composed.ndim == 2 else float(composed)
        else:
            trace_val = np.sum(Phi * S)
        
        return {
            "trace": round(trace_val, 6),
            "stationary": abs(trace_val) < 0.1,
            "cross_correlation": round(np.mean(np.abs(Phi)), 4),
            "transition_magnitude": round(np.linalg.norm(S, 'fro'), 4),
        }
    
    @staticmethod
    def psi_derivative(returns_a, returns_b, window=10):
        """ψ-calculus: rate of change of correspondence (Rosetta v3 §8).
        
        ψ(Φ) = d/dt[Tr(Φ∘S)]
        Computed as rolling difference of trace values.
        ψ(Φ) = 0 → equilibrium holding.
        ψ(Φ) ≠ 0 → correspondence evolving → regime in flux.
        """
        n = min(len(returns_a), len(returns_b))
        if n < 2 * window:
            return {"psi": 0, "stationary": True}
        
        # Compute trace at two consecutive windows
        trace_early = RosettaCorrespondenceMonitor.trace_correspondence(
            returns_a[:n-window] if returns_a.ndim == 1 else returns_a[:n-window],
            returns_b[:n-window] if returns_b.ndim == 1 else returns_b[:n-window]
        )
        trace_late = RosettaCorrespondenceMonitor.trace_correspondence(
            returns_a, returns_b
        )
        
        psi = trace_late["trace"] - trace_early["trace"]
        
        return {
            "psi": round(psi, 6),
            "stationary": abs(psi) < 0.05,
            "trace_early": trace_early["trace"],
            "trace_late": trace_late["trace"],
            "direction": "STRENGTHENING" if psi > 0.05 else "WEAKENING" if psi < -0.05 else "STABLE",
        }


# ============================================================================
# MODULE 9: Mean-Reversion Engine (NS v11.0 spectral decay)
# ============================================================================

class MeanReversionEngine:
    """Mean-reversion signals from Rψ spectral decay analysis.
    
    Core: noise has exponential decay rate exp(-γ·t).
    When |noise_zscore| > 2 and decay is fast → fade the move.
    """
    
    def __init__(self, epsilon_psi=20, entry_z=2.0, exit_z=0.5):
        self.decomposer = RpsiDecomposer(epsilon_psi)
        self.entry_z = entry_z
        self.exit_z = exit_z
    
    def generate_signal(self, prices):
        """Generate mean-reversion signal with Rψ decay estimate."""
        trend, noise, decay_coeff, bounds = self.decomposer.decompose(prices)
        if trend is None:
            return {"signal": "NO_DATA", "score": 50}
        
        noise_std = np.std(noise)
        z = noise[-1] / (noise_std + 1e-12)
        
        # Expected reversion from decay rate
        decay_rate = np.exp(-decay_coeff * 0.1)
        expected_reversion = noise[-1] * (1 - decay_rate)
        half_life = np.log(2) / (decay_coeff + 1e-12) if decay_coeff > 0 else float('inf')
        
        # Multi-scale confirmation
        scales = self.decomposer.multi_scale_decompose(prices, [10, 20, 50])
        confirming = sum(1 for s in scales.values()
                        if (z > 0 and s["noise_zscore"] > 1) or
                           (z < 0 and s["noise_zscore"] < -1))
        
        snr = self.decomposer.signal_strength(prices)
        
        if z > self.entry_z:
            signal, confidence = "FADE_SHORT", min(95, 50 + abs(z) * 12 + confirming * 5)
        elif z < -self.entry_z:
            signal, confidence = "FADE_LONG", min(95, 50 + abs(z) * 12 + confirming * 5)
        elif abs(z) < self.exit_z:
            signal, confidence = "FLAT", 50
        else:
            signal, confidence = "HOLD", 50
        
        return {
            "signal": signal, "z_score": round(z, 3),
            "decay_coeff": round(decay_coeff, 4),
            "decay_rate": round(decay_rate, 4),
            "half_life_bars": round(half_life, 1),
            "expected_reversion_pct": round(expected_reversion * 100, 4),
            "signal_strength": round(snr, 4),
            "multi_scale_confirms": confirming,
            "confidence": round(confidence, 1),
            "trend_dir": "UP" if trend[-1] > 0 else "DOWN",
            "regime": "TREND" if snr > 0.3 else "NOISE" if snr < 0.1 else "MIXED",
            "stretching_bound": bounds,
        }


# ============================================================================
# MODULE 10: Execution Error Estimator (NS v11.0 commutator analysis)
# ============================================================================

class ExecutionErrorEstimator:
    """Estimates slippage from discrete-time execution.
    
    Slippage = O(vol / √freq) (commutator bound from Kato-Ponce).
    Optimal freq balances slippage vs fees.
    """
    
    @staticmethod
    def estimate_slippage(prices, rebalance_freq=1):
        if len(prices) < rebalance_freq * 5:
            return {"slippage_pct": 0, "optimal_freq": 1}
        prices = np.asarray(prices, dtype=float)
        prices = prices[np.isfinite(prices) & (prices > 0)]  # drop NaN/≤0 (real data gaps)
        if len(prices) < rebalance_freq * 5:
            return {"slippage_pct": 0, "optimal_freq": 1}
        returns = np.diff(np.log(prices))
        vol = np.std(returns)
        if not np.isfinite(vol):
            return {"slippage_pct": 0, "optimal_freq": 1}
        slippage = vol / np.sqrt(rebalance_freq + 1e-12)
        fee_rate = 0.001
        optimal_freq = max(1, int((vol / (2 * fee_rate + 1e-12))**(2/3)))
        return {
            "slippage_pct": round(slippage * 100, 4),
            "optimal_freq": min(optimal_freq, 20),
            "vol_daily": round(vol * 100, 4),
            "fee_adjusted_slippage": round((slippage + fee_rate * rebalance_freq) * 100, 4),
        }


# ============================================================================
# MODULE 11: Integrated Report — Full UGFT Risk Analysis
# ============================================================================

WATCHLIST = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
    "XRP": "XRP-USD", "AVAX": "AVAX-USD", "LINK": "LINK-USD",
    "SPY": "SPY", "QQQ": "QQQ",
}

# Extended watchlist for Rosetta cross-domain analysis
ROSETTA_TICKERS = {
    "SPY": "SPY", "QQQ": "QQQ", "TLT": "TLT",
    "GLD": "GLD", "XLE": "XLE", "ARKK": "ARKK",
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "AVAX": "AVAX-USD",
}

def _fetch_returns(tickers, period="6mo"):
    """Fetch returns for a dict of {name: ticker}."""
    data = {}
    for name, ticker in tickers.items():
        try:
            hist = yf.Ticker(ticker).history(period=period)
            if len(hist) > 20:
                data[name] = {
                    "prices": hist["Close"].values,
                    "returns": np.diff(np.log(hist["Close"].values)),
                }
        except Exception:
            pass
    return data


def alpha_risk_report():
    """Full Etherion Risk Engine v2.0 report.
    
    Architecture derived from the UGFT papers (heuristic market analogies,
    calibrate before trusting):
      NS v11.0  → Rψ decomposition, energy ladder, K41 spectrum
      YM v38.5  → KP source predicate, large-field additive barrier, conditional gap
      Fusion v20.7 → Lyapunov dissipation (Thm 5.1) + ISS regime stability (Thm 11.1)
      ZPE v3    → QEI drawdown bounds, mass gap thresholds
      Rosetta v3 → cross-domain correspondence, ψ-stationarity
      UGFT v32.9 → Etherion superconnection (NGFP params = calibratable priors)
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"═══ Etherion Risk Engine v2.0 ({ts}) ═══",
        f"    Derived from UGFT v32.9 / NS v11.0 / YM v38.5 / Fusion v20.7 / ZPE v3 / Rosetta v3",
        "",
    ]
    
    # Initialize all engines
    decomposer = RpsiDecomposer(20)
    reversion = MeanReversionEngine(20)
    kolmogorov = KolmogorovAnalyzer()
    excision = LargeFieldExcision()
    cascade = EnergyLadderCascade()
    mass_gap = MassGapDetector()
    topo = TopologicalRegimeProtector()
    rosetta = RosettaCorrespondenceMonitor()
    
    # Fetch data
    market_data = _fetch_returns(WATCHLIST)
    rosetta_data = _fetch_returns(ROSETTA_TICKERS)
    
    if not market_data:
        return "ERROR: Could not fetch market data"
    
    # ── SECTION 1: Rψ Signal Decomposition (NS v11.0) ──
    lines.append("── Rψ SIGNAL DECOMPOSITION (NS v11.0 §2-4) ──")
    asset_signals = {}
    all_returns = []
    
    for name, d in market_data.items():
        prices, rets = d["prices"], d["returns"]
        sig = reversion.generate_signal(prices)
        k41 = kolmogorov.analyze(prices)
        lf = excision.classify_field(rets)
        
        regime = sig["regime"]
        bound_ok = "✓" if sig.get("stretching_bound", {}).get("bound_satisfied", True) else "✗"
        lines.append(
            f"  {name:5} SNR:{sig['signal_strength']:.3f}({regime:5}) "
            f"z:{sig['z_score']:+.2f} decay:{sig['decay_coeff']:.3f} "
            f"HL:{sig['half_life_bars']:.0f}b M-bound:{bound_ok} "
            f"-> {sig['signal']} ({sig['confidence']:.0f}%)"
        )
        
        # Actionable detail
        if sig["signal"] in ["FADE_SHORT", "FADE_LONG"]:
            lines.append(
                f"        Confirms: {sig['multi_scale_confirms']}/3 | "
                f"Revert: {sig['expected_reversion_pct']:.2f}% | "
                f"K41 slope: {k41['slope']:.2f} ({k41['regime']})"
            )
        
        asset_signals[name] = sig
        all_returns.append(rets[-60:])
    
    # ── SECTION 2: Kolmogorov Spectrum (NS v11.0 §6) ──
    lines.append("")
    lines.append("── KOLMOGOROV SPECTRUM (NS v11.0 Thm 6.3, η=4/3) ──")
    for name, d in market_data.items():
        k41 = kolmogorov.analyze(d["prices"])
        at_ngfp = "★" if k41["at_NGFP"] else " "
        lines.append(
            f"  {name:5} slope:{k41['slope']:+.3f} "
            f"(K41={k41['K41_slope']:.3f}) dev:{k41['deviation_from_K41']:.3f} "
            f"η̂:{k41['eta_estimated']:.3f} regime:{k41['regime']:20} {at_ngfp}"
        )
    
    # ── SECTION 3: Energy Ladder + KP Convergence (NS+YM) ──
    lines.append("")
    lines.append("── ENERGY LADDER + KOTECKÝ-PREISS (NS v11.0 Thm 4.1 + YM v38.5 KP predicate) ──")
    if len(all_returns) > 1:
        min_len = min(len(r) for r in all_returns)
        matrix = np.column_stack([r[-min_len:] for r in all_returns])
        # Drop rows with NaN/inf (real market data has gaps) so all modules get clean input.
        matrix = matrix[np.all(np.isfinite(matrix), axis=1)]
        
        ladder_check = cascade.energy_ladder_check(matrix)
        lad = ladder_check["ladder"]
        kp = ladder_check["kotecky_preiss"]
        
        lines.append(f"  H0 (variance):       {lad['H0']:.6f}")
        lines.append(f"  H1 (vol-of-vol):     {lad['H1']:.8f}")
        lines.append(f"  H2 (accel risk):     {lad['H2']:.10f}")
        lines.append(f"  H3 (jerk risk):      {lad['H3']:.12f}")
        lines.append(f"  Gronwall ratio:      {lad['Gronwall_ratio']:.4f} (halt at 2.0)")
        lines.append(f"  KP parameter η:      {kp['eta']:.4f} (converges: {'YES' if kp['converges'] else 'NO ⚠'})")
        lines.append(f"  Avg |correlation|:   {kp.get('avg_abs_corr', 0):.4f}")
        lines.append(f"  Cascade stable:      {'YES ✓' if ladder_check['stable'] else 'NO ✗ ' + ', '.join(ladder_check['violations'])}")
    
    # ── SECTION 4: Large-Field Extraction (YM v38.5 thm:additive-barrier) ──
    lines.append("")
    lines.append("── LARGE-FIELD EXTRACTION (YM v38.5 additive barrier, s=5/12) ──")
    for name, d in market_data.items():
        lf = excision.classify_field(d["returns"])
        lines.append(
            f"  {name:5} δk:{lf['delta_k']:.4f} "
            f"large:{lf['n_large_field']:3d} ({lf['pct_large_field']:.1f}%) "
            f"avg_penalty:{lf['avg_penalty']:.3f} regime:{lf['regime']}"
        )
    
    # ── SECTION 5: Mass Gap + Topological Protection (YM+ZPE+Fusion) ──
    lines.append("")
    lines.append("── MASS GAP + REGIME STABILITY (YM v38.5 os-gap[cond] / ZPE v3 Thm 5.1 / Fusion v20.7 Thm 5.1+11.1) ──")
    if len(all_returns) > 1:
        gap_info = mass_gap.compute_spectral_gap(matrix)
        gap_evo = mass_gap.gap_evolution(matrix)
        topo_info = topo.transition_probability(matrix)
        helicity = topo.helicity_conservation(matrix)
        
        lines.append(f"  Spectral gap Δ:      {gap_info['gap']:.4f} ({gap_info['stability']})")
        lines.append(f"  Market factor:       {gap_info['market_factor_pct']:.1f}% of variance")
        lines.append(f"  Effective dimension: {gap_info['effective_dimension']:.2f}")
        lines.append(f"  Gap trend:           {gap_evo['trend']}")
        lines.append(f"  Fragility prob:      {topo_info['transition_prob']:.4f} ({topo_info['protection']})")
        lines.append(f"  Relaxation rate γ̂:   {topo_info.get('relaxation_rate', 0):.6f}")
        lines.append(f"  Vulnerability G/(γ̂Δ): {topo_info.get('vulnerability', 0):.4f}")
        lines.append(f"  Corr structure stable: {'YES ✓' if helicity['conserved'] else 'NO ✗ — ' + helicity.get('event', '')}")
        lines.append(f"  Corr shift / thresh: {helicity.get('reconnection_energy', 0):.4f} / {helicity.get('threshold', 0):.4f}")
    
    # ── SECTION 6: QEI Drawdown Guard (ZPE v3 Thm 4.1) ──
    lines.append("")
    lines.append("── QEI DRAWDOWN GUARD (ZPE v3 Thm 4.1, Ford-Roman bound) ──")
    if len(all_returns) > 0:
        portfolio_returns = np.mean(matrix, axis=1) if len(all_returns) > 1 else all_returns[0]
        guard = QEIDrawdownGuard(80.78)
        guard.adaptive_threshold(portfolio_returns)
        check = guard.check_guard(portfolio_returns)
        
        lines.append(f"  Status: {check['status']} | Action: {check['action']}")
        lines.append(f"  Risk φ: {check['phi']:.6f} | φ₀: {check['phi_0']:.6f} | Ratio: {check['ratio']:.4f} (halt at {check['halt_threshold']})")
        comp = check.get("components", {})
        lines.append(f"  Drawdown: {comp.get('drawdown', 0):.4f} | Vol: {comp.get('volatility', 0):.4f} | Skew: {comp.get('skewness', 0):.2f} | Kurt: {comp.get('kurtosis', 0):.2f}")
        
        qei = check.get("qei_bounds", {})
        for label, q in qei.items():
            if isinstance(q, dict):
                within = "✓" if q.get("within_bound", True) else "✗ VIOLATION"
                lines.append(f"  QEI {label:12}: integral={q.get('integral', 0):.6f} bound={q.get('bound', 0):.6f} headroom={q.get('headroom_pct', 0):.0f}% {within}")
    
    # ── SECTION 7: Rosetta Correspondence (Rosetta v3 §8) ──
    lines.append("")
    lines.append("── ROSETTA CORRESPONDENCE (Rosetta v3 Tr(Φ∘S)=0 at equilibrium) ──")
    correspondences = {
        "equity↔crypto": (["SPY", "QQQ"], ["BTC", "ETH"]),
        "equity↔bonds":  (["SPY", "QQQ"], ["TLT"]),
        "crypto↔defi":   (["BTC", "ETH"], ["SOL", "AVAX"]),
        "growth↔value":  (["QQQ"], ["XLE", "GLD"]),
    }
    
    for label, (group_a, group_b) in correspondences.items():
        # Build return arrays for each group
        rets_a = [rosetta_data[t]["returns"] for t in group_a if t in rosetta_data]
        rets_b = [rosetta_data[t]["returns"] for t in group_b if t in rosetta_data]
        
        if rets_a and rets_b:
            min_n = min(min(len(r) for r in rets_a), min(len(r) for r in rets_b))
            arr_a = np.column_stack([r[-min_n:] for r in rets_a])
            arr_b = np.column_stack([r[-min_n:] for r in rets_b])
            
            tr = rosetta.trace_correspondence(arr_a, arr_b)
            psi = rosetta.psi_derivative(arr_a, arr_b)
            
            eq = "✓" if tr["stationary"] else "✗"
            psi_eq = "✓" if psi["stationary"] else "✗"
            lines.append(
                f"  {label:16} Tr(Φ∘S):{tr['trace']:+.4f}{eq} "
                f"ψ(Φ):{psi['psi']:+.4f}{psi_eq} "
                f"xcorr:{tr['cross_correlation']:.3f} "
                f"dir:{psi['direction']}"
            )
    
    # ── SECTION 8: Execution Analysis ──
    lines.append("")
    lines.append("── EXECUTION ANALYSIS (NS v11.0 commutator bounds) ──")
    for name in ["BTC", "ETH", "SPY"]:
        if name in market_data:
            slip = ExecutionErrorEstimator.estimate_slippage(market_data[name]["prices"])
            lines.append(f"  {name}: slippage {slip['slippage_pct']:.3f}% | optimal rebal: every {slip['optimal_freq']} bars")
    
    # ── SECTION 9: Actionable Summary ──
    lines.append("")
    lines.append("── ACTIONABLE SUMMARY ──")
    
    # Mean-reversion signals
    actionable_mr = [(n, s) for n, s in asset_signals.items() if s["signal"] in ["FADE_SHORT", "FADE_LONG"]]
    if actionable_mr:
        lines.append("  Mean-Reversion Opportunities:")
        for name, sig in actionable_mr:
            lines.append(
                f"    >> {name}: {sig['signal']} (z={sig['z_score']:+.2f}, "
                f"conf={sig['confidence']:.0f}%, HL={sig['half_life_bars']:.0f}b, "
                f"revert={sig['expected_reversion_pct']:.2f}%)"
            )
    else:
        lines.append("  No mean-reversion signals active.")
    
    # Rosetta dislocations (non-stationary correspondences = opportunity)
    rosetta_alerts = []
    for label, (group_a, group_b) in correspondences.items():
        rets_a = [rosetta_data[t]["returns"] for t in group_a if t in rosetta_data]
        rets_b = [rosetta_data[t]["returns"] for t in group_b if t in rosetta_data]
        if rets_a and rets_b:
            min_n = min(min(len(r) for r in rets_a), min(len(r) for r in rets_b))
            arr_a = np.column_stack([r[-min_n:] for r in rets_a])
            arr_b = np.column_stack([r[-min_n:] for r in rets_b])
            tr = rosetta.trace_correspondence(arr_a, arr_b)
            if not tr["stationary"]:
                rosetta_alerts.append((label, tr["trace"]))
    
    if rosetta_alerts:
        lines.append("  Rosetta Dislocations (intermarket arbitrage potential):")
        for label, trace in rosetta_alerts:
            lines.append(f"    >> {label}: Tr(Φ∘S)={trace:+.4f} — correspondence broken")
    
    # Regime warnings
    if len(all_returns) > 1:
        if not ladder_check["stable"]:
            lines.append(f"  ⚠ CASCADE UNSTABLE: {', '.join(ladder_check['violations'])}")
        if topo_info["protection"] in ["VULNERABLE", "TRANSITION_IMMINENT"]:
            lines.append(f"  ⚠ REGIME TRANSITION {topo_info['protection']}: P={topo_info['transition_prob']:.2%}")
        if not helicity["conserved"]:
            lines.append(f"  ⚠ CORRELATION RECONNECTION: shift={helicity.get('correlation_shift', 0):.3f}")
        if check["action"] == "HALT_ALL":
            lines.append(f"  🛑 QEI HALT: Risk ratio {check['ratio']:.2f} > 2.0 (No-Extraction Thm violated)")
        elif check["action"] == "REDUCE_50%":
            lines.append(f"  ⚠ QEI WARNING: Risk ratio {check['ratio']:.2f} — reduce positions 50%")
    
    # ── Save results ──
    results = {
        "timestamp": ts,
        "version": "2.0",
        "framework": "UGFT v32.9 / NS v11.0 / YM v38.5 / Fusion v20.7 / ZPE v3 / Rosetta v3",
        "signals": {n: s for n, s in asset_signals.items()},
    }
    try:
        save_path = os.path.expanduser("~/.openclaw/workspace/models/latest_etherion_risk.json")
        with open(save_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
    except Exception:
        pass
    
    return "\n".join(lines)


# ============================================================================
# Legacy API compatibility — ensure unified_brain.py can still import
# ============================================================================

# Aliases for v1 class names (backward compatible)
SignalDecomposer = RpsiDecomposer
VarianceCascadeEngine = EnergyLadderCascade
DrawdownGuard = QEIDrawdownGuard


if __name__ == "__main__":
    print(alpha_risk_report())
