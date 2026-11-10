# Ажлын бүтээмж

Ажилтан компьютерийн өмнө байгаа эсэхийг **камераар** шалгаж, хүн байхад цаг тоолж, гарахад зогсооно.
Апп тус бүр дээр хэдэн минут ажилласныг бүртгээд, өдрийн тайланг менежерт **имэйл** эсвэл **Telegram**-аар илгээнэ.

- **Windows** болон **macOS** дээр ажиллана.
- Дэлгэц бичихгүй, дэлгэцийн зураг авахгүй, камерын зураг хадгалахгүй (кадр санах ойд шалгагдаад устана).
- Цонхны гарчиг биш, зөвхөн **аппын нэр**ийг бүртгэнэ (жишээ нь "Google Chrome", "Microsoft Excel").
- Өгөгдөл тухайн компьютер дээрх SQLite файлд хадгалагдана, сервер хэрэггүй.

## Хэрхэн ажилладаг вэ

| Хэсэг | Технологи |
|---|---|
| Хүн илрүүлэх | OpenCV + YuNet царай илрүүлэх загвар (`assets/`), секунд тутам |
| Идэвхтэй апп | Windows: pywin32 + psutil · macOS: pyobjc (AppKit) |
| Гар/хулгана | Windows: `GetLastInputInfo` · macOS: Quartz |
| Хадгалах | SQLite (`worktime.db`) |
| Цонх | Tkinter |
| Тайлан | HTML + CSV, имэйл (smtplib) / Telegram бот |
| Суулгах | PyInstaller → `AjliinButeemj.exe` / `AjliinButeemj.app` |

Төлөв тодорхойлох дүрэм:

1. Камерт царай харагдаж байвал → **Ажиллаж байна** (тухайн үеийн идэвхтэй аппад цаг нэмнэ).
2. Царай харагдахгүй болбол `away_grace_sec` (20 сек) хүлээнэ. Энэ хугацаанд эргэж харвал алдагдал байхгүй.
3. 20 секундээс удвал → **Ажлаас гарсан**. Тэр 20 секунд ч "гарсан" хугацаанд орно.
4. Гар/хулгана хөдөлж байвал (доош харж бичиж байгаа гэх мэт) хүн байна гэж үзнэ (`input_counts_as_presence`).
5. Дэлгэц түгжигдвэл (Win+L, ⌃⌘Q) шууд **Ажлаас гарсан**.
6. Камер олдохгүй бол гар/хулганы хөдөлгөөнөөр л шалгана (5 мин хөдлөхгүй бол гарсан).
7. Компьютер унтсан хугацааг тоолохгүй.

## Ажиллуулах (Python-оор, хөгжүүлэлтэд)

```bash
python3 -m venv .venv
# macOS:   source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt

python main.py                # цонхтой
python main.py --no-gui       # терминал дээр
python main.py --camera-test  # камер, царай илрүүлэлтийг харах (юу ч хадгалахгүй)
python main.py --selftest     # камер нээхгүйгээр бүх модулийг шалгах
python main.py --report       # өнөөдрийн тайланг нээх
python main.py --send 2026-11-10   # тухайн өдрийн тайланг менежерт илгээх
```

Python-оор ажиллуулахад өгөгдөл `data/` хавтаст хадгалагдана.

## Тохиргоо (`config.json`)

`config.example.json`-г `config.json` нэрээр хуулж засна (git-д орохгүй, нууц үг агуулдаг).

- **Менежерийн `config.json`** — `.exe` / `.app`-ийн **хажууд** тавина. Бүх ажилтанд нэг файл тараана.
  Анх уншихдаа өгөгдлийн хавтаст `managed.json` нэрээр хуулагдана, тиймээс дараа нь `.exe`-г зөөсөн ч алдагдахгүй.
  Хажууд нь шинэчилсэн `config.json` тавибал шинэ тохиргоо хүчинтэй болно.
- **Ажилтны нэр** — анх нээхэд асууж, ажилтны өөрийн хавтаст (`config.json`) хадгална.
  Менежерийн файлд `employee_name` хоосон байж болно.
- Менежерийн файл байхгүй бол өгөгдлийн хавтаст бүрэн `config.json` үүсгэнэ (бие даан ашиглах).
- `build_windows.bat` / `build_mac.sh` нь төслийн үндсэн хавтаст `config.json` байвал түүнийг автоматаар хамт багцална.

| Түлхүүр | Тайлбар |
|---|---|
| `employee_name` | Ажилтны нэр. Хоосон бол анх ажиллахад асууна |
| `camera_index` | Камерын дугаар (ихэвчлэн `0`) |
| `away_grace_sec` | Царай алга болсноос хойш хэдэн секундэд "гарсан" гэж үзэх |
| `input_counts_as_presence` | Гар/хулгана хөдөлж байвал хүн байна гэж үзэх эсэх |
| `report_time` | Өдрийн тайлан илгээх цаг, жишээ нь `"18:00"` |
| `min_away_period_min` | Тайланд үүнээс урт гарсан үеүдийг жагсаана |
| `autostart` | Компьютер асахад автоматаар эхлэх (.exe/.app хувилбарт) |
| `email.*` | SMTP тохиргоо |
| `telegram.*` | Telegram ботын тохиргоо |

**Тайлан илгээх:** өдөр бүр `report_time`-д. Тэр үед компьютер унтарсан байсан бол дараагийн удаа асахад
өмнөх өдрүүдийн (7 хүртэл хоног) тайланг нөхөж илгээнэ. Илгээж чадаагүй бол 10 минут тутам дахин оролдоно.
Цонхон дээрх **"Менежерт илгээх"** товчоор хүссэн үедээ илгээж болно.

### Имэйл (Gmail жишээ)

1. Тайлан илгээхэд зориулсан тусдаа Gmail хаяг нээнэ (ажилтны компьютерт нууц үг нь хадгалагдана).
2. Google Account → Security → 2-Step Verification асаана → **App passwords** → 16 оронтой нууц үг үүсгэнэ.
3. `config.json`:
   ```json
   "email": { "enabled": true, "smtp_host": "smtp.gmail.com", "smtp_port": 465, "use_ssl": true,
              "username": "reports@gmail.com", "password": "xxxx xxxx xxxx xxxx",
              "from_addr": "reports@gmail.com", "to_addrs": ["manager@company.mn"] }
   ```
   Outlook/Office 365: `smtp.office365.com`, порт `587`, `"use_ssl": false` (STARTTLS).

### Telegram бот

1. Telegram дээр **@BotFather** → `/newbot` → токен авна.
2. Менежер ботдоо ямар нэг мессеж бичнэ.
3. Хөтчөөр `https://api.telegram.org/bot<ТОКЕН>/getUpdates` нээж `"chat":{"id": ...}` утгыг олно
   (группт илгээх бол ботыг группт нэмээд группын id-г авна, сөрөг тоо байдаг).
4. `config.json`: `"telegram": { "enabled": true, "bot_token": "...", "chat_id": "..." }`

## .exe / .app бүтээх (PyInstaller)

PyInstaller нь **тухайн үйлдлийн систем дээрээ** л бүтээдэг: `.exe`-г Windows дээр, `.app`-г Mac дээр.

- **Windows:** `build_windows.bat` давхар дарна → `dist\AjliinButeemj.exe` (нэг файл)
- **macOS:** `./build_mac.sh` → `dist/AjliinButeemj.app` болон `dist/AjliinButeemj-mac.zip`
- **Windows компьютергүй бол:** төслийг GitHub руу оруулаад **Actions → build → Run workflow**.
  `.github/workflows/build.yml` нь Windows болон Mac хоёуланд нь бүтээгээд, туршаад, Artifacts хэсэгт гаргана.

Ажилтны компьютерт Python суулгах шаардлагагүй.

## Ажилтны компьютерт суулгах

1. Нэг хавтсанд `AjliinButeemj.exe` (эсвэл `.app`) болон тохируулсан `config.json`-г хамт хуулна.
   `employee_name`-г хоосон үлдээвэл ажилтан анх ажиллуулахад нэрээ оруулна.
2. Програмыг нээнэ. `autostart: true` бол дараа нь компьютер асах бүрт өөрөө эхэлнэ.
3. **Windows:** Settings → Privacy & security → Camera → "Let desktop apps access your camera" асаалттай байх.
   Гарын үсэггүй .exe тул SmartScreen "More info → Run anyway" гэж асууж болно.
4. **macOS:** zip-ийг задлаад, анх нээхээс өмнө Terminal дээр нэг удаа:
   ```bash
   xattr -dr com.apple.quarantine ~/Downloads/AjliinButeemj-mac
   ```
   (Үүнгүй бол гарын үсэггүй апп-ыг macOS түр хавтсаас ажиллуулж, хажуудах `config.json` харагдахгүй.
   Эсвэл баруун товч → **Open**.) Камерын зөвшөөрөл асуухад **Allow**.
   Андуурч татгалзсан бол System Settings → Privacy & Security → Camera хэсэгт асаана.
5. Зөв суусныг шалгах: `AjliinButeemj.exe --selftest` (Windows: лог файлд бичнэ) /
   `AjliinButeemj.app/Contents/MacOS/AjliinButeemj --selftest`.

Өгөгдөл, тайлан, лог хаана хадгалагдах вэ:

- Windows: `%APPDATA%\AjliinButeemj\` (`worktime.db`, `reports\`, `app.log`)
- macOS: `~/Library/Application Support/AjliinButeemj/`

## Анхаарах зүйлс

- **Ажилтанд мэдэгдэх:** камер ашигладаг хяналтын програмыг ажилтанд урьдчилан мэдэгдэж, зөвшөөрөл авах
  (хөдөлмөрийн гэрээ / дотоод журамд тусгах) шаардлагатай. Програм ажиллаж байхад камерын гэрэл асаалттай,
  төлөв нь цонхон дээр үргэлж харагдана.
- `config.json` дахь имэйлийн нууц үг / ботын токеныг ажилтан харах боломжтой. Иймд зөвхөн тайлан
  илгээхэд зориулсан тусдаа хаяг ашиглана уу.
- Тайлан өгөгдөл ажилтны компьютер дээр байдаг тул техникийн мэдлэгтэй хүн өөрчлөх боломжтой.
  Илүү найдвартай хяналт хэрэгтэй бол өгөгдлийг төв сервер рүү илгээх хувилбар хийх шаардлагатай.

## Файлын бүтэц

```
main.py                 эхлүүлэх файл (CLI)
worktracker/
  presence.py           камер + царай илрүүлэх (YuNet)
  activity.py           идэвхтэй апп, гар/хулгана, түгжээ (Windows / macOS)
  tracker.py            цаг тоолох гол логик
  storage.py            SQLite
  report.py             HTML / текст / CSV тайлан
  notify.py             имэйл, Telegram
  app.py                угсрах + тайлан илгээх хуваарь
  gui.py                Tkinter цонх
  system.py             автоматаар эхлэх, давхар ажиллахаас сэргийлэх, лог
  config.py             тохиргоо, замууд
assets/face_detection_yunet_2023mar.onnx   царай илрүүлэх загвар (OpenCV Zoo, MIT)
AjliinButeemj.spec      PyInstaller тохиргоо
build_windows.bat, build_mac.sh, .github/workflows/build.yml
```
