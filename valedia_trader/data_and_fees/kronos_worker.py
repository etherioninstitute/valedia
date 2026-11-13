"""Kronos worker process.

Run by path with the interpreter that has torch and Kronos's dependencies, never imported by the trading process:

    python kronos_worker.py <kronos_source_dir> <model_dir> <tokenizer_dir> [device]

The device defaults to mps when torch reports it, else cpu. The weights are read from the local folders
and the hub is never asked (HF_HUB_OFFLINE=1). It imports nothing from valedia_trader.

Protocol, one JSON object per line each way on stdin/stdout. The first line out is {"ready": true, "device": ...}.
    request  {"id", "timestamps", "open", "high", "low", "close", "volume", "future_timestamps", "paths", "seed"}
    reply    {"id", "final_closes": [one close per path]}   or   {"id", "error": "..."}

Each path is its own draw: Kronos's `sample_count` averages its samples into one path, so the window is
repeated `paths` times with `sample_count=1`. torch is seeded from the request, so the same request
gives the same paths. Anything a library prints goes to stderr, so stdout carries only replies.
"""
import json
import os
import sys

os.environ["HF_HUB_OFFLINE"] = "1"                    # the weights are local; never phone home
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

FIELDS = ("open", "high", "low", "close", "volume")
MAX_CONTEXT = 512


def _load(source, model_dir, tokenizer_dir, device):
    sys.path.insert(0, source)
    import torch
    from model import Kronos, KronosPredictor, KronosTokenizer
    if device is None:
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    tokenizer = KronosTokenizer.from_pretrained(tokenizer_dir)
    model = Kronos.from_pretrained(model_dir)
    tokenizer.eval()
    model.eval()
    return torch, KronosPredictor(model, tokenizer, device=device, max_context=MAX_CONTEXT), device


def _answer(torch, predictor, req):
    import numpy as np
    import pandas as pd
    window = pd.DataFrame({f: np.asarray(req[f], dtype=float) for f in FIELDS})
    xts = pd.Series(pd.to_datetime(req["timestamps"]))
    yts = pd.Series(pd.to_datetime(req["future_timestamps"]))
    n = int(req["paths"])
    if len(window) > MAX_CONTEXT or len(xts) != len(window) or not len(yts) or n < 1:
        raise ValueError(f"bad request: {len(window)} bars, {len(xts)} timestamps, {len(yts)} future dates, {n} paths")
    torch.manual_seed(int(req["seed"]))
    with torch.no_grad():
        outs = predictor.predict_batch(df_list=[window] * n, x_timestamp_list=[xts] * n, y_timestamp_list=[yts] * n,
                                       pred_len=len(yts), T=1.0, top_k=0, top_p=0.9, sample_count=1, verbose=False)
    return [float(o["close"].iloc[-1]) for o in outs]


def main(argv) -> int:
    out, sys.stdout = sys.stdout, sys.stderr          # library chatter goes to stderr; replies alone go to `out`
    def reply(obj):
        out.write(json.dumps(obj) + "\n")
        out.flush()
    torch, predictor, device = _load(argv[0], argv[1], argv[2], argv[3] if len(argv) > 3 else None)
    reply({"ready": True, "device": device})
    for line in sys.stdin:
        if not line.strip():
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            reply({"id": rid, "final_closes": _answer(torch, predictor, req)})
        except Exception as exc:  # the parent decides what to do; the worker stays up
            reply({"id": rid, "error": f"{type(exc).__name__}: {exc}".replace("\n", " ")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
