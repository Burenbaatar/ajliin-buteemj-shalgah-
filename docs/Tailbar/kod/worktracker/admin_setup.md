# `worktracker/admin_setup.py` — админы компьютерийг нэг удаа тохируулах

← [Буцах](../../README.md)

## Юу хийдэг
`AjliinButeemj.exe --admin-setup` (эсвэл `python main.py --admin-setup`) — **админы компьютер дээр нэг удаа**. Цонхоор асуулт
асууж, Windows-ийн админ эрх (UAC) авч дараахыг автоматаар хийнэ:

1. **Админ нууц үг** (самбар нээхэд) → `%APPDATA%\AjliinButeemj\admin.json` (PBKDF2 хэш — нууц үг өөрөө хадгалагдахгүй).
2. **`C:\AjliinButeemjData`** хавтас + `people\` + хоосон `company.json`.
3. **Тусгай Windows хэрэглэгч `ajliinbuteemj`** — санамсаргүй 14 тэмдэгтийн нууц үгтэй, хугацаа дуусахгүй, нэвтрэх дэлгэцэнд
   харагдахгүй. Ажилтнуудын програм хавтсанд энэ хэрэглэгчээр нэвтэрнэ.
4. **Хуваалцах:** `\\<компьютер>\AjliinButeemj` — `ajliinbuteemj`: бичих, Administrators: бүрэн. NTFS эрх (`icacls`).
5. **Firewall:** "File and Printer Sharing"-ийг зөвхөн **Private** сүлжээнд асаана.
6. **`share.json`** — хавтасны хаяг + тусгай хэрэглэгч + нууц үг: энэ компьютерт ба төслийн хавтаст (`build.py` exe-д багцална).

## Функцууд
| Функц | Юу хийдэг |
|---|---|
| `run_admin_setup()` | Бүх урсгал: баталгаажуулах → нууц үг (2 удаа) → хавтас → админ эрхтэй хэсэг → `share.json`, `admin.json` → үр дүнгийн мессеж. |
| `_elevated_system_setup(folder, pw)` | Админ эрхгүй бол PowerShell `Start-Process -Verb RunAs`-аар програмыг `--admin-setup-system`-тэй дахин ажиллуулж, үр дүнг түр файлаар буцааж авна. |
| `run_system_step(job_file)` | UAC-аар эхэлсэн процесс: `system_setup` хийгээд алдааг файлд бичнэ. |
| `system_setup(folder, pw)` | `net user`, `net share`, `icacls`, PowerShell firewall, бүртгэл (winreg) — алдааны жагсаалт буцаана. |
| `_account_password()` | 14 тэмдэгт, том/жижиг үсэг + тоо (`net user` 14-өөс урт нууц үгэнд асуулт асуудаг). |
| `is_elevated()` | Админ эрхтэй ажиллаж байна уу. |

## Тогтмолууд
`SHARE_NAME = "AjliinButeemj"`, `ACCOUNT = "ajliinbuteemj"`, `DEFAULT_FOLDER = C:\AjliinButeemjData`,
`FILE_SHARING_GROUP` — firewall бүлгийн хэлнээс үл хамаарах нэр.

## Анхаарах
- Дахин ажиллуулж болно (өгөгдөл устахгүй), гэхдээ тусгай хэрэглэгчийн **нууц үг шинэчлэгдэнэ** — дараа нь exe-г дахин бүтээж
  (`build.py`) тараах хэрэгтэй.
- Mac дээр хавтсыг гараар хуваалцана (System Settings → Sharing).
