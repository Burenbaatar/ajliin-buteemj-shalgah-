# `worktracker/uiutil.py` — цонхны туслахууд

← [Буцах](../../README.md)

## Юу хийдэг
`gui.py` ба `dashboard.py`-ийн хамтдаа ашигладаг өнгө, фонт, товч, мөн **арын thread-ээс цонх шинэчлэх** арга.

## Өнгө, фонт
| Нэр | Утга | |
|---|---|---|
| `FONT` | Windows: Segoe UI, Mac: Helvetica | |
| `GREEN` / `RED` / `GRAY` | `#1a7f37` / `#cf222e` / `#8c959f` | Ажиллаж байна / гарсан / офлайн |
| `FG`, `MUTED`, `BG`, `SURFACE`, `BORDER`, `GAP`, `ACCENT`, `PENDING_BG` | | Бичвэр, бүдэг бичвэр, дэвсгэр, хүрээ, онцлох (цэнхэр), шинэ хүний мөр (шар) |
| `STATUS_COLOR` | төлөв → өнгө | |

## Функцууд
| Нэр | Юу хийдэг |
|---|---|
| `Bg(root)` | Tkinter зөвхөн main thread-ээс шинэчлэгдэх ёстой. `bg.run(ажил, done, fail)` — ажлыг арын thread-д хийж үр дүнг main thread-д callback-аар өгнө (хавтас унших, имэйл илгээх г.м. үед цонх хөлдөхгүй). `bg.call_soon(fn)` — tray, socket зэрэг өөр thread-ээс цонхны үйлдэл дуудах. 80 мс тутам дараалал шалгана. |
| `button(parent, text, command, primary=False, danger=False)` | Товч: `primary` — цэнхэр, `danger` — улаан ("Програмаас гарах"). Mac дээр дэвсгэр өнгө өөрчлөгддөггүй тул энгийн/улаан бичигтэй. |
| `fmt_dur(sec)` / `fmt_hm(sec)` | `"2ц 05м"` / `"2:05"`. |
| `friendly(exc)` | Алдааны Монгол тайлбар (`.friendly` байвал). |
| `clear(container)` | Цонхны доторх бүх элементийг устгана. |
| `bind_wheel(widget, canvas)` | Хулганы дугуйгаар гүйлгэх (Windows/Mac/Linux ялгаатай). |
