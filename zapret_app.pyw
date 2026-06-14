# -*- coding: utf-8 -*-
"""
Zapret Control — современное GUI (CustomTkinter) для обхода блокировок
Discord, YouTube и Telegram.

Фаза 1: стратегии берутся из декларативного presets.json (zapret_core.load_presets),
а не парсятся из .bat на лету.

Запуск: pythonw zapret_app.pyw   (или собранный ZapretControl.exe)
"""

import os
import sys
import time
import queue
import ctypes
import threading

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import customtkinter as ctk

import zapret_core as zc

try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_OK = True
except Exception:
    _TRAY_OK = False


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Палитра как пары (светлая, тёмная) — CustomTkinter сам выбирает по режиму.
WIN_BG = ("#f2f3f7", "#15161c")
SIDEBAR_BG = ("#e7e9f1", "#0f1014")
CARD_BG = ("#ffffff", "#1f2129")
CARD_HOVER = ("#e9ebf3", "#272a34")
BTN_HOVER = ("#d8dbe6", "#343a46")     # ховер неакцентной кнопки
SWITCH_OFF = ("#aeb4c2", "#3a3f4b")    # дорожка выключенного тумблера
SWITCH_KNOB = ("#ffffff", "#f0f2f5")   # бегунок тумблера
FIELD_BG = ("#dfe3ec", "#2a2e38")      # фон выпадающих списков/полей
LOG_BG = ("#ffffff", "#101218")
LOG_FG = ("#1a1c22", "#d7dbe0")
TEXT = ("#1a1c22", "#e9eaf0")
MUTED = ("#6b7280", "#8a909b")

ACCENT = "#7c5cff"
ACCENT_HOVER = "#6a4ae6"

# темы оформления (акцентный цвет, hover)
THEMES = {
    "Фиолетовая": ("#7c5cff", "#6a4ae6"),
    "Синяя":      ("#2f80ed", "#256fd1"),
    "Бирюзовая":  ("#1f9ec9", "#1b87aa"),
    "Зелёная":    ("#27ae60", "#1f9551"),
    "Янтарная":   ("#e0a52b", "#c98f1f"),
    "Розовая":    ("#e0559b", "#c9468a"),
}
APPEARANCE = {"Тёмная": "dark", "Светлая": "light", "Системная": "system"}

GREEN = "#3ad07a"
RED = "#e0575b"
YELLOW = "#e0b13a"
FONT = "Segoe UI"


def _pick(c):
    """Вернуть одиночный цвет из пары (светлая, тёмная) по текущему режиму ctk."""
    if isinstance(c, (tuple, list)):
        return c[1] if ctk.get_appearance_mode() == "Dark" else c[0]
    return c

APP_NAME = "Zapret GUI"


class ZapretApp(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=WIN_BG)
        self.title(f"{APP_NAME} — обход Discord, YouTube, Telegram")
        self.geometry("1020x700")
        self.minsize(920, 620)
        try:
            self.iconbitmap(self._asset("icon.ico"))
            self.after(300, lambda: self.iconbitmap(self._asset("icon.ico")))
        except Exception:
            pass

        self.cfg = zc.load_config()
        # оформление — задать режим (тёмная/светлая) и акцент до построения UI
        ctk.set_appearance_mode(self.cfg.get("appearance", "dark"))
        global ACCENT, ACCENT_HOVER
        _theme = self.cfg.get("accent_name", "Фиолетовая")
        if _theme in THEMES:
            ACCENT, ACCENT_HOVER = THEMES[_theme]
        self.presets = zc.load_presets()
        self.preset_by_name = {p["name"]: p for p in self.presets}
        self.proc = None
        self.log_queue = queue.Queue()
        self.ui_queue = queue.Queue()
        self._status_busy = False

        self.auto_running = False
        self.auto_cancel = False
        self.auto_best = None
        self.auto_total_targets = 0

        # Фаза 3: авто-восстановление / логи / завершение
        self._closing = False
        self.tray = None
        self._tray_hinted = False
        self.active_args = None          # аргументы текущего запуска (для watchdog)
        self.active_preset_name = None   # имя текущего пресета
        self._auto_full_pass = []        # рабочие стратегии последнего поиска
        try:
            self._logf = open(zc.current_log_path(), "a", encoding="utf-8")
            self._logf.write(f"\n===== Запуск {time.strftime('%Y-%m-%d %H:%M:%S')} "
                             f"(v{zc.APP_VERSION}) =====\n")
            self._logf.flush()
        except Exception:
            self._logf = None

        self.pages = {}
        self.nav_buttons = {}

        self._init_ttk_style()
        self._build_layout()
        self._show_page("control")

        self._poll_ui()
        self.refresh_status()
        self.after(3000, self._auto_refresh)
        self.after(1500, self._startup_update_check)
        threading.Thread(target=self._watchdog_loop, daemon=True).start()
        if self.cfg.get("autostart_bypass"):
            self.after(1400, self._autostart_bypass)
        self.after(900, self._first_run_wizard)
        self._setup_tray()
        self.protocol("WM_DELETE_WINDOW", self._on_x)

    def _asset(self, name):
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, "assets", name)

    def _init_ttk_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Zap.Treeview", background=_pick(CARD_BG),
                        fieldbackground=_pick(CARD_BG), foreground=_pick(TEXT),
                        rowheight=28, borderwidth=0)
        style.configure("Zap.Treeview.Heading", background=_pick(SIDEBAR_BG),
                        foreground=_pick(MUTED), borderwidth=0, relief="flat")
        style.map("Zap.Treeview", background=[("selected", ACCENT)])

    # -- каркас ----------------------------------------------------------- #
    def _build_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        side = ctk.CTkFrame(self, width=212, corner_radius=0, fg_color=SIDEBAR_BG)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)

        head = ctk.CTkFrame(side, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(20, 16))
        try:
            from PIL import Image
            self._logo_img = ctk.CTkImage(Image.open(self._asset("icon.png")),
                                          size=(42, 42))
            ctk.CTkLabel(head, text="", image=self._logo_img).pack(side="left")
        except Exception:
            ctk.CTkLabel(head, text="⚡", font=(FONT, 24),
                         text_color=ACCENT).pack(side="left")
        ttl = ctk.CTkFrame(head, fg_color="transparent")
        ttl.pack(side="left", padx=(10, 0))
        ctk.CTkLabel(ttl, text="Zapret GUI", font=(FONT, 18, "bold"),
                     text_color=ACCENT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(ttl, text="by Enzowax", font=(FONT, 11),
                     text_color=MUTED, anchor="w").pack(anchor="w")

        for key, label in [("control", "🛡   Управление"), ("auto", "🔍   Авто-поиск"),
                           ("tgws", "✈   Telegram"), ("settings", "⚙   Настройки"),
                           ("log", "📜   Журнал")]:
            b = ctk.CTkButton(side, text=label, anchor="w", height=42, corner_radius=8,
                              fg_color="transparent", hover_color=CARD_HOVER,
                              text_color=TEXT, font=(FONT, 14),
                              command=lambda k=key: self._show_page(k))
            b.pack(fill="x", padx=10, pady=3)
            self.nav_buttons[key] = b

        self.side_status = ctk.CTkLabel(side, text="●  проверка…", font=(FONT, 12),
                                        text_color=MUTED, anchor="w")
        self.side_status.pack(side="bottom", fill="x", padx=16, pady=(8, 6))
        admin = "админ" if zc.is_admin() else "без прав админа!"
        ctk.CTkLabel(side, text=f"v{zc.APP_VERSION} · {admin}", font=(FONT, 10),
                     text_color=MUTED, anchor="w").pack(side="bottom", fill="x",
                                                        padx=16, pady=(0, 2))

        self.container = ctk.CTkFrame(self, fg_color=WIN_BG, corner_radius=0)
        self.container.grid(row=0, column=1, sticky="nsew")
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.pages["control"] = self._build_control_page()
        self.pages["auto"] = self._build_auto_page()
        self.pages["tgws"] = self._build_tgws_page()
        self.pages["settings"] = self._build_settings_page()
        self.pages["log"] = self._build_log_page()

    def _show_page(self, key):
        for page in self.pages.values():
            page.grid_remove()
        self.pages[key].grid(row=0, column=0, sticky="nsew")
        for k, b in self.nav_buttons.items():
            b.configure(fg_color=ACCENT if k == key else "transparent")

    # -- конструкторы ----------------------------------------------------- #
    def _page(self):
        return ctk.CTkScrollableFrame(self.container, fg_color=WIN_BG,
                                      scrollbar_button_color=CARD_BG)

    def _title(self, parent, text, subtitle=None):
        ctk.CTkLabel(parent, text=text, font=(FONT, 24, "bold"), text_color=TEXT,
                     anchor="w").pack(fill="x", padx=6, pady=(8, 2))
        if subtitle:
            ctk.CTkLabel(parent, text=subtitle, font=(FONT, 12), text_color=MUTED,
                         anchor="w", justify="left", wraplength=720).pack(
                fill="x", padx=6, pady=(0, 10))

    def _section(self, parent, text):
        ctk.CTkLabel(parent, text=text.upper(), font=(FONT, 12, "bold"),
                     text_color=MUTED, anchor="w").pack(fill="x", padx=8, pady=(16, 4))

    def _card(self, parent):
        f = ctk.CTkFrame(parent, corner_radius=12, fg_color=CARD_BG)
        f.pack(fill="x", padx=4, pady=5)
        f.grid_columnconfigure(1, weight=1)
        return f

    def _card_row(self, parent, icon, title, subtitle):
        f = self._card(parent)
        ctk.CTkLabel(f, text=icon, font=(FONT, 22)).grid(
            row=0, column=0, rowspan=2, padx=(16, 12), pady=14)
        ctk.CTkLabel(f, text=title, font=(FONT, 14, "bold"), text_color=TEXT,
                     anchor="w").grid(row=0, column=1, sticky="sw", pady=(14, 0))
        ctk.CTkLabel(f, text=subtitle, font=(FONT, 11), text_color=MUTED,
                     anchor="w").grid(row=1, column=1, sticky="nw", pady=(0, 14))
        return f

    def _btn(self, parent, text, command, accent=False, width=150):
        return ctk.CTkButton(
            parent, text=text, command=command, width=width, height=36,
            corner_radius=8, font=(FONT, 13),
            fg_color=ACCENT if accent else CARD_HOVER,
            hover_color=ACCENT_HOVER if accent else BTN_HOVER,
            text_color="#ffffff" if accent else TEXT)

    # -- страница: Управление --------------------------------------------- #
    def _build_control_page(self):
        p = self._page()
        self._title(p, "Управление Zapret",
                    "Выберите пресет и запустите обход. Пресеты хранятся в "
                    "presets.json. Тонкая настройка — в разделе «Настройки».")

        self._section(p, "Статус работы")
        c = self._card(p)
        self.ctl_dot = ctk.CTkLabel(c, text="●", font=(FONT, 24), text_color=MUTED)
        self.ctl_dot.grid(row=0, column=0, rowspan=2, padx=(16, 12), pady=14)
        self.ctl_status_title = ctk.CTkLabel(c, text="Проверка…", font=(FONT, 14, "bold"),
                                             text_color=TEXT, anchor="w")
        self.ctl_status_title.grid(row=0, column=1, sticky="sw", pady=(14, 0))
        self.ctl_status_sub = ctk.CTkLabel(c, text="", font=(FONT, 11),
                                           text_color=MUTED, anchor="w")
        self.ctl_status_sub.grid(row=1, column=1, sticky="nw", pady=(0, 14))

        self._section(p, "Запуск")
        c = self._card_row(p, "⚡", "Запуск обхода",
                           "Запускает winws.exe с выбранным пресетом")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        self.btn_start = self._btn(box, "▶  Запустить", self.on_start, accent=True)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = self._btn(box, "■  Остановить", self.on_stop)
        self.btn_stop.pack(side="left", padx=4)

        self._section(p, "Пресет обхода блокировок")
        c = self._card_row(p, "⭐", "Текущий пресет", "Выберите стратегию обхода")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        names = [p["name"] for p in self.presets]
        self.strategy_var = ctk.StringVar()
        last = self.cfg.get("strategy")
        if last in names:
            self.strategy_var.set(last)
        elif "general" in names:
            self.strategy_var.set("general")
        elif names:
            self.strategy_var.set(names[0])
        self.strategy_menu = ctk.CTkOptionMenu(
            box, values=names or ["—"], variable=self.strategy_var, width=290,
            height=36, font=(FONT, 13), corner_radius=8, fg_color=FIELD_BG, text_color=TEXT,
                          dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT,
            button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            command=self._on_strategy_pick)
        self.strategy_menu.pack(side="left", padx=4)
        self._btn(box, "Аргументы", self.show_args, width=110).pack(side="left", padx=4)

        self._section(p, "Автозапуск при старте Windows (служба)")
        c = self._card_row(p, "🔁", "Служба zapret",
                           "Обход стартует автоматически при включении ПК")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        self._btn(box, "Установить", self.on_install_service, accent=True,
                  width=120).pack(side="left", padx=4)
        self._btn(box, "Удалить", self.on_remove_service, width=110).pack(side="left", padx=4)

        self._section(p, "Параметры обхода")
        c = self._card_row(p, "🎮", "Игровой фильтр", "Расширяет диапазон портов для игр")
        self.game_seg = ctk.CTkSegmentedButton(
            c, values=["Выкл", "TCP+UDP", "TCP", "UDP"], command=self._on_game_seg,
            font=(FONT, 12), text_color=TEXT, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER)
        self.game_seg.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        self.game_seg.set({"off": "Выкл", "all": "TCP+UDP", "tcp": "TCP",
                           "udp": "UDP"}[zc.get_game_mode()])

        c = self._card_row(p, "🚀", "Автозапуск обхода",
                           "Запускать обход при старте приложения")
        self.autostart_switch = ctk.CTkSwitch(c, text="", command=self._on_autostart_toggle,
                                              progress_color=ACCENT, fg_color=SWITCH_OFF, button_color=SWITCH_KNOB)
        self.autostart_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20), pady=12, sticky="e")
        if self.cfg.get("autostart_bypass"):
            self.autostart_switch.select()

        c = self._card_row(p, "🩺", "Авто-восстановление",
                           "Перезапускать обход, если он упал или перестал работать")
        self.recovery_switch = ctk.CTkSwitch(c, text="", command=self._on_recovery_toggle,
                                             progress_color=ACCENT, fg_color=SWITCH_OFF, button_color=SWITCH_KNOB)
        self.recovery_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20), pady=12, sticky="e")
        if self.cfg.get("auto_recovery"):
            self.recovery_switch.select()

        c = self._card_row(p, "🔒", "Шифрованный DNS (DoH)",
                           "Системный DNS через DoH (часть блокировок — по DNS)")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        _doh = zc.doh_status()
        self.doh_provider = ctk.StringVar(
            value={"cloudflare": "Cloudflare", "google": "Google"}.get(_doh["provider"], "Cloudflare"))
        ctk.CTkSegmentedButton(box, values=["Cloudflare", "Google"],
                               variable=self.doh_provider, font=(FONT, 12),
                               command=self._on_doh_provider_change,
                               text_color=TEXT, selected_color=ACCENT,
                               selected_hover_color=ACCENT_HOVER).pack(side="left", padx=6)
        self.doh_switch = ctk.CTkSwitch(box, text="", command=self._on_doh_toggle,
                                        progress_color=ACCENT, fg_color=SWITCH_OFF, button_color=SWITCH_KNOB)
        self.doh_switch.pack(side="left", padx=10)
        if _doh["enabled"]:
            self.doh_switch.select()

        c = self._card_row(p, "🌐", "IPSet-фильтр", "Текущее состояние списка IP")
        self.ipset_label = ctk.CTkLabel(c, text="…", font=(FONT, 12), text_color=MUTED)
        self.ipset_label.grid(row=0, column=2, rowspan=2, padx=(0, 8), pady=12, sticky="e")
        self._btn(c, "Обновить", self.on_update_ipset, width=110).grid(
            row=0, column=3, rowspan=2, padx=14, pady=12)

        self._section(p, "Инструменты")
        c = self._card(p)
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=0, columnspan=3, padx=12, pady=12, sticky="w")
        self._btn(box, "Диагностика", self.on_diagnostics).pack(side="left", padx=4)
        self._btn(box, "Тест соединения", self.on_test).pack(side="left", padx=4)
        self._btn(box, "Сохранить отчёт", self.on_support_bundle, width=160).pack(
            side="left", padx=4)
        self._btn(box, "Папка логов", self.on_open_logs, width=130).pack(side="left", padx=4)
        box2 = ctk.CTkFrame(c, fg_color="transparent")
        box2.grid(row=1, column=0, columnspan=3, padx=12, pady=(0, 12), sticky="w")
        self._btn(box2, "Экспорт настроек", self.on_export_settings, width=160).pack(
            side="left", padx=4)
        self._btn(box2, "Импорт настроек", self.on_import_settings, width=160).pack(
            side="left", padx=4)
        return p

    # -- страница: Авто-поиск --------------------------------------------- #
    def _build_auto_page(self):
        p = self._page()
        self._title(p, "Авто-поиск стратегии",
                    "Двухфазный подбор: быстрый отсев всех пресетов, затем точная "
                    "проверка лучших. Проверки хостов выполняются асинхронно.")

        self._section(p, "Что проверять")
        c = self._card(p)
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=0, columnspan=3, padx=12, pady=10, sticky="w")
        self.svc_vars = {}
        for key in ("discord", "youtube", "google"):
            var = ctk.BooleanVar(value=(key != "google"))
            self.svc_vars[key] = var
            ctk.CTkCheckBox(box, text=zc.AUTO_SERVICE_LABELS[key], variable=var,
                            font=(FONT, 13), fg_color=ACCENT,
                            hover_color=ACCENT_HOVER).pack(side="left", padx=12)
        ctk.CTkLabel(box, text="(собирается полный список рабочих стратегий)",
                     font=(FONT, 11), text_color=MUTED).pack(side="left", padx=18)

        c = self._card(p)
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=0, columnspan=3, padx=12, pady=12, sticky="ew")
        self.btn_auto_start = self._btn(box, "🔍  Начать поиск", self.on_auto_start,
                                        accent=True, width=160)
        self.btn_auto_start.pack(side="left", padx=4)
        self.btn_auto_stop = self._btn(box, "■  Остановить", self.on_auto_stop, width=130)
        self.btn_auto_stop.pack(side="left", padx=4)
        self.btn_auto_stop.configure(state="disabled")
        self.auto_bar = ctk.CTkProgressBar(box, width=240, progress_color=ACCENT)
        self.auto_bar.pack(side="left", padx=14)
        self.auto_bar.set(0)
        self.auto_phase_lbl = ctk.CTkLabel(box, text="", font=(FONT, 12), text_color=MUTED)
        self.auto_phase_lbl.pack(side="left", padx=4)

        self._section(p, "Результаты (точная проверка кандидатов)")
        c = self._card(p)
        cols = ("strategy", "discord", "youtube", "google", "total", "ms")
        self.tree = ttk.Treeview(c, columns=cols, show="headings", height=10,
                                 style="Zap.Treeview")
        heads = {"strategy": ("Стратегия", 250), "discord": ("Discord", 80),
                 "youtube": ("YouTube", 80), "google": ("Google", 80),
                 "total": ("Итог", 70), "ms": ("мс", 70)}
        for col in cols:
            t, w = heads[col]
            self.tree.heading(col, text=t)
            self.tree.column(col, width=w, anchor=("w" if col == "strategy" else "center"),
                             stretch=(col == "strategy"))
        self.tree.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        c.grid_columnconfigure(0, weight=1)
        self.tree.tag_configure("best", background="#173d17", foreground="#9f9")
        self.tree.tag_configure("good", foreground=GREEN)
        self.tree.tag_configure("partial", foreground=YELLOW)
        self.tree.tag_configure("bad", foreground=RED)

        c = self._card(p)
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=0, padx=12, pady=12, sticky="w")
        self.btn_apply_best = self._btn(box, "Применить лучшую", self.on_apply_best,
                                        accent=True, width=170)
        self.btn_apply_best.pack(side="left", padx=4)
        self.btn_apply_best.configure(state="disabled")
        self.btn_install_best = self._btn(box, "Установить как службу",
                                          self.on_install_best, width=200)
        self.btn_install_best.pack(side="left", padx=4)
        self.btn_install_best.configure(state="disabled")
        return p

    # -- страница: Telegram ----------------------------------------------- #
    def _build_tgws_page(self):
        p = self._page()
        self._title(p, "Telegram-прокси",
                    "Встроенный MTProto-прокси для Telegram (WebSocket-мост). "
                    "Отдельная программа не нужна — всё работает внутри приложения. "
                    "Запустите прокси и добавьте ссылку в Telegram.")
        self._section(p, "Статус")
        c = self._card(p)
        self.tg_dot = ctk.CTkLabel(c, text="●", font=(FONT, 24), text_color=MUTED)
        self.tg_dot.grid(row=0, column=0, rowspan=2, padx=(16, 12), pady=14)
        self.tg_title = ctk.CTkLabel(c, text="Проверка…", font=(FONT, 14, "bold"),
                                     text_color=TEXT, anchor="w")
        self.tg_title.grid(row=0, column=1, sticky="sw", pady=(14, 0))
        self.tg_sub = ctk.CTkLabel(c, text="", font=(FONT, 11), text_color=MUTED, anchor="w")
        self.tg_sub.grid(row=1, column=1, sticky="nw", pady=(0, 14))

        self._section(p, "Управление")
        c = self._card_row(p, "✈", "Встроенный прокси",
                           f"Слушает {zc.TG_DEFAULT_HOST}:{zc.tg_get_port()}")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        self.btn_tg_start = self._btn(box, "▶  Запустить", self.on_tg_start, accent=True)
        self.btn_tg_start.pack(side="left", padx=4)
        self.btn_tg_stop = self._btn(box, "■  Остановить", self.on_tg_stop)
        self.btn_tg_stop.pack(side="left", padx=4)

        self._section(p, "Ссылка для Telegram")
        c = self._card(p)
        self.tg_link_var = ctk.StringVar(value=zc.tg_proxy_url())
        ctk.CTkEntry(c, textvariable=self.tg_link_var, font=(FONT, 12), height=36,
                     fg_color=FIELD_BG, text_color=TEXT, border_width=0).grid(
            row=0, column=0, sticky="ew", padx=(12, 8), pady=12)
        c.grid_columnconfigure(0, weight=1)
        self._btn(c, "Скопировать", self.on_tg_copy, width=130).grid(
            row=0, column=1, padx=4, pady=12)
        self._btn(c, "Открыть в Telegram", self.on_tg_open, accent=True, width=180).grid(
            row=0, column=2, padx=(4, 12), pady=12)

        ctk.CTkLabel(
            p, wraplength=720, justify="left", font=(FONT, 11), text_color=MUTED,
            text=("Как подключить: «Открыть в Telegram» добавит прокси автоматически, "
                  "либо вручную — Telegram → Настройки → Данные и память → Прокси → "
                  "Добавить прокси → MTProto, и включите его.")
        ).pack(anchor="w", padx=12, pady=(6, 4))

        self._section(p, "Настройки прокси")
        c = self._card_row(p, "⚙", "Порт и секрет",
                           "Порт локального прокси и MTProto-секрет")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        self.tg_port_var = ctk.StringVar(value=str(zc.tg_get_port()))
        ctk.CTkEntry(box, textvariable=self.tg_port_var, width=80, height=36,
                     font=(FONT, 13), justify="center").pack(side="left", padx=4)
        self._btn(box, "Применить", self.on_tg_apply_port, width=110).pack(side="left", padx=4)
        self._btn(box, "Сменить секрет", self.on_tg_regen, width=150).pack(side="left", padx=4)
        return p

    # -- страница: Настройки приложения ----------------------------------- #
    def _build_settings_page(self):
        p = self._page()
        self._title(p, "Настройки приложения",
                    "Параметры самого приложения: обновления, оформление, трей, антивирус.")

        self._section(p, "Обновления")
        c = self._card_row(p, "⬆", f"Версия {zc.APP_VERSION}",
                           "Проверить и установить новую версию с GitHub")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        self.upd_label = ctk.CTkLabel(box, text="", font=(FONT, 11), text_color=MUTED)
        self.upd_label.pack(side="left", padx=(0, 8))
        self._btn(box, "Проверить", self.on_check_update, accent=True,
                  width=120).pack(side="left", padx=4)

        c = self._card_row(p, "🔄", "Автопроверка обновлений",
                           "Проверять новые версии при запуске")
        self.update_switch = ctk.CTkSwitch(c, text="", command=self._on_update_toggle,
                                           progress_color=ACCENT, fg_color=SWITCH_OFF, button_color=SWITCH_KNOB)
        self.update_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20), pady=12, sticky="e")
        if zc.get_update_enabled():
            self.update_switch.select()

        self._section(p, "Оформление")
        c = self._card_row(p, "🌗", "Тема", "Тёмная / светлая / системная")
        self.appearance_var = ctk.StringVar(
            value={v: k for k, v in APPEARANCE.items()}.get(
                self.cfg.get("appearance", "dark"), "Тёмная"))
        ctk.CTkSegmentedButton(c, values=list(APPEARANCE.keys()),
                               variable=self.appearance_var, font=(FONT, 12),
                               command=self._on_appearance_change,
                               text_color=TEXT, selected_color=ACCENT,
                               selected_hover_color=ACCENT_HOVER).grid(
            row=0, column=2, rowspan=2, padx=14, pady=12)

        c = self._card_row(p, "🎨", "Акцентный цвет", "Цвет кнопок и выделения")
        self.theme_var = ctk.StringVar(value=self.cfg.get("accent_name", "Фиолетовая"))
        ctk.CTkOptionMenu(c, values=list(THEMES.keys()), variable=self.theme_var,
                          command=self._on_theme_change, width=160, height=36,
                          font=(FONT, 13), corner_radius=8, fg_color=FIELD_BG, text_color=TEXT,
                          dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT,
                          button_color=ACCENT, button_hover_color=ACCENT_HOVER).grid(
            row=0, column=2, rowspan=2, padx=14, pady=12)

        self._section(p, "Поведение")
        c = self._card_row(p, "📥", "Сворачивать в трей",
                           "При закрытии окна прятать в трей (обход продолжит работать)")
        self.tray_switch = ctk.CTkSwitch(c, text="", command=self._on_tray_toggle,
                                         progress_color=ACCENT, fg_color=SWITCH_OFF, button_color=SWITCH_KNOB)
        self.tray_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20), pady=12, sticky="e")
        if self.cfg.get("minimize_to_tray", True):
            self.tray_switch.select()

        self._section(p, "Антивирус")
        c = self._card_row(p, "🛡", "Windows Defender",
                           "Добавить папку в исключения — меньше ложных срабатываний AV")
        self._btn(c, "Добавить в исключения", self.on_add_defender_exclusion,
                  accent=True, width=200).grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        return p

    # -- страница: Журнал ------------------------------------------------- #
    def _build_log_page(self):
        p = ctk.CTkFrame(self.container, fg_color=WIN_BG)
        p.grid_rowconfigure(1, weight=1)
        p.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(p, text="Журнал", font=(FONT, 24, "bold"), text_color=TEXT,
                     anchor="w").grid(row=0, column=0, sticky="w", padx=16, pady=(12, 6))
        self.logbox = ctk.CTkTextbox(p, font=("Consolas", 12), fg_color=LOG_BG,
                                     text_color=LOG_FG, wrap="word")
        self.logbox.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))
        self.logbox.configure(state="disabled")
        self._btn(p, "Очистить журнал", self.clear_log, width=160).grid(
            row=2, column=0, sticky="e", padx=16, pady=(0, 12))
        self.log_msg("Готово. Выберите пресет и нажмите «Запустить».")
        return p

    # -- журнал / очередь ------------------------------------------------- #
    def log_msg(self, text):
        self.log_queue.put(str(text))

    def post(self, fn):
        self.ui_queue.put(fn)

    def _poll_ui(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.logbox.configure(state="normal")
                self.logbox.insert("end", line.rstrip() + "\n")
                self.logbox.see("end")
                self.logbox.configure(state="disabled")
                if self._logf:
                    try:
                        self._logf.write(time.strftime("%H:%M:%S ") + line.rstrip() + "\n")
                        self._logf.flush()
                    except Exception:
                        pass
        except queue.Empty:
            pass
        try:
            while True:
                fn = self.ui_queue.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.after(100, self._poll_ui)

    def clear_log(self):
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")

    # -- статус ----------------------------------------------------------- #
    def _auto_refresh(self):
        self.refresh_status()
        self.after(3000, self._auto_refresh)

    def refresh_status(self):
        if self._status_busy:
            return
        self._status_busy = True

        def worker():
            running = zc.winws_running()
            installed = zc.service_installed()
            svc_run = zc.service_running() if installed else False
            ipset = zc.get_ipset_status()
            tg = zc.tg_proxy_running()
            self.post(lambda: self._apply_status(running, installed, svc_run, ipset, tg))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_status(self, running, installed, svc_run, ipset, tg):
        self._status_busy = False
        if running:
            self.ctl_dot.configure(text_color=GREEN)
            self.ctl_status_title.configure(text="Zapret работает")
            sub = "Обход блокировок активен"
            self.side_status.configure(text="●  Zapret работает", text_color=GREEN)
        else:
            self.ctl_dot.configure(text_color=RED)
            self.ctl_status_title.configure(text="Zapret остановлен")
            sub = "Обход не запущен"
            self.side_status.configure(text="●  остановлен", text_color=RED)
        if installed:
            sub += f"   ·   служба: {'работает' if svc_run else 'установлена'}"
        self.ctl_status_sub.configure(text=sub)
        self.ipset_label.configure(text=f"IPSet: {ipset}")

        if tg:
            self.tg_dot.configure(text_color=GREEN)
            self.tg_title.configure(text="Telegram-прокси работает")
            self.tg_sub.configure(text=f"Слушает {zc.TG_DEFAULT_HOST}:{zc.tg_get_port()}")
        else:
            self.tg_dot.configure(text_color=RED)
            self.tg_title.configure(text="Telegram-прокси остановлен")
            self.tg_sub.configure(text="Прокси не запущен")

        if self.tray is not None:
            try:
                self.tray.icon = self._make_tray_image(running)
            except Exception:
                pass

    # -- управление обходом ----------------------------------------------- #
    def _selected_preset(self):
        name = self.strategy_var.get()
        if not name or name == "—":
            messagebox.showwarning("Zapret", "Сначала выберите пресет.")
            return None
        p = self.preset_by_name.get(name)
        if not p:
            messagebox.showerror("Zapret", f"Пресет не найден: {name}")
            return None
        return p

    def _on_strategy_pick(self, _=None):
        self.cfg["strategy"] = self.strategy_var.get()
        zc.save_config(self.cfg)

    def on_start(self):
        if not os.path.exists(zc.WINWS):
            messagebox.showerror("Zapret", f"Не найден winws.exe:\n{zc.WINWS}")
            return
        if zc.service_running():
            messagebox.showwarning("Zapret", "Служба zapret уже запущена. "
                                   "Удалите службу, чтобы запускать обход вручную.")
            return
        preset = self._selected_preset()
        if not preset:
            return
        zc.kill_winws_only()
        mode = zc.get_game_mode()
        args = zc.build_args_str(preset["args"], mode)
        if not args:
            messagebox.showerror("Zapret", "Не удалось разобрать аргументы пресета.")
            return
        self.active_args = args
        self.active_preset_name = preset["name"]
        zc.enable_tcp_timestamps()
        self._on_strategy_pick()
        self.log_msg(f"--- Запуск пресета: {preset['name']} (фильтр игр: {mode}) ---")
        try:
            self.proc = zc.start_winws_logged(args)
        except Exception as e:
            self.log_msg(f"[ОШИБКА] {e}")
            messagebox.showerror("Zapret", f"Не удалось запустить winws.exe:\n{e}")
            return
        threading.Thread(target=self._read_output, args=(self.proc,), daemon=True).start()
        self.log_msg("winws.exe запущен.")
        self.after(700, self.refresh_status)

    def _read_output(self, proc):
        try:
            for line in proc.stdout:
                if line:
                    self.log_msg(line)
        except Exception:
            pass
        self.log_msg(f"--- winws.exe завершился (код {proc.poll()}) ---")
        self.post(self.refresh_status)

    def on_stop(self):
        self.log_msg("--- Остановка обхода ---")

        def worker():
            if zc.service_installed():
                zc.run_hidden(["net", "stop", zc.SERVICE_NAME])
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
            zc.kill_winws_only()
            zc.remove_windivert()
            self.proc = None
            self.active_args = None
            self.active_preset_name = None
            self.log_msg("Обход остановлен.")
            self.post(self.refresh_status)

        threading.Thread(target=worker, daemon=True).start()

    def on_install_service(self):
        preset = self._selected_preset()
        if not preset:
            return
        if not messagebox.askyesno("Служба", f"Установить пресет «{preset['name']}» "
                                   "как службу автозапуска?"):
            return
        mode = zc.get_game_mode()
        self.log_msg(f"--- Установка службы из «{preset['name']}» ---")

        def worker():
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
            self.proc = None
            ok, log = zc.install_service(preset["name"], preset["args"], mode)
            if log:
                self.log_msg(log)
            self.log_msg("Служба установлена." if ok else "[ОШИБКА] Служба не установлена.")
            self.post(self.refresh_status)

        threading.Thread(target=worker, daemon=True).start()

    def on_remove_service(self):
        if not zc.service_installed():
            messagebox.showinfo("Zapret", "Служба не установлена.")
            return
        self.log_msg("--- Удаление службы ---")

        def worker():
            zc.remove_service()
            self.proc = None
            self.log_msg("Служба удалена.")
            self.post(self.refresh_status)

        threading.Thread(target=worker, daemon=True).start()

    # -- настройки / инструменты ------------------------------------------ #
    def _on_game_seg(self, value):
        mode = {"Выкл": "off", "TCP+UDP": "all", "TCP": "tcp", "UDP": "udp"}[value]
        zc.set_game_mode(mode)
        self.log_msg(f"Игровой фильтр: {mode}. Перезапустите обход, чтобы применить.")

    def _on_update_toggle(self):
        en = bool(self.update_switch.get())
        zc.set_update_enabled(en)
        self.log_msg("Проверка обновлений: " + ("включена" if en else "выключена"))

    def _on_autostart_toggle(self):
        self.cfg["autostart_bypass"] = bool(self.autostart_switch.get())
        zc.save_config(self.cfg)
        self.log_msg("Автозапуск обхода: "
                     + ("включён" if self.cfg["autostart_bypass"] else "выключен"))

    def _on_recovery_toggle(self):
        self.cfg["auto_recovery"] = bool(self.recovery_switch.get())
        zc.save_config(self.cfg)
        self.log_msg("Авто-восстановление: "
                     + ("включено" if self.cfg["auto_recovery"] else "выключено"))

    # -- первый запуск ---------------------------------------------------- #
    def _first_run_wizard(self):
        if self.cfg.get("first_run_done"):
            return
        self.cfg["first_run_done"] = True
        zc.save_config(self.cfg)
        if messagebox.askyesno(
                "Добро пожаловать в Zapret GUI",
                "Запустить авто-поиск рабочих стратегий?\n\n"
                "Программа подберёт оптимальную стратегию обхода и составит "
                "список запасных. Если активная стратегия перестанет работать, "
                "приложение само переключится на другую рабочую.\n\n"
                "Поиск займёт пару минут."):
            self._show_page("auto")
            self.after(400, self.on_auto_start)

    # -- авто-восстановление (watchdog) ----------------------------------- #
    def _autostart_bypass(self):
        if not zc.winws_running() and not zc.service_running():
            self.log_msg("Автозапуск обхода…")
            self.on_start()

    def _watchdog_loop(self):
        # фоновая проверка раз в WATCHDOG_INTERVAL; работает и для ручного запуска,
        # и для службы. При отказе текущей стратегии — переключение на следующую
        # рабочую из пула.
        fails = 0
        while not self._closing:
            for _ in range(int(zc.WATCHDOG_INTERVAL * 4)):
                if self._closing:
                    return
                time.sleep(0.25)
            if self._closing:
                return
            if not self.cfg.get("auto_recovery") or self.auto_running:
                fails = 0
                continue

            manual = bool(self.proc and self.proc.poll() is None)
            proc_dead = bool(self.proc is not None and self.proc.poll() is not None)
            service = zc.service_running()

            if not manual and not service:
                fails = 0
                if proc_dead:   # мы запускали процесс, а он умер
                    self.log_msg("[watchdog] winws.exe не работает — перезапуск")
                    self._recover(switch=False)
                continue

            if not self.active_preset_name:   # для службы берём из конфига
                self.active_preset_name = self.cfg.get("strategy")

            res = zc.check_hosts(zc.WATCHDOG_HEALTH_HOSTS, 3.0, attempts=1)
            ok = sum(1 for h in zc.WATCHDOG_HEALTH_HOSTS if res[h][0])
            if ok == 0:
                fails += 1
                self.log_msg(f"[watchdog] цели недоступны ({fails}/{zc.WATCHDOG_FAIL_THRESHOLD})")
                if fails >= zc.WATCHDOG_FAIL_THRESHOLD:
                    self._recover(switch=True)
                    fails = 0
            else:
                fails = 0

    def _watchdog_restart(self):
        # перезапуск текущей стратегии (служба или ручной режим)
        if zc.service_installed() and not (self.proc and self.proc.poll() is None):
            zc.run_hidden(["net", "stop", zc.SERVICE_NAME])
            zc.run_hidden(["net", "start", zc.SERVICE_NAME])
            self.post(self.refresh_status)
            return
        if not self.active_args:
            return
        try:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
            zc.kill_winws_only()
            time.sleep(1.0)
            self.proc = zc.start_winws_logged(self.active_args)
            threading.Thread(target=self._read_output, args=(self.proc,),
                             daemon=True).start()
            self.post(self.refresh_status)
        except Exception as e:
            self.log_msg(f"[watchdog] не удалось перезапустить: {e}")

    def _recover(self, switch):
        """Переключиться на следующую рабочую стратегию из пула (switch=True)
        либо перезапустить текущую (switch=False)."""
        if switch:
            pool = self.cfg.get("recovery_pool", []) or []
            cands = [n for n in pool
                     if n != self.active_preset_name and n in self.preset_by_name]
            if cands:
                self.log_msg(f"[watchdog] «{self.active_preset_name}» не работает — "
                             f"переключаюсь на «{cands[0]}»")
                self._switch_to(cands[0])
                return
            self.log_msg("[watchdog] запасных рабочих стратегий нет — перезапуск текущей")
        self._watchdog_restart()

    def _switch_to(self, name):
        preset = self.preset_by_name.get(name)
        if not preset:
            self._watchdog_restart()
            return
        mode = zc.get_game_mode()

        # режим службы — переустановить службу с новым пресетом
        if zc.service_installed() and not (self.proc and self.proc.poll() is None):
            ok, _log = zc.install_service(name, preset["args"], mode)
            self.active_preset_name = name
            self.cfg["strategy"] = name
            zc.save_config(self.cfg)
            self.post(lambda: self.strategy_var.set(name))
            self.post(self.refresh_status)
            self.log_msg(f"[watchdog] служба переустановлена со стратегией «{name}»"
                         if ok else "[watchdog] не удалось переустановить службу")
            return

        # ручной режим
        args = zc.build_args_str(preset["args"], mode)
        if not args:
            self._watchdog_restart()
            return
        try:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
            zc.kill_winws_only()
            time.sleep(1.0)
            self.proc = zc.start_winws_logged(args)
            self.active_args = args
            self.active_preset_name = name
            threading.Thread(target=self._read_output, args=(self.proc,),
                             daemon=True).start()
            self.cfg["strategy"] = name
            zc.save_config(self.cfg)
            self.post(lambda: self.strategy_var.set(name))
            self.post(self.refresh_status)
        except Exception as e:
            self.log_msg(f"[watchdog] не удалось переключиться: {e}")

    # -- отчёт / логи ----------------------------------------------------- #
    def on_support_bundle(self):
        self.log_msg("Сбор отчёта поддержки…")

        def worker():
            try:
                path = zc.make_support_bundle()
                self.log_msg(f"Отчёт сохранён: {path}")
                try:
                    os.startfile(os.path.dirname(path))
                except Exception:
                    pass
            except Exception as e:
                self.log_msg(f"[ОШИБКА] отчёт: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def on_open_logs(self):
        try:
            os.makedirs(zc.LOGS, exist_ok=True)
            os.startfile(zc.LOGS)
        except Exception as e:
            self.log_msg(str(e))

    def _on_appearance_change(self, value):
        mode = APPEARANCE.get(value, "dark")
        self.cfg["appearance"] = mode
        zc.save_config(self.cfg)
        ctk.set_appearance_mode(mode)
        self._init_ttk_style()   # перенастроить цвета таблицы под новый режим
        self.log_msg(f"Тема: {value.lower()}.")

    def _on_theme_change(self, value):
        self.cfg["accent_name"] = value
        zc.save_config(self.cfg)
        if messagebox.askyesno("Тема", f"Тема «{value}» применится после перезапуска.\n"
                               "Перезапустить приложение сейчас?"):
            try:
                zc.relaunch_app()
            except Exception as e:
                self.log_msg(f"[ОШИБКА] перезапуск: {e}")
                return
            self.after(300, self._real_quit)

    def on_add_defender_exclusion(self):
        self.log_msg("Добавляю папку в исключения Windows Defender…")

        def worker():
            ok, msg = zc.add_defender_exclusion(zc.BASE)
            self.log_msg(("Defender: " if ok else "[!] Defender: ") + msg)

        threading.Thread(target=worker, daemon=True).start()

    def on_export_settings(self):
        path = filedialog.asksaveasfilename(
            title="Экспорт настроек", defaultextension=".json",
            initialfile="zapret-gui-settings.json",
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            zc.export_settings(path)
            self.log_msg(f"Настройки сохранены: {path}")
        except Exception as e:
            self.log_msg(f"[ОШИБКА] экспорт: {e}")

    def on_import_settings(self):
        path = filedialog.askopenfilename(
            title="Импорт настроек", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            ok, msg = zc.import_settings(path)
            self.log_msg(("Импорт: " if ok else "[ОШИБКА] импорт: ") + msg)
            if ok:
                self.cfg = zc.load_config()
                self.presets = zc.load_presets()
                self.preset_by_name = {p["name"]: p for p in self.presets}
                messagebox.showinfo("Импорт",
                                    "Настройки импортированы. Перезапустите "
                                    "приложение, чтобы применить полностью.")
        except Exception as e:
            self.log_msg(f"[ОШИБКА] импорт: {e}")

    # -- трей ------------------------------------------------------------- #
    def _on_tray_toggle(self):
        self.cfg["minimize_to_tray"] = bool(self.tray_switch.get())
        zc.save_config(self.cfg)

    def _make_tray_image(self, running):
        try:
            base = Image.open(self._asset("icon.png")).convert("RGBA").resize((64, 64))
        except Exception:
            base = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(base)
            d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=ACCENT)
            d.polygon([(34, 11), (19, 38), (30, 38), (27, 53), (45, 25), (33, 25)],
                      fill="#ffffff")
        img = base.copy()
        d = ImageDraw.Draw(img)
        dot = GREEN if running else RED
        d.ellipse([44, 44, 60, 60], fill=dot, outline="#15161c", width=2)
        return img

    def _setup_tray(self):
        if not _TRAY_OK:
            return

        def menu():
            return pystray.Menu(
                pystray.MenuItem("Показать", lambda: self.post(self._tray_show),
                                 default=True),
                pystray.MenuItem(
                    lambda i: "Остановить обход" if zc.winws_running()
                    else "Запустить обход",
                    lambda: self.post(self._tray_toggle_bypass)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Выход", lambda: self.post(self._real_quit)),
            )

        try:
            self.tray = pystray.Icon("ZapretGUI", self._make_tray_image(False),
                                     "Zapret GUI", menu())
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception as e:
            self.tray = None
            self.log_msg(f"[трей] недоступен: {e}")

    def _tray_show(self):
        try:
            self.deiconify()
            self.after(50, self.lift)
            self.focus_force()
        except Exception:
            pass

    def _tray_toggle_bypass(self):
        if zc.winws_running():
            self.on_stop()
        else:
            self.on_start()

    def _on_x(self):
        # закрытие окна: свернуть в трей или выйти
        if self.tray is not None and self.cfg.get("minimize_to_tray", True):
            self.withdraw()
            if not self._tray_hinted:
                self._tray_hinted = True
                try:
                    self.tray.notify("Свёрнуто в трей. Обход продолжает работать. "
                                     "Выход — через меню значка.", "Zapret GUI")
                except Exception:
                    pass
            return
        self._real_quit()

    def on_update_ipset(self):
        self.log_msg("Обновление ipset-all.txt…")

        def worker():
            ok, msg = zc.update_ipset()
            self.log_msg(msg)
            self.post(self.refresh_status)

        threading.Thread(target=worker, daemon=True).start()

    def on_diagnostics(self):
        self.log_msg("=== Диагностика ===")

        def worker():
            for ok, text in zc.run_diagnostics():
                self.log_msg(("  [OK] " if ok else "  [!]  ") + text)
            self.log_msg("=== Диагностика завершена ===")

        threading.Thread(target=worker, daemon=True).start()

    def on_test(self):
        ps1 = os.path.join(zc.UTILS, "test zapret.ps1")
        if not os.path.exists(ps1):
            messagebox.showerror("Zapret", f"Не найден файл теста:\n{ps1}")
            return
        self.log_msg("Запуск теста соединения в окне PowerShell…")
        import subprocess
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-File", ps1])

    # -- обновление приложения -------------------------------------------- #
    def _startup_update_check(self):
        if not zc.get_update_enabled():
            return

        def worker():
            info = zc.check_update()

            def show():
                if info.get("error"):
                    return
                if info.get("available"):
                    self.upd_label.configure(text=f"есть {info['latest']}")
                    self.log_msg(f"[Обновление] доступна версия {info['latest']} — "
                                 "раздел «Управление» → «Проверить».")
                else:
                    self.upd_label.configure(text=f"актуально ({info.get('current','')})")
            self.post(show)

        threading.Thread(target=worker, daemon=True).start()

    def on_check_update(self):
        self.upd_label.configure(text="проверка…")
        self.log_msg("Проверка обновлений приложения…")

        def worker():
            info = zc.check_update()
            self.post(lambda: self._show_update_result(info))

        threading.Thread(target=worker, daemon=True).start()

    def _show_update_result(self, info):
        if info.get("error"):
            self.upd_label.configure(text="ошибка")
            self.log_msg(f"[Обновление] ошибка: {info['error']}")
            return
        if not info.get("available"):
            self.upd_label.configure(text=f"актуально ({info['current']})")
            self.log_msg(f"[Обновление] установлена последняя версия: {info['current']}")
            return
        self.upd_label.configure(text=f"есть {info['latest']}")
        notes = (info.get("notes") or "").strip()
        msg = f"Доступна версия {info['latest']} (у вас {info['current']}).\n\n"
        if notes:
            msg += notes[:600] + "\n\n"
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("Обновление",
                                msg + "Самообновление работает только в собранном приложении.")
            return
        if not info.get("url"):
            messagebox.showwarning("Обновление", msg + "В релизе нет архива (.zip).")
            return
        if messagebox.askyesno("Обновление", msg + "Скачать и установить сейчас?"):
            self._do_update(info["url"], info.get("size", 0))

    def _do_update(self, url, size=0):
        self.log_msg("Скачивание обновления…")
        self.upd_label.configure(text="скачивание…")

        def worker():
            dest = os.path.join(os.environ.get("TEMP", zc.BASE), "ZapretControl_update.zip")
            last = [0]

            def prog(fr):
                pct = int(fr * 100)
                if pct >= last[0] + 10:
                    last[0] = pct
                    self.log_msg(f"  скачано {pct}%")

            try:
                zc.download_update(url, dest, progress_cb=prog, expected_size=size)
                self.log_msg("Загрузка завершена. Установка и перезапуск…")
                zc.apply_update(dest)
                self.post(self._quit_for_update)
            except Exception as e:
                self.log_msg(f"[ОШИБКА] обновление: {e}")
                self.post(lambda: self.upd_label.configure(text="ошибка"))

        threading.Thread(target=worker, daemon=True).start()

    def _quit_for_update(self):
        self.log_msg("Закрываю приложение для применения обновления…")
        self.after(500, self.destroy)

    def show_args(self):
        preset = self._selected_preset()
        if not preset:
            return
        args = zc.build_args_str(preset["args"], zc.get_game_mode())
        if not args:
            messagebox.showerror("Zapret", "Не удалось разобрать аргументы.")
            return
        win = ctk.CTkToplevel(self)
        win.title(f"Аргументы — {preset['name']}")
        win.geometry("760x440")
        box = ctk.CTkTextbox(win, font=("Consolas", 12), wrap="word")
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box.insert("1.0", zc.WINWS + "\n  " + "\n  ".join(args))
        box.configure(state="disabled")

    # -- Telegram-прокси (встроенный) ------------------------------------- #
    def on_tg_start(self):
        self.log_msg(f"Запуск встроенного Telegram-прокси на "
                     f"{zc.TG_DEFAULT_HOST}:{zc.tg_get_port()}…")

        def worker():
            try:
                zc.tg_proxy_start()
                time.sleep(1.3)
                if zc.tg_proxy_running():
                    self.log_msg("Прокси запущен. Нажмите «Открыть в Telegram» "
                                 "или «Скопировать».")
                else:
                    self.log_msg("[ОШИБКА] прокси не запустился: "
                                 + (zc.tg_last_error() or "возможно, порт занят"))
            except Exception as e:
                self.log_msg(f"[ОШИБКА] Telegram-прокси: {e}")
            self.post(self.refresh_status)

        threading.Thread(target=worker, daemon=True).start()

    def on_tg_stop(self):
        self.log_msg("Остановка Telegram-прокси…")

        def worker():
            zc.tg_proxy_stop()
            self.log_msg("Telegram-прокси остановлен.")
            self.post(self.refresh_status)

        threading.Thread(target=worker, daemon=True).start()

    def on_tg_apply_port(self):
        try:
            port = int(self.tg_port_var.get())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showwarning("Прокси", "Порт должен быть числом 1–65535.")
            return
        zc.set_tg_port(port)
        self.tg_link_var.set(zc.tg_proxy_url())
        self.log_msg(f"Порт прокси: {port}. Перезапустите прокси, чтобы применить.")

    def on_tg_regen(self):
        zc.tg_regenerate_secret()
        self.tg_link_var.set(zc.tg_proxy_url())
        self.log_msg("Секрет прокси обновлён. Перезапустите прокси и обновите ссылку в Telegram.")

    def _on_doh_toggle(self):
        on = bool(self.doh_switch.get())
        prov = {"Cloudflare": "cloudflare", "Google": "google"}[self.doh_provider.get()]
        self.log_msg(("Включаю" if on else "Выключаю") + " шифрованный DNS (DoH)…")

        def worker():
            try:
                if on:
                    zc.doh_enable(prov)
                    self.log_msg(f"DoH включён ({prov}): системный DNS переведён на провайдера.")
                else:
                    zc.doh_disable()
                    self.log_msg("DoH выключен: прежний DNS восстановлен.")
            except Exception as e:
                self.log_msg(f"[ОШИБКА] DNS: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_doh_provider_change(self, _value=None):
        # если DoH уже включён — сразу переключить DNS на нового провайдера
        if not self.doh_switch.get():
            return
        prov = {"Cloudflare": "cloudflare", "Google": "google"}[self.doh_provider.get()]
        self.log_msg(f"Смена DNS-провайдера на {prov}…")

        def worker():
            try:
                zc.doh_enable(prov)
                self.log_msg(f"DNS-провайдер изменён: {prov}.")
            except Exception as e:
                self.log_msg(f"[ОШИБКА] DNS: {e}")

        threading.Thread(target=worker, daemon=True).start()


    def on_tg_copy(self):
        link = zc.tg_proxy_url()
        try:
            self.clipboard_clear()
            self.clipboard_append(link)
            self.log_msg("Ссылка прокси скопирована в буфер обмена.")
        except Exception as e:
            self.log_msg(f"[ОШИБКА] копирование: {e}")

    def on_tg_open(self):
        link = zc.tg_proxy_url()
        if not zc.tg_proxy_running():
            self.log_msg("Сначала запустите прокси.")
        try:
            os.startfile(link)
        except Exception:
            try:
                import webbrowser
                webbrowser.open(link)
            except Exception as e:
                self.log_msg(f"[ОШИБКА] открытие ссылки: {e}")

    # -- авто-поиск (двухфазный) ------------------------------------------ #
    def on_auto_start(self):
        if self.auto_running:
            return
        services = [s for s in ("discord", "youtube", "google") if self.svc_vars[s].get()]
        if not services:
            messagebox.showwarning("Авто-поиск", "Выберите хотя бы один сервис.")
            return
        if not self.presets:
            messagebox.showwarning("Авто-поиск", "Не найдено ни одного пресета.")
            return
        self.auto_total_targets = sum(len(zc.AUTO_TARGETS[s]) for s in services)
        self.auto_best = None
        self._auto_full_pass = []
        self.auto_cancel = False
        self.auto_running = True
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.btn_auto_start.configure(state="disabled")
        self.btn_auto_stop.configure(state="normal")
        self.btn_apply_best.configure(state="disabled")
        self.btn_install_best.configure(state="disabled")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="disabled")
        self.auto_bar.set(0)
        threading.Thread(target=self._auto_worker, args=(list(self.presets), services),
                         daemon=True).start()

    def on_auto_stop(self):
        if self.auto_running:
            self.auto_cancel = True
            self.auto_phase_lbl.configure(text="останавливаю…")
            self.log_msg("Авто-поиск: запрошена остановка…")

    def _auto_sleep(self, seconds):
        import time
        end = time.time() + seconds
        while time.time() < end:
            if self.auto_cancel:
                return False
            time.sleep(0.15)
        return not self.auto_cancel

    def _auto_worker(self, presets, services):
        mode = zc.get_game_mode()
        svc_was_running = zc.service_running()
        quick_hosts = [zc.AUTO_QUICK_HOST[s] for s in services]
        full_targets = [(s, h) for s in services for h in zc.AUTO_TARGETS[s]]
        counts = {s: len(zc.AUTO_TARGETS[s]) for s in services}
        MAX_CAND = 6

        try:
            if svc_was_running:
                self.log_msg("Останавливаю службу zapret на время поиска…")
                zc.run_hidden(["net", "stop", zc.SERVICE_NAME])
            if self.proc and self.proc.poll() is None:
                self.log_msg("Текущий обход остановлен на время поиска.")
            zc.kill_winws_only()
            self.proc = None

            self.log_msg("=== Авто-поиск (двухфазный): %d пресетов, %d целей ==="
                         % (len(presets), len(full_targets)))

            # Фаза 1 — отсев
            self.post(lambda: self.auto_phase_lbl.configure(text="Фаза 1: быстрый отсев"))
            phase1 = []
            n = len(presets)
            for idx, preset in enumerate(presets, 1):
                if self.auto_cancel:
                    break
                name = preset["name"]
                self.post(lambda i=idx, nn=n, nm=name:
                          self._auto_prog(i / nn, f"Фаза 1 · {i}/{nn}: {nm}"))
                args = zc.build_args_str(preset["args"], mode)
                if not args:
                    continue
                zc.kill_winws_only()
                try:
                    zc.start_winws_silent(args)
                except Exception as e:
                    self.log_msg(f"[{name}] запуск не удался: {e}")
                    continue
                if not self._auto_sleep(zc.QUICK_WAIT):
                    zc.kill_winws_only()
                    break
                res = zc.check_hosts(quick_hosts, zc.QUICK_TIMEOUT, attempts=1)
                zc.kill_winws_only()
                score = sum(1 for h in quick_hosts if res[h][0])
                lats = [res[h][1] for h in quick_hosts if res[h][0] and res[h][1]]
                avg = sum(lats) / len(lats) if lats else None
                phase1.append((name, score, avg))
                self.log_msg(f"  отсев: {name} — {score}/{len(quick_hosts)}")

            if self.auto_cancel:
                candidates = []
            else:
                full_pass = [p for p in phase1 if p[1] == len(quick_hosts)]
                if full_pass:
                    full_pass.sort(key=lambda x: (x[2] or 9e9))
                    candidates = [p[0] for p in full_pass[:MAX_CAND]]
                else:
                    scored = [p for p in phase1 if p[1] > 0]
                    scored.sort(key=lambda x: (-x[1], x[2] or 9e9))
                    candidates = [p[0] for p in scored[:MAX_CAND]]

            # Фаза 2 — точная проверка
            if candidates:
                self.post(lambda c=len(candidates):
                          self.auto_phase_lbl.configure(text=f"Фаза 2: проверка {c} лучших"))
                self.post(lambda: self.auto_bar.set(0))
                for j, name in enumerate(candidates, 1):
                    if self.auto_cancel:
                        break
                    preset = self.preset_by_name.get(name)
                    if not preset:
                        continue
                    self.post(lambda jj=j, cc=len(candidates), nm=name:
                              self._auto_prog(jj / cc, f"Фаза 2 · {jj}/{cc}: {nm}"))
                    args = zc.build_args_str(preset["args"], mode)
                    zc.kill_winws_only()
                    try:
                        zc.start_winws_silent(args)
                    except Exception:
                        continue
                    if not self._auto_sleep(zc.FULL_WAIT):
                        zc.kill_winws_only()
                        break
                    hosts = [h for _, h in full_targets]
                    res = zc.check_hosts(hosts, zc.FULL_TIMEOUT, attempts=2)
                    zc.kill_winws_only()
                    per = {s: 0 for s in services}
                    total, lat_sum, lat_n = 0, 0.0, 0
                    for s, h in full_targets:
                        ok, lat = res[h]
                        if ok:
                            per[s] += 1
                            total += 1
                            if lat:
                                lat_sum += lat
                                lat_n += 1
                    avg = lat_sum / lat_n if lat_n else None
                    self.post(lambda nm=name, p=dict(per), t=total, a=avg, cn=dict(counts):
                              self._auto_add_row(nm, p, t, a, cn))
            elif not self.auto_cancel:
                self.log_msg("Рабочих пресетов на отсеве не найдено.")
        finally:
            zc.kill_winws_only()
            zc.remove_windivert()
            if svc_was_running:
                self.log_msg("Возвращаю службу zapret…")
                zc.run_hidden(["net", "start", zc.SERVICE_NAME])
            self.post(self._auto_done)

    def _auto_prog(self, frac, text):
        self.auto_bar.set(max(0.0, min(1.0, frac)))
        self.auto_phase_lbl.configure(text=text)

    def _auto_add_row(self, name, per, total, avg_lat, counts):
        def cell(s):
            return f"{per.get(s, 0)}/{counts[s]}" if s in counts else "—"

        ms = "—" if not avg_lat else str(round(avg_lat))
        total_str = f"{total}/{self.auto_total_targets}"
        tag = "good" if total == self.auto_total_targets else ("partial" if total > 0 else "bad")
        self.tree.insert("", "end",
                         values=(name, cell("discord"), cell("youtube"), cell("google"),
                                 total_str, ms), tags=(tag,))
        if total > 0:
            cur = (total, -(avg_lat if avg_lat else 1e9))
            best = (self.auto_best[1], -(self.auto_best[2] or 1e9)) if self.auto_best else None
            if best is None or cur > best:
                self.auto_best = (name, total, avg_lat)
            self.btn_apply_best.configure(state="normal")
            self.btn_install_best.configure(state="normal")
        if total == self.auto_total_targets:   # полностью рабочая — в пул запаса
            self._auto_full_pass.append((name, avg_lat if avg_lat else 1e9))

    def _auto_done(self):
        self.auto_running = False
        self.btn_auto_start.configure(state="normal")
        self.btn_auto_stop.configure(state="disabled")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="normal")
        self.auto_bar.set(1.0)
        self.auto_phase_lbl.configure(text="готово")
        # пул запасных рабочих стратегий (для авто-восстановления), лучшие первыми
        pool = [n for n, _ in sorted(self._auto_full_pass, key=lambda x: x[1])]
        self.cfg["recovery_pool"] = pool
        if pool:
            self.cfg["auto_recovery"] = True   # есть запас — включаем восстановление
        zc.save_config(self.cfg)
        if pool:
            try:
                self.recovery_switch.select()
            except Exception:
                pass
            self.log_msg(f"В запас для авто-восстановления: {len(pool)} стратегий. "
                         "Авто-восстановление включено.")
        if self.auto_best:
            name, total, avg = self.auto_best
            self.log_msg("=== Лучшая стратегия: %s (%d/%d, ~%s мс) ==="
                         % (name, total, self.auto_total_targets,
                            round(avg) if avg else "?"))
            for item in self.tree.get_children():
                if self.tree.item(item, "values")[0] == name:
                    self.tree.item(item, tags=("best",))
                    self.tree.see(item)
                    break
        else:
            self.log_msg("=== Авто-поиск завершён: рабочих стратегий не найдено ===")
        self.refresh_status()

    def on_apply_best(self):
        if not self.auto_best:
            return
        name = self.auto_best[0]
        self.strategy_var.set(name)
        self._on_strategy_pick()
        self._show_page("control")
        self.log_msg(f"Выбран пресет «{name}». Нажмите «Запустить».")

    def on_install_best(self):
        if not self.auto_best:
            return
        self.strategy_var.set(self.auto_best[0])
        self._show_page("control")
        self.on_install_service()

    # -------------------------------------------------------------------- #
    def _real_quit(self):
        if self.proc and self.proc.poll() is None:
            if messagebox.askyesno("Выход", "Обход запущен. Остановить при выходе?"):
                self.active_args = None
                try:
                    self.proc.terminate()
                except Exception:
                    pass
                zc.kill_winws_only()
                zc.remove_windivert()
        self._closing = True
        try:
            zc.tg_proxy_stop()
        except Exception:
            pass
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        if self._logf:
            try:
                self._logf.close()
            except Exception:
                pass
        self.destroy()


_SINGLETON = None


def main():
    if not zc.is_admin():
        zc.relaunch_as_admin()
        return
    # single-instance: не запускать вторую копию
    global _SINGLETON
    _SINGLETON = zc.acquire_single_instance()
    if _SINGLETON is None:
        try:
            ctypes.windll.user32.MessageBoxW(
                0, "Zapret GUI уже запущен.", "Zapret GUI", 0x40)
        except Exception:
            pass
        return
    # развернуть и проверить встроенные файлы (только для собранного .exe)
    copied = []
    try:
        copied = zc.ensure_runtime()
        zc.verify_runtime()
    except Exception:
        pass
    # в режиме разработки создать presets.json из .bat, если его ещё нет
    try:
        zc.generate_presets_json()
    except Exception:
        pass
    # откатить прежний (нерабочий) Xbox-фикс: убрать из hosts и списков
    try:
        zc.cleanup_xbox_legacy()
    except Exception:
        pass
    try:
        os.chdir(zc.BASE)
    except Exception:
        pass
    app = ZapretApp()
    if copied:
        app.log_msg(f"Развёрнуто встроенных файлов: {len(copied)} (папка: {zc.BASE})")
    app.mainloop()


if __name__ == "__main__":
    main()
