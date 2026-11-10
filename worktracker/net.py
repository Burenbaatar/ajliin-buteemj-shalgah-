"""HTTPS/SSL туслах.

PyInstaller-ээр багцалсан macOS апп системийн гэрчилгээг олж чаддаггүй тул
certifi-ийн гэрчилгээний багцыг ашиглана (Windows дээр ч адил ажиллана).
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

_ctx: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    global _ctx
    if _ctx is None:
        try:
            import certifi

            _ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            _ctx = ssl.create_default_context()
    return _ctx


def post_json(url: str, payload: dict | bytes, headers: dict, timeout: float = 20) -> bytes:
    """POST хийж хариуг буцаана. HTTP алдааны үед серверийн мессежтэй RuntimeError."""
    data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from None
