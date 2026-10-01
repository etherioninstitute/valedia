#!/usr/bin/env python3
"""Write ~/.openclaw/okx_keys.json from the ProtonDrive OKX txt files.

Stdout is ok/path/mode only. Never prints key material.
"""
import json
import os
import sys

DEST = os.path.expanduser("~/.openclaw/okx_keys.json")
_PROTON = (
    "/Users/burtonchatelain/Library/CloudStorage/"
    "ProtonDrive-burtworthy@protonmail.com-folder/VPS/API Keys"
)
DEFAULT_PATHS = (
    os.path.join(_PROTON, "OKX API Key.txt"),
    os.path.join(_PROTON, "OKX API Sec Key.txt"),
    os.path.join(_PROTON, "OKX Passphrase.txt"),
)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read().lstrip("\ufeff").strip()


def materialize(key_path, sec_path, pass_path, dest=DEST):
    api_key = _read(key_path)
    secret = _read(sec_path)
    passphrase = _read(pass_path)
    if not api_key or not secret or not passphrase:
        raise RuntimeError("empty")
    payload = json.dumps(
        {"api_key": api_key, "secret": secret, "passphrase": passphrase},
        separators=(",", ":"),
    ).encode() + b"\n"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.chmod(dest, 0o600)
    mode = oct(os.stat(dest).st_mode & 0o777).replace("0o", "")
    return dest, mode


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    paths = list(DEFAULT_PATHS)
    dest = DEST
    if argv:
        # tests pass three sources and a dest. Production CLI takes no args.
        if len(argv) != 4:
            print("ok=0 path=%s" % dest)
            return 1
        paths = argv[:3]
        dest = argv[3]
    try:
        path, mode = materialize(paths[0], paths[1], paths[2], dest)
    except Exception:
        print("ok=0 path=%s" % dest)
        return 1
    print("ok=1 path=%s mode=%s" % (path, mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
