"""5-р гишүүн — админы самбарын тооцоо: төлөв, хоцролт, бүтээмж, өдрийн явц, камерын шалгалт, 8 хавтан,
7 хоног, хэлтэс, зөвшөөрсөн түр зогсолт."""
STATUS = {"work": "Ажиллаж байна", "away": "Гарсан", "offline": "Офлайн", "absent": "Ирээгүй"}
COLUMNS = ("Ажилтан", "Одоо", "Одоогийн апп", "Ажилласан", "Гарсан", "Ирсэн – Явсан", "Хоцорсон", "Бүтээмж",
           "Өдрийн явц", "Өөр хүн", "Баталгаажаагүй")
TILES = ("Хүн", "Ажиллаж байна", "Гарсан", "Офлайн", "Нийт ажилласан", "Дундаж / хүн", "Хоцорсон", "Ирээгүй")


def minutes(seconds):
    return f"{seconds / 60:.0f} мин"


def hours_minutes(seconds):
    """8760 → «2ц 26м»."""
    return f"{int(seconds // 3600)}ц {int(seconds % 3600 // 60)}м"


def duration(seconds):
    """45 → «45 сек», 600 → «10 мин», 4000 → «1ц 6м»."""
    if seconds < 60:
        return f"{seconds:.0f} сек"
    return minutes(seconds) if seconds < 3600 else hours_minutes(seconds)


def to_minutes(hhmm):
    """«09:30» → 570 (өдрийн эхнээс хойш хэдэн минут)."""
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


def late_seconds(first, settings):
    """Анх ирсэн цаг ажил эхлэх цагаас хүлцлээс илүү хоцорсон бол хоцорсон секунд, үгүй бол 0."""
    late = to_minutes(first) - to_minutes(settings["work_start"])
    return late * 60 if late > settings["late_grace_min"] else 0


def productivity_pct(apps, settings):
    """Ажилласан хугацааны хэдэн хувь нь бүтээмжгүй програм/сайт (жишээ нь YouTube) биш байсан бэ."""
    total = sum(apps.values())
    if not total:
        return None
    bad = sum(seconds for name, seconds in apps.items()
              if any(word.casefold() in name.casefold() for word in settings["unproductive"]))
    return round((total - bad) / total * 100)


def timeline_bar(minutes_map):
    """08:00–20:00-ийн зурвас, 30 минут тутам нэг тэмдэгт: █ ажилласан, ▒ гарсан, · байхгүй."""
    bar = ""
    for start in range(8 * 60, 20 * 60, 30):
        states = {minutes_map.get(f"{m // 60:02d}:{m % 60:02d}") for m in range(start, start + 30)}
        bar += "█" if "work" in states else "▒" if "away" in states else "·"
    return bar


def add_approved_pauses(records, pauses, date):
    """Админ зөвшөөрсөн түр зогсолтын хугацааг тухайн өдрийн «Ажилласан»-д нэмж, «Гарсан»-аас хасна.
    Өдрийн явцад тэр минутууд ажилласан (█) болно. Файлд бичихгүй — зөвхөн самбарт тооцно,
    тиймээс админ дараа нь татгалзвал буцаад хасагдана."""
    for pause in pauses:
        record = records.get(pause["employee_id"])
        seconds = pause.get("seconds")
        if not record or pause.get("status") != "approved" or not seconds or not pause["time"].startswith(date):
            continue
        record["work"] += seconds
        record["away"] = max(0, record["away"] - seconds)
        start = to_minutes(pause["time"][11:16])
        end = min((start * 60 + int(pause["time"][17:19]) + seconds) // 60, 24 * 60 - 1)
        for minute in range(start, int(end) + 1):
            record["minutes"][f"{minute // 60:02d}:{minute % 60:02d}"] = "work"
    return records


def ago(seconds):
    return "дөнгөж сая" if seconds < 60 else f"{seconds // 60:.0f} мин өмнө"


def row_for(employee, record, live, settings, now):
    """«Ажилтнууд» хүснэгтийн нэг мөр. record = өнөөдрийн бичлэг (эсвэл None), live = шууд төлөв (эсвэл None)."""
    record = record or {"work": 0, "away": 0, "apps": {}, "minutes": {}}
    times = sorted(record["minutes"])
    status = live["status"] if live and live["status"] in ("work", "away") else "offline" if times else "absent"
    now_text = "● " + STATUS[status]
    if status == "away" and live.get("reason"):
        now_text += f" ({live['reason']})"
    if live and live.get("camera") is False:          # камер ажиллахгүй — хэн сууж байгааг шалгаагүй
        now_text += " (камергүй)"
    if live:
        now_text += " · " + ago(now - live["time"])
    late = late_seconds(times[0], settings) if times else 0
    pct = productivity_pct(record["apps"], settings)
    computer = record.get("computer", "")
    values = (employee["name"] + (f" · {computer}" if computer else ""), now_text,
              live["app"] if status in ("work", "away") else "—",
              duration(record["work"]), duration(record["away"]),
              f"{times[0]} – {times[-1]}" if times else "— – —",
              hours_minutes(late) if late else "—",
              f"{pct}%" if pct is not None else "—",
              timeline_bar(record["minutes"])) + camera_columns(record.get("camera"))
    return {"status": status, "work": record["work"], "late": late > 0, "pct": pct, "values": values}


def camera_columns(camera):
    """Камерын шалгалт: бүртгэлтэй ажилтны оронд өөр хүн суусан, амьд хүн гэж баталгаажаагүй хугацаа."""
    if not camera:                                       # камер ажиллаагүй (жишээ нь камергүй компьютер)
        return ("—", "—")
    return duration(camera.get("other_seconds", 0)), duration(camera.get("unverified_seconds", 0))


def tiles(rows):
    """Дээд талын 8 хавтангийн утга."""
    total = sum(row["work"] for row in rows)
    count = {status: sum(row["status"] == status for row in rows) for status in STATUS}
    return {"Хүн": len(rows), "Ажиллаж байна": count["work"], "Гарсан": count["away"],
            "Офлайн": count["offline"] + count["absent"], "Нийт ажилласан": duration(total),
            "Дундаж / хүн": duration(total / len(rows)) if rows else "0 сек",
            "Хоцорсон": sum(row["late"] for row in rows), "Ирээгүй": count["absent"]}


def week_row(employee, week_records):
    """7 хоногийн мөр: нэр, Да–Ня өдөр бүрийн ажилласан цаг, нийт."""
    seconds = [day.get(employee["id"], {}).get("work", 0) for day in week_records]
    return [employee["name"]] + [f"{s / 3600:.1f}ц" if s else "—" for s in seconds] + [f"{sum(seconds) / 3600:.1f}ц"]


def department_rows(employees, rows):
    """Хэлтэс бүрийн мөр: хэлтэс, хүн, ажиллаж байна, нийт ажилласан, дундаж бүтээмж."""
    groups = {}
    for employee, row in zip(employees, rows):
        groups.setdefault(employee.get("department") or "—", []).append(row)
    result = []
    for name, group in sorted(groups.items()):
        pcts = [row["pct"] for row in group if row["pct"] is not None]
        result.append((name, len(group), sum(row["status"] == "work" for row in group),
                       duration(sum(row["work"] for row in group)), f"{sum(pcts) / len(pcts):.0f}%" if pcts else "—"))
    return result
