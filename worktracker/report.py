"""Өдрийн тайлан (HTML, текст, CSV) үүсгэнэ."""
from __future__ import annotations

import csv
import html
import io
from datetime import datetime
from pathlib import Path

from .config import reports_dir
from .storage import Storage


def fmt_dur(seconds: float) -> str:
    """3725 -> '1 ц 02 мин'"""
    m = int(round(seconds / 60.0))
    h, m = divmod(m, 60)
    return f"{h} ц {m:02d} мин" if h else f"{m} мин"


def _minutes_between(start: str, end: str) -> float:
    fmt = "%H:%M:%S"
    return (datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds() / 60.0


def _merge_periods(periods: list[dict]) -> list[dict]:
    """Дараалсан ижил төлөвтэй үеүдийг нэгтгэнэ (програм дахин эхэлсэн гэх мэт)."""
    out: list[dict] = []
    for p in periods:
        if p["end"] <= p["start"]:
            continue
        if out and out[-1]["status"] == p["status"] and p["start"] <= out[-1]["end"]:
            out[-1]["end"] = max(out[-1]["end"], p["end"])
        else:
            out.append(dict(p))
    return out


def build_report(storage: Storage, day: str, employee: str, min_away_min: float = 1) -> dict:
    d = storage.get_day(day)
    work, away = d["work_seconds"], d["away_seconds"]
    total_apps = sum(s for _, s in d["apps"]) or 1.0
    away_periods = [
        p
        for p in _merge_periods(d["periods"])
        if p["status"] == "away" and _minutes_between(p["start"], p["end"]) >= min_away_min
    ]
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    name = employee or "(нэргүй)"

    # ---------------------------------------------------------------- текст
    lines = [
        f"Өдрийн тайлан — {name}",
        f"Огноо: {day}",
        f"Ажилласан: {fmt_dur(work)}",
        f"Ажлаас гарсан: {fmt_dur(away)}",
        f"Анх ирсэн: {d['first_seen'] or '-'}   Сүүлд: {d['last_seen'] or '-'}",
        "",
        "Апп тус бүр:",
    ]
    for app, sec in d["apps"]:
        lines.append(f"  {app:<30} {fmt_dur(sec):>12}  {sec / total_apps * 100:5.1f}%")
    if away_periods:
        lines += ["", "Ажлаас гарсан үеүд:"]
        for p in away_periods:
            lines.append(f"  {p['start'][:5]} – {p['end'][:5]}  ({fmt_dur(_minutes_between(p['start'], p['end']) * 60)})")
    lines += ["", f"Тайлан гаргасан: {generated}"]
    text = "\n".join(lines)

    # ---------------------------------------------------------------- HTML
    e = html.escape
    app_rows = "".join(
        f"""<tr><td>{e(app)}</td><td class="num">{fmt_dur(sec)}</td>
        <td class="bar"><div style="width:{sec / total_apps * 100:.1f}%"></div></td>
        <td class="num">{sec / total_apps * 100:.1f}%</td></tr>"""
        for app, sec in d["apps"]
    ) or '<tr><td colspan="4">Мэдээлэл алга</td></tr>'
    away_rows = "".join(
        f"<tr><td>{p['start'][:5]} – {p['end'][:5]}</td>"
        f"<td class='num'>{fmt_dur(_minutes_between(p['start'], p['end']) * 60)}</td></tr>"
        for p in away_periods
    ) or '<tr><td colspan="2">Байхгүй</td></tr>'

    html_doc = f"""<!doctype html>
<html lang="mn"><head><meta charset="utf-8">
<title>Өдрийн тайлан — {e(name)} — {day}</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; color:#1f2328; background:#fff;
        max-width: 720px; margin: 24px auto; padding: 0 16px; }}
 h1 {{ font-size: 20px; margin: 0 0 4px; }}
 .sub {{ color:#656d76; margin-bottom: 20px; }}
 .cards {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom: 24px; }}
 .card {{ flex:1 1 150px; border:1px solid #d0d7de; border-radius:8px; padding:12px; }}
 .card .k {{ color:#656d76; font-size:13px; }}
 .card .v {{ font-size:20px; font-weight:600; margin-top:4px; }}
 .work .v {{ color:#1a7f37; }} .away .v {{ color:#cf222e; }}
 h2 {{ font-size:16px; margin: 24px 0 8px; }}
 table {{ width:100%; border-collapse: collapse; font-size:14px; }}
 td, th {{ padding:6px 8px; border-bottom:1px solid #eaeef2; text-align:left; }}
 th {{ color:#656d76; font-weight:500; }}
 .num {{ text-align:right; white-space:nowrap; }}
 .bar {{ width:35%; }} .bar div {{ height:10px; background:#2f81f7; border-radius:3px; min-width:2px; }}
 .foot {{ color:#8c959f; font-size:12px; margin-top:24px; }}
</style></head><body>
<h1>Өдрийн тайлан — {e(name)}</h1>
<div class="sub">{day}</div>
<div class="cards">
  <div class="card work"><div class="k">Ажилласан</div><div class="v">{fmt_dur(work)}</div></div>
  <div class="card away"><div class="k">Ажлаас гарсан</div><div class="v">{fmt_dur(away)}</div></div>
  <div class="card"><div class="k">Анх ирсэн / Сүүлд</div>
       <div class="v">{(d['first_seen'] or '-')[:5]} / {(d['last_seen'] or '-')[:5]}</div></div>
</div>
<h2>Апп тус бүр дээр ажилласан хугацаа</h2>
<table><tr><th>Апп</th><th class="num">Хугацаа</th><th></th><th class="num">%</th></tr>{app_rows}</table>
<h2>Ажлаас гарсан үеүд (≥ {min_away_min:g} мин)</h2>
<table><tr><th>Цаг</th><th class="num">Үргэлжилсэн</th></tr>{away_rows}</table>
<div class="foot">Тайлан гаргасан: {generated}. Дэлгэцийн зураг, камерын бичлэг хадгалаагүй.</div>
</body></html>"""

    # ---------------------------------------------------------------- CSV
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "employee", "app", "minutes"])
    for app, sec in d["apps"]:
        w.writerow([day, name, app, round(sec / 60.0, 1)])

    return {"day": day, "employee": name, "data": d, "text": text, "html": html_doc, "csv": buf.getvalue()}


def save_report(report: dict) -> tuple[Path, Path]:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in report["employee"]) or "employee"
    base = reports_dir() / f"{report['day']}_{safe}"
    html_path = base.with_suffix(".html")
    csv_path = base.with_suffix(".csv")
    html_path.write_text(report["html"], encoding="utf-8")
    csv_path.write_text(report["csv"], encoding="utf-8-sig")  # Excel кирилл зөв уншина
    return html_path, csv_path
