# Ажлын бүтээмж — тайлбарын хавтас

Энэ хавтсанд **"Ажлын бүтээмж"** програмын кодыг тайлбарласан баримтууд болон төслийг танилцуулах слайд байна.
Кодын файлуудад өөрчлөлт ороогүй — бүх тайлбар энд тусдаа.

| Юу | Хэнд | Хаана |
|---|---|---|
| **Танилцуулга (PowerPoint)** | Удирдлага, хамт олон | [`Ajliin_buteemj_tanilcuulga.pptx`](Ajliin_buteemj_tanilcuulga.pptx) |
| **Програм бүхэлдээ хэрхэн ажилладаг** | Хөгжүүлэгч, багийн гишүүн | [`kod/00_ajillah_urgal.md`](kod/00_ajillah_urgal.md) |
| **Кодын файл бүрийн тайлбар** | Хөгжүүлэгч | доорх жагсаалт |
| Слайдад орсон дэлгэцийн зургууд | — | [`zurag/`](zurag/) |

Код өөрөө: `C:\Users\Burene\Documents\ajliin buteemj shalgah\ajliin-buteemj-shalgah-\`
(GitHub: `Burenbaatar/ajliin-buteemj-shalgah-`). Хувилбар: **3.1.1**.

## Унших дараалал (шинэ хөгжүүлэгчид)

1. [`00_ajillah_urgal.md`](kod/00_ajillah_urgal.md) — том зураг: юу хаана ажилладаг, өгөгдөл хэрхэн урсдаг.
2. [`main.md`](kod/main.md) — програм хаанаас эхэлдэг.
3. [`app.md`](kod/worktracker/app.md) → [`tracker.md`](kod/worktracker/tracker.md) → [`presence.md`](kod/worktracker/presence.md) → [`activity.md`](kod/worktracker/activity.md) — цаг хэрхэн тоологддог.
4. [`storage.md`](kod/worktracker/storage.md) → [`cloud.md`](kod/worktracker/cloud.md) → [`share.md`](kod/worktracker/share.md) — өгөгдөл хаана хадгалагдаж, хэрхэн админ руу очдог.
5. [`gui.md`](kod/worktracker/gui.md) → [`dashboard.md`](kod/worktracker/dashboard.md) → [`dashdata.md`](kod/worktracker/dashdata.md) — цонх ба самбар.
6. Бусад: тохиргоо, шинэчлэл, суулгалт.

## Кодын файлууд

### Үндсэн хавтас
| Файл | Тайлбар | Товч |
|---|---|---|
| `main.py` | [main.md](kod/main.md) | Эхлүүлэх файл, командын сонголтууд |
| `build.py` | [build.md](kod/build.md) | exe бүтээх, ажилтнуудад шинэчлэл байршуулах |
| `AjliinButeemj.spec` | [AjliinButeemj_spec.md](kod/AjliinButeemj_spec.md) | PyInstaller-ийн тохиргоо |

### `worktracker/` — програмын код
| Файл | Тайлбар | Товч |
|---|---|---|
| `__init__.py` | [\_\_init\_\_.md](kod/worktracker/__init__.md) | Нэр, хувилбар |
| `app.py` | [app.md](kod/worktracker/app.md) | Хэсгүүдийг угсардаг "цөм" |
| `tracker.py` | [tracker.md](kod/worktracker/tracker.md) | Секунд тутам ажиллаж байна уу гэж шийддэг гол логик |
| `presence.py` | [presence.md](kod/worktracker/presence.md) | Камер, царай илрүүлэх, зураг/таглагдсан камер |
| `activity.py` | [activity.md](kod/worktracker/activity.md) | Идэвхтэй апп, гар/хулгана, дэлгэцийн түгжээ |
| `storage.py` | [storage.md](kod/worktracker/storage.md) | Ажилтны компьютер дээрх JSON хадгалалт |
| `cloud.py` | [cloud.md](kod/worktracker/cloud.md) | Админы хавтас руу хуулах хуваарь |
| `share.py` | [share.md](kod/worktracker/share.md) | Админы хуваалцсан хавтастай ажиллах |
| `gui.py` | [gui.md](kod/worktracker/gui.md) | Үндсэн цонх, tray |
| `dashboard.py` | [dashboard.md](kod/worktracker/dashboard.md) | Админы самбар (цонх) |
| `dashdata.py` | [dashdata.md](kod/worktracker/dashdata.md) | Самбарын тооцоо (хоцролт, бүтээмж …) |
| `alerts.py` | [alerts.md](kod/worktracker/alerts.md) | Telegram мэдэгдэл |
| `admin_setup.py` | [admin_setup.md](kod/worktracker/admin_setup.md) | Админы компьютерийг нэг удаа тохируулах |
| `updater.py` | [updater.md](kod/worktracker/updater.md) | Автомат шинэчлэл |
| `config.py` | [config.md](kod/worktracker/config.md) | Тохиргоо, файлын замууд, нууц үг |
| `report.py` | [report.md](kod/worktracker/report.md) | Өдрийн HTML/CSV тайлан |
| `notify.py` | [notify.md](kod/worktracker/notify.md) | Имэйл / Telegram илгээх |
| `system.py` | [system.md](kod/worktracker/system.md) | Лог, нэг хувь, автоматаар эхлэх |
| `net.py` | [net.md](kod/worktracker/net.md) | HTTPS туслах |
| `uiutil.py` | [uiutil.md](kod/worktracker/uiutil.md) | Цонхны өнгө, товч, арын thread |
