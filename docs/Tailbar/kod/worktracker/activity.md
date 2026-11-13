# `worktracker/activity.py` — идэвхтэй апп, гар/хулгана, түгжээ

← [Буцах](../../README.md)

## Юу хийдэг
Үйлдлийн системээс гурван зүйл асууна. Windows, macOS, Linux тус бүрд өөр хэрэгжүүлэлттэй (файл ачаалагдахдаа аль системд
байгаагаа шалгаад зохих хувилбарыг сонгоно). **Зөвхөн аппын нэрийг** авна — цонхны гарчиг, дэлгэцийн зураг авахгүй.

| Функц | Буцаах утга | Windows | macOS |
|---|---|---|---|
| `get_active_app()` | Дээр нь байгаа аппын нэр ("Google Chrome", "Microsoft Excel") эсвэл `None` (түгжээтэй) | pywin32 + psutil: идэвхтэй цонхны процесс → exe файлын "FileDescription" | AppKit `NSWorkspace` (эсвэл `lsappinfo`) |
| `get_idle_seconds()` | Гар/хулгана хэдэн секунд хөдлөөгүй | `GetLastInputInfo` | Quartz (эсвэл `ioreg`) |
| `is_screen_locked()` | Дэлгэц түгжигдсэн эсэх (Win+L) | Оролтын desktop нээгдэхгүй бол түгжээтэй | `CGSessionCopyCurrentDictionary` |

## Нарийн зүйлс (Windows)
- `_friendly_name(exe_path)` — `chrome.exe` → "Google Chrome". Үр дүнг кэшлэнэ.
- `_uwp_real_pid` — Тооцоолуур, Тохиргоо зэрэг UWP аппууд `ApplicationFrameHost` дотор ажилладаг тул жинхэнэ процессыг олно.
- Админ эрхтэй процессын (Task Manager) exe-г уншиж чадахгүй бол процессын нэрийг өгнө.
- `LockApp.exe`, `LogonUI.exe` идэвхтэй бол түгжээтэй гэж `None` буцаана.
- `UNKNOWN_APP = "(Тодорхойгүй)"` — нэр олдоогүй үед.

## Хэн дууддаг
Зөвхөн `tracker.py` — секунд тутам гурвууланг нь.
