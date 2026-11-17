"""1-р гишүүн — Tkinter GUI. Нэвтрэхэд ажилтан эсвэл админы эрхээр өөр табууд нээгдэнэ.

Админ:   Ажилтнууд (шууд самбар), Түр зогсолт, 7 хоног, Хэрэглэгчид, Хэлтэс, Тохиргоо. Админы компьютер сервер болно.
Ажилтан: админы серверт нэвтэрмэгц камер асаж, амьд хүн мөн эсэх, ажилтан өөрөө сууж байгаа эсэхийг шалгана.
         Өөрийн идэвхийн хэмжилт. 5 секунд тутам админы сервер рүү JSON-оор илгээнэ.
"""
import os
import time
import tkinter as tk
from tkinter import ttk, messagebox

import cv2

import productivity as prod                 # 5-р гишүүн
import register as reg                      # 2-р гишүүн
from activity import ActivityTracker        # 4-р гишүүн
from camera import CameraService, FaceEnrollment, FaceMonitor, draw   # 3-р гишүүн

BOLD = ("Segoe UI", 12, "bold")
# Камерын төлөв → (хүрээний өнгө BGR, бичгийн өнгө)
CAMERA_COLORS = {"owner": ((0, 200, 0), "#1a7f37"), "unknown": ((0, 200, 0), "#1a7f37"),
                 "other": ((0, 0, 255), "#cf222e"), "fake": ((0, 0, 255), "#cf222e"),
                 "checking": ((0, 200, 255), "#9a6700"), "no_face": ((0, 200, 255), "#9a6700")}
# Камер ажиллаж байхад ажилтныг өөрийг нь баталгаажуулаагүй шалтгаан — тэр хугацаа ажилласанд тоологдохгүй,
# самбарт «Гарсан (…)». Камер ажиллахгүй үед (төлөв None) гар, хулганы хөдөлгөөнөөр л тооцно.
CAMERA_REASONS = {"other": "камерт өөр хүн", "no_face": "камерт хүн алга", "checking": "амьд хүн шалгаж байна",
                  "fake": "амьд хүн баталгаажаагүй", "unknown": "нүүр бүртгээгүй"}
COLORS ={"work": "green", "away": "red", "offline": "gray", "absent": "#c0392b"}
TILE_COLORS = {"Ажиллаж байна": "green", "Гарсан": "red", "Офлайн": "gray", "Хоцорсон": "#c05000",
               "Ирээгүй": "#c0392b"}
SETTING_LABELS = {"work_start": "Ажил эхлэх (ЦЦ:ММ)", "work_end": "Ажил дуусах (ЦЦ:ММ)",
                  "late_grace_min": "Хоцролтын хүлцэл (мин)", "away_after_sec": "«Гарсан» гэж үзэх (сек хөдөлгөөнгүй)",
                  "unproductive": "Бүтээмжгүй програм / сайт (таслалаар)"}
DAYS = ("Да", "Мя", "Лх", "Пү", "Ба", "Бя", "Ня")


def row(parent):
    """Элементүүдийг нэг мөрөнд зүүнээс баруун тийш байрлуулах хүрээ."""
    frame = ttk.Frame(parent)
    frame.pack(anchor="w", pady=(0, 8))
    return frame


def button(parent, text, command):
    ttk.Button(parent, text=text, command=command).pack(side="left", padx=4)


def entry(parent, text, width):
    ttk.Label(parent, text=text).pack(side="left")
    field = ttk.Entry(parent, width=width)
    field.pack(side="left", padx=4)
    return field


def table(parent, columns, widths=None):
    tree = ttk.Treeview(parent, columns=columns, show="headings")
    for column in columns:
        tree.heading(column, text=column, anchor="w")
        tree.column(column, width=(widths or {}).get(column, 80), anchor="w")
    tree.pack(fill="both", expand=True)
    return tree


def fill(tree, rows):
    """Хүснэгтийг цэвэрлээд шинэ мөрүүдээр дүүргэнэ."""
    tree.delete(*tree.get_children())
    for values in rows:
        tree.insert("", "end", values=values)


class ProductivityApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Ажилтны бүтээмж")
        self.geometry("1100x680")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.server = None             # админ нэвтэрсэн үед JSON сервер
        self.camera_service = None
        self.camera_job = None
        self.tracker = None            # идэвх хэмжиж байгаа үед ActivityTracker
        self.monitor = None            # FaceMonitor: амьд хүн мөн эсэх, хэн суусан
        self.enrollment = None         # нүүр бүртгэж байгаа үед FaceEnrollment
        self.last_check = None         # шинэ амьд хүн шалгалт эхэлснийг мэдэхийн тулд
        self.camera_state = None       # FaceMonitor-ийн сүүлийн төлөв; None = камер ажиллахгүй (кадр ирэхгүй)
        self.pending = []              # админ руу хараахан илгээгдээгүй өсөлтүүд
        self.pause_reason = ""
        self.pause_id = None           # одоогийн түр зогсолтын id (админы серверээс)
        self.pause_started = 0         # түр зогсоосон мөч (monotonic)
        self.pause_ends = []           # админ руу хараахан илгээгдээгүй дууссан түр зогсолтын хугацаа
        self.pause_statuses = {}       # түр зогсолтын id → админы шийдвэр (өөрчлөгдвөл дуу гаргана)
        self.seconds = 0
        self.retry_at = 0              # админ холбогдохгүй бол энэ хүртэл дахин оролдохгүй
        self.build_login()
        self.after(1000, self.tick)

    def show_error(self, text):
        messagebox.showerror("Алдаа", str(text), parent=self)

    # ---------------------------------------------------------- нэвтрэх
    def build_login(self):
        self.login = ttk.Frame(self, padding=20)
        self.login.pack(fill="x")
        ttk.Label(self.login, text="Ажилтан", font=BOLD).pack(anchor="w", pady=(0, 6))
        form = row(self.login)
        self.host_entry = entry(form, "Админы компьютер:", 20)
        self.host_entry.insert(0, "127.0.0.1")
        form = row(self.login)
        self.login_name = entry(form, "Нэр:", 20)
        self.login_code = entry(form, "Код:", 10)
        self.login_code.config(show="*")
        button(form, "Нэвтрэх", self.open_employee)
        ttk.Label(self.login, foreground="gray", text="Нэр, кодыг админ бүртгэхдээ өгнө. Админы програм "
                  "асаалттай байх ёстой.").pack(anchor="w")
        ttk.Label(self.login, text="Админ", font=BOLD).pack(anchor="w", pady=(24, 6))
        form = row(self.login)
        self.password_entry = entry(form, "Нууц үг:", 20)
        self.password_entry.config(show="*")
        button(form, "Админаар нэвтрэх", self.open_admin)
        ttk.Label(self.login, foreground="gray", text="Анх нэвтрэхэд оруулсан нууц үг админы нууц үг болно.").pack(
            anchor="w")

    def open_tabs(self, title, pages):
        """Нэвтрэх хэсгийг хаагаад тухайн эрхийн табуудыг нээнэ."""
        self.login.destroy()
        top = row(self)
        top.pack_configure(padx=10, pady=8)
        ttk.Label(top, text=title, font=BOLD).pack(side="left", padx=(0, 20))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tab_frames = {}
        for name, build in pages:
            frame = ttk.Frame(self.tabs, padding=10)
            self.tabs.add(frame, text=name)
            self.tab_frames[name] = frame
            build(frame)
        return top

    def open_employee(self):
        """Нэр, кодыг админы сервер шалгана. Зөв бол ажилтны табууд нээгдэнэ."""
        self.host = self.host_entry.get().strip()
        request = {"action": "login", "name": self.login_name.get(), "code": self.login_code.get()}
        try:
            self.me = reg.ask(self.host, request)      # {"id", "name", "face": нүүрний хээ эсвэл None}
        except OSError:
            return self.show_error(f"Админы компьютертэй ({self.host}, порт {reg.PORT}) холбогдсонгүй. Шалгах зүйл:\n"
                                   "1. Админы програм нээлттэй, админаар нэвтэрсэн эсэх.\n"
                                   "2. «Админы компьютер» талбарт админы цонхны дээд талд бичигдсэн IP хаяг байгаа эсэх "
                                   "(127.0.0.1 нь зөвхөн нэг компьютер дээр ажиллана).\n"
                                   "3. Хоёр компьютер нэг сүлжээнд (Wi-Fi) байгаа эсэх.\n"
                                   "4. Админы компьютерийн Windows Firewall ProductivitySystem-д зөвшөөрөл өгсөн эсэх.")
        except ValueError as error:                    # бүртгэлгүй нэр, код буруу г.м.
            return self.show_error(error)
        self.open_tabs("Ажилтан: " + self.me["name"],
                       [("Камер", self.build_camera), ("Компьютерийн идэвх", self.build_activity)])
        self.show_my_pauses(self.me.get("pauses"))
        self.start_watching()                          # серверт холбогдмогц камер шууд ажиллана

    def open_admin(self):
        password = self.password_entry.get()
        if not password:
            return self.show_error("Нууц үгээ бичнэ үү.")
        try:
            correct = reg.check_password(password)
        except OSError as error:
            return self.show_error(f"Өгөгдлийн хавтсанд бичиж чадсангүй: {error}\n"
                                   "Програмыг Documents гэх мэт өөрийн хавтсанд хуулж ажиллуулна уу.")
        if not correct:
            return self.show_error("Нууц үг буруу.\nЭнэ компьютерийн админы нууц үг энд хадгалагдсан:\n"
                                   f"{reg.get_data_dir()}\\admin.json\n"
                                   "Мартсан бол тэр файлыг устгаад шинэ нууц үгээр нэвтэрнэ.")
        try:
            self.server = reg.start_server()
        except OSError:
            return self.show_error("Сервер асаж чадсангүй. Энэ компьютер дээр админы програм аль хэдийн "
                                   "нээлттэй байж магадгүй.")
        self.geometry("1420x700")
        top = self.open_tabs("Админ", [("Ажилтнууд", self.build_board), ("Түр зогсолт", self.build_pauses),
                                       ("7 хоног", self.build_week),
                                       ("Хэрэглэгчид", self.build_employee), ("Хэлтэс", self.build_departments),
                                       ("Тохиргоо", self.build_settings)])
        ttk.Label(top, foreground="#1a5fb4", text=f"Ажилтнууд «Админы компьютер» талбарт: {reg.my_address()}   ·   "
                  f"Өгөгдөл: {reg.get_data_dir()}").pack(side="left")
        self.refresh_employees()
        self.board_loop()
        self.serve_loop()

    def serve_loop(self):
        """0.1 сек тутам ажилтнуудын JSON хүсэлтэд хариулна."""
        self.after(100, self.serve_loop)
        reg.serve(self.server)

    # ---------------------------------------------------------- админ: Ажилтнууд (шууд самбар)
    def build_board(self, frame):
        boxes = row(frame)
        self.tiles = {}
        for name in prod.TILES:
            box = tk.Frame(boxes, background="white", highlightthickness=1, highlightbackground="#d0d0d0",
                           padx=12, pady=6)
            box.pack(side="left", padx=(0, 8))
            tk.Label(box, text=name, background="white", foreground="gray").pack(anchor="w")
            self.tiles[name] = tk.Label(box, text="0", background="white", font=("Segoe UI", 18, "bold"),
                                        foreground=TILE_COLORS.get(name, "black"))
            self.tiles[name].pack(anchor="w")
        ttk.Label(frame, foreground="gray", wraplength=1380, text="Мөр дээр давхар дарвал тухайн ажилтны өнөөдрийн "
                  "бүх програм, хуудас, хулганы байршлыг харна. 5 секунд тутам шинэчлэгдэнэ. «Ажилласан» — камер "
                  "ажилтныг өөрийг нь баталгаажуулсан (камер ажиллахгүй бол гар, хулгана хөдөлсөн) хугацаа, мөн "
                  "«Түр зогсолт» таб дээр зөвшөөрсөн түр зогсолт. «Өөр хүн» — камерт бүртгэлтэй "
                  "ажилтны оронд өөр хүн суусан, «Баталгаажаагүй» — амьд хүн гэдэг нь батлагдаагүй хугацаа."
                  ).pack(anchor="w", pady=(0, 6))
        self.board = table(frame, prod.COLUMNS, {"Ажилтан": 170, "Одоо": 210, "Одоогийн апп": 220,
                                                 "Ирсэн – Явсан": 100, "Өдрийн явц": 200, "Баталгаажаагүй": 100})
        for status, color in COLORS.items():
            self.board.tag_configure(status, foreground=color)
        self.board.bind("<Double-1>", self.show_detail)

    def board_loop(self):
        self.after(5000, self.board_loop)
        self.refresh_board()

    def refresh_board(self):
        """Ажилтнууд, Түр зогсолт, Хэлтэс, 7 хоног табуудыг JSON болон шууд төлөвөөс шинэчилнэ.
        Админ зөвшөөрсөн түр зогсолтын хугацаа «Ажилласан»-д нэмэгдэнэ."""
        settings, employees, pauses = reg.load_settings(), reg.list_employees(), reg.load_pauses()

        def records_of(date):
            return prod.add_approved_pauses(reg.day_records(date), pauses, date)

        records = records_of(time.strftime("%Y-%m-%d"))
        rows = [prod.row_for(e, records.get(e["id"]), reg.live_status(e["id"]), settings, time.time())
                for e in employees]
        self.board.delete(*self.board.get_children())
        for employee, line in zip(employees, rows):
            self.board.insert("", "end", iid=employee["id"], values=line["values"], tags=(line["status"],))
        for name, value in prod.tiles(rows).items():
            self.tiles[name].config(text=value)
        fill(self.departments, prod.department_rows(employees, rows))
        monday = time.time() - time.localtime().tm_wday * 86400
        week = [records_of(time.strftime("%Y-%m-%d", time.localtime(monday + i * 86400))) for i in range(7)]
        fill(self.week, [prod.week_row(e, week) for e in employees])
        self.refresh_pauses(time.strftime("%Y-%m-%d", time.localtime(monday)))

    def show_detail(self, event):
        """Давхар дарсан ажилтны өнөөдрийн бүх програм, хуудас, хулгана, нээлттэй програм, түр зогсолт."""
        employee_id = self.board.focus()
        if not employee_id:
            return
        name = next(e["name"] for e in reg.list_employees() if e["id"] == employee_id)
        record = reg.day_records(time.strftime("%Y-%m-%d")).get(employee_id, {})
        window = tk.Toplevel(self)
        window.title("Дэлгэрэнгүй — " + name)
        window.geometry("850x500")
        tree = table(window, ("Хаана", "Програм / хуудас", "Хугацаа"), {"Хаана": 110, "Програм / хуудас": 600})
        for label, key in (("Урд талд", "apps"), ("Хулгана дээр", "mouse")):
            for title, seconds in sorted(record.get(key, {}).items(), key=lambda item: -item[1]):
                tree.insert("", "end", values=(label, title, prod.duration(seconds)))
        for app in record.get("open", []):
            tree.insert("", "end", values=("Нээлттэй", app, ""))
        for pause in reg.pauses_today(employee_id):
            tree.insert("", "end", values=("Түр зогссон", f"{pause['reason']} · {reg.PAUSE_STATUS[pause['status']]}",
                                           f"{pause['time'][11:16]} · {self.pause_length(pause)}"))

    # ---------------------------------------------------------- админ: Түр зогсолт
    def build_pauses(self, frame):
        buttons = row(frame)
        button(buttons, "Зөвшөөрөх", lambda: self.set_pause_ui("approved"))
        button(buttons, "Татгалзах", lambda: self.set_pause_ui("rejected"))
        ttk.Label(frame, foreground="gray", wraplength=1380, text="Энэ 7 хоногийн түр зогсолтууд. Шалтгааныг уншаад "
                  "«Зөвшөөрөх» дарвал түр зогссон хугацаа ажилтны «Ажилласан» цагт нэмэгдэж, «Гарсан»-аас хасагдана. "
                  "«Татгалзах» дарвал «Гарсан» хэвээр үлдэнэ (зөвшөөрснөө ч буцааж болно). Хугацаа нь ажилтан "
                  "хэмжилтээ үргэлжлүүлэх эсвэл дуусгах үед тодорхой болно.").pack(anchor="w", pady=(0, 6))
        self.pauses = table(frame, ("Ажилтан", "Шалтгаан", "Эхэлсэн", "Хугацаа", "Төлөв"),
                            {"Ажилтан": 170, "Шалтгаан": 650, "Эхэлсэн": 120, "Хугацаа": 120, "Төлөв": 130})
        for status, color in (("pending", "#9a6700"), ("approved", "#1a7f37"), ("rejected", "#cf222e")):
            self.pauses.tag_configure(status, foreground=color)

    @staticmethod
    def pause_length(pause):
        seconds = pause.get("seconds")
        return prod.duration(seconds) if seconds is not None else "дуусаагүй"

    def refresh_pauses(self, since):
        """Түр зогсолтын жагсаалт. Сонгосон мөрийг хадгална — 5 сек тутам шинэчлэхэд сонголт алдагдахгүй."""
        selected = self.pauses.focus()
        self.pauses.delete(*self.pauses.get_children())
        pauses = reg.pauses_since(since)
        for pause in pauses:
            self.pauses.insert("", "end", iid=pause["id"], tags=(pause["status"],),
                               values=(pause["employee_name"], pause["reason"], pause["time"][5:16],
                                       self.pause_length(pause), reg.PAUSE_STATUS[pause["status"]]))
        if selected and self.pauses.exists(selected):
            self.pauses.focus(selected)
            self.pauses.selection_set(selected)
        waiting = sum(pause["status"] == "pending" for pause in pauses)
        self.tabs.tab(self.tab_frames["Түр зогсолт"], text=f"Түр зогсолт ({waiting})" if waiting else "Түр зогсолт")

    def set_pause_ui(self, status):
        pause_id = self.pauses.focus()
        if not pause_id:
            return self.show_error("Түр зогсолтоо жагсаалтаас сонгоно уу.")
        reg.set_pause_status(pause_id, status)
        self.refresh_board()

    # ---------------------------------------------------------- админ: 7 хоног, хэлтэс
    def build_week(self, frame):
        ttk.Label(frame, foreground="gray", text="Энэ 7 хоногийн өдөр бүрийн ажилласан цаг.").pack(anchor="w")
        self.week = table(frame, ("Ажилтан",) + DAYS + ("Нийт",), {"Ажилтан": 200})

    def build_departments(self, frame):
        self.departments = table(frame, ("Хэлтэс", "Хүн", "Ажиллаж байна", "Нийт ажилласан", "Дундаж бүтээмж"),
                                 {"Хэлтэс": 200, "Ажиллаж байна": 120, "Нийт ажилласан": 120, "Дундаж бүтээмж": 120})

    # ---------------------------------------------------------- админ: Хэрэглэгчид
    def build_employee(self, frame):
        form = row(frame)
        self.name_entry = entry(form, "Нэр:", 22)
        self.department_entry = entry(form, "Хэлтэс:", 18)
        self.code_entry = entry(form, "Код:", 8)
        button(form, "Бүртгэх", self.add_employee_ui)
        button(form, "Жагсаалт шинэчлэх", self.refresh_employees)
        button(form, "Устгах", self.delete_selected_ui)
        button(form, "Нүүр устгах", self.delete_face_ui)
        ttk.Label(frame, foreground="gray", text="Ажилтан нэр, кодоороо нэвтэрнэ. Байгаа нэрийг дахин бүртгэвэл "
                  "хэлтэс, код нь шинэчлэгдэнэ. Ажилтан нүүрээ нэг л удаа бүртгүүлнэ — шинэчлэх бол «Нүүр устгах» "
                  "дарна.").pack(anchor="w", pady=(0, 6))
        self.tree = table(frame, ("Нэр", "Хэлтэс", "Код", "Нүүр"), {"Нэр": 250, "Хэлтэс": 250})

    def add_employee_ui(self):
        try:
            reg.add_employee(self.name_entry.get(), self.code_entry.get(), self.department_entry.get())
        except (ValueError, OSError) as error:
            return self.show_error(error)
        for field in (self.name_entry, self.department_entry, self.code_entry):
            field.delete(0, "end")
        self.refresh_employees()

    def refresh_employees(self):
        self.tree.delete(*self.tree.get_children())
        for emp in reg.list_employees():
            self.tree.insert("", "end", iid=emp["id"], values=(emp["name"], emp.get("department", ""),
                                                               "байгаа" if emp.get("code") else "алга",
                                                               "бүртгэсэн" if reg.get_face(emp["id"]) else "—"))
        self.refresh_board()

    def delete_selected_ui(self):
        employee_id = self.tree.focus()
        if not employee_id:
            return self.show_error("Устгах ажилтнаа жагсаалтаас сонгоно уу.")
        if messagebox.askyesno("Устгах", "Сонгосон ажилтныг устгах уу?", parent=self):
            reg.delete_employee(employee_id)
            self.refresh_employees()

    def delete_face_ui(self):
        employee_id = self.tree.focus()
        if not employee_id:
            return self.show_error("Ажилтнаа жагсаалтаас сонгоно уу.")
        if messagebox.askyesno("Нүүр устгах", "Сонгосон ажилтны нүүрийг устгах уу? Ажилтан дараа нь дахин "
                               "бүртгүүлэх боломжтой болно.", parent=self):
            reg.delete_face(employee_id)
            self.refresh_employees()

    # ---------------------------------------------------------- админ: Тохиргоо
    def build_settings(self, frame):
        settings = reg.load_settings()
        self.setting_fields = {}
        for key, text in SETTING_LABELS.items():
            form = row(frame)
            ttk.Label(form, text=text, width=36).pack(side="left")
            field = ttk.Entry(form, width=50)
            field.pack(side="left")
            value = settings[key]
            field.insert(0, ", ".join(value) if isinstance(value, list) else str(value))
            self.setting_fields[key] = field
        button(row(frame), "Хадгалах", self.save_settings_ui)

    def save_settings_ui(self):
        get = {key: field.get().strip() for key, field in self.setting_fields.items()}
        try:
            settings = {"work_start": get["work_start"], "work_end": get["work_end"],
                        "late_grace_min": int(get["late_grace_min"]), "away_after_sec": int(get["away_after_sec"]),
                        "unproductive": [word.strip() for word in get["unproductive"].split(",") if word.strip()]}
            prod.to_minutes(settings["work_start"])     # ЦЦ:ММ хэлбэртэй эсэхийг шалгана
            prod.to_minutes(settings["work_end"])
        except ValueError:
            return self.show_error("Цагийг ЦЦ:ММ, тоог бүхэл тоогоор бичнэ үү.")
        reg.save_settings(settings)
        messagebox.showinfo("Тохиргоо", "Хадгаллаа. Ажилтнуудад 5 секундын дотор хүрнэ.", parent=self)
        self.refresh_board()

    # ---------------------------------------------------------- ажилтан: камер
    def build_camera(self, frame):
        buttons = row(frame)
        button(buttons, "Камер нээх", lambda: self.start_watching(quiet=False))
        button(buttons, "Камер хаах", self.close_camera_ui)
        button(buttons, "Нүүр бүртгэх", self.enroll_face_ui)
        face = "бүртгэсэн" if self.me.get("face") else "бүртгээгүй — «Нүүр бүртгэх» дарна уу"
        self.face_status = ttk.Label(frame, foreground="gray", text=f"Нүүр: {face}")
        self.face_status.pack(anchor="w")
        self.face_label = ttk.Label(frame, text="Камер хаалттай. Зураг хадгалахгүй.", font=("Segoe UI", 13),
                                    wraplength=1000)
        self.face_label.pack()
        self.video_label = ttk.Label(frame)
        self.video_label.pack(pady=8)

    def start_watching(self, quiet=True):
        """Камер асааж, амьд хүн мөн эсэх, бүртгэлтэй ажилтан өөрөө сууж байгаа эсэхийг шалгаж эхэлнэ.
        Хугацааг зөвхөн хэмжилт явагдаж байх үед тоолно."""
        self.start_camera(quiet)
        if self.camera_service and self.monitor is None:
            self.monitor = FaceMonitor(self.me.get("face"))
            self.last_check = None

    def start_camera(self, quiet=False):
        """quiet=True (автоматаар асаахад) бол алдааны цонх гаргахгүй, камерын табд бичнэ."""
        if self.camera_service:                        # аль хэдийн нээлттэй
            return
        try:
            self.camera_service = CameraService()
            opened = self.camera_service.start()
            message = ("Камер нээгдсэнгүй. Шалгах зүйл: 1) Zoom, Teams, «Камер» зэрэг өөр програм камер ашиглаж "
                       "байгаа эсэх; 2) Windows → Тохиргоо → Нууцлал ба аюулгүй байдал → Камер → «Камерт хандах» болон "
                       "«Ширээний програмуудад камер ашиглахыг зөвшөөрөх» асаалттай эсэх; 3) камерын хаалт (shutter) "
                       "эсвэл камер унтраах F-товч; 4) Device Manager дээр камерын драйвер.")
        except (cv2.error, OSError, TypeError) as error:   # загвар ачаалж чадсангүй — OpenCV хуучин, models алга
            opened, message = False, (f"Камерын нүүр таних загвар ачаалж чадсангүй: {error}\nOpenCV-г шинэчилнэ үү: "
                                      "python -m pip install -U \"opencv-python>=4.11,<5\". Мөн програмын хажууд "
                                      "models хавтас байгаа эсэхийг шалгана уу.")
        if not opened:
            self.camera_service = None
            if quiet:                                  # камергүй үед гар, хулганы хөдөлгөөнөөр тооцно
                return self.face_label.config(text=message + " Камер ажиллахгүй үед ажилласан хугацааг гар, "
                                              "хулганы хөдөлгөөнөөр тооцно.", foreground="#9a6700")
            return self.show_error(message)
        self.render_camera_frame()

    def render_camera_frame(self):
        result = self.camera_service.get_frame()
        if result:
            frame, faces = result
            state, text, check = "no_face" if not len(faces) else "owner", f"Илэрсэн нүүр: {len(faces)}", None
            self.camera_state = "checking"             # нүүр бүртгэлийн үед баталгаажаагүй гэж үзнэ
            if self.enrollment:
                state, text, check = "checking", *self.enroll_step(frame, faces)
            elif self.monitor:
                counting = self.tracker is not None and self.tracker.running
                state, text = self.monitor.update(self.camera_service, frame, faces, counting)
                self.camera_state = state
                check = self.monitor.check
                if check is not None and check is not self.last_check:   # шинэ шалгалт эхэллээ
                    self.bring_to_front()
                self.last_check = check
            box, color = CAMERA_COLORS[state]
            if frame.mean() < 12:                      # бараг хар дүрс — камер «ажиллаж» байгаа ч юу ч харахгүй
                text = ("Камерын дүрс хар байна: камерын хаалт (shutter) хаалттай, F-товчоор камер унтарсан эсвэл "
                        "Windows-ийн нууцлалын тохиргоо камерыг хориглосон байж магадгүй.\n" + text)
            draw(frame, faces, box, check.step if check else None)
            frame = cv2.resize(frame, (480, 360))
            photo = tk.PhotoImage(data=cv2.imencode(".ppm", frame)[1].tobytes())   # OpenCV кадр → Tkinter зураг
            self.video_label.config(image=photo)
            self.video_label.image = photo             # устахгүйн тулд хадгална
            self.face_label.config(text=text, foreground=color)
        else:                                          # кадр ирэхгүй (камер салсан г.м.) — камер ажиллахгүй
            self.camera_state = None
            self.face_label.config(text="Камераас кадр ирэхгүй байна. Ажилласан хугацааг гар, хулганы "
                                        "хөдөлгөөнөөр тооцно.", foreground="#9a6700")
        self.camera_job = self.after(60, self.render_camera_frame)

    def enroll_face_ui(self):
        if self.me.get("face"):
            return self.show_error("Нүүр аль хэдийн бүртгэгдсэн. Шинэчлэх бол админд хандана уу.")
        self.start_watching(quiet=False)
        if self.camera_service:
            self.enrollment = FaceEnrollment()

    def enroll_step(self, frame, faces):
        """Нүүр бүртгэлийн нэг алхам. Хээ гарвал админы сервер рүү илгээнэ. (тайлбар, шалгалт) буцаана."""
        enrollment = self.enrollment
        feature, text = enrollment.update(self.camera_service, frame, faces)
        if feature is not None:
            self.enrollment = None
            feature = [float(value) for value in feature.ravel()]
            try:
                reg.ask(self.host, {"action": "face", "employee_id": self.me["id"], "feature": feature})
            except (OSError, ValueError) as error:
                self.after_idle(self.show_error, f"Нүүрийг админ руу илгээж чадсангүй: {error}")
                return "Нүүр бүртгэгдсэнгүй", None
            self.me["face"] = feature
            if self.monitor:
                self.monitor.set_owner(feature)        # хэмжилт явагдаж байвал шууд ашиглана
            self.face_status.config(text="Нүүр: бүртгэсэн")
            self.after_idle(messagebox.showinfo, "Нүүр бүртгэл", "Нүүр бүртгэгдлээ.")
        return text, enrollment.check

    def bring_to_front(self):
        """Амьд хүн шалгалт эхлэхэд цонхыг урд гаргаж, камерын табыг нээнэ."""
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(500, self.attributes, "-topmost", False)
        self.tabs.select(self.tab_frames["Камер"])
        self.bell()

    def close_camera_ui(self):
        if self.tracker:
            return self.show_error("Хэмжилт явагдаж байхад камер хаах боломжгүй. «Дуусгах» дарна уу.")
        self.stop_camera()

    def stop_camera(self):
        if self.camera_service:
            self.after_cancel(self.camera_job)
            self.camera_service.stop()
            self.camera_service = None
            self.enrollment = None
            self.camera_state = None
            self.video_label.config(image="")
            self.face_label.config(text="Камер хаалттай. Зураг хадгалахгүй.", foreground="")

    # ---------------------------------------------------------- ажилтан: компьютерийн идэвх
    def build_activity(self, frame):
        ttk.Label(frame, wraplength=1000, text="Хэмжилт зөвхөн «Хэмжилт эхлэх» дарсны дараа, зөвшөөрөлтэйгээр "
                  "эхэлнэ. Урд талын болон хулганы доорх цонхны програм, гарчиг (браузерын хуудасны гарчиг), "
                  "нээлттэй програмууд, гар/хулгана хөдөлсөн эсэхийг бүртгэж 5 секунд тутам админ руу илгээнэ. "
                  "Товчлуурын даралт, дэлгэцийн зураг, хуудасны агуулга авахгүй. Камер админы серверт нэвтэрмэгц "
                  "асаж, амьд хүн мөн эсэх, бүртгэлтэй ажилтан өөрөө сууж байгаа эсэхийг шалгана. Камер ажиллаж "
                  "байвал ажилласан хугацаа зөвхөн камер таныг баталгаажуулсан үед тоологдоно. Камер ажиллахгүй бол "
                  "гар, хулганы хөдөлгөөнөөр тооцно. Хэмжилтийн үеэр камер хаагдахгүй. «Түр зогсоох» дарвал "
                  "шалтгаанаа бичнэ — админ зөвшөөрвөл түр зогссон хугацаа ажилласанд тооцогдоно."
                  ).pack(anchor="w", pady=(0, 8))
        self.activity_buttons = row(frame)
        button(self.activity_buttons, "Хэмжилт эхлэх", self.start_activity)
        button(self.activity_buttons, "Түр зогсоох", self.pause_activity)
        button(self.activity_buttons, "Дуусгах", self.finish_activity)
        # «Түр зогсоох» дарахад товчлуурын доор гарч, шалтгааныг бичүүлнэ
        self.pause_form = ttk.Frame(frame)
        ttk.Label(self.pause_form, text="Ямар шалтгаанаар түр зогсоож байна вэ?").pack(side="left")
        self.pause_entry = ttk.Entry(self.pause_form, width=60)
        self.pause_entry.pack(side="left", padx=4)
        self.pause_entry.bind("<Return>", lambda event: self.send_pause())
        button(self.pause_form, "Илгээх", self.send_pause)
        button(self.pause_form, "Болих", self.hide_pause_form)
        self.activity_label = ttk.Label(frame, text="Хэмжилт эхлээгүй.", font=("Segoe UI", 11), wraplength=1000)
        self.activity_label.pack(anchor="w", pady=(4, 2))
        self.open_label = ttk.Label(frame, foreground="gray", wraplength=1000)
        self.open_label.pack(anchor="w", pady=(0, 6))
        self.pause_list_label = ttk.Label(frame, foreground="#1a5fb4", wraplength=1000)   # админы шийдвэр
        self.pause_list_label.pack(anchor="w", pady=(0, 6))
        self.usage_graph = tk.Canvas(frame, height=300, background="white")
        self.usage_graph.pack(fill="both", expand=True)

    def update_usage_graph(self, apps):
        """Програм, хуудас тус бүрийн хугацааг хэвтээ баганаар зурна (хамгийн их 8)."""
        self.usage_graph.delete("all")
        items = sorted(apps.items(), key=lambda item: item[1], reverse=True)[:8]
        maximum = max((seconds for _, seconds in items), default=0) or 1   # 0-д хуваахаас сэргийлнэ
        for index, (name, seconds) in enumerate(items):
            y = 18 + index * 34
            self.usage_graph.create_text(8, y + 10, text=name if len(name) <= 38 else name[:37] + "…", anchor="w")
            width = 380 * seconds / maximum
            self.usage_graph.create_rectangle(290, y, 290 + width, y + 20, fill="#4a90d9", outline="")
            self.usage_graph.create_text(760, y + 10, text=prod.duration(seconds), anchor="e")

    def start_activity(self):
        self.hide_pause_form()
        if self.tracker:                               # түр зогсоосон хэмжилтийг үргэлжлүүлнэ
            self.end_pause()
            self.pause_reason = ""
            self.tracker.resume()
        else:
            self.tracker = ActivityTracker()
            self.tracker.start()
            self.update_usage_graph({})
            if self.monitor:
                self.monitor.reset()                   # камерын хугацаа энэ хэмжилтээс тоологдоно
        self.start_watching()                          # камерыг хаасан эсвэл нэвтрэхэд нээгдээгүй бол дахин асаана

    def pause_activity(self):
        """«Түр зогсоох» дарахад товчлуурын доор шалтгаан бичих талбар гаргана."""
        if not self.tracker or self.tracker.paused:   # хэмжиж байгаа үед л (аль хэдийн зогссон бол асуухгүй)
            return
        self.pause_form.pack(anchor="w", pady=(0, 8), after=self.activity_buttons)
        self.pause_entry.focus_set()

    def send_pause(self):
        """Бичсэн шалтгааныг админ руу илгээгээд хэмжилтийг түр зогсооно."""
        if not self.tracker or self.tracker.paused:
            return self.hide_pause_form()
        reason = self.pause_entry.get().strip()
        if not reason:
            self.pause_entry.focus_set()
            return self.show_error("Шалтгаанаа бичнэ үү.")
        try:
            answer = reg.ask(self.host, {"action": "pause", "employee_id": self.me["id"], "reason": reason})
        except (ValueError, OSError) as error:
            return self.show_error(f"Админ руу илгээж чадсангүй: {error}")
        self.tracker.pause()
        self.pause_reason = reason
        self.pause_id, self.pause_started = answer.get("id"), time.monotonic()
        self.hide_pause_form()
        self.activity_label.config(text=f"Түр зогсоосон. Шалтгаан: {reason}\n"
                                        "Админд илгээлээ. Админ зөвшөөрвөл түр зогссон хугацаа ажилласанд "
                                        "тооцогдоно. Үргэлжлүүлэх бол «Хэмжилт эхлэх» дарна уу.")

    def end_pause(self):
        """Түр зогсолт дууслаа: хэдэн секунд зогссоныг дараагийн илгээлтээр админ руу явуулна."""
        if self.tracker and self.tracker.paused and self.pause_id:
            self.pause_ends.append({"id": self.pause_id, "seconds": round(time.monotonic() - self.pause_started)})
        self.pause_id = None

    def show_my_pauses(self, pauses):
        """Өнөөдрийн түр зогсолтууд ба админы шийдвэр. Админ шийдвэр гаргамагц дуу гаргана."""
        if pauses is None:                             # хуучин админы програм илгээдэггүй
            return
        lines = []
        for pause in pauses:
            status = pause.get("status") or "pending"
            if self.pause_statuses.get(pause["id"], status) != status:   # админ дөнгөж шийдвэрлэлээ
                self.bell()
            self.pause_statuses[pause["id"]] = status
            note = {"approved": " — ажилласанд тооцогдоно", "rejected": " — ажилласанд тооцогдохгүй"}.get(status, "")
            lines.append(f"{pause['time'][11:16]}  {pause['reason']} · {self.pause_length(pause)} · "
                         f"{reg.PAUSE_STATUS.get(status, status)}{note}")
        self.pause_list_label.config(text="Өнөөдрийн түр зогсолт:\n" + "\n".join(lines) if lines else "")

    def hide_pause_form(self):
        self.pause_entry.delete(0, "end")
        self.pause_form.pack_forget()

    def finish_activity(self):
        """Хэмжилтийг дуусгаад сүүлийн өсөлтийг илгээнэ. Админы самбарт «Офлайн» болно."""
        if not self.tracker:
            return
        self.hide_pause_form()
        self.end_pause()                               # түр зогссон байсан бол хугацааг нь хамт илгээнэ
        summary = self.tracker.stop()
        self.send_live("offline")
        self.tracker = None
        self.update_usage_graph(summary["apps"])
        self.activity_label.config(text=f"Хэмжилт дууслаа: {summary['total_seconds']:.0f} сек ажилласан.")

    def send_live(self, status=None):
        """Шууд төлөв болон сүүлд илгээснээс хойших өсөлтийг админ руу илгээнэ (0.5 сек л хүлээнэ)."""
        tracker = self.tracker
        delta = tracker.take_delta()
        if self.monitor:                               # камерын шалгалт: хэн хэдэн секунд суусан
            delta["camera"] = self.monitor.summary()
            self.monitor.reset()
        self.pending.append(delta)
        request = {"action": "live", "employee_id": self.me["id"], "computer": os.environ.get("COMPUTERNAME", ""),
                   "status": status or tracker.status, "app": tracker.app, "mouse": tracker.mouse_on,
                   "open": tracker.open, "reason": self.away_reason(), "deltas": self.pending,
                   "pause_ends": self.pause_ends,              # дууссан түр зогсолтын хугацаа
                   "pause_id": self.pause_id if tracker.paused else None,   # үргэлжилж буй түр зогсолт
                   "camera": self.camera_state is not None}   # False бол самбарт «(камергүй)»
        try:
            answer = reg.ask(self.host, request, timeout=0.5)
            tracker.away_after = answer["settings"]["away_after_sec"]     # админы тохиргоо
            self.show_my_pauses(answer.get("pauses"))  # админ түр зогсолтыг зөвшөөрсөн эсэх
        except OSError:                                # админ холбогдохгүй — 30 сек дараа хамт илгээнэ
            self.retry_at = time.time() + 30
            return
        except ValueError:
            pass                                       # сервер хүлээж аваагүй (жишээ нь ажилтан устсан)
        self.pending, self.pause_ends = [], []

    def away_reason(self):
        """Админы самбарт «Гарсан (…)» гэж харагдах шалтгаан: түр зогсоосон эсвэл камер баталгаажуулаагүй."""
        if self.tracker.paused:
            return self.pause_reason
        return "" if self.tracker.verified else CAMERA_REASONS.get(self.camera_state, "")

    def tick(self):
        """Секунд тутам идэвхийг хэмжиж харуулна, 5 секунд тутам админ руу илгээнэ."""
        self.after(1000, self.tick)
        if not self.tracker:
            return
        # Камер ажиллаж байвал зөвхөн ажилтан өөрөө сууж байхад, ажиллахгүй бол гар, хулганаар л ажилласан гэж тоолно
        self.tracker.verified = self.camera_state in ("owner", None)
        info = self.tracker.tick()
        self.seconds += 1
        if self.seconds % 5 == 0 and time.time() >= self.retry_at:
            self.send_live()
        self.open_label.config(text="Нээлттэй програмууд: " + ", ".join(info["open"]))
        if not self.tracker.paused:                    # түр зогсоосон бичгийг дарахгүй
            if info["status"] == "work":
                state = "Ажиллаж байна" + (" (камер ажиллахгүй — гар, хулганаар тооцож байна)"
                                           if self.camera_state is None else "")
            elif not self.tracker.verified:
                state = f"Тоологдохгүй байна ({self.away_reason()}) — камер таныг баталгаажуулаагүй"
            else:
                state = "Гарсан (гар, хулгана хөдлөөгүй)"
            camera = f"\nКамер: {self.face_label.cget('text')}" if self.monitor else ""
            self.activity_label.config(text=f"{state} · {info['total_seconds']:.0f} сек\nУрд: {info['app']}\n"
                                            f"Хулгана: {info['mouse']}{camera}")
            self.update_usage_graph(self.tracker.apps)

    def on_close(self):
        if messagebox.askokcancel("Гарах", "Програмыг хаах уу?", parent=self):
            self.finish_activity()                     # хэмжиж байсан бол сүүлийн өсөлтийг илгээнэ
            self.stop_camera()
            if self.server:
                self.server.close()
            self.destroy()


if __name__ == "__main__":
    ProductivityApp().mainloop()
