# `worktracker/config.py` — тохиргоо, файлын замууд, нууц үг

← [Буцах](../../README.md)

## Юу хийдэг
1. **Тохиргоо** (`config.json`) уншиж/хадгалах.
2. Програм **хаана** юу хадгалахыг шийдэх (exe эсвэл Python-оор ажиллаж байгаагаас хамаарч).
3. **`share.json`** (админы хавтас) ба **`admin.json`** (админ нууц үг) унших/бичих, нууц үг хэшлэх.

## Тохиргооны 3 давхарга (`load_config`)
```
DEFAULTS (кодонд)  <  ажилтны config.json (%APPDATA%)  <  менежерийн config.json (exe-ийн хажууд)
```
Хамгийн сүүлийнх нь давуу. Харин ажилтны нэр (`employee_name`) үргэлж ажилтны файлаас. Мөн админы самбарын тохиргоо
(`COMPANY_KEYS`) хавтаснаас 10 мин тутам дээрээс нь хэрэглэгдэнэ (`cloud.pull_settings`).

| Анхдагч түлхүүр | Утга | |
|---|---|---|
| `camera_index`, `camera_check_interval_sec` | 0, 1.0 | Аль камер, хэр олон шалгах |
| `away_grace_sec` | 20 | Царай харагдахгүй болоод "гарсан" гэх хүртэл |
| `input_counts_as_presence`, `input_presence_window_sec` | true, 5 | Гар/хулгана хөдөлж байвал хүн байна гэж үзэх |
| `no_camera_idle_timeout_sec` | 300 | Камергүй үед |
| `autostart`, `auto_update`, `always_on_top` | true, true, false | |
| `email`, `telegram` | унтраалттай | Өдрийн тайлан илгээх ([notify.md](notify.md)) |
| `cloud.push_interval_sec` | 60 | Хавтас руу хуулах давтамж |

## Замууд
| Функц | Буцаах |
|---|---|
| `is_frozen()` | exe/.app хувилбар уу (PyInstaller) |
| `app_dir()` | exe-ийн хавтас (Python бол төслийн хавтас) |
| `resource_path(...)` | exe дотор багцлагдсан файл (`assets/…`, `share.json`) |
| `installed_data_dir()` | `%APPDATA%\AjliinButeemj` (Mac: `~/Library/Application Support/AjliinButeemj`) |
| `data_dir()` | exe бол дээрх; Python-оор бол төслийн `data\` |
| `user_data_dir(uid)`, `reports_dir()` | Хэрэглэгчийн өгөгдөл, тайлангийн хавтас |

## Админы хавтас ба админ
| Функц | Юу хийдэг |
|---|---|
| `load_share()` | `share.json`-г 3 газраас (энэ компьютер → exe-ийн хажууд → exe дотор) хайна. Гараар бичсэн хаягт нэвтрэх эрх дутуу бол бусдаас нөхнө. |
| `save_share(info)` | Хадгалах (Python-оор `--admin-setup` хийсэн бол exe-ийн хавтсанд ч давхар). |
| `hash_password` / `check_password` | PBKDF2-SHA256, 200 000 давталт, санамсаргүй давс. |
| `load_admin()` / `save_admin(d)` | `admin.json` — байвал энэ компьютер **админых**. |

## `Config` класс
`dict` + хадгалах зам. `save()` — менежерийн файл байгаа үед зөвхөн ажилтны түлхүүрийг (нэр) хадгална.

## Шинэ тохиргоо нэмэх
1. `DEFAULTS`-д анхдагч утга. 2. Админ самбараас өөрчилдөг байх бол `COMPANY_KEYS`-д нэр, `dashboard.py`-ийн Тохиргоо табд талбар.
