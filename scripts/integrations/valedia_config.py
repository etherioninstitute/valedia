"""
Valedia shared configuration — the single source of truth for cross-process constants.

"All processes connected & informed by each other" starts here: a number used by more
than one process lives HERE, and each module imports it instead of hardcoding a local
copy. Hardcoded copies are exactly how the MiroFish weight drifted (0.10 vs 0.15 vs a
0.2 docstring) and how Kelly split (0.5 vs 0.25). The drift detector
(valedia_consistency_check.py) fails if a shared constant is redefined in a module.
(c) GOATWORTHY Post LLC.
"""

# ── Manuscript linkage ──────────────────────────────────────────────
# Bump this when Burton's UGFT/Etherion manuscript changes. The canonical implementations
# — kernel_predictor_v16e.py (market functionals) and Etherion_Risk_Engine.py (risk
# functionals) — carry the SAME stamp, and MANUSCRIPT_MANIFEST.md maps
# version -> canonical module -> consumers, so an update's blast radius is explicit.
MANUSCRIPT_VERSION = "UGFT-v32.9"   # overarching framework version; per-module stamps live in the
                                    # canonical files + MANUSCRIPT_MANIFEST.md. Set to your authoritative version.

# Runtime identity (0.32.0 = H7 Arena offline paper stress / FTL score).
# Not a substitute for MANUSCRIPT_VERSION.
VALEDIA_VERSION = "0.32.0"

# ── AGREEING shared constants (wired into consumers; one definition) ──
# MIROFISH_* caps. Design: recommendations/VALEDIA-MIROFISH-DYNAMIC-WEIGHT.md
# Master flip VALEDIA_SELF_ADJUST (False here; env=1). Do NOT True this in live config.
# Dormant ≠ dead: pause TTL re-open + neural enrich may run under the flip.
# SELF_ADJUST does not open venue writers / SPOT GET / NAUTILUS_LIVE.
VALEDIA_SELF_ADJUST   = False  # Burton master flip only
MIROFISH_WEIGHT       = 0.10   # configured fusion weight. Runtime: effective_mirofish_weight().
MIROFISH_UNPROVEN_CAP = 0.10   # never exceed while accuracy unproven
MIROFISH_PROVEN_CAP   = 0.15   # seatbelted 10–15% when SELF_ADJUST is False
MIROFISH_ACCURACY_BAR = 0.55   # Burton-configurable directional bar (strict >)
MIROFISH_MIN_SCORED   = 20     # fee-true scored sample before proven
MIROFISH_AUTO_RAISE   = False  # stays False unless SELF_ADJUST allows in-band raise
# Band used only when self_adjust_enabled(). Hard ceiling until Burton raises.
MIROFISH_BAND_FLOOR   = 0.05
MIROFISH_BAND_TOP     = 0.25
MIROFISH_HARD_CEILING = 0.30
MIROFISH_ENRICH_BONUS = 0.02   # enriched-vs-plain, inside band only
CRYPTO_FEE_PCT      = 0.001    # crypto taker-fee assumption for backtests/sizing.
CRYPTO_SLIPPAGE_PCT = 0.0005   # crypto slippage assumption.

# Canonical LIVE fusion (kernel-first). Consumed by unified_brain.stage_fusion.
# signal_fusion.WEIGHTS is a NON-LIVE scanner (LIVE_PATH=False) — not an exec brain.
# Remainder (1 - kernel - mirofish - ml - intel) is a 50-neutral fill so weights sum to 1.
FUSION_WEIGHTS = {
    "kernel_equity": 0.35,
    "kernel_crypto": 0.30,
    "mirofish": MIROFISH_WEIGHT,
    "ml": 0.18,
    "intel_equity": 0.10,
    "intel_crypto": 0.15,
}

# Live-exec fence (Slice 8). These names must not place orders from this lane
# (weather create_order / oil via allocator Kalshi POST / scalper alp_buy).
# This is NOT a learning grave: pause/TTL in pnl_tracker can re-enable
# evaluation + sizing + category gating. Unpause ≠ live-buy.
# Alias KILLED_STRATEGIES kept for Slice 8 tests.
LIVE_EXEC_FENCED = frozenset({"WEATHER_ARB", "OIL_BRACKET"})
KILLED_STRATEGIES = LIVE_EXEC_FENCED

# MicroScalper / weather live buys. This lane never sets True.
SCALPER_LIVE = False
WEATHER_LIVE = False

# Position sizing — half-Kelly + DYNAMIC per-trade cap + absolute fuses (backtested 2026-09-12).
# The per-trade ceiling BREATHES with regime/track-record/risk instead of a flat number; the
# fuses are circuit-breakers (rarely bind), not operating caps. Consumers: kelly_sizing, quant_engine, sports_engine.
KELLY_SAFETY_FRACTION   = 0.5     # half-Kelly (unified 2026-09-12; was 0.5 vs 0.25).
KELLY_BASE_CAP          = 0.08    # operating per-trade ceiling at NEUTRAL conditions (8%).
KELLY_FUSE              = 0.15    # absolute per-trade backstop (circuit-breaker; not the operating cap).
KELLY_DAILY_FUSE        = 0.20    # absolute daily cumulative backstop.
KELLY_REGIME_CAP_FACTOR = {"BULL": 1.25, "SIDEWAYS": 1.0, "BEAR": 0.5, "UNKNOWN": 0.7}

# Conservative-until-proven: below this many REAL settled trades we have no edge to bet on,
# so the track-record multiplier stays at KELLY_UNPROVEN_MULT (→ 4% per trade at neutral,
# not the 8% a naive "empty=neutral" would give, nor the accidental 2.4% a stale 0-win bug gave).
# You EARN aggression above 0.5 only once a real settled sample shows a real hit-rate.
KELLY_MIN_PROVEN_TRADES = 20
KELLY_UNPROVEN_MULT     = 0.5

# Category calibration gate (forward_test_ledger.should_trade_category). Blocks a category ONLY
# on VERIFIED recent real losses — never on errors/failed/pending trades or paper, and never
# permanently. Gates on NET (real money lost), not win-rate, so a fat-tailed low-win winner
# (e.g. CRYPTO_BRACKET: 25% win but net-positive) is NOT killed.
CATEGORY_MIN_TRADES    = 4      # min REAL settled trades in-window before a block is even possible
CATEGORY_LOOKBACK_DAYS = 45     # recency window; stale real losses age out -> automatic retest
CATEGORY_BLOCK_NET     = 0.0    # block only if fee-inclusive net is an actual loss (< $0.00)

# Learning pause (Slice 9 / P1.4). strategy_blacklist.json is a pause, not a life sentence.
# Rule: pause on fee-true NET (category_stats when available), never raw WR after 3 trades.
# TTL expiry → probation (eligible for evaluation/sizing). Evidence net>=0 unpauses immediately.
# Category gate still blocks execute on verified 45d net loss. Live-exec fence still holds.
PAUSE_TTL_DAYS = 7

# Spot GET-reconcile HTTP. DEFAULT OFF (Slice 7 helpers; Slice 14 runtime wiring).
# Single Burton/Valedia flip (this lane never sets True):
#   export SPOT_RECONCILE_HTTP=1
# or set SPOT_RECONCILE_HTTP = True below.
# Then reconcile_run.get_spot_order_getter() → spot_order_get.live_spot_order_getter
# (read-only Coinbase/Alpaca GET). No extra set_spot_order_getter call.
# Tests inject mocked transport; they never live-GET.
# Enabling live GET is a Valedia go, not a coding-agent go.
SPOT_RECONCILE_HTTP = False

# Rebalancer auto-exec. Default OFF. Shadow/propose only until Burton flips:
#   export REBALANCER_AUTOEXEC=1
# AND passes live_flag gates. This lane never sets True. NT never writes.
REBALANCER_AUTOEXEC = False

# Cron stagger (Slice 7): sports every 2h at :00, allocator every 4h at :20.
# Same-minute collision was a double-bet race on 4h boundaries.
SPORTS_CRON_MINUTE = 0
ALLOCATOR_CRON_MINUTE = 20

# Execution policy (execution_policy.py maker-order layer). DRY-RUN by default: Valedia decides
# maker/taker/no_trade autonomously, but placing REAL resting maker orders stays off until proven
# on paper (earn it). Flip to False to let her place/manage live maker orders under her risk stack.
EXEC_DRY_RUN = False   # LIVE maker orders enabled 2026-09-12 (wired into master_allocator, the live authority)

import os as _os, json as _json   # for auto-sourcing live track-record / risk state
_MODELS = _os.path.expanduser("~/.openclaw/workspace/models")

def _env_flag(name, default):
    env = _os.environ.get(name)
    if env is not None and str(env).strip() != "":
        return str(env).strip().lower() in ("1", "true", "yes", "on")
    return bool(default)


_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_STAMP_CACHE = None


def version_identity():
    """Build identity: version, short sha, branch, dirty. Fail-closed to unknowns."""
    def _git(*args):
        import subprocess
        try:
            return subprocess.check_output(
                ["git", "-C", _ROOT, *args],
                stderr=subprocess.DEVNULL, timeout=2,
            ).decode().strip()
        except Exception:
            return ""
    sha = _git("rev-parse", "--short", "HEAD") or "unknown"
    branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    dirty = bool(_git("status", "--porcelain"))
    return {"version": VALEDIA_VERSION, "sha": sha, "branch": branch, "dirty": dirty}


def version_stamp():
    """Short 'vX.Y.Z sha' (dirty → trailing *) for Signal/status headers."""
    global _STAMP_CACHE
    if _STAMP_CACHE is None:
        i = version_identity()
        _STAMP_CACHE = f"v{i['version']} {i['sha']}" + ("*" if i["dirty"] else "")
    return _STAMP_CACHE


def version_line():
    """Match scripts/valedia_version.sh: vX.Y.Z sha branch clean|dirty."""
    i = version_identity()
    return f"v{i['version']} {i['sha']} {i['branch']} {'dirty' if i['dirty'] else 'clean'}"


def spot_reconcile_http_enabled():
    """True only if Burton/Valedia flipped the spot GET HTTP flag.

    Single flip: env SPOT_RECONCILE_HTTP=1/true/yes/on OR this module's
    SPOT_RECONCILE_HTTP = True. Env wins over the constant. Anything else
    (unset, empty, 0, false) is OFF. Default constant is False.
    """
    return _env_flag("SPOT_RECONCILE_HTTP", SPOT_RECONCILE_HTTP)


def self_adjust_enabled():
    """True only if Burton flipped VALEDIA_SELF_ADJUST. Default False.

    Wakes MiroFish AUTO_RAISE *inside the band* and documents that pause
    re-open / neural enrich may run. Does not open VENUE_WRITER, SPOT GET,
    NAUTILUS_LIVE, or AUTOEXEC. Env VALEDIA_SELF_ADJUST=1 wins over the constant.
    This lane never sets the constant True.
    """
    return _env_flag("VALEDIA_SELF_ADJUST", VALEDIA_SELF_ADJUST)


def rebalancer_autoexec_enabled():
    """True only if Burton flipped REBALANCER_AUTOEXEC. Default False.

    Necessary but not sufficient: rebalancer still needs live_flag gates and
    venue_writer==valedia. NT cannot dispatch. This lane never sets True.
    """
    return _env_flag("REBALANCER_AUTOEXEC", REBALANCER_AUTOEXEC)


def scalper_live_enabled():
    """MicroScalper live buys. Hard-off. Env cannot enable — dead path."""
    return False


def weather_live_enabled():
    """WEATHER_ARB live orders. Hard-off. Env cannot enable — dead path."""
    return False


def is_killed_strategy(name):
    """Live-exec fence. True means do not place orders. Not a learning grave."""
    return live_exec_fenced(name)


def live_exec_fenced(name):
    """True if this strategy must not place live orders (Slice 8 fence).

    Independent of learning pause. Unpause / TTL expiry re-enters evaluation
    and Kelly/category gating only. This lane never lifts the fence.
    """
    return str(name or "").strip().upper() in LIVE_EXEC_FENCED


def effective_mirofish_weight():
    """Runtime MiroFish fusion weight from the fee-true accuracy ledger.

    SELF_ADJUST off: unproven ≤ 0.10; proven auto-raise only if MIROFISH_AUTO_RAISE
    (default False → stays 0.10; True → proven cap 0.15).
    SELF_ADJUST on: AUTO_RAISE inside band (floor 0.05 / top 0.25 / ceiling 0.30).
    Any error fails closed to the unproven cap. Never opens venue writers.
    """
    try:
        import mirofish_accuracy_ledger as _mal
        return float(_mal.effective_weight())
    except Exception:
        return min(float(MIROFISH_WEIGHT), float(MIROFISH_UNPROVEN_CAP))


def fusion_weights_for(asset_class="equity"):
    """Resolved kernel-first weights for one asset class. Sum + remainder = 1."""
    crypto = str(asset_class or "").strip().lower() == "crypto"
    kernel = FUSION_WEIGHTS["kernel_crypto"] if crypto else FUSION_WEIGHTS["kernel_equity"]
    intel = FUSION_WEIGHTS["intel_crypto"] if crypto else FUSION_WEIGHTS["intel_equity"]
    mf = effective_mirofish_weight()
    ml = FUSION_WEIGHTS["ml"]
    remainder = 1.0 - kernel - mf - ml - intel
    return {
        "kernel": kernel,
        "mirofish": mf,
        "ml": ml,
        "intel": intel,
        "remainder": remainder,
    }


def _track_record_mult():
    """Track-record scalar from the REAL fee-inclusive settlement ledger (forward_test_ledger,
    the single source — NOT the paper-marked brain_log): KELLY_UNPROVEN_MULT (0.5) until there
    are KELLY_MIN_PROVEN_TRADES real settled trades, then 0.3 (losing) .. 1.5 (proven) by real
    hit-rate. A thin/empty/errored record is UNPROVEN, not neutral — conservative by default
    (Burton: earn the aggression; no fake/paper record opens the cap). Any error returns 0.5."""
    try:
        import sys as _sys
        _here = _os.path.dirname(_os.path.abspath(__file__))
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        import forward_test_ledger as _ftl
        tr = _ftl.real_track_record()
        if tr["n"] < KELLY_MIN_PROVEN_TRADES:
            return KELLY_UNPROVEN_MULT               # unproven — not enough real settled evidence
        b = tr["recent_20"] * 0.7 + tr["hit_rate"] * 0.3
        if b >= 65: return 1.5
        if b >= 60: return 1.3
        if b >= 55: return 1.1
        if b >= 50: return 0.8                        # breakeven — still below neutral; earn more
        if b <= 40: return 0.3                        # real losing record — throttle hard
        return 0.6                                    # 40 < b < 50: sub-breakeven
    except Exception:
        return KELLY_UNPROVEN_MULT

def _risk_factor():
    """Live risk scalar from latest_etherion_risk.json risk_level: NORMAL 1.0 .. HALT 0.0.
    Defensive — any error returns 1.0 (neutral)."""
    try:
        lvl = str(_json.load(open(_os.path.join(_MODELS, "latest_etherion_risk.json"))).get("risk_level", "NORMAL")).upper()
        return {"NORMAL": 1.0, "CAUTION": 0.6, "ELEVATED": 0.5, "WARNING": 0.25, "HALT": 0.0, "DANGER": 0.0}.get(lvl, 1.0)
    except Exception:
        return 1.0

def category_track_record_mult(category):
    """Per-strategy Kelly scalar from fee-true category_stats (not the global mix).

    Thin (< KELLY_MIN_PROVEN_TRADES real settled in-window) → KELLY_UNPROVEN_MULT (0.5).
    Loser (net < 0 or hit-rate ≤ 40) → 0.3.
    Winner earns toward 1.0 (never above 1.0; KELLY_FUSE still binds the cap).
    Paper/sandbox/unreconciled/backfill never appear in category_stats, so they
    cannot lift a category. A hot fake/other category cannot lift this one.
    Alias labels (SPORTS_TONIGHT) fold into the canonical bucket.
    Any error → unproven 0.5.
    """
    if not category:
        return KELLY_UNPROVEN_MULT
    try:
        import sys as _sys
        _here = _os.path.dirname(_os.path.abspath(__file__))
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        import forward_test_ledger as _ftl
        cat = _ftl.canonical_category(category)
        s = _ftl.category_stats().get(cat)
        if not s or int(s.get("n") or 0) < KELLY_MIN_PROVEN_TRADES:
            return KELLY_UNPROVEN_MULT
        n = int(s["n"])
        wins = int(s.get("wins") or 0)
        net = float(s.get("net") or 0)
        hit = (wins / n) * 100.0 if n else 0.0
        if net < 0 or hit <= 40:
            return 0.3
        if hit >= 65 and net > 0:
            return 1.0
        if hit >= 60:
            return 0.9
        if hit >= 55:
            return 0.8
        if hit >= 50:
            return 0.7
        return KELLY_UNPROVEN_MULT
    except Exception:
        return KELLY_UNPROVEN_MULT


def dynamic_per_trade_cap(regime="SIDEWAYS", track_record_mult=None, risk_factor=None,
                          category=None):
    """Per-trade position ceiling that BREATHES with regime + live track-record + live Etherion risk:
        cap = KELLY_BASE_CAP * regime_factor * track_record * risk, fused at KELLY_FUSE.
    track_record_mult / risk_factor default to None -> AUTO-SOURCED from the canonical state files
    (brain_log.json recent hit-rate; latest_etherion_risk.json risk_level). Pass values to override.
    category: when set (and track_record_mult is None) uses category_track_record_mult so a
    hot grave/fake book cannot lift a different strategy. Paper/unreconciled/backfill cannot lift.
    - regime: favorable regime raises the ceiling so the regime-boosted Kelly fraction isn't clipped.
    Returns a capital fraction in [0, KELLY_FUSE]."""
    rf = KELLY_REGIME_CAP_FACTOR.get(regime, 1.0)
    if track_record_mult is not None:
        tr = float(track_record_mult)
    elif category:
        tr = category_track_record_mult(category)
    else:
        tr = _track_record_mult()
    risk = _risk_factor() if risk_factor is None else float(risk_factor)
    cap = KELLY_BASE_CAP * rf * max(0.0, tr) * max(0.0, risk)
    return max(0.0, min(cap, KELLY_FUSE))

# ── KNOWN DIVERGENCES — do NOT unify without a backtest (behavioral pass) ──
# Listed so the disagreement is visible in ONE place; the detector tracks them. When the
# behavioral pass picks a winner (with before/after backtests), move it up to the block
# above and wire every consumer to it.
#
# Kelly safety fraction:  kelly_sizing.py = 0.5 (half) | quant_engine.py = 0.25 (quarter)
#                         | sports_engine.py = 0.5 | master_allocator.py = ad-hoc.
# Kalshi fee:             fee_engine.STATIC_FEES is canonical (brain fee gate uses it).
#                         kalshi_executor 0.02 flat / quant_engine 0.007 still diverge — do not
#                         unify without a fee-true backtest. weather_arb live path is dead.
# Signal-fusion weights:  unified (Slice 8). Live = FUSION_WEIGHTS → unified_brain.stage_fusion.
#                         signal_fusion.WEIGHTS is NON-LIVE (LIVE_PATH=False).

# ── Nautilus sidecar seam (Phase 0) — exclusive writer lock ──────────────
# Default deny. Coding agents never flip these. Valedia runtime is the only
# process allowed to enable NT dispatch / live fills. Dual-write is -EV.
class ExclusiveWriterError(ValueError):
    """Raised when a claimant is not the exclusive live writer for a venue."""


NAUTILUS_ENABLED = False  # sidecar dispatch off until Valedia runtime enables
NAUTILUS_LIVE = False     # live ledger source via NT sink; default deny
NAUTILUS_SHADOW_COINBASE = True  # allocator + brain + rebalancer Coinbase: never-dispatchable Intents
# Slice 10: MiroFish/news/scanner/hot_list/arb → gated Intents. Always shadow.
# This lane never sets dispatchable. Writer stays valedia. No --live.
INTEL_SHADOW_INTENTS = True
# Slice 11: HL shorts + Polymarket arb shadow Intents. Never-dispatchable.
# This lane never sets dispatchable. No --live. No writer flip.
NAUTILUS_SHADOW_VENUES = True
# Slice 12: nautilus_shadow_parity.py — 7d compare.ok report. No flag flips.
# H6: nautilus_shadow_events.py — shadow OMS states + CommandFailure. NAUTILUS_LIVE stays False.

# SUBSUME NT cutover preference (later, Valedia-gated). Not a writer flip.
# Coinbase first → Hyperliquid shorts/futures → Polymarket arb → Kalshi last.
# Kraken stays read-only. Mercury never. Alpaca stays Valedia until an adapter.
VENUE_NT_CUTOVER_ORDER = ("coinbase", "hyperliquid", "polymarket", "kalshi")

VENUE_WRITER = {
    "kalshi": "valedia",
    "coinbase": "valedia",
    "alpaca": "valedia",
    "kraken": "none",
    "polymarket": "none",
    "hyperliquid": "none",
}


def venue_writer(venue):
    """Exclusive live writer for a venue: valedia | nautilus | none."""
    return VENUE_WRITER.get(str(venue or "").strip().lower(), "none")


def assert_exclusive_writer(venue, claimant):
    """Lock hook: claimant may be the live writer only if it owns the venue."""
    owner = venue_writer(venue)
    want = str(claimant or "").strip().lower()
    if owner != want:
        raise ExclusiveWriterError(
            f"venue {venue!r} writer is {owner!r}, not {want!r}"
        )
    return owner


def nautilus_may_dispatch(venue):
    return bool(NAUTILUS_ENABLED) and venue_writer(venue) == "nautilus"


def nautilus_live_fills_allowed(venue=None):
    """Default deny. source=live requires explicit Valedia NT flags + writer lock."""
    if not NAUTILUS_ENABLED or not NAUTILUS_LIVE:
        return False
    if venue is not None and venue_writer(venue) != "nautilus":
        return False
    return True
