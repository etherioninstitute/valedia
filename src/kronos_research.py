"""Staged manual Kronos research runner; copy beside research_desk_cli.py only after review.

No import-time network, model load, broker, database, or execution side effect.
"""
import argparse
import contextlib
import hashlib
import importlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_desk import KronosAdapter, evaluate_origins
from research_desk.core import DeskError, canonical, num, open_directory, regular_bytes, utc, validate_event
from research_desk.forecast import _atomic_new
import research_desk_cli


CODE_SHA = "67b630e67f6a18c9e9be918d9b4337c960db1e9a"
MODEL_SHA = "901c26c1332695a2a8f243eb2f37243a37bea320"
TOKENIZER_SHA = "0e0117387f39004a9016484a186a908917e22426"
MODEL_WEIGHT_SHA256 = "b082dfcbd8e8c142a725c8bbb99781802f38fec81210e13479effb32b3c3e020"
TOKENIZER_WEIGHT_SHA256 = "59d85f6af76a2c3b8240ea06cb21db4213b4eeca053f246b23e29cf832fc6bee"
MODEL_CONFIG_SHA256 = "5e0f6a605d5f81b5c9b559fe5cf716a1acb041c744e6f41bd05b097b7a685396"
TOKENIZER_CONFIG_SHA256 = "2366e7ccfec76cbc19cf3c4c1b9c5d901be336ca1e83f2d2292c9bff381b77a2"
PAIR = "Kronos-small", "Kronos-Tokenizer-base"
STEP = 3600
MAX_REQUESTS = 3
PAGE_BARS = 250  # margin below the provider's 300-row cap for pre-start extras
MAX_RESPONSE_BYTES = 120_000
PRODUCT = re.compile(r"[A-Z0-9]{2,12}-[A-Z0-9]{2,12}\Z")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise DeskError("provider redirect refused")


def request_bytes(url):
    """One fixed-host read-only GET with no credentials, redirects, or unbounded body."""
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "api.exchange.coinbase.com" or
            not re.fullmatch(r"/products/[A-Z0-9]{2,12}-[A-Z0-9]{2,12}/candles", parsed.path)):
        raise DeskError("unexpected market-data endpoint")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ValediaResearchDesk/1.0"}, method="GET")
    with opener.open(req, timeout=10) as response:
        if response.status != 200 or response.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise DeskError("provider status or content type invalid")
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise DeskError("provider response too large")
    return body


def _decode_page(raw, start, end):
    try:
        page = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(DeskError("nonfinite source")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DeskError("malformed provider JSON") from exc
    if not isinstance(page, list) or len(page) > 300:
        raise DeskError("malformed provider page")
    out = []
    for row in page:
        if not isinstance(row, list) or len(row) != 6:
            raise DeskError("malformed provider candle")
        stamp, low, high, opn, close, volume = row
        if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp > end or stamp % STEP:
            raise DeskError("provider candle outside requested grid")
        if stamp < start or stamp == end:
            continue  # Provider may return pre-start rows and the still-open end bucket.
        low, high, opn, close = (num(x, positive=True) for x in (low, high, opn, close))
        volume = num(volume, nonnegative=True)
        if low > min(opn, close) or high < max(opn, close) or low > high:
            raise DeskError("malformed provider OHLC")
        close_at = datetime.fromtimestamp(stamp + STEP, timezone.utc).isoformat()
        out.append({"at": close_at, "available_at": close_at, "open": opn, "high": high,
                    "low": low, "close": close, "volume": volume})
    return out


def fetch_capture(product, end_at, *, pages=2, fetch=request_bytes):
    """Capture at most 3 hourly Coinbase Exchange pages as immutable bytes for caller storage."""
    if not isinstance(product, str) or not PRODUCT.fullmatch(product) or pages not in (1, 2, 3):
        raise DeskError("unsupported product or page count")
    end = int(utc(end_at).timestamp())
    now = int(datetime.now(timezone.utc).timestamp())
    if end % STEP or end > now or now - end >= STEP:
        raise DeskError("end must be the latest completed hourly boundary")
    captured, candles = [], []
    for index in range(pages):
        stop = end - index * PAGE_BARS * STEP
        start = stop - PAGE_BARS * STEP
        query = urllib.parse.urlencode({"start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                                        "end": datetime.fromtimestamp(stop, timezone.utc).isoformat(),
                                        "granularity": STEP})
        url = f"https://api.exchange.coinbase.com/products/{product}/candles?{query}"
        raw = fetch(url)
        if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
            raise DeskError("provider response bytes invalid")
        candles.extend(_decode_page(raw, start, stop))
        try:
            body = raw.decode("utf-8")
        except UnicodeError as exc:
            raise DeskError("provider response is not UTF-8") from exc
        captured.append({"url": url, "sha256": hashlib.sha256(raw).hexdigest(), "body": body})
    candles.sort(key=lambda b: b["at"])
    if len(candles) < 402 or any(candles[i]["at"] == candles[i - 1]["at"] for i in range(1, len(candles))):
        raise DeskError("insufficient or duplicate history")
    for i in range(1, len(candles)):
        if (utc(candles[i]["at"]) - utc(candles[i - 1]["at"])).total_seconds() != STEP:
            raise DeskError("gapped provider history")
    captured_at = datetime.now(timezone.utc).isoformat()
    snapshot = canonical({"schema": "valedia.research-desk.coinbase-capture.v1", "product": product,
                          "captured_at": captured_at, "requests": captured})
    return candles, snapshot, captured_at


def _git_head(repo):
    # Never inherit GIT_CONFIG_* / GIT_EXTERNAL_DIFF / GIT_* environment overrides.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_COUNT="0", GIT_OPTIONAL_LOCKS="0")
    base = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null", "-C", str(repo)]
    try:
        top = subprocess.run(base + ["rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, check=True, timeout=5, env=env).stdout.strip()
        head = subprocess.run(base + ["rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True, timeout=5, env=env).stdout.strip()
        dirty = subprocess.run(base + ["status", "--porcelain=v1", "--untracked-files=all"],
                               capture_output=True, text=True, check=True, timeout=5, env=env).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise DeskError("official checkout verification failed") from exc
    if Path(top).resolve() != Path(repo).resolve() or head != CODE_SHA or dirty:
        raise DeskError("official checkout commit mismatch or dirty tree")


def _snapshot(path, revision, weight_sha256, config_sha256):
    p = Path(path)
    if (p.name != revision or p.is_symlink() or not p.is_dir() or
            {child.name for child in p.iterdir()} != {"config.json", "model.safetensors"} or
            not all((p / name).is_file() for name in ("config.json", "model.safetensors"))):
        raise DeskError("local pinned snapshot required")
    if any((p / name).is_symlink() for name in ("config.json", "model.safetensors")):
        raise DeskError("snapshot symlink refused")
    for name, expected in (("config.json", config_sha256), ("model.safetensors", weight_sha256)):
        digest = hashlib.sha256()
        with (p / name).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise DeskError("snapshot file hash mismatch")
    return p


class BucketStartPredictor:
    """Translate validated desk close labels to model bucket-start features only."""
    def __init__(self, official_predictor):
        self.official_predictor = official_predictor

    def predict(self, *, df, x_timestamp, y_timestamp, pred_len, **sampling):
        import pandas as pd
        if (not isinstance(x_timestamp, pd.Series) or not isinstance(y_timestamp, pd.Series) or
                len(x_timestamp) != len(df) or len(y_timestamp) != pred_len or
                str(x_timestamp.dt.tz) != "UTC" or str(y_timestamp.dt.tz) != "UTC"):
            raise DeskError("UTC close timestamp series required")
        offset = pd.Timedelta(seconds=STEP)
        x_start, y_start = x_timestamp - offset, y_timestamp - offset
        if x_start.iloc[-1] + offset != y_start.iloc[0]:
            raise DeskError("model bucket boundary mismatch")
        return self.official_predictor.predict(df=df, x_timestamp=x_start,
                                               y_timestamp=y_start, pred_len=pred_len, **sampling)


def load_official_predictor(repo, model_snapshot, tokenizer_snapshot):
    """Explicit activation only; accepts verified local snapshots and forces CPU."""
    repo = Path(repo)
    _git_head(repo)
    model_dir = _snapshot(model_snapshot, MODEL_SHA, MODEL_WEIGHT_SHA256, MODEL_CONFIG_SHA256)
    token_dir = _snapshot(tokenizer_snapshot, TOKENIZER_SHA, TOKENIZER_WEIGHT_SHA256, TOKENIZER_CONFIG_SHA256)
    if not (repo / "model" / "__init__.py").is_file():
        raise DeskError("official model package missing")
    if "model" in sys.modules:
        raise DeskError("ambiguous preloaded model package")
    sys.path.insert(0, str(repo))
    try:
        official = importlib.import_module("model")
    finally:
        sys.path.pop(0)
    tokenizer = official.KronosTokenizer.from_pretrained(str(token_dir))
    model = official.Kronos.from_pretrained(str(model_dir))
    tokenizer.eval(); model.eval()
    predictor = official.KronosPredictor(model, tokenizer, device="cpu", max_context=512)
    return KronosAdapter(BucketStartPredictor(predictor), model=PAIR[0], tokenizer=PAIR[1],
                         revision=f"m-{MODEL_SHA}-t-{TOKENIZER_SHA}", max_context=512)


def future_times(origin_at, horizon):
    origin = utc(origin_at)
    return [(origin + timedelta(seconds=STEP * j)).isoformat() for j in range(1, horizon + 1)]


def make_bundle(product, candles, snapshot_sha256, captured_at, adapter, *, horizon=24, samples=5):
    if not 1 <= horizon <= 24 or not 1 <= samples <= 30:
        raise DeskError("forecast work bound exceeded")
    history = candles[-400:]
    origin = history[-1]["at"]
    capture_time = utc(captured_at).isoformat()
    if (utc(capture_time) >= utc(origin) + timedelta(seconds=STEP) or
            datetime.now(timezone.utc) >= utc(origin) + timedelta(seconds=STEP)):
        raise DeskError("capture too late for next-bar research forecast")
    paths = adapter.paths(history, future_times(origin, horizon), as_of=capture_time,
                          horizon=horizon, sample_count=samples, T=1.0, top_p=0.9)
    if paths["status"] != "available":
        raise DeskError("predictor unavailable")
    as_of = datetime.now(timezone.utc).isoformat()
    if utc(as_of) >= utc(origin) + timedelta(seconds=STEP):
        raise DeskError("inference completed after forecast horizon began")
    event = {"venue": "coinbase-exchange", "symbol": product, "market_type": "continuous",
             "interval_seconds": STEP, "decision_at": as_of, "published_at": capture_time,
             "available_at": capture_time, "expires_at": (utc(origin) + timedelta(seconds=STEP)).isoformat(),
             "source_sha256": snapshot_sha256,
             "source_uri": f"https://api.exchange.coinbase.com/products/{product}/candles",
             "candles": history}
    validate_event(event)
    forecast = {"schema": "valedia.research-desk.forecast.v1", "venue": event["venue"],
                "symbol": product, "interval_seconds": STEP, "origin_at": origin, "as_of": as_of,
                "last_close": history[-1]["close"], "source_sha256": snapshot_sha256,
                "model": paths["model"], "timestamps": future_times(origin, horizon), "paths": paths["paths"]}
    return {"event": event, "roles": None, "digest": None, "forecast": forecast, "evaluation": None}


def evaluate_real(candles, adapter, *, origins, fee_bps, slippage_bps):
    if not 1 <= origins <= 10 or len(candles) < 401 + origins:
        raise DeskError("finite evaluation history required")
    first = len(candles) - origins - 1
    indices = list(range(first, len(candles) - 1))
    def next_close(history, decision):
        # Evaluator supplies only prefix <= origin; tokenizer sees last 400 bars.
        window = history[-400:]
        result = adapter.paths(window, future_times(window[-1]["at"], 1), as_of=decision,
                               horizon=1, sample_count=1, T=1.0, top_p=0.9)
        if result["status"] != "available":
            raise DeskError("predictor unavailable")
        return result["paths"][0][0]["close"]
    score = evaluate_origins(candles, indices, predictor=next_close,
                             fee_bps=fee_bps, slippage_bps=slippage_bps)
    score["status"] = "RETROSPECTIVE_DIAGNOSTIC_ONLY"
    score["model"] = adapter.meta
    score["source_note"] = "public historical capture; revisions and point-in-time availability unverified"
    score["cost_note"] = "fee_bps_each_side and slippage_bps_each_side are caller-supplied assumptions, not observed costs"
    return score


def verify_bound_artifacts(receipts):
    for item in receipts.values():
        raw, info = regular_bytes(item["path"], 2_000_000, with_stat=True)
        if (len(raw) != item["size"] or hashlib.sha256(raw).hexdigest() != item["sha256"] or
                (info.st_dev, info.st_ino) != (item["dev"], item["ino"])):
            raise DeskError("source or evaluation artifact changed")


def main(argv=None):
    p = argparse.ArgumentParser(description="Explicit CPU-only Kronos research run")
    p.add_argument("--activate-inference", action="store_true", required=True)
    p.add_argument("--product", default="BTC-USD")
    p.add_argument("--end-at", required=True, help="completed UTC hourly boundary")
    p.add_argument("--pages", type=int, default=2, choices=(2, 3))
    p.add_argument("--repo", required=True)
    p.add_argument("--model-snapshot", required=True)
    p.add_argument("--tokenizer-snapshot", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--stem", required=True)
    p.add_argument("--horizon", type=int, default=24)
    p.add_argument("--samples", type=int, default=5)
    p.add_argument("--eval-origins", type=int, default=0)
    p.add_argument("--fee-bps", type=float)
    p.add_argument("--slippage-bps", type=float)
    a = p.parse_args(argv)
    if a.eval_origins and (a.fee_bps is None or a.slippage_bps is None):
        p.error("evaluation needs supplied fee and slippage on each side")
    candles, snapshot, captured_at = fetch_capture(a.product, a.end_at, pages=a.pages)
    source_sha = hashlib.sha256(snapshot).hexdigest()
    adapter = load_official_predictor(a.repo, a.model_snapshot, a.tokenizer_snapshot)
    bundle = make_bundle(a.product, candles, source_sha, captured_at, adapter, horizon=a.horizon, samples=a.samples)
    if datetime.now(timezone.utc) >= utc(bundle["event"]["expires_at"]):
        raise DeskError("forecast expired before output")
    fd = open_directory(a.output_dir)
    receipts = {}
    try:
        _atomic_new(a.output_dir, a.stem + "-source.json", snapshot, directory_fd=fd, receipt_sink=receipts)
        bundle_path = _atomic_new(a.output_dir, a.stem + "-bundle.json", canonical(bundle), directory_fd=fd, receipt_sink=receipts)
    finally:
        os.close(fd)
    verify_bound_artifacts(receipts)
    cli_out = io.StringIO()
    with contextlib.redirect_stdout(cli_out):
        result = research_desk_cli.main(["--input", bundle_path, "--output-dir", a.output_dir, "--stem", a.stem])
    if result != 0:
        return result
    response = json.loads(cli_out.getvalue())
    if a.eval_origins:
        eval_score = evaluate_real(candles, adapter, origins=a.eval_origins,
                                   fee_bps=a.fee_bps, slippage_bps=a.slippage_bps)
        eval_score["source_sha256"] = source_sha
        eval_score["source_captured_at"] = captured_at
        fd = open_directory(a.output_dir)
        try:
            eval_path = _atomic_new(a.output_dir, a.stem + "-real-evaluation.json", canonical(eval_score),
                                    directory_fd=fd, receipt_sink=receipts)
        finally:
            os.close(fd)
        response["retrospective_evaluation"] = eval_path
    fd = open_directory(a.output_dir)
    try:
        receipt_path = _atomic_new(a.output_dir, a.stem + "-source-receipt.json", canonical(receipts), directory_fd=fd)
    finally:
        os.close(fd)
    verify_bound_artifacts(receipts)
    response["source_receipt"] = receipt_path
    print(json.dumps(response, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
