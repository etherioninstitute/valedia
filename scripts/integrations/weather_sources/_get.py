"""Read-only GET. httpx when installed, urllib otherwise. No browser.

Only https. The response body is text. Callers redact query secrets.
"""
from urllib.request import Request, urlopen

UA = "valedia-weather-shadow/research (contact.etherion@protonmail.com)"


def get_text(url, timeout=12, accept="text/html,application/json"):
    url = str(url or "")
    if not url.startswith("https://"):
        raise ValueError("https_only")
    headers = {"User-Agent": UA, "Accept": accept}
    try:
        import httpx
    except ImportError:
        httpx = None
    if httpx is not None:
        resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.text, "httpx"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        code = getattr(resp, "status", 200) or 200
        body = resp.read().decode("utf-8", "replace")
    if int(code) >= 400:
        raise RuntimeError("http_%s" % code)
    return body, "urllib"
