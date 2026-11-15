"""Интернэтийн цаг — админы компьютерийн цаг буруу эсэхийг шалгана (зөвхөн Python стандарт сан).

HTTPS сайтуудын хариуны `Date` толгойг ашиглана: TLS-ээр баталгаажсан тул дотоод сүлжээнд хуурамчаар өөрчлөх
боломжгүй, UDP 123 (NTP) хаалттай сүлжээнд ч ажиллана. Нарийвчлал ~1 сек — бидэнд минутын түвшин хангалттай.
Хүсэлтэд ямар ч хувийн мэдээлэл явуулахгүй (HEAD хүсэлт, хоосон).
"""
from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)

# api.telegram.org — програм Telegram мэдэгдэлд аль хэдийн ашигладаг
SOURCES = ("https://api.telegram.org", "https://www.google.com", "https://www.cloudflare.com")
AGREE_SEC = 5.0


def _one(url: str, timeout: float) -> float | None:
    """Нэг сайтаас: (интернэтийн цаг − энэ компьютерийн цаг) сек."""
    t0, m0 = time.time(), time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=timeout) as resp:
            date = resp.headers.get("Date")
    except urllib.error.HTTPError as exc:
        date = exc.headers.get("Date") if exc.headers else None
    except (urllib.error.URLError, OSError, ValueError):
        return None
    rtt = time.monotonic() - m0
    if not date or rtt > timeout:
        return None
    try:
        server = parsedate_to_datetime(date).timestamp()
    except (TypeError, ValueError, IndexError):
        return None
    # Date нь бүхэл секунд (доош тайрсан) → +0.5; хариу ирэх агшин ≈ илгээснээс хойш замын хагас
    return server + 0.5 - (t0 + rtt / 2)


def measure(timeout: float = 5.0, sources=None) -> float | None:
    """Энэ компьютерийн цаг интернэтийнхээс хэр зөрүүтэй (эерэг = хоцорч байна). Интернэтгүй бол None.
    Хоёр сайт AGREE_SEC дотор тохирвол тэр дороо зогсоно; бусад тохиолдолд олдсон утгуудын медиан."""
    found: list[float] = []
    for url in sources or SOURCES:
        off = _one(url, timeout)
        if off is None:
            continue
        found.append(off)
        if len(found) >= 2 and abs(found[-1] - found[-2]) <= AGREE_SEC:
            return (found[-1] + found[-2]) / 2
    if not found:
        return None
    found.sort()
    return found[len(found) // 2]
