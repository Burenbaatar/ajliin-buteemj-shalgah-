# `AjliinButeemj.spec` — PyInstaller-ийн тохиргоо

← [Буцах](../README.md)

## Юу хийдэг
PyInstaller нь Python кодыг Python суулгаагүй компьютер дээр ажилладаг **нэг файл** болгодог. Энэ файл түүнд юуг, хэрхэн
багцлахыг заана. `build.py` автоматаар ашигладаг — гараар ажиллуулах шаардлагагүй.

## Агуулга
| Хэсэг | Утга |
|---|---|
| `APP = "AjliinButeemj"` | Гаралтын файлын нэр |
| `VERSION = "3.1.1"` | macOS `.app`-ийн хувилбар. **`worktracker/__init__.py`-ийн `__version__`-тэй ижил байлгана.** |
| `datas` | exe-д хамт багцлах файлууд: царай илрүүлэх загвар `assets/face_detection_yunet_2023mar.onnx`; байвал **`share.json`** (админы хавтасны хаяг + нэвтрэх эрх). |
| `Analysis(["main.py"], …)` | Эхлэх файл; `hiddenimports=["pystray._win32"]` (Windows tray), `excludes` — хэрэггүй том сангууд (matplotlib, scipy, pandas …). |
| Windows (`EXE(...)`) | Нэг файл, `console=False` (хар цонхгүй). |
| macOS (`BUNDLE(...)`) | `.app` хавтас; `NSCameraUsageDescription` — үүнгүй бол macOS камерт хандахыг хориглоно; нэр "Ажлын бүтээмж". |

## Анхаарах
- Шинэ нөөц файл (зураг, загвар) нэмбэл `datas`-д нэмнэ, кодонд `config.resource_path("assets", ...)`-ээр уншина.
- `share.json` нууц үгтэй — exe-г зөвхөн өөрийн ажилтнуудад тараана, нийтэд байршуулахгүй.
