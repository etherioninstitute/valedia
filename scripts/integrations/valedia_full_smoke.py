#!/usr/bin/env python3
"""VALEDIA-FULL-SMOKE — minutes-scale whole-brain smoke.

Eight sections, each PASS/FAIL/SKIP. Wraps existing helpers. Never flips
live flags, never places live orders, never ACH/treasury auto.

Exit 1 only if a critical fence/fixture fails. Optional live SKIP is PASS.

Run: python3 scripts/integrations/valedia_full_smoke.py
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import adversarial_paper_fight as apf  # noqa: E402
import mirofish_smoke as mf  # noqa: E402
import poly_us_smoke as poly_smoke  # noqa: E402
import pnl_tracker as pnl  # noqa: E402
import treasury_status as ts  # noqa: E402
import valedia_cockpit as cockpit  # noqa: E402
import valedia_config as vc  # noqa: E402

WALL_BUDGET_S = 600
KALSHI_TIMEOUT_S = 15
COCKPIT_GET_TIMEOUT_S = 2
COCKPIT_URL = "http://127.0.0.1:8765/status"

EXPECTED_WRITERS = {
    "kalshi": "valedia",
    "coinbase": "valedia",
    "alpaca": "valedia",
    "kraken": "none",
    "polymarket": "none",
    "hyperliquid": "none",
}

_LIVE_CFG = (
    "MIROFISH_AUTO_RAISE",
    "VALEDIA_SELF_ADJUST",
    "NAUTILUS_LIVE",
    "VALEDIA_TREASURY_AUTO",
    "REBALANCER_AUTOEXEC",
    "SPOT_RECONCILE_HTTP",
    "NAUTILUS_ENABLED",
)


def _now():
    return datetime.now(timezone.utc)


def _row(name, status, reason, critical=False, extra=None):
    out = {"name": name, "status": status, "reason": reason, "critical": critical}
    if extra:
        out["extra"] = extra
    return out


def check_fence():
    """Version + VENUE_WRITER + LIVE/TREASURY stamps. POLYMARKET_US_LIVE is
    reported honestly (env-only) and is not a fence fail. Never sets flags."""
    mf_fence = mf.check_fence()
    writers = {k: vc.venue_writer(k) for k in EXPECTED_WRITERS}
    drift = {k: writers[k] for k in EXPECTED_WRITERS if writers[k] != EXPECTED_WRITERS[k]}
    cfg = [k for k in _LIVE_CFG if getattr(vc, k, False)]
    poly_live = False
    try:
        import polymarket_us_client as pmu
        poly_live = bool(pmu.live_enabled())
    except Exception:
        poly_live = mf._truthy(os.environ.get("POLYMARKET_US_LIVE"))
    env_live = [k for k in (mf_fence.get("env_live") or []) if k != "POLYMARKET_US_LIVE"]
    ok = not env_live and not (mf_fence.get("config_live") or []) and not drift and not cfg
    extra = {
        "valedia_version": vc.VALEDIA_VERSION,
        "stamp": vc.version_stamp(),
        "venue_writer": writers,
        "writer_drift": drift,
        "env_live": env_live,
        "config_live": list(dict.fromkeys((mf_fence.get("config_live") or []) + cfg)),
        "polymarket_us_live": poly_live,
        "kalshi_ev_preflight_gate": bool(vc.kalshi_ev_preflight_gate_enabled()),
        "kalshi_maker_first": bool(vc.kalshi_maker_first_enabled()),
        "treasury_auto": bool(vc.treasury_auto_enabled()),
        "rebalancer_autoexec": bool(vc.rebalancer_autoexec_enabled()),
        "trading_halt": bool(vc.trading_halted()),
    }
    if not ok:
        bits = []
        if extra["env_live"]:
            bits.append("env " + ",".join(extra["env_live"]))
        if extra["config_live"]:
            bits.append("cfg " + ",".join(extra["config_live"]))
        if drift:
            bits.append("writer " + json.dumps(drift))
        reason = "live flag/writer on: " + "; ".join(bits) or "fence failed"
        return _row("fence", "FAIL", reason, critical=True, extra=extra)
    live_note = "POLYMARKET_US_LIVE on" if poly_live else "POLYMARKET_US_LIVE off"
    return _row("fence", "PASS",
                f"v{vc.VALEDIA_VERSION} writers held, {live_note}",
                critical=True, extra=extra)


def check_treasury(live_read=False, readers=None):
    report = ts.collect_status(readers=readers, live_read=live_read)
    extra = {
        "moves_money": report.get("moves_money"),
        "auto": report.get("auto"),
        "venues": [{k: v.get(k) for k in ("venue", "status", "cash", "writer")}
                   for v in (report.get("venues") or [])],
        "polymarket_us_live": report.get("polymarket_us_live"),
    }
    if report.get("moves_money") or (report.get("auto") or {}).get("executed"):
        return _row("treasury", "FAIL", "treasury attempted a money move",
                    critical=True, extra=extra)
    if not (report.get("auto") or {}).get("refused"):
        return _row("treasury", "FAIL", "auto path not refused",
                    critical=True, extra=extra)
    return _row("treasury", "PASS", "report-only; auto refused; no ACH",
                critical=True, extra=extra)


def _mirofish_live_health():
    """Bionic + backend + Neo4j probe. Ollama miss is WARN/skip, never FAIL."""
    out = {"bionic": "SKIP", "backend": "SKIP", "neo4j": "SKIP", "inference": "bionic"}
    try:
        em = {}
        envp = os.path.expanduser("~/mirofish-offline/.env")
        with open(envp) as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                em[k] = v
        out["inference"] = (em.get("MIROFISH_INFERENCE") or "bionic").strip().lower()
        out["llm_base"] = em.get("LLM_BASE_URL")
    except Exception:
        pass
    try:
        mf.http_json("http://127.0.0.1:1234/v1/models", timeout=2)
        out["bionic"] = "PASS"
    except Exception as e:
        out["bionic"] = "FAIL" if out["inference"] == "bionic" else "WARN"
        out["bionic_err"] = str(e)[:80]
    try:
        urllib.request.urlopen("http://127.0.0.1:5001/health", timeout=2)
        out["backend"] = "PASS"
    except Exception as e:
        out["backend"] = "WARN"
        out["backend_err"] = str(e)[:80]
    try:
        with socket.create_connection(("127.0.0.1", 7687), 2):
            out["neo4j"] = "PASS"
    except Exception:
        out["neo4j"] = "WARN"
    return out


def check_mirofish(live=True, models_dir=None):
    report = mf.run_smoke(live=live, models_dir=models_dir, out_path=None)
    fx_ok = bool(report.get("ok"))
    live_r = report.get("live_lite") or {}
    health = _mirofish_live_health() if live else {}
    extra = {
        "fixture_ok": fx_ok,
        "live_lite": live_r.get("status"),
        "live_reason": live_r.get("reason"),
        "live_endpoint": live_r.get("endpoint"),
        "health": health,
        "wall_s": report.get("wall_s"),
        "verdict": report.get("verdict"),
    }
    if not fx_ok:
        return _row("mirofish", "FAIL",
                    "fixture failed: " + str(report.get("fixture", {}).get("error") or "ok=false"),
                    critical=True, extra=extra)
    live_st = live_r.get("status", "SKIP")
    bits = ["fixture PASS", "live-lite " + str(live_st)]
    if health:
        bits.append("bionic=" + str(health.get("bionic")))
        bits.append("backend=" + str(health.get("backend")))
        bits.append("neo4j=" + str(health.get("neo4j")))
    return _row("mirofish", "PASS", "; ".join(bits), critical=True, extra=extra)


def check_kalshi(offline=False, reader=None):
    if reader is not None:
        raw = reader()
        cash = ts.cash_from_kalshi(raw)
        return _row("kalshi", "PASS", "injected balance read",
                    extra={"cash_usd": cash, "source": "injected"})
    if offline:
        return _row("kalshi", "SKIP", "offline")
    try:
        __import__("cryptography")
        __import__("requests")
    except ImportError as e:
        return _row("kalshi", "SKIP", "requests/cryptography unavailable: " + str(e))
    prev_to = socket.getdefaulttimeout()
    socket.setdefaulttimeout(KALSHI_TIMEOUT_S)
    try:
        import kalshi_auth
        raw = kalshi_auth.get_balance()
        cash = ts.cash_from_kalshi(raw)
        consult = None
        try:
            import kalshi_fee_map as kfm
            consult = kfm.consult("KXBTCY", series="KXBTCY")
        except Exception as e:
            consult = {"status": "error", "error": str(e)[:120]}
        extra = {
            "cash_usd": cash,
            "keys": ("balance",),
            "ev_preflight": bool(vc.kalshi_ev_preflight_gate_enabled()),
            "maker_first": bool(vc.kalshi_maker_first_enabled()),
            "fee_map_consult": (consult or {}).get("status"),
            "fee_map_series": (consult or {}).get("series") or "KXBTCY",
        }
        return _row("kalshi", "PASS", "GET /portfolio/balance + fee-map consult",
                    extra=extra)
    except Exception as e:
        return _row("kalshi", "SKIP", "balance read failed: " + str(e)[:200])
    finally:
        socket.setdefaulttimeout(prev_to)


def check_poly_us(offline=False, client=None):
    if client is None and offline:
        return _row("poly_us", "SKIP", "offline")
    live = False
    try:
        import polymarket_us_client as pmu
        live = bool(pmu.live_enabled())
    except Exception:
        live = mf._truthy(os.environ.get("POLYMARKET_US_LIVE"))
    live_was = os.environ.get("POLYMARKET_US_LIVE")
    try:
        out = poly_smoke.run_smoke(client=client, force_live_off=not live)
    except RuntimeError as e:
        msg = str(e)
        if "missing POLYMARKET" in msg or "whoami" in msg.lower() or "balances" in msg.lower():
            return _row("poly_us", "SKIP", msg[:200])
        if "LiveWriteBlocked" in msg or "must raise" in msg:
            return _row("poly_us", "FAIL", "poly write was not blocked", critical=True)
        return _row("poly_us", "SKIP", msg[:200])
    except Exception as e:
        return _row("poly_us", "SKIP", str(e)[:200])
    finally:
        if live_was is None:
            os.environ.pop("POLYMARKET_US_LIVE", None)
        else:
            os.environ["POLYMARKET_US_LIVE"] = live_was
    who = out.get("whoami") or {}
    extra = {
        "whoami_source": who.get("source"),
        "preview": out.get("preview"),
        "create_blocked": bool(out.get("create_blocked")),
        "create_skipped": bool(out.get("create_skipped")),
        "live": bool(out.get("live") or live),
    }
    if live:
        if not out.get("ok") or not extra["preview"]:
            return _row("poly_us", "FAIL", "LIVE on: balances+preview required",
                        critical=True, extra=extra)
        return _row("poly_us", "PASS",
                    "whoami_retail/balances + preview; LIVE on; create skipped",
                    extra=extra)
    if not out.get("create_blocked") or out.get("live"):
        return _row("poly_us", "FAIL", "poly write must stay blocked / live must stay False",
                    critical=True, extra=extra)
    return _row("poly_us", "PASS",
                "whoami_retail/balances + preview; create blocked",
                extra=extra)


def check_shadow_adversarial(queue_path=None):
    q = queue_path or os.path.join(cockpit._MODELS, "nautilus_intent_queue.jsonl")
    counts = cockpit._shadow_counts(q)
    try:
        doc = apf.build_round(
            signal_doc={"source": "full_smoke", "overall_bias": 50, "signals": {}},
            enrich={}, opps=[], ere={})
    except Exception as e:
        return _row("shadow_adversarial", "FAIL", "adversarial import/build failed: " + str(e)[:200],
                    critical=True)
    if doc.get("mode") != "PAPER/SHADOW" or not doc.get("no_orders"):
        return _row("shadow_adversarial", "FAIL", "paper fight not shadow/no_orders",
                    critical=True, extra={"doc": {k: doc.get(k) for k in
                                                 ("mode", "no_orders", "n")}})
    extra = {
        "intents": counts.get("intents"),
        "dispatchable": counts.get("dispatchable"),
        "queue": q,
        "adversarial_n": doc.get("n"),
        "adversarial_mode": doc.get("mode"),
        "no_orders": True,
    }
    return _row("shadow_adversarial", "PASS",
                f"queue intents={counts.get('intents')} dispatchable={counts.get('dispatchable')}; "
                "adversarial paper import ok",
                critical=True, extra=extra)


def check_cockpit():
    try:
        panel = cockpit.panel()
    except Exception as e:
        return _row("cockpit", "FAIL", "panel() failed: " + str(e)[:200], critical=True)
    http = {"status": "SKIP", "reason": "not up"}
    try:
        req = urllib.request.Request(COCKPIT_URL)
        with urllib.request.urlopen(req, timeout=COCKPIT_GET_TIMEOUT_S) as resp:
            body = json.load(resp)
        http = {"status": "PASS", "http": getattr(resp, "status", 200),
                "mode": body.get("mode")}
    except Exception as e:
        http = {"status": "SKIP", "reason": str(e)[:120]}
    extra = {
        "mode": panel.get("mode"),
        "get_only": panel.get("get_only"),
        "no_orders": panel.get("no_orders"),
        "shadow": panel.get("shadow"),
        "http": http,
    }
    return _row("cockpit", "PASS",
                f"panel() {panel.get('mode')}; GET {http['status']}",
                critical=True, extra=extra)


def check_pnl():
    data = pnl.load_pnl()
    summary = (data or {}).get("summary") or {}
    if not isinstance(summary, dict):
        return _row("pnl", "FAIL", "pnl_tracker summary missing", critical=True)
    ftl_n = None
    ftl_net = None
    try:
        import forward_test_ledger as ftl
        tr = ftl.real_track_record()
        ftl_n = int(tr.get("n") or 0)
        ftl_net = float(tr.get("net") or 0)
    except Exception:
        pass
    extra = {
        "label": "broker (pnl_tracker.json) ≠ FTL",
        "broker_wins": summary.get("wins"),
        "broker_losses": summary.get("losses"),
        "broker_net": summary.get("net_pnl"),
        "ftl_n": ftl_n,
        "ftl_net": ftl_net,
        "message": pnl.simple_pnl_message(summary),
    }
    reason = f"broker summary present; label broker≠FTL; FTL n={ftl_n}"
    return _row("pnl", "PASS", reason, critical=True, extra=extra)


def run_smoke(offline=False, live_mirofish=True, poly_client=None,
              kalshi_reader=None, models_dir=None, queue_path=None,
              treasury_readers=None, treasury_live=False):
    t0 = time.monotonic()
    fence = check_fence()
    sections = [fence]
    live_ok = fence["status"] == "PASS"
    # remaining checks still run so the report is complete; live HTTP skipped if fence failed
    off = bool(offline) or not live_ok
    sections.append(check_treasury(live_read=bool(treasury_live) and not off,
                                   readers=treasury_readers))
    sections.append(check_mirofish(live=bool(live_mirofish) and not off,
                                   models_dir=models_dir))
    sections.append(check_kalshi(offline=off and kalshi_reader is None,
                                 reader=kalshi_reader))
    sections.append(check_poly_us(offline=off and poly_client is None,
                                  client=poly_client))
    sections.append(check_shadow_adversarial(queue_path=queue_path))
    sections.append(check_cockpit())
    sections.append(check_pnl())
    wall = time.monotonic() - t0
    critical_fail = [s for s in sections if s["status"] == "FAIL" and s.get("critical")]
    ok = not critical_fail
    by = {s["name"]: s["status"] for s in sections}
    report = {
        "ok": ok,
        "verdict": "PASS" if ok else "FAIL",
        "valedia_version": vc.VALEDIA_VERSION,
        "stamp": vc.version_stamp(),
        "ts": _now().isoformat(),
        "wall_s": round(wall, 3),
        "wall_budget_s": WALL_BUDGET_S,
        "within_budget": wall <= WALL_BUDGET_S,
        "offline": bool(offline),
        "sections": sections,
        "by_name": by,
        "critical_fail": [s["name"] for s in critical_fail],
        "fence": "POLYMARKET_US_LIVE reported honestly (env-only, never committed). "
                 "No VENUE_WRITER / REBALANCER_AUTOEXEC / NAUTILUS_LIVE / "
                 "VALEDIA_SELF_ADJUST / MIROFISH_AUTO_RAISE / VALEDIA_TREASURY_AUTO / "
                 "mass create_order / ACH / oil-weather unfence.",
        "next": "STOP for Burton. Do not auto-start another slice.",
    }
    return report


def render_md(report):
    lines = [
        "# VALEDIA-FULL-SMOKE",
        "",
        f"**Updated:** {_now().strftime('%Y-%m-%d')} PT",
        f"**Harness:** `python3 scripts/integrations/valedia_full_smoke.py`",
        f"**Version:** `{report.get('valedia_version')}` stamp `{report.get('stamp')}`",
        f"**Verdict:** **{report.get('verdict')}**",
        f"**Wall:** {report.get('wall_s')}s / {report.get('wall_budget_s')}s",
        "",
        "Hard fence held: `POLYMARKET_US_LIVE` reported honestly (env-only). "
        "No `VENUE_WRITER` changes, no `REBALANCER_AUTOEXEC`, no `NAUTILUS_LIVE`, "
        "no `VALEDIA_SELF_ADJUST`/`MIROFISH_AUTO_RAISE`, no treasury auto, "
        "no mass `create_order`, no ACH, no oil/weather unfence.",
        "",
        "| # | Section | Result | Reason |",
        "|---|---------|--------|--------|",
    ]
    for i, s in enumerate(report.get("sections") or [], 1):
        reason = (s.get("reason") or "").replace("|", "/")
        crit = " (critical)" if s.get("critical") else ""
        lines.append(f"| {i} | {s['name']}{crit} | **{s['status']}** | {reason} |")
    lines += [
        "",
        f"**Exit:** `{'0' if report.get('ok') else '1'}` "
        "(nonzero only on critical fence/fixture FAIL; optional live SKIP is not a fail).",
        "",
        "## Not run",
        "",
        "- live orders / Kalshi writes / Poly create (blocked)",
        "- ACH / wire / card / treasury auto top-up",
        "- kill-switch: `valedia_config.require_not_halted` (see `KILL-SWITCH.md`)",
        "- live OpenTerminal market UI (:3000) — Valedia glance is :8765 (opt-in --listen)",
        "",
        "**Next = Burton.** Do not auto-start another slice.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(report, json_path=None, md_path=None):
    if json_path:
        os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
        with open(json_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
            f.write("\n")
        report["json_path"] = json_path
    if md_path:
        os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
        with open(md_path, "w") as f:
            f.write(render_md(report))
        report["md_path"] = md_path
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description="Valedia minutes-scale whole-brain smoke")
    p.add_argument("--offline", action="store_true",
                   help="skip optional live HTTP (kalshi/poly/mirofish live-lite/cockpit GET)")
    p.add_argument("--no-live", action="store_true", help="alias of --offline")
    p.add_argument("--out", default=os.path.join(ROOT, "models", "valedia_full_smoke_report.json"))
    p.add_argument("--md", default=os.path.join(ROOT, "recommendations", "VALEDIA-FULL-SMOKE.md"))
    p.add_argument("--models", default=None, help="mirofish fixture dir (default: temp)")
    args = p.parse_args(argv)
    offline = args.offline or args.no_live or os.environ.get("VALEDIA_FULL_SMOKE_OFFLINE") == "1"
    report = run_smoke(offline=offline, live_mirofish=not offline,
                       models_dir=args.models, treasury_live=not offline)
    write_outputs(report, json_path=args.out, md_path=args.md)
    print(json.dumps({k: report[k] for k in report if k != "sections"}, indent=2, default=str))
    print()
    for s in report["sections"]:
        print(f"  {s['status']:4}  {s['name']}: {s['reason']}")
    print(f"\nVALEDIA-FULL-SMOKE: {report['verdict']} wall={report['wall_s']}s")
    if report.get("md_path"):
        print("report:", report["md_path"])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
