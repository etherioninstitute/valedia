#!/usr/bin/env python3
"""Move trading credentials into ~/.hermes/secrets (owner-only), without printing any value.

Copies credential variables from a source .env and the exchange key files beside it. Behaviour
flags (anything that switches live trading or automation on) are deliberately NOT copied; only
ALPACA_PAPER=true is written so the paper endpoint is explicit. Existing destination values are
kept unless --force. Dry run by default; pass --apply to write."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

CREDENTIAL = re.compile(r"(API_KEY|API_SECRET|SECRET_KEY|SECRET|PRIVATE_KEY|PUBLIC_KEY|USER_KEY|KEY_ID|WALLET|"
                        r"PASSPHRASE|TOKEN|RPC_URL|SIGNAL_ID)$")
PATH_VARS = {"KALSHI_PRIVATE_KEY_PATH": "kalshi_private_key.pem", "COINBASE_KEY_FILE": "coinbase_key.json"}
KEY_FILES = ("kalshi_private_key.pem", "kalshi_api_key_id.txt", "coinbase_key.json", "kraken_keys.json", "okx_keys.json")
SAFE_DEFAULTS = {"ALPACA_PAPER": "true"}


def parse(path: Path) -> dict[str, str]:
    out = {}
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.replace("export ", "", 1).strip()] = v.strip()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="directory holding the old .env and key files")
    ap.add_argument("--dest", default="~/.hermes/secrets")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true", help="overwrite values already present at the destination")
    args = ap.parse_args(argv)
    src, dest = Path(args.source).expanduser(), Path(args.dest).expanduser()
    env_src = src / ".env"
    if not env_src.is_file():
        print(f"no .env in {src}", file=sys.stderr)
        return 2
    old = parse(env_src)
    target = dest / "trading.env"
    current = parse(target) if target.is_file() else {}
    chosen, skipped, empty = {}, [], []
    for name, value in old.items():
        if name in PATH_VARS:
            chosen[name] = str(dest / PATH_VARS[name])
        elif CREDENTIAL.search(name):
            if value:
                chosen[name] = value
            else:
                empty.append(name)
        else:
            skipped.append(name)
    chosen.update(SAFE_DEFAULTS)
    merged = dict(current)
    written, kept = [], []
    for name, value in chosen.items():
        if name in current and not args.force and name not in PATH_VARS and name not in SAFE_DEFAULTS:
            kept.append(name)
        else:
            merged[name] = value
            written.append(name)
    files = [f for f in KEY_FILES if (src / f).is_file()]
    print(f"source: {env_src}")
    print(f"destination: {target}")
    print(f"credentials to write ({len(written)}): {' '.join(sorted(written))}")
    print(f"already present, kept ({len(kept)}): {' '.join(sorted(kept))}")
    print(f"empty at the source, nothing to copy ({len(empty)}): {' '.join(sorted(empty))}")
    print(f"not copied, behaviour flags and paths ({len(skipped)}): {' '.join(sorted(skipped))}")
    print(f"key files ({len(files)}): {' '.join(files)}")
    if not args.apply:
        print("(dry run; pass --apply to write)")
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    for f in files:
        out = dest / f
        if not out.exists() or args.force:
            shutil.copy2(src / f, out)
        os.chmod(out, 0o600)
    body = ["# Valedia Trader credentials. Owner-only. Loaded by the trading code, not by Hermes at large.",
            "# Flags that switch on live trading are intentionally absent; set them deliberately, one at a time."]
    body += [f"{k}={merged[k]}" for k in sorted(merged)]
    tmp = target.with_suffix(".env.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(body) + "\n")
    os.replace(tmp, target)
    os.chmod(target, 0o600)
    print(f"written: {target} (mode 600), {len(merged)} variables; directory mode 700")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
