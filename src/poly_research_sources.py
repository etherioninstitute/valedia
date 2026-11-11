"""Offline read-only receipt adapter for official Polymarket US public response shapes.

Never infers historical fills from display prices; never writes a broker record.
Input files must have been captured independently. It neither fetches nor authenticates
an HTTP response, so provenance remains operator-supplied/unverified.
"""
import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

HISTORY_URL="https://docs.polymarket.us/api-reference/price-history/get-price-history.md"
SETTLEMENT_URL="https://docs.polymarket.us/api-reference/markets/get-market-settlement.md"

def read_receipt(path, kind, slug):
    raw=Path(path).read_bytes()
    data=json.loads(raw)
    receipt={"sha256":hashlib.sha256(raw).hexdigest(),"source_path":str(Path(path).resolve()),
             "documentation":HISTORY_URL if kind=="history" else SETTLEMENT_URL,
             "slug":slug,"provenance":"LOCAL_CAPTURE_UNVERIFIED_NOT_A_FILL"}
    if kind=="history":
        if not isinstance(data,dict) or not isinstance(data.get("history"),list) or not data["history"]:
            raise ValueError("history missing or empty")
        points=[]
        for x in data["history"]:
            if (not isinstance(x,dict) or type(x.get("timestamp")) is not int
                or not all(type(x.get(k)) in (int,float) and math.isfinite(x[k]) and 0<x[k]<1
                           for k in ("longPrice","shortPrice"))):
                raise ValueError("invalid US display price point")
            try:
                observed=datetime.fromtimestamp(x["timestamp"],timezone.utc).isoformat()
            except (OverflowError,OSError,ValueError) as exc:
                raise ValueError("invalid US display timestamp") from exc
            point={"observed_at":observed,"yes_display":x["longPrice"],"no_display":x["shortPrice"]}
            # Optional publisher metadata is passed through only when explicitly
            # present in captured bytes; the public history schema does not promise it.
            if "published_at" in x:
                try:
                    published=datetime.fromisoformat(x["published_at"])
                    if published.tzinfo is None: raise ValueError("timezone required")
                    point["published_at"]=published.astimezone(timezone.utc).isoformat()
                except (TypeError,AttributeError,ValueError,OverflowError) as exc:
                    raise ValueError("invalid explicit publisher timestamp") from exc
                point["publication_source"]=receipt["source_path"]+"#sha256="+receipt["sha256"]
            points.append(point)
        if len({x["observed_at"] for x in points})!=len(points) or points!=sorted(points,key=lambda x:x["observed_at"]):
            raise ValueError("duplicated/out-of-order history")
        receipt.update({"points":points,"executable":False,"reason":"display prices have no depth or fill confirmation"})
    elif kind=="settlement":
        if not isinstance(data,dict) or data.get("slug")!=slug or type(data.get("settlement")) not in (int,float) or data["settlement"] not in (0,1):
            raise ValueError("unmatched or unresolved US settlement")
        receipt.update({"outcome":data["settlement"],"availability":"UNKNOWN_RESPONSE_CAPTURE_TIME_REQUIRED"})
    else: raise ValueError("unsupported source kind")
    return receipt

if __name__=="__main__":
    p=argparse.ArgumentParser(description="Inspect captured public US responses; no network, no orders")
    p.add_argument("kind",choices=("history","settlement")); p.add_argument("path",type=Path); p.add_argument("--slug",required=True)
    a=p.parse_args(); print(json.dumps(read_receipt(a.path,a.kind,a.slug),sort_keys=True))
