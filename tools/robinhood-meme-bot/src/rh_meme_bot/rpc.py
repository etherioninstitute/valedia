"""Plain JSON-RPC client over HTTP with injectable transport and retry/backoff."""
from __future__ import annotations

import itertools
import time
from typing import Any, Callable, Optional

# A transport takes a JSON-RPC request dict and returns the JSON-RPC response dict.
Transport = Callable[[dict], dict]

_RETRYABLE_CODES = {-32005, -32603, -32097}


class RpcError(Exception):
    def __init__(self, message: str, code: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _from_error(err: Any) -> RpcError:
    if not isinstance(err, dict):
        return RpcError(f"rpc error: {err!r}", retryable=False)
    code, msg = err.get("code"), str(err.get("message", ""))
    low = msg.lower()
    retry = code in _RETRYABLE_CODES or "rate limit" in low or "timeout" in low
    return RpcError(f"rpc error {code}: {msg}", code=code, retryable=retry)


def http_transport(url: str, timeout: float = 15.0) -> Transport:
    import requests  # lazy: tests with a fake transport never touch the network stack

    session = requests.Session()

    def send(payload: dict) -> dict:
        try:
            resp = session.post(url, json=payload, timeout=timeout)
        except requests.RequestException as e:
            raise RpcError(f"http failure: {e}", retryable=True) from e
        if resp.status_code == 429 or resp.status_code >= 500:
            raise RpcError(f"http {resp.status_code}", code=resp.status_code, retryable=True)
        if resp.status_code != 200:
            raise RpcError(f"http {resp.status_code}: {resp.text[:200]}", code=resp.status_code)
        try:
            return resp.json()
        except ValueError as e:
            raise RpcError(f"invalid json from rpc: {e}", retryable=True) from e

    return send


class RpcClient:
    def __init__(self, url: str, transport: Optional[Transport] = None, retries: int = 3,
                 backoff: float = 0.5, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 15.0):
        self.url = url
        self._transport = transport or http_transport(url, timeout)
        self._retries = max(0, retries)
        self._backoff = backoff
        self._sleep = sleep
        self._ids = itertools.count(1)

    def call(self, method: str, params: Optional[list] = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method,
                   "params": params or []}
        for attempt in range(self._retries + 1):
            try:
                resp = self._transport(payload)
            except RpcError as e:
                err = e
            except Exception as e:  # noqa: BLE001 - any transport failure is retryable
                err = RpcError(f"transport failure: {e}", retryable=True)
            else:
                if not isinstance(resp, dict):
                    err = RpcError("malformed rpc response", retryable=True)
                elif resp.get("error"):
                    err = _from_error(resp["error"])
                elif "result" in resp:
                    return resp["result"]
                else:
                    err = RpcError("rpc response missing result", retryable=True)
            if not err.retryable or attempt >= self._retries:
                raise err
            self._sleep(min(30.0, self._backoff * (2 ** attempt)))
        raise RpcError("unreachable")  # pragma: no cover
