"""Explicit offline JSON research intake. Prints proposals; has no execution route."""
import argparse
import json
from pathlib import Path
from atlas_research_adapter import (ResearchConfig, Evidence, Recommendation,
    DevelopmentOutcome, Scenario, build_research, ResearchRefused)

def _object(pairs):
    result = {}
    for k, v in pairs:
        if k in result: raise ResearchRefused("duplicate JSON key")
        result[k] = v
    return result

def _bad_constant(value):
    raise ResearchRefused("nonfinite JSON refused")

def _rows(value, cls):
    if not isinstance(value, list) or len(value) > 100: raise ResearchRefused("bounded record list required")
    result = []
    for item in value:
        if not isinstance(item, dict): raise ResearchRefused("object record required")
        row = dict(item)
        ev = row.get("evidence")
        if not isinstance(ev, list) or len(ev) > 32: raise ResearchRefused("bounded evidence list required")
        row["evidence"] = tuple(Evidence(**x) for x in ev)
        result.append(cls(**row))
    return tuple(result)

def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline ATLAS research only; no orders or model/provider calls")
    parser.add_argument("--input", required=True)
    parser.add_argument("--enable-cohorts", action="store_true")
    parser.add_argument("--enable-scenarios", action="store_true")
    args = parser.parse_args(argv)
    try:
        with Path(args.input).open("rb") as stream:
            raw = stream.read(1048577)
        if len(raw) > 1048576: raise ResearchRefused("input exceeds one MiB")
        data = json.loads(raw, object_pairs_hook=_object, parse_constant=_bad_constant)
        if not isinstance(data, dict) or set(data) - {"as_of", "recommendations", "outcomes", "scenarios"}: raise ResearchRefused("research-only fields required")
        config = ResearchConfig(enable_cohorts=args.enable_cohorts, enable_scenarios=args.enable_scenarios)
        artifact = build_research(config, as_of=data["as_of"],
            recommendations=_rows(data.get("recommendations", []), Recommendation),
            outcomes=_rows(data.get("outcomes", []), DevelopmentOutcome),
            scenarios=_rows(data.get("scenarios", []), Scenario))
        print(json.dumps({"artifact_id":artifact.artifact_id,"research_only":True,"synthetic_scenarios_are_hypotheses":True,"artifact":artifact.payload()}, sort_keys=True, allow_nan=False))
        return 0
    except (ResearchRefused, TypeError, KeyError, ValueError, OSError) as exc:
        print(json.dumps({"status":"REFUSED", "reason":str(exc)}))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
