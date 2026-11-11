"""Read-only local artifact audit. This CLI never grants receipt or paper authority."""
import argparse
import json
import re
from mirofish_provenance import read_file, decode, sha
from recovery_state import Refused


def audit(root, report_id):
    if not re.fullmatch(r"report_[0-9a-f]{12}", report_id):
        raise Refused("canonical report ID required")
    prefix = "backend/uploads/reports/" + report_id
    raw = read_file(root, prefix + "/meta.json")
    body = read_file(root, prefix + "/full_report.md")
    meta = decode(raw)
    declared = meta.get("markdown_content")
    return {"status": "RESEARCH_ONLY", "eligible_for_paper": False,
            "proof": "local bytes and self-declared metadata only; no producer/run/time authority",
            "report_id": meta.get("report_id"), "simulation_id": meta.get("simulation_id"),
            "graph_id": meta.get("graph_id"), "declared_status": meta.get("status"),
            "created_at_native": meta.get("created_at"), "completed_at_native": meta.get("completed_at"),
            "native_timezone": "unknown; not translated to UTC",
            "meta_sha256": sha(raw), "report_sha256": sha(body),
            "directory_id_matches": meta.get("report_id") == report_id,
            "markdown_matches": isinstance(declared, str) and declared.encode("utf8") == body,
            "next_step": "Prospective capture/paper handoff requires an explicitly reviewed Python authority/profile/clock context; this audit cannot authorize it."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("producer_root", help="Explicit local report-artifact root; never executed")
    parser.add_argument("report_id")
    args = parser.parse_args(argv)
    try:
        result = audit(args.producer_root, args.report_id)
    except (Refused, OSError, ValueError) as exc:
        print(json.dumps({"status": "AUDIT_REFUSED", "eligible_for_paper": False, "reason": str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
