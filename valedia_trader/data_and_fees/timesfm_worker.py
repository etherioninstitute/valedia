"""TimesFM worker process.

Run by path, never imported by the trading process:

    python timesfm_worker.py <checkpoint_dir> <max_context> <max_horizon> <batch_size>

It exists because torch ships its own OpenMP runtime and scikit-learn and xgboost ship another; two
copies in one process crash on macOS. Here torch is alone.

Protocol, one line each way on stdin/stdout, fields separated by tabs. Replies start with MARK so
anything a library prints is ignored:
    request   <horizon> <in.npz> <out.npz>      in.npz:  flat (float32), lengths (int64)
    reply     @@TFM ok | @@TFM error <text>      out.npz: point (n, h), quant (n, h, 10)
"""
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")          # the weights are local; never phone home

import numpy as np  # noqa: E402

MARK = "@@TFM"


def main(argv) -> int:
    path, max_context, max_horizon, batch = argv[0], int(argv[1]), int(argv[2]), int(argv[3])
    import timesfm
    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(path, torch_compile=False)
    model.compile(timesfm.ForecastConfig(
        max_context=max_context, max_horizon=max_horizon, normalize_inputs=True, use_continuous_quantile_head=True,
        force_flip_invariance=True, infer_is_positive=False, fix_quantile_crossing=True, per_core_batch_size=batch))
    print(MARK, "ready", flush=True)
    for line in sys.stdin:
        try:
            horizon, src, dst = line.rstrip("\n").split("\t")
            with np.load(src) as req:
                flat, lengths = req["flat"], req["lengths"]
            bounds = np.concatenate([[0], np.cumsum(lengths)])
            inputs = [flat[bounds[i]: bounds[i + 1]] for i in range(len(lengths))]
            n = len(inputs)
            point, quant = model.forecast(horizon=int(horizon), inputs=inputs)     # pads `inputs` in place
            np.savez(dst, point=np.asarray(point)[:n], quant=np.asarray(quant)[:n])
            print(MARK, "ok", flush=True)
        except Exception as exc:  # the parent decides what to do; the worker stays up
            print(MARK, "error", f"{type(exc).__name__}: {exc}".replace("\n", " "), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
