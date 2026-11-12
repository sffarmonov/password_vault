from __future__ import annotations

import os
import sys
import threading
import time
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import biometric
import browser_server
import xlsx_io
from vault_core import (
    InvalidMasterPassword,
    VaultError,
    VaultStore,
    generate_password,
)


APP_NAME = "Password Vault 0.2"
# After the master password or Touch ID is confirmed, browser autofill works
# without another fingerprint for this many seconds.
BROWSER_TRUST_SECONDS = 5 * 60

# Tk on macOS draws font points at 72 dpi (Windows uses 96), so the same size
# looks about a quarter smaller there; scale it up to keep text readable.
IS_MAC = sys.platform == "darwin"
UI_FONT = "Helvetica Neue" if IS_MAC else "Segoe UI"
SYMBOL_FONT = "Apple Symbols" if IS_MAC else "Segoe UI Symbol"


def fs(size: int) -> int:
    return round(size * 1.25) if IS_MAC else size
COLORS = {
    "nav": "#111827",
    "nav_hover": "#1F2937",
    "accent": "#5B5FEF",
    "accent_hover": "#494DD8",
    "bg": "#F4F6FA",
    "card": "#FFFFFF",
    "text": "#18212F",
    "muted": "#718096",
    "line": "#E4E8F0",
    "danger": "#D6455D",
    "success": "#15966A",
}


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    folder = base / "PasswordVault02"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def resource_path(name: str) -> Path:
    """Path to a bundled file, both from source and inside a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


APP_EXTENSIONS = {".app"} if sys.platform == "darwin" else {".exe", ".lnk"}


def looks_like_path(target: str) -> bool:
    """True for local paths: /..., ~/..., file://... or a Windows drive like C:/ or C:\\."""
    is_drive = len(target) > 2 and target[1] == ":" and target[2] in ("/", "\\")
    return target.startswith(("/", "~", "file://")) or is_drive


def local_app_path(target: str) -> Path | None:
    """Path of a local program from the URL field, e.g. /Applications/AnyConnect.app.

    Only application bundles/executables are accepted, so an imported entry
    cannot make the "Open" button run an arbitrary script or document.
    """
    if not looks_like_path(target):
        return None
    if target.startswith("file://"):
        target = target[len("file://"):]
    path = Path(target).expanduser()
    return path if path.suffix.lower() in APP_EXTENSIONS else None


def center_window(window: tk.Misc, width: int, height: int) -> None:
    window.update_idletasks()
    x = max(0, (window.winfo_screenwidth() - width) // 2)
    y = max(0, (window.winfo_screenheight() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def fit_window(window: tk.Misc, min_width: int, min_height: int) -> None:
    """Grow a dialog to fit its content (fonts differ between OSes) and center it."""
    window.update_idletasks()
    width = max(min_width, window.winfo_reqwidth())
    height = min(max(min_height, window.winfo_reqheight()), window.winfo_screenheight() - 80)
    center_window(window, width, height)


class EntryDialog(tk.Toplevel):
    def __init__(self, app: "PasswordVaultApp", entry: dict | None = None) -> None:
        super().__init__(app.root)
        self.app = app
        self.original = entry
        self.result: dict | None = None
        self.title("Изменить запись" if entry else "Новая запись")
        self.configure(bg=COLORS["card"])
        self.resizable(False, False)
        center_window(self, 590, 650)
        self.transient(app.root)
        self.grab_set()

        data = entry or {}
        self.vars = {
            "title": tk.StringVar(value=data.get("title", "")),
            "username": tk.StringVar(value=data.get("username", "")),
            "password": tk.StringVar(value=data.get("password", "")),
            "url": tk.StringVar(value=data.get("url", "")),
            "category": tk.StringVar(value=data.get("category", app.active_category if app.active_category not in ("Все записи", "Избранное") else "Личные")),
            "favorite": tk.BooleanVar(value=bool(data.get("favorite", False))),
        }

        body = tk.Frame(self, bg=COLORS["card"], padx=34, pady=25)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Данные аккаунта", bg=COLORS["card"], fg=COLORS["text"], font=(UI_FONT, fs(19), "bold")).pack(anchor="w")
        tk.Label(body, text="Все поля шифруются перед сохранением", bg=COLORS["card"], fg=COLORS["muted"], font=(UI_FONT, fs(10))).pack(anchor="w", pady=(2, 18))

        self._field(body, "Название *", "title")
        self._field(body, "Логин / email", "username")

        tk.Label(body, text="Пароль", bg=COLORS["card"], fg=COLORS["text"], font=(UI_FONT, fs(10), "bold")).pack(anchor="w", pady=(9, 5))
        password_row = tk.Frame(body, bg=COLORS["card"])
        password_row.pack(fill="x")
        self.password_entry = ttk.Entry(password_row, textvariable=self.vars["password"], show="•", font=(UI_FONT, fs(11)))
        self.password_entry.pack(side="left", fill="x", expand=True, ipady=7)
        ttk.Button(password_row, text="Показать", style="Soft.TButton", command=self._toggle_password).pack(side="left", padx=(7, 0))
        ttk.Button(password_row, text="Создать", style="Soft.TButton", command=self._generate).pack(side="left", padx=(7, 0))

        self._field(body, "Сайт / URL", "url")
        tk.Label(body, text="Категория", bg=COLORS["card"], fg=COLORS["text"], font=(UI_FONT, fs(10), "bold")).pack(anchor="w", pady=(9, 5))
        category = ttk.Combobox(body, textvariable=self.vars["category"], values=app.store.list_categories(), state="readonly", font=(UI_FONT, fs(10)))
        category.pack(fill="x", ipady=5)

        tk.Label(body, text="Заметка", bg=COLORS["card"], fg=COLORS["text"], font=(UI_FONT, fs(10), "bold")).pack(anchor="w", pady=(9, 5))
        self.notes = tk.Text(body, height=4, wrap="word", relief="flat", bd=0, highlightthickness=1, highlightbackground=COLORS["line"], highlightcolor=COLORS["accent"], bg="#F8FAFC", fg=COLORS["text"], insertbackground=COLORS["text"], selectbackground="#DADCFF", selectforeground=COLORS["text"], font=(UI_FONT, fs(10)), padx=8, pady=7)
        self.notes.pack(fill="x")
        self.notes.insert("1.0", data.get("notes", ""))

        ttk.Checkbutton(body, text="Добавить в избранное", variable=self.vars["favorite"]).pack(anchor="w", pady=(12, 0))

        actions = tk.Frame(body, bg=COLORS["card"])
        actions.pack(fill="x", pady=(22, 0))
        ttk.Button(actions, text="Отмена", style="Soft.TButton", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Сохранить", style="Accent.TButton", command=self._save).pack(side="right", padx=(0, 9))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Control-Return>", lambda _e: self._save())
        fit_window(self, 590, 650)

    def _field(self, parent: tk.Widget, label: str, key: str) -> None:
        tk.Label(parent, text=label, bg=COLORS["card"], fg=COLORS["text"], font=(UI_FONT, fs(10), "bold")).pack(anchor="w", pady=(9, 5))
        widget = ttk.Entry(parent, textvariable=self.vars[key], font=(UI_FONT, fs(11)))
        widget.pack(fill="x", ipady=7)
        if key == "title":
            widget.focus_set()

    def _toggle_password(self) -> None:
        self.password_entry.configure(show="" if self.password_entry.cget("show") else "•")

    def _generate(self) -> None:
        self.vars["password"].set(generate_password(20))
        self.password_entry.configure(show="")

    def _save(self) -> None:
        title = self.vars["title"].get().strip()
        if not title:
            messagebox.showwarning(APP_NAME, "Укажите название записи.", parent=self)
            return
        self.result = {
            "title": title,
            "username": self.vars["username"].get(),
            "password": self.vars["password"].get(),
            "url": self.vars["url"].get(),
            "notes": self.notes.get("1.0", "end-1c"),
            "category": self.vars["category"].get() or "Другое",
            "favorite": self.vars["favorite"].get(),
        }
        self.destroy()


class PasswordVaultApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.minsize(980, 640)
        center_window(self.root, 1180, 740)
        self.root.configure(bg=COLORS["bg"])
        self._set_icon()
        self.store = VaultStore(app_data_dir() / "vault.db")
        self.active_category = "Все записи"
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render_cards())
        self.last_activity = time.monotonic()
        self.clipboard_value: str | None = None
        self.last_auth = 0.0  # time.monotonic() of the last master password / Touch ID confirmation
        self._auth_lock = threading.Lock()
        self.browser = browser_server.BrowserServer(app_data_dir() / "browser.sock", self._handle_browser_request)
        self.browser.start()
        self._build_styles()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.bind_all("<KeyPress>", self._activity, add="+")
        self.root.bind_all("<Button>", self._activity, add="+")
        self.root.bind_all("<MouseWheel>", self._mousewheel, add="+")
        self.root.after(1000, self._auto_lock_tick)
        if self.store.is_initialized:
            self.show_unlock()
        else:
            self.show_setup()

    def _set_icon(self) -> None:
        icon = resource_path("assets/icon.png")
        if icon.exists():
            try:
                self._icon_image = tk.PhotoImage(file=str(icon))
                self.root.iconphoto(True, self._icon_image)
            except tk.TclError:
                pass

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TCheckbutton", background=COLORS["card"], foreground=COLORS["text"], font=(UI_FONT, fs(10)))
        style.map("TCheckbutton", background=[("active", COLORS["card"])], indicatorbackground=[("selected", COLORS["accent"]), ("!selected", "white")])
        style.configure("TEntry", foreground=COLORS["text"], insertcolor=COLORS["text"], fieldbackground="#F8FAFC", bordercolor=COLORS["line"], lightcolor=COLORS["line"], darkcolor=COLORS["line"], padding=7)
        style.configure("TCombobox", foreground=COLORS["text"], fieldbackground="#F8FAFC", padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", "#F8FAFC")], foreground=[("readonly", COLORS["text"])], selectbackground=[("readonly", "#F8FAFC")], selectforeground=[("readonly", COLORS["text"])])
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="white", borderwidth=0, padding=(16, 10), font=(UI_FONT, fs(10), "bold"))
        style.map("Accent.TButton", background=[("pressed", "#3D41C9"), ("active", COLORS["accent_hover"])], foreground=[("disabled", "#C7C9F9"), ("!disabled", "white")])
        style.configure("Soft.TButton", background="#EEF0FF", foreground=COLORS["accent"], borderwidth=0, padding=(12, 9), font=(UI_FONT, fs(9), "bold"))
        style.map("Soft.TButton", background=[("pressed", "#D3D7FF"), ("active", "#E1E4FF")], foreground=[("!disabled", COLORS["accent"])])
        style.configure("Danger.TButton", background="#FFF0F2", foreground=COLORS["danger"], borderwidth=0, padding=(10, 8), font=(UI_FONT, fs(9), "bold"))
        style.map("Danger.TButton", background=[("pressed", "#FFD2DA"), ("active", "#FFE1E6")], foreground=[("!disabled", COLORS["danger"])])

    def clear(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()

    def _auth_shell(self, title: str, subtitle: str) -> tk.Frame:
        self.clear()
        outer = tk.Frame(self.root, bg=COLORS["bg"])
        outer.pack(fill="both", expand=True)
        panel = tk.Frame(outer, bg=COLORS["card"], padx=46, pady=40, highlightbackground=COLORS["line"], highlightthickness=1)
        panel.place(relx=.5, rely=.5, anchor="center", width=480)
        tk.Label(panel, text="◆", bg=COLORS["card"], fg=COLORS["accent"], font=(SYMBOL_FONT, fs(32), "bold")).pack()
        tk.Label(panel, text=title, bg=COLORS["card"], fg=COLORS["text"], font=(UI_FONT, fs(23), "bold")).pack(pady=(12, 5))
        tk.Label(panel, text=subtitle, bg=COLORS["card"], fg=COLORS["muted"], font=(UI_FONT, fs(10)), wraplength=370, justify="center").pack(pady=(0, 24))
        return panel

    def show_setup(self) -> None:
        panel = self._auth_shell("Создайте хранилище", "Придумайте мастер-пароль. Он не сохраняется и не может быть восстановлен.")
        first = tk.StringVar()
        second = tk.StringVar()
        self._labeled_auth_entry(panel, "Мастер-пароль · минимум 12 символов", first)
        self._labeled_auth_entry(panel, "Повторите мастер-пароль", second)

        def create() -> None:
            if first.get() != second.get():
                messagebox.showerror(APP_NAME, "Пароли не совпадают.")
                return
            try:
                self.store.setup_master_password(first.get())
                self.last_auth = time.monotonic()
                self.last_activity = time.monotonic()
                self.show_main()
            except VaultError as exc:
                messagebox.showerror(APP_NAME, str(exc))

        ttk.Button(panel, text="Создать защищённое хранилище", style="Accent.TButton", command=create).pack(fill="x", pady=(22, 0))

    def _labeled_auth_entry(self, parent: tk.Widget, label: str, variable: tk.StringVar) -> ttk.Entry:
        tk.Label(parent, text=label, bg=COLORS["card"], fg=COLORS["text"], font=(UI_FONT, fs(10), "bold")).pack(anchor="w", pady=(9, 5))
        entry = ttk.Entry(parent, textvariable=variable, show="•", font=(UI_FONT, fs(12)))
        entry.pack(fill="x", ipady=7)
        return entry

    def show_unlock(self, reason: str | None = None) -> None:
        self.store.lock()
        self.last_auth = 0.0
        panel = self._auth_shell("Password Vault", reason or "Введите мастер-пароль, чтобы открыть локальное хранилище.")
        password = tk.StringVar()
        entry = self._labeled_auth_entry(panel, "Мастер-пароль", password)
        status = tk.Label(panel, text="", bg=COLORS["card"], fg=COLORS["danger"], font=(UI_FONT, fs(9)))
        status.pack(anchor="w", pady=(8, 0))

        def unlock() -> None:
            try:
                self.store.unlock(password.get())
                password.set("")
                self.last_activity = self.last_auth = time.monotonic()
                self.show_main()
            except InvalidMasterPassword:
                status.configure(text="Неверный мастер-пароль")
                password.set("")
                entry.focus_set()
            except VaultError as exc:
                messagebox.showerror(APP_NAME, str(exc))

        ttk.Button(panel, text="Разблокировать", style="Accent.TButton", command=unlock).pack(fill="x", pady=(14, 0))
        entry.bind("<Return>", lambda _e: unlock())
        entry.focus_set()

        if biometric.is_available() and biometric.is_enabled(self.store.db_path):
            def touch_unlock() -> None:
                if not biometric.authenticate("открыть хранилище Password Vault"):
                    status.configure(text="Touch ID не подтверждён. Введите мастер-пароль.")
                    entry.focus_set()
                    return
                key = biometric.load_key(self.store.db_path)
                try:
                    if key is None:
                        raise InvalidMasterPassword("Ключ не найден")
                    self.store.unlock_with_key(key)
                except InvalidMasterPassword:
                    biometric.disable(self.store.db_path)
                    status.configure(text="Ключ Touch ID устарел и отключён. Введите мастер-пароль.")
                    entry.focus_set()
                    return
                self.last_activity = self.last_auth = time.monotonic()
                self.show_main()

            ttk.Button(panel, text="Войти по Touch ID", style="Soft.TButton", command=touch_unlock).pack(fill="x", pady=(8, 0))

    def show_main(self) -> None:
        self.clear()
        shell = tk.Frame(self.root, bg=COLORS["bg"])
        shell.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(shell, bg=COLORS["nav"], width=245)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.content = tk.Frame(shell, bg=COLORS["bg"])
        self.content.pack(side="left", fill="both", expand=True)
        self._render_sidebar()
        self._render_content()

    def _render_sidebar(self) -> None:
        for child in self.sidebar.winfo_children():
            child.destroy()
        brand = tk.Frame(self.sidebar, bg=COLORS["nav"], padx=20, pady=24)
        brand.pack(fill="x")
        tk.Label(brand, text="◆  VAULT", bg=COLORS["nav"], fg="white", font=(UI_FONT, fs(16), "bold")).pack(anchor="w")
        tk.Label(brand, text="LOCAL · ENCRYPTED", bg=COLORS["nav"], fg="#8290A7", font=(UI_FONT, fs(8), "bold")).pack(anchor="w", pady=(3, 0))
        self._nav_button("Все записи", "▦", lambda: self._select_category("Все записи"))
        self._nav_button("Избранное", "★", lambda: self._select_category("Избранное"))
        tk.Label(self.sidebar, text="КАТЕГОРИИ", bg=COLORS["nav"], fg="#748198", font=(UI_FONT, fs(8), "bold"), padx=20, pady=12).pack(fill="x", anchor="w")
        for category in self.store.list_categories():
            self._nav_button(category, "•", lambda c=category: self._select_category(c))
        self._nav_button("Новая категория", "+", self._add_category, muted=True)
        spacer = tk.Frame(self.sidebar, bg=COLORS["nav"])
        spacer.pack(fill="both", expand=True)
        self._nav_button("Резервная копия", "↥", self._backup_menu)
        self._nav_button("Excel: экспорт / импорт", "▤", self._excel_menu)
        self._nav_button("Настройки", "⚙", self._settings)
        self._nav_button("Заблокировать", "◉", lambda: self.show_unlock("Хранилище заблокировано вручную."))
        tk.Label(self.sidebar, text="v0.2 · база хранится локально", bg=COLORS["nav"], fg="#667085", font=(UI_FONT, fs(8)), pady=14).pack()

    def _nav_button(self, text: str, icon: str, command, muted: bool = False) -> None:
        selected = text == self.active_category
        bg = COLORS["accent"] if selected else COLORS["nav"]
        fg = "#9AA7BA" if muted else "white"
        hover = COLORS["accent_hover"] if selected else COLORS["nav_hover"]
        # tk.Label instead of tk.Button: on macOS native buttons ignore bg/fg,
        # which made light text unreadable on a light system button.
        button = tk.Label(self.sidebar, text=f"  {icon}   {text}", anchor="w", bg=bg, fg=fg, padx=17, pady=9, font=(UI_FONT, fs(10), "bold" if selected else "normal"), cursor="hand2")
        button.bind("<Enter>", lambda _e: button.configure(bg=hover, fg="white"))
        button.bind("<Leave>", lambda _e: button.configure(bg=bg, fg=fg))
        button.bind("<ButtonRelease-1>", lambda _e: command())
        button.pack(fill="x", padx=10, pady=1)

    def _render_content(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        header = tk.Frame(self.content, bg=COLORS["bg"], padx=32, pady=24)
        header.pack(fill="x")
        left = tk.Frame(header, bg=COLORS["bg"])
        left.pack(side="left")
        tk.Label(left, text=self.active_category, bg=COLORS["bg"], fg=COLORS["text"], font=(UI_FONT, fs(22), "bold")).pack(anchor="w")
        self.count_label = tk.Label(left, text="", bg=COLORS["bg"], fg=COLORS["muted"], font=(UI_FONT, fs(9)))
        self.count_label.pack(anchor="w", pady=(3, 0))
        ttk.Button(header, text="+  Новая запись", style="Accent.TButton", command=self._new_entry).pack(side="right")
        search_frame = tk.Frame(header, bg=COLORS["card"], highlightbackground=COLORS["line"], highlightthickness=1)
        search_frame.pack(side="right", padx=(0, 12))
        tk.Label(search_frame, text="⌕", bg=COLORS["card"], fg=COLORS["muted"], font=(SYMBOL_FONT, fs(16)), padx=10).pack(side="left")
        search = tk.Entry(search_frame, textvariable=self.search_var, bd=0, relief="flat", bg=COLORS["card"], fg=COLORS["text"], insertbackground=COLORS["text"], font=(UI_FONT, fs(10)), width=27)
        search.pack(side="left", ipady=10, padx=(0, 10))
        self.cards_canvas = tk.Canvas(self.content, bg=COLORS["bg"], bd=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.content, orient="vertical", command=self.cards_canvas.yview)
        self.cards_frame = tk.Frame(self.cards_canvas, bg=COLORS["bg"], padx=32, pady=2)
        self.cards_window = self.cards_canvas.create_window((0, 0), window=self.cards_frame, anchor="nw")
        self.cards_canvas.configure(yscrollcommand=scrollbar.set)
        self.cards_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.cards_frame.bind("<Configure>", lambda _e: self.cards_canvas.configure(scrollregion=self.cards_canvas.bbox("all")))
        self.cards_canvas.bind("<Configure>", lambda e: self.cards_canvas.itemconfigure(self.cards_window, width=e.width))
        self._render_cards()

    def _mousewheel(self, event: tk.Event) -> None:
        if hasattr(self, "cards_canvas") and self.cards_canvas.winfo_exists():
            step = -event.delta if sys.platform == "darwin" else int(-event.delta / 120)
            if step:
                self.cards_canvas.yview_scroll(step, "units")

    def _filtered_entries(self) -> list[dict]:
        entries = self.store.list_entries()
        if self.active_category == "Избранное":
            entries = [entry for entry in entries if entry.get("favorite")]
        elif self.active_category != "Все записи":
            entries = [entry for entry in entries if entry.get("category") == self.active_category]
        query = self.search_var.get().strip().casefold()
        if query:
            entries = [entry for entry in entries if query in " ".join((entry.get("title", ""), entry.get("username", ""), entry.get("url", ""), entry.get("category", ""))).casefold()]
        return entries

    def _render_cards(self) -> None:
        if not hasattr(self, "cards_frame") or not self.cards_frame.winfo_exists():
            return
        for child in self.cards_frame.winfo_children():
            child.destroy()
        try:
            entries = self._filtered_entries()
        except VaultError:
            return
        self.count_label.configure(text=f"{len(entries)} сохранённых записей")
        if not entries:
            empty = tk.Frame(self.cards_frame, bg=COLORS["card"], padx=30, pady=55, highlightbackground=COLORS["line"], highlightthickness=1)
            empty.pack(fill="x", pady=(8, 0))
            tk.Label(empty, text="◇", bg=COLORS["card"], fg=COLORS["accent"], font=(SYMBOL_FONT, fs(34))).pack()
            tk.Label(empty, text="Здесь пока пусто", bg=COLORS["card"], fg=COLORS["text"], font=(UI_FONT, fs(15), "bold")).pack(pady=(8, 2))
            tk.Label(empty, text="Добавьте первую запись или измените фильтр поиска", bg=COLORS["card"], fg=COLORS["muted"], font=(UI_FONT, fs(10))).pack()
            return
        for entry in entries:
            self._entry_card(entry)

    def _entry_card(self, entry: dict) -> None:
        card = tk.Frame(self.cards_frame, bg=COLORS["card"], padx=18, pady=14, highlightbackground=COLORS["line"], highlightthickness=1)
        card.pack(fill="x", pady=6)
        icon = tk.Label(card, text=(entry.get("title", "?")[:1] or "?").upper(), bg="#EEF0FF", fg=COLORS["accent"], font=(UI_FONT, fs(15), "bold"), width=3, height=2)
        icon.pack(side="left", padx=(0, 14))
        info = tk.Frame(card, bg=COLORS["card"])
        info.pack(side="left", fill="x", expand=True)
        title_row = tk.Frame(info, bg=COLORS["card"])
        title_row.pack(fill="x")
        tk.Label(title_row, text=entry.get("title", ""), bg=COLORS["card"], fg=COLORS["text"], font=(UI_FONT, fs(11), "bold")).pack(side="left")
        if entry.get("favorite"):
            tk.Label(title_row, text="★", bg=COLORS["card"], fg="#E3A008", font=(UI_FONT, fs(10))).pack(side="left", padx=7)
        detail = entry.get("username") or entry.get("url") or "Без логина"
        tk.Label(info, text=f"{detail}   ·   {entry.get('category', 'Другое')}", bg=COLORS["card"], fg=COLORS["muted"], font=(UI_FONT, fs(9))).pack(anchor="w", pady=(4, 0))
        ttk.Button(card, text="Удалить", style="Danger.TButton", command=lambda: self._delete_entry(entry)).pack(side="right", padx=(7, 0))
        ttk.Button(card, text="Изменить", style="Soft.TButton", command=lambda: self._edit_entry(entry)).pack(side="right", padx=(7, 0))
        ttk.Button(card, text="Пароль", style="Soft.TButton", command=lambda: self._copy(entry.get("password", ""), "Пароль")).pack(side="right", padx=(7, 0))
        ttk.Button(card, text="Логин", style="Soft.TButton", command=lambda: self._copy(entry.get("username", ""), "Логин")).pack(side="right", padx=(7, 0))
        if entry.get("url"):
            ttk.Button(card, text="Открыть", style="Soft.TButton", command=lambda: self._open_target(entry["url"])).pack(side="right", padx=(7, 0))

    def _select_category(self, category: str) -> None:
        self.active_category = category
        self._render_sidebar()
        self._render_content()

    def _new_entry(self) -> None:
        dialog = EntryDialog(self)
        self.root.wait_window(dialog)
        if dialog.result:
            try:
                self.store.add_entry(dialog.result)
                self._render_cards()
            except VaultError as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def _edit_entry(self, entry: dict) -> None:
        dialog = EntryDialog(self, entry)
        self.root.wait_window(dialog)
        if dialog.result:
            try:
                self.store.update_entry(entry["id"], dialog.result)
                self._render_cards()
            except VaultError as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def _delete_entry(self, entry: dict) -> None:
        if messagebox.askyesno(APP_NAME, f"Удалить «{entry.get('title', '')}» без возможности отмены?"):
            self.store.delete_entry(entry["id"])
            self._render_cards()

    def _open_target(self, target: str) -> None:
        """Open a website, or launch a local application if the URL field holds its path."""
        target = target.strip()
        app_path = local_app_path(target)
        if app_path is not None:
            if not app_path.exists():
                messagebox.showerror(APP_NAME, f"Программа не найдена:\n{app_path}")
                return
            try:
                if sys.platform == "darwin":
                    subprocess.Popen(["open", str(app_path)])
                else:
                    os.startfile(str(app_path))  # type: ignore[attr-defined]  # Windows only
            except OSError as exc:
                messagebox.showerror(APP_NAME, f"Не удалось запустить программу:\n{exc}")
            return
        if looks_like_path(target):
            messagebox.showerror(APP_NAME, "Можно открыть только сайт или программу (.app на macOS, .exe или ярлык .lnk на Windows).")
            return
        webbrowser.open(target if "://" in target else "https://" + target)

    def _copy(self, value: str, label: str) -> None:
        if not value:
            messagebox.showinfo(APP_NAME, f"Поле «{label}» пустое.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.root.update()
        self.clipboard_value = value
        seconds = int(self.store.get_preference("clipboard_seconds", "30"))
        self.root.after(seconds * 1000, lambda expected=value: self._clear_clipboard(expected))
        messagebox.showinfo(APP_NAME, f"{label} скопирован. Буфер очистится через {seconds} сек.")

    def _clear_clipboard(self, expected: str) -> None:
        if self.clipboard_value != expected:
            return
        try:
            if self.root.clipboard_get() == expected:
                self.root.clipboard_clear()
                self.root.update()
        except tk.TclError:
            pass
        self.clipboard_value = None

    def _add_category(self) -> None:
        name = simpledialog.askstring(APP_NAME, "Название новой категории:", parent=self.root)
        if not name:
            return
        try:
            self.store.add_category(name)
            self._render_sidebar()
        except VaultError as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def _backup_menu(self) -> None:
        answer = messagebox.askyesnocancel(APP_NAME, "Да — создать резервную копию\nНет — восстановить из копии\nОтмена — закрыть")
        if answer is True:
            self._create_backup()
        elif answer is False:
            self._restore_backup()

    def _excel_menu(self) -> None:
        answer = messagebox.askyesnocancel(APP_NAME, "Да — экспортировать записи в Excel (.xlsx)\nНет — импортировать записи из Excel\nОтмена — закрыть")
        if answer is True:
            self._export_xlsx()
        elif answer is False:
            self._import_xlsx()

    def _export_xlsx(self) -> None:
        entries = self.store.list_entries()
        if not entries:
            messagebox.showinfo(APP_NAME, "Нет записей для экспорта.")
            return
        if not messagebox.askokcancel(
            APP_NAME,
            "Файл Excel НЕ зашифрован: любой, кто его откроет, увидит все логины и пароли.\n\n"
            "Храните его в надёжном месте и удалите, когда он станет не нужен.",
            icon="warning",
        ):
            return
        password = simpledialog.askstring(APP_NAME, "Для экспорта подтвердите мастер-пароль:", show="•", parent=self.root)
        if password is None:
            return
        try:
            self.store.unlock(password)
        except InvalidMasterPassword:
            messagebox.showerror(APP_NAME, "Неверный мастер-пароль. Экспорт отменён.")
            return
        default = f"password-vault-{datetime.now():%Y-%m-%d_%H-%M}.xlsx"
        path = filedialog.asksaveasfilename(title="Экспорт в Excel", defaultextension=".xlsx", initialfile=default, filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            xlsx_io.export_entries(entries, path)
            messagebox.showinfo(APP_NAME, f"Экспортировано записей: {len(entries)}\n{path}")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Не удалось сохранить файл:\n{exc}")

    def _import_xlsx(self) -> None:
        path = filedialog.askopenfilename(title="Импорт из Excel", filetypes=[("Excel", "*.xlsx"), ("Все файлы", "*.*")])
        if not path:
            return
        try:
            rows = xlsx_io.read_entries(path)
        except xlsx_io.XlsxImportError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        existing = {
            (e.get("title", "").casefold(), e.get("username", "").casefold(), e.get("url", "").casefold())
            for e in self.store.list_entries()
        }
        new_rows, duplicates = [], 0
        for row in rows:
            signature = (row["title"].casefold(), row.get("username", "").casefold(), row.get("url", "").casefold())
            if signature in existing:
                duplicates += 1
                continue
            existing.add(signature)
            new_rows.append(row)
        if not new_rows:
            messagebox.showinfo(APP_NAME, f"Новых записей не найдено.\nСтрок в файле: {len(rows)}, уже есть в хранилище: {duplicates}.")
            return
        if not messagebox.askokcancel(APP_NAME, f"Будет добавлено записей: {len(new_rows)}\nПропущено дубликатов: {duplicates}\n\nСуществующие записи не изменятся."):
            return
        known = {c.casefold(): c for c in self.store.list_categories()}
        imported = 0
        for row in new_rows:
            category = " ".join(row.get("category", "").split())
            if not category or len(category) > 40:
                category = "Другое"
            elif category.casefold() not in known:
                self.store.add_category(category)
                known[category.casefold()] = category
            row["category"] = known[category.casefold()]
            self.store.add_entry(row)
            imported += 1
        self.active_category = "Все записи"
        self.show_main()
        messagebox.showinfo(APP_NAME, f"Импортировано записей: {imported}\nПропущено дубликатов: {duplicates}")

    def _create_backup(self) -> None:
        default = f"password-vault-{datetime.now():%Y-%m-%d_%H-%M}.pvbackup"
        path = filedialog.asksaveasfilename(title="Сохранить резервную копию", defaultextension=".pvbackup", initialfile=default, filetypes=[("Password Vault backup", "*.pvbackup"), ("Все файлы", "*.*")])
        if not path:
            return
        try:
            self.store.create_backup(path)
            messagebox.showinfo(APP_NAME, "Зашифрованная резервная копия создана.")
        except (VaultError, OSError) as exc:
            messagebox.showerror(APP_NAME, f"Не удалось создать копию:\n{exc}")

    def _restore_backup(self) -> None:
        path = filedialog.askopenfilename(title="Выберите резервную копию", filetypes=[("Password Vault backup", "*.pvbackup"), ("Все файлы", "*.*")])
        if not path:
            return
        password = simpledialog.askstring(APP_NAME, "Введите мастер-пароль, которым была защищена эта копия:", show="•", parent=self.root)
        if password is None:
            return
        if not messagebox.askyesno(APP_NAME, "Текущие записи будут заменены данными из резервной копии. Продолжить?"):
            return
        try:
            count = self.store.restore_backup(path, password)
            self.active_category = "Все записи"
            self.show_main()
            messagebox.showinfo(APP_NAME, f"Восстановлено записей: {count}")
        except (VaultError, OSError) as exc:
            messagebox.showerror(APP_NAME, f"Не удалось восстановить копию:\n{exc}")

    def _settings(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Настройки")
        window.configure(bg=COLORS["card"])
        window.resizable(False, False)
        center_window(window, 470, 390)
        window.transient(self.root)
        window.grab_set()
        body = tk.Frame(window, bg=COLORS["card"], padx=30, pady=28)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Настройки безопасности", bg=COLORS["card"], fg=COLORS["text"], font=(UI_FONT, fs(18), "bold")).pack(anchor="w")
        tk.Label(body, text="Автоблокировка после бездействия", bg=COLORS["card"], fg=COLORS["text"], font=(UI_FONT, fs(10), "bold")).pack(anchor="w", pady=(22, 6))
        auto = tk.StringVar(value=self.store.get_preference("auto_lock_minutes", "5"))
        ttk.Combobox(body, textvariable=auto, values=("1", "3", "5", "10", "15", "30"), state="readonly").pack(fill="x")
        tk.Label(body, text="Очистка буфера обмена, секунд", bg=COLORS["card"], fg=COLORS["text"], font=(UI_FONT, fs(10), "bold")).pack(anchor="w", pady=(16, 6))
        clip = tk.StringVar(value=self.store.get_preference("clipboard_seconds", "30"))
        ttk.Combobox(body, textvariable=clip, values=("10", "20", "30", "45", "60"), state="readonly").pack(fill="x")

        def save() -> None:
            self.store.set_preference("auto_lock_minutes", auto.get())
            self.store.set_preference("clipboard_seconds", clip.get())
            self.store.set_preference("browser_fill", "1" if browser_fill.get() else "0")
            if biometric.is_available():
                was_enabled = biometric.is_enabled(self.store.db_path)
                if touch_id.get() and not was_enabled:
                    if not biometric.authenticate("включить вход по Touch ID в Password Vault"):
                        messagebox.showwarning(APP_NAME, "Touch ID не подтверждён, вход по отпечатку не включён.", parent=window)
                        return
                    try:
                        biometric.store_key(self.store.db_path, self.store.export_key())
                    except Exception as exc:
                        messagebox.showerror(APP_NAME, f"Не удалось сохранить ключ в Связку ключей:\n{exc}", parent=window)
                        return
                elif not touch_id.get() and was_enabled:
                    biometric.disable(self.store.db_path)
            self.last_activity = time.monotonic()
            window.destroy()

        touch_id = tk.BooleanVar(value=biometric.is_enabled(self.store.db_path))
        if biometric.is_available():
            ttk.Checkbutton(body, text="Вход по Touch ID", variable=touch_id).pack(anchor="w", pady=(16, 0))
            tk.Label(body, text="Ключ хранилища сохраняется в Связке ключей macOS\nи выдаётся только после отпечатка пальца.", bg=COLORS["card"], fg=COLORS["muted"], font=(UI_FONT, fs(9)), justify="left").pack(anchor="w", pady=(2, 0))

        browser_fill = tk.BooleanVar(value=self.store.get_preference("browser_fill", "1") == "1")
        if browser_server.is_supported():
            ttk.Checkbutton(body, text="Заполнение логинов в браузере", variable=browser_fill).pack(anchor="w", pady=(16, 0))
            tk.Label(body, text="Через расширение Password Vault для Chromium-браузеров.\nTouch ID запрашивается, если вход был более 5 минут назад.", bg=COLORS["card"], fg=COLORS["muted"], font=(UI_FONT, fs(9)), justify="left").pack(anchor="w", pady=(2, 0))

        ttk.Button(body, text="Сменить мастер-пароль", style="Soft.TButton", command=lambda: self._change_password(window)).pack(fill="x", pady=(22, 8))
        ttk.Button(body, text="Сохранить настройки", style="Accent.TButton", command=save).pack(fill="x")
        fit_window(window, 470, 390)

    def _change_password(self, parent: tk.Misc) -> None:
        current = simpledialog.askstring(APP_NAME, "Текущий мастер-пароль:", show="•", parent=parent)
        if current is None:
            return
        new = simpledialog.askstring(APP_NAME, "Новый мастер-пароль (минимум 12 символов):", show="•", parent=parent)
        if new is None:
            return
        confirm = simpledialog.askstring(APP_NAME, "Повторите новый мастер-пароль:", show="•", parent=parent)
        if new != confirm:
            messagebox.showerror(APP_NAME, "Новые пароли не совпадают.", parent=parent)
            return
        try:
            self.store.change_master_password(current, new)
            if biometric.is_enabled(self.store.db_path):
                biometric.store_key(self.store.db_path, self.store.export_key())
            self.last_activity = time.monotonic()
            messagebox.showinfo(APP_NAME, "Мастер-пароль изменён. Старые резервные копии открываются старым паролем.", parent=parent)
        except VaultError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=parent)

    def _activity(self, _event=None) -> None:
        if self.store.is_unlocked:
            self.last_activity = time.monotonic()

    def _auto_lock_tick(self) -> None:
        if self.store.is_unlocked:
            minutes = int(self.store.get_preference("auto_lock_minutes", "5"))
            if time.monotonic() - self.last_activity >= minutes * 60:
                self.show_unlock(f"Автоблокировка после {minutes} мин. бездействия.")
        self.root.after(1000, self._auto_lock_tick)

    # --- Browser extension -------------------------------------------------
    # Called from the browser server thread: must not touch Tk widgets.

    def _handle_browser_request(self, request: dict) -> dict:
        command = request.get("cmd")
        if command == "status":
            return {"ok": True, "unlocked": self.store.is_unlocked}
        if not self.store.is_unlocked:
            return {"ok": False, "error": "locked"}
        if self.store.get_preference("browser_fill", "1") != "1":
            return {"ok": False, "error": "disabled"}
        url = str(request.get("url", ""))
        if command == "list":
            matches = [
                {"id": e["id"], "title": e.get("title", ""), "username": e.get("username", "")}
                for e in self.store.list_entries()
                if browser_server.site_matches(e.get("url", ""), url)
            ]
            return {"ok": True, "entries": matches, "insecure": url.lower().startswith("http://")}
        if command == "credentials":
            entry = next((e for e in self.store.list_entries() if e["id"] == request.get("id")), None)
            if entry is None:
                return {"ok": False, "error": "not_found"}
            if not browser_server.site_matches(entry.get("url", ""), url):
                return {"ok": False, "error": "site_mismatch"}
            denied = self._confirm_browser_access(browser_server.host_of(url))
            if denied:
                return {"ok": False, "error": denied}
            if not self.store.is_unlocked:  # locked while waiting for Touch ID
                return {"ok": False, "error": "locked"}
            return {"ok": True, "username": entry.get("username", ""), "password": entry.get("password", "")}
        return {"ok": False, "error": "unknown_command"}

    def _confirm_browser_access(self, host: str) -> str | None:
        """None if access is allowed, otherwise an error code for the extension."""
        with self._auth_lock:  # one Touch ID prompt at a time
            if time.monotonic() - self.last_auth < BROWSER_TRUST_SECONDS:
                return None
            if not biometric.is_available():
                return "reauth_required"
            if not biometric.authenticate(f"заполнить логин на сайте {host}"):
                return "touch_id_failed"
            self.last_auth = time.monotonic()
            return None

    def _close(self) -> None:
        self.browser.stop()
        self.store.lock()
        if self.clipboard_value:
            self._clear_clipboard(self.clipboard_value)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    PasswordVaultApp().run()
