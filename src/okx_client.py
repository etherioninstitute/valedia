#!/usr/bin/env python3
"""OKX REST v5 client. Reads always. Writes only if OKX_LIVE=1 and not halted.

Mac calls go through socks5h://127.0.0.1:1080. Private calls fail closed
unless the tunnel exit IP is the Contabo allowlist address.
No withdraw. CLI --live does not place an order.
"""
import base64
import hashlib
import hmac
import json
import os
import platform
import socket
import ssl
import sys
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal

import requests

CONTABO_IP = os.environ.get("VALEDIA_VPS_EGRESS_IP", "85.239.234.32")
# ponytail: a US key 50119s on www.okx.com. us.okx.com is the v5 host. OKX_BASE overrides.
BASE = os.environ.get("OKX_BASE", "https://us.okx.com").rstrip("/")
OKX_HOST = urllib.parse.urlparse(BASE).hostname or "www.okx.com"
KEY_PATH = os.path.expanduser("~/.openclaw/okx_keys.json")


class OKXError(RuntimeError):
    pass


class OKXLiveRefused(OKXError):
    pass


class OKXProxyError(OKXError):
    pass


class OKXAPIError(OKXError):
    pass


def live_enabled():
    return os.environ.get("OKX_LIVE", "").strip() == "1"


def load_keys(path=None):
    path = path or KEY_PATH
    api_key = secret = passphrase = ""
    try:
        with open(path) as f:
            doc = json.load(f)
        if isinstance(doc, dict):
            api_key = str(doc.get("api_key") or "").strip()
            secret = str(doc.get("secret") or "").strip()
            passphrase = str(doc.get("passphrase") or "").strip()
    except Exception:
        api_key = secret = passphrase = ""
    if not (api_key and secret and passphrase):
        api_key = os.environ.get("OKX_API_KEY", "").strip()
        secret = (os.environ.get("OKX_SECRET") or os.environ.get("OKX_API_SECRET") or "").strip()
        passphrase = os.environ.get("OKX_PASSPHRASE", "").strip()
    if not (api_key and secret and passphrase):
        raise OKXError("okx keys missing")
    return {"api_key": api_key, "secret": secret, "passphrase": passphrase}


def _sign(secret, ts, method, path, body):
    msg = "%s%s%s%s" % (ts, method.upper(), path, body or "")
    dig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.b64encode(dig).decode()


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _nonzero(value):
    try:
        return Decimal(str(value)) != 0
    except Exception:
        return False


def _ipv4(text):
    parts = str(text or "").strip().split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 and str(int(p)) == p for p in parts)
    except Exception:
        return False


def _socks_socket(proxies):
    if not proxies:
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    url = proxies.get("https") or proxies.get("http") or ""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("socks5", "socks5h") or not parsed.hostname or not parsed.port:
        raise OKXProxyError("socks proxy required")
    import socks
    sock = socks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
    sock.set_proxy(socks.SOCKS5, parsed.hostname, parsed.port, rdns=True)
    return sock


def _read_http(sock):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 1048576:
            raise OKXAPIError("http header too large")
    if b"\r\n\r\n" not in buf:
        raise OKXAPIError("http truncated")
    head, rest = buf.split(b"\r\n\r\n", 1)
    try:
        status = int(head.split(b"\r\n", 1)[0].split()[1])
    except Exception:
        raise OKXAPIError("http status unreadable")
    headers = {}
    for line in head.split(b"\r\n")[1:]:
        if b":" in line:
            key, val = line.split(b":", 1)
            headers[key.decode("ascii", "replace").lower()] = val.strip().decode("ascii", "replace")
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = _dechunk(rest, sock)
    else:
        need = int(headers.get("content-length") or "0")
        while len(rest) < need:
            chunk = sock.recv(65536)
            if not chunk:
                break
            rest += chunk
        body = rest[:need] if need else rest
    return status, body.decode("utf-8", "replace")


def _dechunk(rest, sock):
    parts = []
    while True:
        while b"\r\n" not in rest:
            chunk = sock.recv(65536)
            if not chunk:
                raise OKXAPIError("chunk truncated")
            rest += chunk
        line, rest = rest.split(b"\r\n", 1)
        size = int(line.split(b";", 1)[0] or b"0", 16)
        if size == 0:
            return b"".join(parts)
        while len(rest) < size + 2:
            chunk = sock.recv(65536)
            if not chunk:
                raise OKXAPIError("chunk truncated")
            rest += chunk
        parts.append(rest[:size])
        rest = rest[size + 2:]


def _https_v4(method, host, ip, path, headers, body, proxies):
    # ponytail: OKX has AAAA; hostname SOCKS would egress VPS v6 and miss the v4 allowlist.
    if not _ipv4(ip):
        raise OKXProxyError("okx target is not ipv4")
    sock = _socks_socket(proxies)
    sock.settimeout(20)
    try:
        sock.connect((ip, 443))
        tls = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        raw = body.encode() if body else b""
        hdr = dict(headers)
        hdr["Host"] = host
        hdr.setdefault("Accept", "application/json")
        hdr["Connection"] = "close"
        if raw:
            hdr["Content-Length"] = str(len(raw))
        lines = ["%s %s HTTP/1.1" % (method, path)]
        lines.extend("%s: %s" % (k, v) for k, v in hdr.items())
        tls.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + raw)
        return _read_http(tls)
    except OKXError:
        raise
    except Exception:
        raise OKXAPIError("transport failed")
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _resolve_a(host, proxies):
    # ponytail: DoH via the tunnel so the A record is chosen remotely. No AAAA.
    status, text = _https_v4(
        "GET",
        "cloudflare-dns.com",
        "1.1.1.1",
        "/dns-query?name=%s&type=A" % urllib.parse.quote(host),
        {"Accept": "application/dns-json"},
        "",
        proxies,
    )
    if status != 200:
        raise OKXProxyError("okx dns failed")
    try:
        doc = json.loads(text)
        for row in doc.get("Answer") or []:
            if row.get("type") == 1 and _ipv4(row.get("data")):
                return row["data"]
    except Exception:
        pass
    raise OKXProxyError("okx A record missing")


def _exit_ip(proxies):
    # ponytail: api4.ipify.org is v4-only. ifconfig.me answers the VPS AAAA.
    try:
        r = requests.get(
            "https://api4.ipify.org",
            proxies=proxies or None,
            timeout=20,
            headers={"User-Agent": "curl/8.0"},
        )
        r.raise_for_status()
        ip = (r.text or "").strip()
    except Exception:
        raise OKXProxyError("exit ip unreadable")
    if not _ipv4(ip):
        raise OKXProxyError("exit ip unreadable")
    return ip


class OKXClient:
    def __init__(self, keys=None, transport=None):
        self._keys = keys
        self._transport = transport
        self._proxies = None
        self._exit_ip = None

    def __repr__(self):
        return "OKXClient(keys=hidden,live=%s)" % int(live_enabled())

    def __str__(self):
        return repr(self)

    def keys(self):
        if self._keys is None:
            self._keys = load_keys()
        return self._keys

    def ensure_exit(self):
        if self._exit_ip:
            return self._exit_ip
        if self._transport is not None:
            self._exit_ip = "mock"
            return self._exit_ip
        proxies = {}
        if platform.system() == "Darwin":
            from vps_proxy import ensure_tunnel, get_proxies
            if not ensure_tunnel():
                raise OKXProxyError("tunnel down")
            proxies = get_proxies()
        ip = _exit_ip(proxies)
        if ip != CONTABO_IP:
            raise OKXProxyError("exit ip %s != %s" % (ip, CONTABO_IP))
        self._proxies = proxies or None
        self._exit_ip = ip
        return ip

    def _call(self, method, path, params=None, body=None, auth=True):
        self.ensure_exit()
        query = ""
        if params:
            query = "?" + urllib.parse.urlencode(params)
        full = path + query
        raw = ""
        if body is not None:
            raw = json.dumps(body, separators=(",", ":"))
        headers = {"Content-Type": "application/json"}
        if auth:
            keys = self.keys()
            ts = _ts()
            headers["OK-ACCESS-KEY"] = keys["api_key"]
            headers["OK-ACCESS-SIGN"] = _sign(keys["secret"], ts, method, full, raw)
            headers["OK-ACCESS-TIMESTAMP"] = ts
            headers["OK-ACCESS-PASSPHRASE"] = keys["passphrase"]
        url = BASE + full
        if self._transport is not None:
            status, text = self._transport(method, url, headers, raw)
        else:
            ip = _resolve_a(OKX_HOST, self._proxies)
            status, text = _https_v4(method, OKX_HOST, ip, full, headers, raw, self._proxies)
        return _parse(status, text)

    def _gate(self, action):
        if not live_enabled():
            raise OKXLiveRefused("%s refused (OKX_LIVE=0)" % action)
        try:
            import valedia_config as vc
        except Exception:
            raise OKXLiveRefused("%s refused (halt gate unavailable)" % action)
        vc.require_not_halted("okx.%s" % action)

    def get_balance(self):
        rows = []
        errors = []
        funding_n = None
        try:
            data = self._call("GET", "/api/v5/account/balance")
            for acct in data or []:
                eq = acct.get("totalEq")
                for d in acct.get("details") or []:
                    rows.append({
                        "acct": "trading",
                        "ccy": d.get("ccy") or "",
                        "eq": d.get("eq") if d.get("eq") not in (None, "") else (d.get("cashBal") or "0"),
                        "avail": d.get("availBal") or "0",
                        "total_eq": eq,
                    })
        except OKXAPIError as exc:
            errors.append("trading %s" % exc)
        try:
            data = self._call("GET", "/api/v5/asset/balances")
            funding_n = 0
            for d in data or []:
                funding_n += 1
                rows.append({
                    "acct": "funding",
                    "ccy": d.get("ccy") or "",
                    "eq": d.get("bal") or "0",
                    "avail": d.get("availBal") or "0",
                })
        except OKXAPIError as exc:
            errors.append("funding %s" % exc)
        if not rows and errors:
            raise OKXAPIError("; ".join(errors))
        return {"rows": rows, "errors": errors, "funding_n": funding_n}

    def get_ticker(self, inst_id="BTC-USDT"):
        data = self._call("GET", "/api/v5/market/ticker", params={"instId": inst_id}, auth=False)
        row = (data or [{}])[0]
        return {
            "instId": row.get("instId") or inst_id,
            "last": row.get("last"),
            "bid": row.get("bidPx"),
            "ask": row.get("askPx"),
        }

    def get_open_orders(self):
        data = self._call("GET", "/api/v5/trade/orders-pending")
        out = []
        for o in data or []:
            out.append({
                "ordId": o.get("ordId"),
                "instId": o.get("instId"),
                "side": o.get("side"),
                "sz": o.get("sz"),
                "state": o.get("state"),
                "ordType": o.get("ordType"),
            })
        return out

    def portfolio_summary(self):
        ip = self.ensure_exit()
        bal = self.get_balance()
        lines = ["OKX portfolio exit_ip=%s" % ip]
        if not bal["rows"]:
            lines.append("  balances: empty")
        for row in bal["rows"]:
            flag = "nonzero" if _nonzero(row["eq"]) or _nonzero(row["avail"]) else "zero"
            lines.append(
                "  %s %s eq=%s avail=%s %s" % (row["acct"], row["ccy"], row["eq"], row["avail"], flag)
            )
        if bal.get("funding_n") == 0:
            lines.append("  funding: empty")
        for err in bal["errors"]:
            lines.append("  note: %s" % err)
        try:
            orders = self.get_open_orders()
            lines.append("  open_orders=%d" % len(orders))
        except OKXAPIError as exc:
            lines.append("  open_orders_error: %s" % exc)
        return "\n".join(lines)

    def create_order(self, inst_id, side, sz, ord_type="limit", px=None, td_mode="cash"):
        self._gate("create_order")
        body = {
            "instId": str(inst_id),
            "tdMode": str(td_mode),
            "side": str(side),
            "ordType": str(ord_type),
            "sz": str(sz),
        }
        if px is not None:
            body["px"] = str(px)
        return self._call("POST", "/api/v5/trade/order", body=body)

    def cancel_order(self, inst_id, ord_id):
        self._gate("cancel_order")
        return self._call("POST", "/api/v5/trade/cancel-order", body={
            "instId": str(inst_id),
            "ordId": str(ord_id),
        })


def _parse(status, text):
    try:
        doc = json.loads(text)
    except Exception:
        raise OKXAPIError("http %s" % status)
    if not isinstance(doc, dict):
        raise OKXAPIError("http %s" % status)
    code = str(doc.get("code", ""))
    if code != "0":
        msg = str(doc.get("msg") or "")[:180]
        raise OKXAPIError("okx %s %s" % (code, msg))
    return doc.get("data")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--live" in argv:
        print("refused: --live does not place orders in this slice")
        return 2
    if argv != ["summary"]:
        print("usage: okx_client.py summary")
        return 2
    try:
        print(OKXClient().portfolio_summary())
    except OKXError as exc:
        print("okx_error: %s" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
