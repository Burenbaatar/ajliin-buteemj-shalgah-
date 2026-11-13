# `build.py` — exe бүтээх, ажилтнуудад шинэчлэл байршуулах

← [Буцах](../README.md)

## Юу хийдэг
Нэг командаар: Python орчин бэлдэж → PyInstaller-ээр нэг файл `AjliinButeemj.exe` (Mac дээр `.app`) бүтээж → (сонголтоор) шалгаж →
(сонголтоор) **админы хавтасны `update\`-д байршуулна** — тэгснээр бүх ажилтны програм 30 мин дотор өөрөө шинэчлэгдэнэ.
Програмын нэг хэсэг биш, зөвхөн **админы компьютер дээр** ажиллуулдаг скрипт.

## Хэрэглээ
```
python build.py              # бүтээнэ → dist\AjliinButeemj.exe
python build.py --selftest   # + бүтээсэн exe-г --selftest-ээр шалгана
python build.py --publish    # + админы хавтасны update\ руу байршуулна
python build.py --clean      # build\, dist\, .venv\-г устгаад эхнээс нь
```
Ихэвчлэн: `python build.py --selftest --publish`.

## Алхмууд (`main()`)
1. Python ≥ 3.9 эсэх, Windows/macOS эсэхийг шалгана.
2. `share.json` төслийн хавтаст байгаа эсэхийг хэлнэ (байвал exe-д багцлагдана — ажилтан хаяг асуухгүй).
3. `ensure_dist_writable()` — `dist\AjliinButeemj.exe` ажиллаж байвал (түгжигдсэн) ойлгомжтой алдаа өгч зогсоно.
4. `ensure_venv()` — `.venv` үүсгэж, `requirements.txt` + `pyinstaller` суулгана.
5. PyInstaller → `AjliinButeemj.spec` ([тайлбар](AjliinButeemj_spec.md)).
6. `package_windows()` / `package_mac()` — `config.json` байвал хажууд нь хуулна; Mac дээр `.zip` хийнэ.
7. `--selftest` → `selftest()`; `--publish` → `publish()`.

## Функцууд
| Функц | Юу хийдэг |
|---|---|
| `ensure_venv()` | `.venv`-г үүсгэж сангуудыг суулгана, түүний python-ыг буцаана. |
| `copy_config(dest)` | Имэйл/Telegram тохируулсан `config.json` байвал exe-ийн хажууд хуулна. |
| `package_windows()` / `package_mac()` | Бүтээгдсэн файлыг шалгаж, тараахад бэлдэнэ. |
| `selftest(target)` | Бүтээсэн exe-г `--selftest`-ээр ажиллуулж, `app.log`-ийг хэвлэнэ. Алдаа бол зогсоно. |
| `app_version()` | `worktracker/__init__.py`-аас `__version__`-ийг уншина. |
| `share_local_path()` | Энэ компьютер дээрх админы хавтас (`share.json`-ийн `local_path`, ихэвчлэн `C:\AjliinButeemjData`). |
| `protect_update_dir(folder)` | `icacls`-аар `update\`-г: админ, SYSTEM, Administrators — бүрэн эрх; ажилтнуудын `ajliinbuteemj` хэрэглэгч — **зөвхөн унших**. Хэн нэгэн хортой exe тавьж тараахаас хамгаална. |
| `publish(target)` | exe-г `update\AjliinButeemj.exe`-д хуулж, дараа нь `version.json` (хувилбар, хэмжээ, SHA-256, огноо) бичнэ. version.json **хамгийн сүүлд** — exe бүрэн хуулагдахаас өмнө хэн ч шинэчлэл эхлүүлэхгүй. |
| `ensure_dist_writable()` | Ажиллаж буй exe-г дарж бичих гэж оролдохоос сэргийлнэ. |

## Анхаарах
- **Хувилбар нэмэгдүүлэхгүй бол ажилтнууд шинэчлэгдэхгүй.** `worktracker/__init__.py` → `__version__` (`3.1.1` → `3.1.2`)
  ба `AjliinButeemj.spec` → `VERSION`-ийг ижил болгоно.
- Админ өөрийн програмаа `dist\`-ээс биш, тусдаа хавтаснаас (`C:\Users\<нэр>\AjliinButeemj\`) ажиллуулах нь зөв — тэгвэл
  бүтээх үед хаах шаардлагагүй.
- `--publish` зөвхөн `--admin-setup` хийсэн компьютер дээр ажиллана (`share_local_path` олдох ёстой).
