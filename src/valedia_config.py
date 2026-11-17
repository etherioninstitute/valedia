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

# Runtime identity (0.75.0 = unified valedia_trader package, quant engine, security hardening;
# 0.74.1 = Kalshi dollars quote helper for weather morning-go).
# Prior 0.74.0 = weather morning + live preflight (Jev retired; Nimble 2 notes).
# Prior 0.73.0 = weather scrape lookup, shadow only; live exec stays fenced.
# Prior 0.72.0 = oil/weather RESEARCH/SHADOW desks.
# Prior 0.70.0 = Kalshi CONVICTION INFORMED-before-create (Jev retired; Nimble 2 notes).
# Prior 0.68.0 = BB-Terminal INTEL display on OpenTerminal-Valedia.
# Prior 0.67.0 = eToro research + CopyTrader paper/shadow.
# Prior 0.66.0 = Vaughan Poly↔Kalshi paper/shadow arb.
# Prior 0.65.0 = MiroFish intel archive: PASS archives then atomic live replace.
# Prior 0.64.0 = CE-3 fee-true paper decision log + single-flight tick.
# Prior 0.63.0 = CE-2 crypto paper MACD + Qwen tick assist; Ornith stays the gate.
# Prior 0.62.0 = CE-1 crypto paper rules + Ornith fail-closed gate.
# Prior 0.61.0 = MiroFish split: Ollama 8B sync, Bionic Ornith report.
# Prior 0.60.0 = weather RESEARCH/SHADOW scorer; live fence stays.
# Prior 0.59.0 = session_clock + resolve_clock + fence_gate.
# Prior 0.58.0 = PHASE-1 Kalshi create quality. Prior 0.57.0 = INFORMED-BEFORE-CREATE.
# Not a substitute for MANUSCRIPT_VERSION.
VALEDIA_VERSION = "0.75.0"

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
CRYPTO_PAPER_MODE   = "PAPER/SHADOW"  # would-have ledger; never live/wallets.
CRYPTO_PAPER_SAFETY = "PAPER/SHADOW+ORNITH"  # CE-1 gate. CE-2 tick cannot replace it.

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
# Scanner may tag KXWTI/KXOIL as NO_HARVEST/CONVICTION_*; name-only fence misses those.
LIVE_EXEC_FENCED_SERIES = frozenset({"KXWTI", "KXOIL"})

# MicroScalper / weather live buys. This lane never sets True.
SCALPER_LIVE = False
WEATHER_LIVE = False
# US DCM writes. Env POLYMARKET_US_LIVE=1 is a Burton/Valedia flip.
POLYMARKET_US_LIVE = False  # this lane never sets True
# POLY-LOOP paper desk (read-only snapshot). Execution still POLYMARKET_US_LIVE.
POLY_LOOP_EDGE_PTS = 8.0          # |FV-mid| percentage points to flag
POLY_LOOP_MAX_OPEN = 5            # max open paper positions
POLY_LOOP_DD_STOP = 0.40          # peak drawdown halt; no auto category flip
POLY_LOOP_DD_STOP_ARMED = True    # stop proposing when DD hits stop
# Create-path quality bar. Hard on. Env cannot disable. Halt off is not enough.
INFORMED_BEFORE_CREATE = True
# tag_prior-alone is never a Poly create. Env cannot enable. LIVE stays off.
POLY_TAG_PRIOR_CREATE = False

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

# H8 fortress intel fallback. DEFAULT OFF. Failure-gated after 403/429/empty/WAF
# only. Engine is PARKED on this Mac (no Colima/Docker/CDP). This lane never
# sets True. Env FORTRESS_INTEL_FALLBACK=1 is a Burton/Valedia flip.
FORTRESS_INTEL_FALLBACK = False

# Rebalancer auto-exec. Default OFF. Shadow/propose only until Burton flips:
#   export REBALANCER_AUTOEXEC=1
# AND passes live_flag gates. This lane never sets True. NT never writes.
REBALANCER_AUTOEXEC = False

# TREASURY-1 — report-only cash floors. Never ACH/wire/card, never withdraw.
# Optional later auto top-up from one treasury bank. This lane never sets True.
# First human fund is still required even if the flag is later flipped.
VALEDIA_TREASURY_AUTO = False
# Master kill-switch. Default unset/False = normal. Env=1 or halt file arms.
# Manual OFF: env=0/false/off/unset+file cleared. This lane never sets True.
# Docs: recommendations/KILL-SWITCH.md. Gate: require_not_halted().
VALEDIA_TRADING_HALT = False

# KALSHI-FEE-MAP blocking gate. Default OFF (annotate only). Env=1 drops
# unknown + taker_taxed (multiplier > 1) at select/size. Consult always
# fail-closed on unknown fields. This lane never sets True.
KALSHI_FEE_MAP_GATE = False
# PROFIT-1. All default OFF (annotate/shadow). This lane never sets True.
# Env=1: skip suggest/rest/size when fee-true net EV <= 0.
KALSHI_EV_PREFLIGHT_GATE = False
# Env=1: rest on maker_viable; skip taker unless EV >= maker_net + hurdle.
# Off = soft annotate only (existing taker fallthrough).
KALSHI_MAKER_FIRST = False
# Env=1: hard-skip series/strategy with adverse mid-drift score. Off = persist only.
KALSHI_FILL_LEDGER_DEMOTE = False
TREASURY_FLOOR_POLYMARKET_US = 25.0  # known ~$30.55; WARN below 25
TREASURY_FLOOR_KALSHI = 25.0          # stub until Burton sets
TREASURY_FLOOR_COINBASE = 20.0
TREASURY_FLOOR_ALPACA = 10.0
TREASURY_FLOORS = {
    "polymarket_us": TREASURY_FLOOR_POLYMARKET_US,
    "kalshi": TREASURY_FLOOR_KALSHI,
    "coinbase": TREASURY_FLOOR_COINBASE,
    "alpaca": TREASURY_FLOOR_ALPACA,
    "hyperliquid": None,  # writer none; document only
    "kraken": None,
}
TREASURY_ALERT_WEBHOOK = ""  # unused stub; treasury_status never POSTs it

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

# Durable wire for Burton flips that live in ~/.hermes/secrets/trading.env (same file
# cron_runner_mac.sh sources). LaunchAgents do not inherit that file; any
# process that imports this module picks flags up via setdefault so a bare
# `python3 .../all_knowing_movers_scan.py` sees VALEDIA_SYSTEMONE without a manual
# export. Process env always wins. Opt out: OPENCLAW_SKIP_ENV=1 (unit tests).
# Override path: OPENCLAW_ENV_FILE. This lane never sets a flip True in git.
_OPENCLAW_ENV_LOADED = False
_OPENCLAW_ENV_DEFAULT = _os.path.expanduser("~/.hermes/secrets/trading.env")


def ensure_openclaw_env(path=None):
    """Load OpenClaw .env into os.environ via setdefault. Idempotent.

    Returns the path loaded (or None if skipped/missing). Does not override
    keys already present in the process environment.
    """
    return False  # Recovery never reads inherited home/env files.
    global _OPENCLAW_ENV_LOADED
    if _OPENCLAW_ENV_LOADED:
        return _os.environ.get("_OPENCLAW_ENV_PATH") or None
    _OPENCLAW_ENV_LOADED = True
    skip = str(_os.environ.get("OPENCLAW_SKIP_ENV", "")).strip().lower()
    if skip in ("1", "true", "yes", "on"):
        return None
    path = (path or _os.environ.get("VALEDIA_ENV_FILE") or _os.environ.get("OPENCLAW_ENV_FILE")
            or _OPENCLAW_ENV_DEFAULT)
    path = _os.path.expanduser(path)
    if not _os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if not k:
                    continue
                _os.environ.setdefault(
                    k, v.strip().strip('"').strip("'")
                )
    except OSError:
        return None
    _os.environ["_OPENCLAW_ENV_PATH"] = path
    return path


def _env_flag(name, default):
    ensure_openclaw_env()
    env = _os.environ.get(name)
    if env is not None and str(env).strip() != "":
        return str(env).strip().lower() in ("1", "true", "yes", "on")
    return bool(default)


def _src(name):
    ensure_openclaw_env()
    env = _os.environ.get(name)
    if env is not None and str(env).strip() != "":
        return "env"
    return "const"


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


def fortress_intel_fallback_enabled():
    """True only if Burton flipped FORTRESS_INTEL_FALLBACK. Default False.

    Does not start CDP, Docker, or Colima. Engine stays PARKED/fail-closed
    until a later Linux sidecar. Env FORTRESS_INTEL_FALLBACK=1 wins.
    This lane never sets the constant True.
    """
    return _env_flag("FORTRESS_INTEL_FALLBACK", FORTRESS_INTEL_FALLBACK)


def self_adjust_enabled():
    """True only if Burton flipped VALEDIA_SELF_ADJUST. Default False.

    Wakes MiroFish AUTO_RAISE *inside the band* and documents that pause
    re-open / neural enrich may run. Does not open VENUE_WRITER, SPOT GET,
    NAUTILUS_LIVE, or AUTOEXEC. Env VALEDIA_SELF_ADJUST=1 wins over the constant.
    This lane never sets the constant True.
    """
    return _env_flag("VALEDIA_SELF_ADJUST", VALEDIA_SELF_ADJUST)


def systemone_enabled():
    """Same switch as systemone_hotpath_client.enabled(): env VALEDIA_SYSTEMONE == "1".

    The shared Elephant-1 instance on the loopback LM Studio (list-pinned) is the only decision
    model. Off: zero calls.
    """
    return _os.environ.get("VALEDIA_SYSTEMONE") == "1"


def polymarket_us_live_enabled():
    """True only if Burton flipped POLYMARKET_US_LIVE. Default False.

    This lane never sets True. Env POLYMARKET_US_LIVE=1 wins.
    """
    return _env_flag("POLYMARKET_US_LIVE", POLYMARKET_US_LIVE)


def rebalancer_autoexec_enabled():
    """True only if Burton flipped REBALANCER_AUTOEXEC. Default False.

    Necessary but not sufficient: rebalancer still needs live_flag gates and
    venue_writer==valedia. NT cannot dispatch. This lane never sets True.
    """
    return _env_flag("REBALANCER_AUTOEXEC", REBALANCER_AUTOEXEC)


def treasury_auto_enabled():
    """True only if Burton flipped VALEDIA_TREASURY_AUTO. Default False.

    Necessary but not sufficient: treasury_status still refuses to move
    money. This lane never sets True. Env VALEDIA_TREASURY_AUTO=1 wins.
    """
    return _env_flag("VALEDIA_TREASURY_AUTO", VALEDIA_TREASURY_AUTO)


def kalshi_fee_map_gate_enabled():
    """True only if Burton flipped KALSHI_FEE_MAP_GATE. Default False.

    Off: consult annotates, never drops. On: drop unknown + taker_taxed.
    This lane never sets True.
    """
    return _env_flag("KALSHI_FEE_MAP_GATE", KALSHI_FEE_MAP_GATE)


def kalshi_ev_preflight_gate_enabled():
    """True only if Burton flipped KALSHI_EV_PREFLIGHT_GATE. Default False.

    Off: annotate net EV. On: skip suggest/rest/size when net <= 0.
    This lane never sets True.
    """
    return _env_flag("KALSHI_EV_PREFLIGHT_GATE", KALSHI_EV_PREFLIGHT_GATE)


def kalshi_maker_first_enabled():
    """True only if Burton flipped KALSHI_MAKER_FIRST. Default False.

    Off: annotate preferred role. On: skip taker unless hurdle clears.
    This lane never sets True.
    """
    return _env_flag("KALSHI_MAKER_FIRST", KALSHI_MAKER_FIRST)


def kalshi_fill_ledger_demote_enabled():
    """True only if Burton flipped KALSHI_FILL_LEDGER_DEMOTE. Default False.

    Off: persist mid-drift scores. On: skip high-score series/strategy.
    This lane never sets True.
    """
    return _env_flag("KALSHI_FILL_LEDGER_DEMOTE", KALSHI_FILL_LEDGER_DEMOTE)


class HaltedError(RuntimeError):
    """New venue orders refused while VALEDIA_TRADING_HALT is on."""


def halt_state_path():
    """JSON arm/disarm file. Env VALEDIA_TRADING_HALT_FILE overrides."""
    return _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.realpath(__file__))), "state", "halt.json")
    env = _os.environ.get("VALEDIA_TRADING_HALT_FILE")
    if env is not None and str(env).strip() != "":
        return str(env).strip()
    return _os.path.join(_ROOT, "models", "valedia_trading_halt.json")


def halt_ping_path():
    """Status file for Valedia exit-ping / Signal. Never sent from here."""
    return _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.realpath(__file__))), "state", "halt-ping.json")
    env = _os.environ.get("VALEDIA_TRADING_HALT_PING")
    if env is not None and str(env).strip() != "":
        return str(env).strip()
    return _os.path.join(_ROOT, "recommendations", "KILL-SWITCH-STATUS.md")


def _halt_file_state():
    """Read new recovery halt state; existing ambiguous/invalid state is armed."""
    path = halt_state_path()
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate halt-state JSON key")
            result[key] = value
        return result
    try:
        with open(path) as f:
            doc = _json.load(f, object_pairs_hook=unique_object)
    except FileNotFoundError:
        if not _os.path.lexists(path):
            return None  # Genuinely absent bootstrap; never a bad existing object.
        return {"halted": True, "reason": "existing halt state unreadable", "corrupt": True}
    except Exception:
        return {"halted": True, "reason": "halt state unreadable/ambiguous", "corrupt": True}
    if not isinstance(doc, dict) or "halted" not in doc or type(doc["halted"]) is not bool:
        return {"halted": True, "reason": "invalid halt-state boolean schema", "corrupt": True}
    return doc


def trading_halted():
    """True if new venue orders must refuse.

    Independent environment and validated persistent halt sources combine by OR.
    An invalid existing persistent document is armed; false environment cannot clear it.
    """
    env = str(_os.environ.get("VALEDIA_TRADING_HALT", "")).strip().lower()
    env_halt = bool(env) and env not in {"0", "false", "no", "off"}
    doc = _halt_file_state()
    return env_halt or (doc["halted"] if doc is not None else bool(VALEDIA_TRADING_HALT))
    env = _os.environ.get("VALEDIA_TRADING_HALT")
    if env is not None and str(env).strip() != "":
        return str(env).strip().lower() in ("1", "true", "yes", "on")
    doc = _halt_file_state()
    if doc is not None:
        return bool(doc.get("halted"))
    return bool(VALEDIA_TRADING_HALT)


def halt_reason():
    """Human reason for the current halt (empty if not halted)."""
    if not trading_halted():
        return ""
    env = _os.environ.get("VALEDIA_TRADING_HALT")
    if env is not None and str(env).strip() != "":
        return "VALEDIA_TRADING_HALT env"
    doc = _halt_file_state() or {}
    why = str(doc.get("reason") or "").strip()
    if why:
        return why
    if VALEDIA_TRADING_HALT:
        return "VALEDIA_TRADING_HALT config"
    return "VALEDIA_TRADING_HALT"


def require_not_halted(action="create_order"):
    """Central write gate. Raise HaltedError if halted. Reads stay allowed."""
    if trading_halted():
        why = halt_reason() or "VALEDIA_TRADING_HALT"
        raise HaltedError(f"{action} refused ({why})")
    return False


def write_halt_state(halted, reason="", source="manual"):
    """Arm/disarm the halt file. No Signal send. No auto-spend."""
    from datetime import datetime, timezone
    if type(halted) is not bool:
        raise TypeError("halt state write requires an exact boolean")
    doc = {
        "halted": halted,
        "reason": str(reason or ("VALEDIA_TRADING_HALT on" if halted else "cleared")),
        "source": str(source or "manual"),
        "ts": datetime.now(timezone.utc).isoformat(),
        "valedia_version": VALEDIA_VERSION,
        "ping": {
            "send": False,
            "channel": "signal",
            "audience": "valedia-exit-ping",
            "text": (
                f"VALEDIA_TRADING_HALT halted={str(halted).lower()} "
                f"reason={reason or '-'} (not sent; Valedia/Grok Bot to deliver)"
            ),
        },
    }
    path = halt_state_path()
    _os.makedirs(_os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        _json.dump(doc, f, indent=2)
        f.write("\n")
    _write_halt_ping(doc)
    return doc


def _write_halt_ping(doc):
    path = halt_ping_path()
    _os.makedirs(_os.path.dirname(path) or ".", exist_ok=True)
    ping = doc.get("ping") or {}
    lines = [
        "# KILL-SWITCH status (not sent)",
        "",
        f"- halted: `{doc.get('halted')}`",
        f"- reason: {doc.get('reason') or '-'}",
        f"- source: {doc.get('source') or '-'}",
        f"- ts: {doc.get('ts') or '-'}",
        f"- version: {doc.get('valedia_version') or VALEDIA_VERSION}",
        f"- ping.send: `{ping.get('send', False)}` (Valedia/Grok Bot delivers)",
        f"- ping.text: {ping.get('text') or '-'}",
        "",
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path


def record_halt_event(reason, source="observe", arm=False):
    """Return a read-only observation; explicit arm=True may persist a halt.

    Observation must never resume, repair, or overwrite persistent halt state.
    Callers may store the returned event separately; no ping or state is written.
    """
    if type(arm) is not bool:
        raise TypeError("arm must be an explicit boolean")
    if arm:
        return write_halt_state(True, reason=reason, source=source)
    from datetime import datetime, timezone
    return {
        "halted": trading_halted(),
        "reason": str(reason or "halt observation"),
        "source": str(source or "observe"),
        "ts": datetime.now(timezone.utc).isoformat(),
        "valedia_version": VALEDIA_VERSION,
        "observation_only": True,
        "state_changed": False,
        "ping": {"send": False},
    }


def panel_looks_sick(panel):
    """Obvious cockpit/panel failure. Uncertain → True (fail-closed)."""
    if not isinstance(panel, dict):
        return True
    if panel.get("error") or panel.get("ok") is False:
        return True
    if not panel.get("valedia_version"):
        return True
    return False


def note_writer_exception(exc, source="writer", arm=False):
    """Report a writer exception. Default report-only (arm=False)."""
    return record_halt_event(f"writer exception: {exc}", source=source, arm=arm)


def treasury_floors():
    """Copy of TREASURY_FLOORS (None = no floor / document-only venue)."""
    return dict(TREASURY_FLOORS)


def treasury_alert_webhook():
    """Unused stub. treasury_status never POSTs this URL."""
    env = _os.environ.get("TREASURY_ALERT_WEBHOOK")
    if env is not None and str(env).strip() != "":
        return str(env).strip()
    return str(TREASURY_ALERT_WEBHOOK or "")


def scalper_live_enabled():
    """MicroScalper live buys. Hard-off. Env cannot enable — dead path."""
    return False


_WEATHER_PREFLIGHT_DEPTH = 0


def weather_live_enabled():
    """WEATHER_ARB orders. The constant stays False.

    Env WEATHER_LIVE=1 is honored only when weather_live_preflight is PASS.
    Unset env stays off. Oil is not this switch. Valedia morning go decides WEATHER_LIVE (PASS→1, else→0); Grok Bot does not flip it.
    """
    global _WEATHER_PREFLIGHT_DEPTH
    if _WEATHER_PREFLIGHT_DEPTH:
        return False
    if not _env_flag("WEATHER_LIVE", False):
        return False
    _WEATHER_PREFLIGHT_DEPTH += 1
    try:
        import weather_live_preflight as wlp
        body = wlp.run()
    except Exception:
        return False
    finally:
        _WEATHER_PREFLIGHT_DEPTH -= 1
    return body.get("status") == "PASS" and body.get("weather_live_flip_allowed") is True


def ticker_family_fenced(ticker):
    """True if ticker/series is an oil bracket (KXWTI / KXOIL / WTI). Not an unfence."""
    t = str(ticker or "").strip().upper()
    if not t:
        return False
    series = t.split("-", 1)[0]
    for prefix in LIVE_EXEC_FENCED_SERIES:
        if series == prefix or series.startswith(prefix):
            return True
    return series == "WTI" or series.startswith("WTI")


def is_killed_strategy(name, ticker=None):
    """Live-exec fence. True means do not place orders. Not a learning grave."""
    return live_exec_fenced(name, ticker=ticker)


def live_exec_fenced(name, ticker=None):
    """True if this strategy or ticker family must not place live orders.

    Independent of learning pause. Unpause / TTL expiry re-enters evaluation
    and Kelly/category gating only. This lane never lifts the fence.
    Name fence: WEATHER_ARB / OIL_BRACKET. Series fence: KXWTI / KXOIL / WTI
    even when the scanner labeled the pick NO_HARVEST.
    """
    n = str(name or "").strip().upper()
    if n in LIVE_EXEC_FENCED:
        return True
    if ticker_family_fenced(ticker):
        return True
    return ticker_family_fenced(n)


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
    from recovery_state import sizing_context
    ctx = sizing_context()
    if ctx is not None: return ctx["track"]
    return 0.0  # No implicit historical ledger access in recovery.
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

def _risk_factor(scope="BOOK"):
    """Canonical ERE v3 scalar; faults forbid new risk. No label remapping."""
    from recovery_state import sizing_context
    ctx = sizing_context()
    if ctx is not None: return ctx["risk"]
    return 0.0  # Explicit prepared risk evidence required.
    try:
        from ere_runtime import read_gate
        return float(read_gate(scope, models_dir=_MODELS)["multiplier"])
    except Exception:
        import logging
        logging.getLogger("ere").exception("Kelly risk reader CLOSED")
        return 0.0

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
    (settled track record; ere_state.json via read_gate). An explicit risk value can only tighten.
    category: when set (and track_record_mult is None) uses category_track_record_mult so a
    hot grave/fake book cannot lift a different strategy. Paper/unreconciled/backfill cannot lift.
    - regime: favorable regime raises the ceiling so the regime-boosted Kelly fraction isn't clipped.
    Returns a capital fraction in [0, KELLY_FUSE]."""
    try:
        rf = KELLY_REGIME_CAP_FACTOR.get(regime, 1.0)
        if track_record_mult is not None:
            tr = float(track_record_mult)
        elif category:
            tr = category_track_record_mult(category)
        else:
            tr = _track_record_mult()
    except (ValueError, TypeError, OverflowError):
        return 0.0
    # Explicit per-asset risk never bypasses the canonical book gate. Use min,
    # NOT a product, to avoid applying a shared gate twice.
    import math
    try:
        canonical = float(_risk_factor())
        risk = min(canonical, float(risk_factor)) if risk_factor is not None else canonical
    except (ValueError, TypeError, OverflowError):
        return 0.0
    cap = KELLY_BASE_CAP * rf * max(0.0, tr) * max(0.0, risk)
    if not math.isfinite(cap):
        return 0.0
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
    "polymarket": "valedia",  # TODO(Burton): "valedia" was committed in snapshot a49cece although the old comment said working-tree only. Burton acknowledged 2026-10-01 and kept it. test_h8, test_session_clocks, test_controls_readout still assert "none"; revisit when Burton decides (flip back or update those asserts).
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


def controls_snapshot():
    """Read-only dict of every Valedia control. Never flips a gate.

    Returns {version, sha, branch, dirty, items:[{name, value, source, category, notes}]}.
    source is env | const | file (halt file only).
    """
    ident = version_identity()
    halt_env = _os.environ.get("VALEDIA_TRADING_HALT")
    if halt_env is not None and str(halt_env).strip() != "":
        halt_src = "env"
    elif _halt_file_state() is not None:
        halt_src = "file"
    else:
        halt_src = "const"
    try:
        import kalshi_fee_map as _kfm
        _art = _kfm.artifact_status()
        _fee_refresh = bool(_art.get("healthy"))
        _fee_note = "mode=%s as_of=%s" % (_art.get("mode"), _art.get("as_of"))
    except Exception:
        _fee_refresh, _fee_note = False, "unreadable"
    rows = [
        ("VALEDIA_VERSION", ident["version"], "const", "identity",
         "runtime identity"),
        ("GIT_SHA", ident["sha"] + ("*" if ident["dirty"] else ""), "const",
         "identity", ident["branch"]),
        ("VALEDIA_TRADING_HALT", trading_halted(), halt_src, "halt",
         halt_reason() or "new orders refuse when on"),
        ("KALSHI_FEE_MAP_GATE", kalshi_fee_map_gate_enabled(),
         _src("KALSHI_FEE_MAP_GATE"), "kalshi",
         "off=annotate; on=drop unknown+taker_taxed"),
        ("KALSHI_FEE_MAP_REFRESH", _fee_refresh, "file", "kalshi", _fee_note),
        ("KALSHI_EV_PREFLIGHT_GATE", kalshi_ev_preflight_gate_enabled(),
         _src("KALSHI_EV_PREFLIGHT_GATE"), "kalshi",
         "off=annotate; on=skip net EV<=0"),
        ("KALSHI_MAKER_FIRST", kalshi_maker_first_enabled(),
         _src("KALSHI_MAKER_FIRST"), "kalshi",
         "off=annotate; on=skip taker unless hurdle"),
        ("KALSHI_FILL_LEDGER_DEMOTE", kalshi_fill_ledger_demote_enabled(),
         _src("KALSHI_FILL_LEDGER_DEMOTE"), "kalshi",
         "env gates annotate-skip only. Create refuses on ledger evidence "
         "(n>=5, score>=0.03) even when this is off. Empty or missing file "
         "does not refuse and is not a zero score."),
        ("INFORMED_BEFORE_CREATE", True, "const", "kalshi",
         "fail-closed before create. Env cannot disable. Needs fee-true EV, FV, "
         "book, depth, size, ledger and clock checks. Halt off is necessary, "
         "not sufficient."),
        ("POLY_TAG_PRIOR_CREATE", False, "const", "poly",
         "tag_prior-alone is never create-eligible. Env cannot enable. "
         "US writes stay blocked while POLYMARKET_US_LIVE is off."),
        ("VALEDIA_SYSTEMONE", systemone_enabled(), _src("VALEDIA_SYSTEMONE"), "autonomy",
         "the shared Elephant-1 instance is the only decision model (Jev retired). off=zero model "
         "calls; on=loopback Elephant-1 decide + notes on CONVICTION, weather, MiroFish, movers; "
         "notes never gate or size; fail-open; not OMS"),
        ("VALEDIA_SELF_ADJUST", self_adjust_enabled(),
         _src("VALEDIA_SELF_ADJUST"), "autonomy",
         "wakes MiroFish AUTO_RAISE in-band only"),
        ("MIROFISH_AUTO_RAISE", bool(MIROFISH_AUTO_RAISE), "const", "autonomy",
         "stays False unless SELF_ADJUST allows"),
        ("POLYMARKET_US_LIVE", polymarket_us_live_enabled(),
         _src("POLYMARKET_US_LIVE"), "poly", "US DCM create/cancel"),
        ("POLYMARKET_US_HTTP", _env_flag("POLYMARKET_US_HTTP", False),
         _src("POLYMARKET_US_HTTP"), "poly",
         "read HTTP only. Not a writer. Env on does not enable create."),
        ("POLY_LOOP_EDGE_PTS", POLY_LOOP_EDGE_PTS, "const", "poly",
         "|FV-mid| pts to flag; POLY-LOOP paper desk"),
        ("POLY_LOOP_MAX_OPEN", POLY_LOOP_MAX_OPEN, "const", "poly",
         "max open paper positions"),
        ("POLY_LOOP_DD_STOP", POLY_LOOP_DD_STOP, "const", "poly",
         "peak drawdown halt; no auto category flip"),
        ("POLY_LOOP_DD_STOP_ARMED", bool(POLY_LOOP_DD_STOP_ARMED), "const",
         "poly", "read-only; stop proposing when DD hits stop"),
        ("NAUTILUS_LIVE", bool(NAUTILUS_LIVE), "const", "nautilus",
         "live fills via NT sink"),
        ("NAUTILUS_ENABLED", bool(NAUTILUS_ENABLED), "const", "nautilus",
         "sidecar dispatch"),
        ("SCALPER_LIVE", scalper_live_enabled(), "const", "fence",
         "hard-off; env cannot enable"),
        ("WEATHER_LIVE", weather_live_enabled(), _src("WEATHER_LIVE"), "fence",
         "env on only after weather_live_preflight PASS; default off; oil stays fenced"),
        ("LIVE_EXEC_FENCED", ",".join(sorted(LIVE_EXEC_FENCED)), "const",
         "fence", "not a learning grave; unpause != live-buy"),
        ("LIVE_EXEC_FENCED_SERIES", ",".join(sorted(LIVE_EXEC_FENCED_SERIES)),
         "const", "fence", "oil ticker family even as NO_HARVEST"),
        ("CRYPTO_PAPER_MODE", CRYPTO_PAPER_MODE, "const", "crypto",
         "would-have fee-true PnL; no wallets/orders"),
        ("CRYPTO_PAPER_SAFETY", CRYPTO_PAPER_SAFETY, "const", "crypto",
         "CE-2 tick still Ornith fail-closed; Qwen assist cannot fill; paper only"),
    ]
    try:
        import crypto_paper as _cp
        _tick = _cp.tick_status()
    except Exception:
        _tick = {"last_tick_age_s": "none", "last_ornith": "none", "stale": True}
    rows.extend([
        ("CRYPTO_PAPER_TICK_AGE_S", _tick.get("last_tick_age_s"), "file", "crypto",
         "seconds since last paper tick; none if the decision log is empty"),
        ("CRYPTO_PAPER_LAST_ORNITH", _tick.get("last_ornith") or "none", "file", "crypto",
         "last tick reason (pass, ornith_refuse, ornith_down, duplicate_tick, tick_busy)"),
        ("CRYPTO_PAPER_TICK_STALE", bool(_tick.get("stale")), "file", "crypto",
         "true when the last paper tick is missing or older than 2h"),
    ])
    try:
        import vaughan_arb_paper as _va
        _va_tick = _va.tick_status()
        _va_mode = _va.MODE
    except Exception:
        _va_tick = {"last_tick_age_s": "none", "last_ornith": "none", "stale": True}
        _va_mode = "PAPER/SHADOW"
    rows.extend([
        ("VAUGHAN_ARB_MODE", _va_mode, "const", "prediction",
         "Poly-Kalshi paper/shadow. Heuristic pairs are review-only. No writers."),
        ("VAUGHAN_ARB_TICK_AGE_S", _va_tick.get("last_tick_age_s"), "file", "prediction",
         "seconds since last paper arb decision; none if the log is empty"),
        ("VAUGHAN_ARB_LAST_ORNITH", _va_tick.get("last_ornith") or "none", "file",
         "prediction", "last decision reason"),
    ])
    try:
        import etoro_copy_paper as _et
        _et_tick = _et.tick_status()
        _et_mode = _et.MODE
        _et_keys = "present" if _et.keys_present() else "missing"
        _et_live = bool(_et.live_enabled())
    except Exception:
        _et_tick = {"last_tick_age_s": "none", "last_decision": "none", "stale": True}
        _et_mode, _et_keys, _et_live = "PAPER/SHADOW", "missing", False
    rows.extend([
        ("ETORO_MODE", _et_mode, "const", "etoro",
         "research intel for Valedia. Paper would-copy only. No copy allocation."),
        ("ETORO_LIVE", _et_live, _src("ETORO_LIVE"), "etoro",
         "off unless ETORO_LIVE=1 and ETORO_UNLOCK=burton-documented. Writes still refuse."),
        ("ETORO_KEYS", _et_keys, "file", "etoro",
         "x-api-key and x-user-key present. Values are not shown."),
        ("ETORO_TICK_AGE_S", _et_tick.get("last_tick_age_s"), "file", "etoro",
         "seconds since last paper decision; none if the log is empty"),
        ("ETORO_LAST_DECISION", _et_tick.get("last_decision") or "none", "file", "etoro",
         "last paper reason: would_copy, review_only, refuse, or a fence code"),
    ])
    try:
        import vibe_shadow_paper as _vb
        _vb_tick = _vb.tick_status()
        _vb_mode = _vb.MODE
        _vb_live = bool(_vb.live_enabled())
    except Exception:
        _vb_tick = {"last_tick_age_s": "none", "last_decision": "none", "stale": True}
        _vb_mode, _vb_live = "PAPER/SHADOW", False
    rows.extend([
        ("VIBE_SHADOW_MODE", _vb_mode, "const", "vibe",
         "HKUDS shadow pattern. Paper log only. Alpha zoo is inventory, not imported."),
        ("VIBE_SHADOW_LIVE", _vb_live, "const", "vibe",
         "hard-off. Env cannot enable HKUDS broker connectors."),
        ("VIBE_BROKER_WRITE", False, "const", "vibe",
         "hard-off. Every HKUDS broker write path raises vibe_broker_write."),
        ("VIBE_SHADOW_TICK_AGE_S", _vb_tick.get("last_tick_age_s"), "file", "vibe",
         "seconds since last paper shadow decision; none if the log is empty"),
        ("VIBE_SHADOW_LAST_DECISION", _vb_tick.get("last_decision") or "none", "file",
         "vibe", "last paper reason: shadow_log, review_only, fee_true, or a fence code"),
    ])
    try:
        import mirofish_intel_archive as _mia
        _mf = _mia.freshness_of(_mia.DEFAULT_LIVE)
    except Exception:
        _mf = {"as_of": None, "stale": True, "usable_for_fv": False}
    rows.extend([
        ("MIROFISH_INTEL_AS_OF", _mf.get("as_of") or "none", "file", "mirofish",
         "live signal as_of. Dated archive is not create fuel"),
        ("MIROFISH_INTEL_STALE", bool(_mf.get("stale")), "file", "mirofish",
         "true on FAIL marker, missing as_of, or age >= 24h. Live bytes stay"),
        ("MIROFISH_INTEL_USABLE_FOR_FV", bool(_mf.get("usable_for_fv")), "file",
         "mirofish",
         "allocators read live only when fresh and not near-neutral"),
    ])
    rows.extend([
        ("SPOT_RECONCILE_HTTP", spot_reconcile_http_enabled(),
         _src("SPOT_RECONCILE_HTTP"), "spot", "read-only GET reconcile"),
        ("FORTRESS_INTEL_FALLBACK", fortress_intel_fallback_enabled(),
         _src("FORTRESS_INTEL_FALLBACK"), "intel", "PARKED until Burton flip"),
        ("REBALANCER_AUTOEXEC", rebalancer_autoexec_enabled(),
         _src("REBALANCER_AUTOEXEC"), "rebalance", "shadow/propose until flip"),
        ("VALEDIA_TREASURY_AUTO", treasury_auto_enabled(),
         _src("VALEDIA_TREASURY_AUTO"), "treasury",
         "never ACH/wire even if on"),
        ("EXEC_DRY_RUN", bool(EXEC_DRY_RUN), "const", "exec",
         "False=maker live path exists; halt/writer still bind"),
    ])
    import session_clocks as _clocks
    rows.extend(_clocks.clock_control_rows())
    for venue in sorted(VENUE_WRITER):
        rows.append((
            f"VENUE_WRITER.{venue}", VENUE_WRITER[venue], "const", "writer",
            "valedia|nautilus|none",
        ))
    items = [
        {"name": n, "value": v, "source": s, "category": c, "notes": notes}
        for n, v, s, c, notes in rows
    ]
    return {
        "version": ident["version"],
        "sha": ident["sha"],
        "branch": ident["branch"],
        "dirty": bool(ident["dirty"]),
        "items": items,
    }
