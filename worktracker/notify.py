"""Тайланг менежерт илгээнэ: имэйл (smtplib) болон/эсвэл Telegram бот."""
from __future__ import annotations

import html
import json
import logging
import smtplib
import uuid
from email.message import EmailMessage
from pathlib import Path

from .net import post_json, ssl_context
from .report import build_report, fmt_dur, save_report
from .storage import Storage

log = logging.getLogger(__name__)


def send_email(cfg: dict, subject: str, text: str, html_body: str, attachments: list[Path]) -> None:
    to = cfg["to_addrs"]
    if isinstance(to, str):
        to = [a.strip() for a in to.split(",") if a.strip()]
    if not to:
        raise ValueError("email.to_addrs хоосон байна")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("from_addr") or cfg["username"]
    msg["To"] = ", ".join(to)
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    for p in attachments:
        sub = "html" if p.suffix == ".html" else "csv"
        msg.add_attachment(p.read_bytes(), maintype="text", subtype=sub, filename=p.name)

    host, port = cfg["smtp_host"], int(cfg["smtp_port"])
    ctx = ssl_context()
    if cfg.get("use_ssl", True):
        server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.starttls(context=ctx)
    with server:
        if cfg.get("username"):
            server.login(cfg["username"], cfg["password"])
        server.send_message(msg)


def _tg_call(token: str, method: str, data: bytes, content_type: str) -> None:
    raw = post_json(
        f"https://api.telegram.org/bot{token}/{method}", data, {"Content-Type": content_type}, timeout=30
    )
    body = json.loads(raw.decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"Telegram: {body}")


def send_telegram(cfg: dict, text: str, document: Path | None = None) -> None:
    token, chat_id = cfg["bot_token"], str(cfg["chat_id"])
    if not token or not chat_id:
        raise ValueError("telegram.bot_token / chat_id хоосон байна")
    payload = json.dumps({"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML"}).encode()
    _tg_call(token, "sendMessage", payload, "application/json")

    if document is not None:
        boundary = uuid.uuid4().hex
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode(),
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{document.name}"\r\n'
                "Content-Type: text/html\r\n\r\n"
            ).encode(),
            document.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        _tg_call(token, "sendDocument", b"".join(parts), f"multipart/form-data; boundary={boundary}")


def _telegram_text(r: dict) -> str:
    d, e = r["data"], html.escape
    lines = [
        f"<b>Өдрийн тайлан — {e(r['employee'])}</b>",
        f"📅 {r['day']}",
        f"🟢 Ажилласан: <b>{fmt_dur(d['work_seconds'])}</b>",
        f"🔴 Гарсан: {fmt_dur(d['away_seconds'])}",
        f"🕘 {(d['first_seen'] or '-')[:5]} – {(d['last_seen'] or '-')[:5]}",
        "",
        "<b>Апп:</b>",
    ]
    lines += [f"• {e(app)} — {fmt_dur(sec)}" for app, sec in d["apps"][:15]]
    return "\n".join(lines)


def channels_enabled(cfg: dict) -> bool:
    return bool(cfg["email"].get("enabled") or cfg["telegram"].get("enabled"))


def is_fully_sent(cfg: dict, storage: Storage, day: str) -> bool:
    return all(storage.get_meta(f"sent:{ch}:{day}") for ch in ("email", "telegram") if cfg[ch].get("enabled"))


def send_report(cfg: dict, storage: Storage, day: str, only_unsent: bool = False) -> tuple[bool, str]:
    """Тайлан үүсгэж, идэвхтэй сувгуудаар илгээнэ. (бүгд амжилттай эсэх, мессеж)

    only_unsent=True: тухайн өдөр аль хэдийн амжилттай илгээсэн сувгийг алгасна
    (автомат илгээлтэд давхардахгүйн тулд).
    """
    r = build_report(storage, day, cfg["employee_name"], cfg["min_away_period_min"])
    html_path, csv_path = save_report(r)
    if not channels_enabled(cfg):
        return False, f"Илгээх суваг тохируулаагүй. Тайлан хадгалсан: {html_path}"

    senders = {
        "email": lambda: send_email(
            cfg["email"], f"Өдрийн тайлан — {r['employee']} — {day}", r["text"], r["html"], [html_path, csv_path]
        ),
        "telegram": lambda: send_telegram(cfg["telegram"], _telegram_text(r), html_path),
    }
    errors, sent = [], []
    for ch, send in senders.items():
        if not cfg[ch].get("enabled"):
            continue
        if only_unsent and storage.get_meta(f"sent:{ch}:{day}"):
            continue
        try:
            send()
            storage.set_meta(f"sent:{ch}:{day}", r["data"]["last_seen"] or "")
            sent.append(ch)
        except Exception as exc:  # noqa: BLE001
            log.exception("%s илгээж чадсангүй", ch)
            errors.append(f"{ch}: {exc}")

    if errors:
        return False, "Алдаа — " + "; ".join(errors) + (f" (илгээсэн: {', '.join(sent)})" if sent else "")
    return True, f"Илгээлээ ({', '.join(sent) or 'өмнө нь илгээсэн'})"
