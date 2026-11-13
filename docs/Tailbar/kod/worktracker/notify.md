# `worktracker/notify.py` — имэйл / Telegram илгээх

← [Буцах](../../README.md)

## Юу хийдэг
Хоёр зориулалттай:
1. **Ажилтан бүрийн өдрийн тайланг** менежер рүү илгээх (заавал биш) — `config.json`-д `email` / `telegram` хэсгийг асаасан үед.
   `app.ReportScheduler` өдөр бүр `report_time`-д (18:00) автоматаар, цонхны "Илгээх" товч гараар.
2. **Админы самбарын Telegram** туслахууд — чат олох, туршилтын мессеж, `alerts.py`-ийн мэдэгдэл илгээх.

## Функцууд
| Функц | Юу хийдэг |
|---|---|
| `send_report(cfg, storage, day, only_unsent)` | Тайлан үүсгэж (`report.build_report`), идэвхтэй суваг бүрээр илгээнэ. Амжилттай илгээснийг `meta.json`-д `sent:<суваг>:<өдөр>` гэж тэмдэглэнэ → `only_unsent=True` үед давтахгүй. `(амжилттай эсэх, мессеж)` буцаана. |
| `send_email(cfg, subject, text, html, attachments)` | SMTP (Gmail App Password г.м.), SSL эсвэл STARTTLS; HTML + CSV хавсралттай. |
| `send_telegram(cfg, text, document)` | Бот → чат: товч текст + HTML тайлан файлаар. |
| `tg_find_chats(token)` | Ботыг нэмсэн / боттой бичсэн чатуудын (нэр, id) — админ чат сонгоход. |
| `tg_send(token, chat_id, text)` | Энгийн мессеж (туршилт, `alerts.py`). |
| `channels_enabled(cfg)`, `is_fully_sent(...)` | Ямар нэг суваг асаалттай эсэх; тухайн өдөр бүх сувгаар илгээсэн эсэх. |

## Анхаарах
`config.json` дахь имэйлийн нууц үгийг ажилтан харах боломжтой — тусгай хаяг ашиглана.
