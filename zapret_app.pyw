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

from tkinter import ttk, messagebox, filedialog

import customtkinter as ctk

import zapret_core as zc
from zapret_runtime import RuntimeController, Superseded
from zapret_search import run_search
from types import GeneratorType

try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_OK = True
except Exception:
    _TRAY_OK = False


ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# Поверхности и границы в духе Windows 11; пары (светлая, тёмная).
WIN_BG = ("#f3f3f3", "#202020")
SIDEBAR_BG = ("#eeeeee", "#191919")
CARD_BG = ("#ffffff", "#2b2b2b")
CARD_HOVER = ("#f5f5f5", "#333333")
BTN_HOVER = ("#eaeaea", "#3b3b3b")
BORDER = ("#e5e5e5", "#383838")
SWITCH_OFF = ("#8a8a8a", "#646464")
SWITCH_KNOB = ("#ffffff", "#ffffff")
SWITCH_KNOB_HOVER = ("#f5f5f5", "#eeeeee")
SWITCH_BORDER = ("#8a8a8a", "#646464")
FIELD_BG = ("#fafafa", "#242424")
LOG_BG = ("#ffffff", "#242424")
LOG_FG = ("#252525", "#ededed")
TEXT = ("#202020", "#f5f5f5")
MUTED = ("#626262", "#b4b4b4")
MENU_BTN_LIGHT = "#f0f0f0"
MENU_BTN_HOVER_LIGHT = "#e6e6e6"

ON_ACCENT = "#ffffff"                  # текст/иконки поверх акцентной заливки
ACCENT = "#0e7c75"                     # текущий акцент (для активного режима)
ACCENT_HOVER = "#0b645e"
SEG_SEL = "#9ad6cf"                    # фон выбранного сегмента (для активного режима)
SEG_SEL_HOVER = "#8accc4"

# Темы оформления: для КАЖДОЙ — свой акцент под светлую и под тёмную тему.
# На ярком белом фоне нужны насыщеннее/темнее (чтобы белый текст читался и цвет
# не выцветал), на тёмном — светлее/ярче. dark-варианты = прежние удачные.
# dark-варианты = прежние (не трогаем — в тёмной теме они удачные),
# light-варианты подобраны заново под яркий белый фон.
#            (светлая: basic, hover)      (тёмная: basic, hover — как было)
THEMES = {
    "Сигнальная": (("#0f766e", "#0c5f58"), ("#0e7c75", "#0b645e")),
    "Синяя":      (("#005fb8", "#00549f"), ("#005fb8", "#006acb")),
    "Индиго":     (("#4f46e5", "#4338ca"), ("#5a5cf0", "#4a4cdb")),
    "Зелёная":    (("#15803d", "#166534"), ("#1f9e57", "#1a8849")),
    "Янтарная":   (("#b45309", "#92400e"), ("#c98a14", "#b0780f")),
    "Розовая":    (("#be185d", "#9d174d"), ("#d94f8f", "#c2417e")),
}
APPEARANCE = {"Тёмная": "dark", "Светлая": "light", "Системная": "system"}

GREEN = ("#107c10", "#6ccb5f")
RED = ("#c42b1c", "#ff99a4")
YELLOW = ("#8a5200", "#fce100")
FONT = "Segoe UI"
FONT_DISPLAY = "Segoe UI Semibold"     # заголовки/секции — характерный вес
FONT_MONO = "Consolas"                 # данные/журнал


def _pick(c):
    """Вернуть одиночный цвет из пары (светлая, тёмная) по текущему режиму ctk."""
    if isinstance(c, (tuple, list)):
        return c[1] if ctk.get_appearance_mode() == "Dark" else c[0]
    return c


def _lighten(hexc, amt):
    """Смешать цвет с белым на долю amt (0..1) — для светлого оттенка акцента."""
    h = hexc.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r = int(r + (255 - r) * amt)
    g = int(g + (255 - g) * amt)
    b = int(b + (255 - b) * amt)
    return f"#{r:02x}{g:02x}{b:02x}"


def _apply_accent(name):
    """Обновить глобальные ACCENT/ACCENT_HOVER/SEG_SEL под ТЕКУЩИЙ режим
    (светлый/тёмный): у каждой темы свой акцент под каждый режим. Значения —
    одиночные цвета для активного режима; интерфейс пересобирается при смене
    режима/акцента, поэтому пересчёт здесь всегда актуален."""
    global ACCENT, ACCENT_HOVER, SEG_SEL, SEG_SEL_HOVER
    light_pair, dark_pair = THEMES.get(name, THEMES["Сигнальная"])
    light_mode = ctk.get_appearance_mode() == "Light"
    ACCENT, ACCENT_HOVER = light_pair if light_mode else dark_pair
    if light_mode:
        # выбранный сегмент — бледный тон акцента (тёмный текст на нём читается)
        SEG_SEL = _lighten(ACCENT, 0.74)
        SEG_SEL_HOVER = _lighten(ACCENT, 0.64)
    else:
        SEG_SEL, SEG_SEL_HOVER = ACCENT, ACCENT_HOVER


APP_NAME = "Zapret GUI"


class ZapretApp(ctk.CTk):
    def __init__(self, autostart=False):
        super().__init__(fg_color=WIN_BG)
        self.autostart_launch = autostart
        self.title(f"{APP_NAME} — обход Discord, YouTube, Telegram")
        self.geometry("1120x780")
        self.minsize(920, 620)
        try:
            self.iconbitmap(self._asset("icon.ico"))
            self.after(300, lambda: self.iconbitmap(self._asset("icon.ico")))
        except Exception:
            pass

        self.cfg = zc.load_config()
        # новым пользователям — простой режим по умолчанию (мастер первого
        # запуска предложит выбрать; опытные при обновлении остаются в полном)
        if "ui_mode" not in self.cfg and not self.cfg.get("first_run_done"):
            self.cfg["ui_mode"] = "simple"
        # оформление — задать режим (тёмная/светлая) и акцент до построения UI
        ctk.set_appearance_mode(self.cfg.get("appearance", "light"))
        _apply_accent(self.cfg.get("accent_name", "Синяя"))
        self.presets = zc.load_presets()
        self.preset_by_name = {p["name"]: p for p in self.presets}
        self.proc = None
        self.runtime = RuntimeController()
        self._initial_runtime_token = self.runtime.token
        self._status_snapshot = None
        self._log_dirty = False
        self._page_jobs = {}
        self._start_busy = False
        self._recovery_busy = False
        self._health_snapshot = {}
        self._health_state = "Не проверено"
        self.log_queue = queue.Queue()
        self.ui_queue = queue.Queue()
        self._status_busy = False
        self._stop_busy = False          # идёт асинхронная остановка обхода
        self._log_lines = []             # все строки журнала (для фильтра/копии)
        self._health_checked_at = None
        self._health_summary = "Проверка не запускалась"

        self.auto_running = False
        self.auto_cancel = False
        self.auto_best = None
        self.auto_total_targets = 0
        self._auto_autoapply = False     # авто-применить лучшую (запуск из watchdog)
        self._health_busy = False

        # Фаза 3: авто-восстановление / логи / завершение
        self._closing = False
        self.tray = None
        self._tray_hinted = False
        self.active_args = None          # аргументы текущего запуска (для watchdog)
        self.active_preset_name = None   # имя текущего пресета
        self._auto_full_pass = []        # рабочие стратегии последнего поиска
        self._recovery_failed = set()    # не возвращаться к стратегии, уже упавшей в этом цикле
        try:
            self._logf = open(zc.current_log_path(), "a", encoding="utf-8")
            self._logf.write(f"\n===== Запуск {time.strftime('%Y-%m-%d %H:%M:%S')} "
                             f"(v{zc.APP_VERSION}) =====\n")
            self._logf.flush()
        except Exception:
            self._logf = None

        self.pages = {}
        self.nav_buttons = {}
        self.nav_badges = {}

        self._init_ttk_style()
        self._build_layout()
        self._show_page("control")

        self._poll_ui()
        self.refresh_status()
        self.after(3000, self._auto_refresh)
        self.after(1500, self._startup_update_check)
        self.after(2500, self._health_auto)
        self.after(3500, self._proxy_stats_auto)
        self.after(4000, self._startup_lists_check)
        self.after(6000, self._startup_diag_badge)
        self.after(900, self._startup_service_restore)
        self.after(2200, self._repair_autostart_bg)
        self._bg(self._watchdog_loop)
        if self.autostart_launch:
            # запуск при входе в систему: поднять обход и прокси, свернуться в трей
            self.after(800, self._autostart_full)
        elif self.cfg.get("autostart_bypass"):
            self.after(1400, self._autostart_bypass)
        if not self.autostart_launch:
            self.after(900, self._first_run_wizard)
        self._setup_tray()
        self.protocol("WM_DELETE_WINDOW", self._on_x)

    def _asset(self, name):
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, "assets", name)

    def _relaunch_as_admin(self):
        if zc.relaunch_as_admin() > 32:
            self._closing = True
            self.destroy()
        else:
            messagebox.showwarning("Zapret", "Windows не разрешила запуск от администратора.")

    def _init_ttk_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Zap.Treeview", background=_pick(CARD_BG),
                        fieldbackground=_pick(CARD_BG), foreground=_pick(TEXT),
                        rowheight=30, borderwidth=0, font=(FONT, 11))
        style.configure("Zap.Treeview.Heading", background=_pick(FIELD_BG),
                        foreground=_pick(MUTED), borderwidth=0, relief="flat",
                        font=(FONT_DISPLAY, 10))
        style.map("Zap.Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", ON_ACCENT)])
        self._apply_tree_tags()

    # -- каркас ----------------------------------------------------------- #
    def _build_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        side = ctk.CTkFrame(self, width=204, corner_radius=0, fg_color=SIDEBAR_BG)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)
        self._sidebar = side

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
        ctk.CTkLabel(ttl, text="Zapret GUI", font=(FONT_DISPLAY, 18),
                     text_color=TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(ttl, text="Контроль подключения", font=(FONT, 11),
                     text_color=ACCENT, anchor="w").pack(anchor="w")

        simple = self._simple_mode()
        nav = ([("control", "Главная"), ("sites", "Свои сайты"),
                ("settings", "Настройки"), ("log", "Журнал")] if simple else
               [("control", "Главная"), ("sites", "Свои сайты"),
                ("auto", "Авто-поиск"), ("tgws", "Telegram"),
                ("diag", "Диагностика"), ("settings", "Настройки"),
                ("log", "Журнал")])
        for key, label in nav:
            row = ctk.CTkFrame(side, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=2)
            # акцентная полоска-индикатор активного пункта (тема-независимая)
            bar = ctk.CTkFrame(row, width=3, height=22, corner_radius=2,
                               fg_color="transparent")
            bar.pack(side="left", pady=10)
            badge = ctk.CTkLabel(row, text="", font=(FONT, 15), text_color=RED, width=14)
            badge.pack(side="right", padx=(0, 10))
            self.nav_badges[key] = badge
            b = ctk.CTkButton(row, text=label, anchor="w", height=42, corner_radius=9,
                              fg_color="transparent", hover_color=CARD_HOVER,
                              text_color=TEXT, font=(FONT, 14),
                              command=lambda k=key: self._show_page(k))
            b.pack(side="left", fill="x", expand=True, padx=(7, 0))
            self.nav_buttons[key] = (b, bar)

        self.side_status = ctk.CTkLabel(side, text="●  проверка…", font=(FONT, 12),
                                        text_color=MUTED, anchor="w")
        self.side_status.pack(side="bottom", fill="x", padx=16, pady=(8, 6))
        admin = zc.is_admin()
        ctk.CTkLabel(side, text=f"v{zc.APP_VERSION} · " + ("права администратора есть" if admin
                                                               else "нужны права администратора"),
                     font=(FONT, 10),
                     text_color=MUTED, anchor="w").pack(side="bottom", fill="x",
                                                         padx=16, pady=(0, 2))
        if not admin:
            self._btn(side, "Запустить от администратора", self._relaunch_as_admin,
                      accent=True, width=176).pack(side="bottom", padx=14, pady=(4, 2))
        # переключатель режима интерфейса — всегда на виду
        self.mode_seg = self._seg(side, ["Простой", "Полный"],
                                  command=self._on_mode_change)
        self.mode_seg.set("Простой" if simple else "Полный")
        self.mode_seg.pack(side="bottom", fill="x", padx=12, pady=(6, 8))

        self.container = ctk.CTkFrame(self, fg_color=WIN_BG, corner_radius=0)
        self.container.grid(row=0, column=1, sticky="nsew")
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self._page_builders = {
            "control": (self._build_simple_page if simple
                        else self._build_control_page),
            "sites": self._build_sites_page,
            "auto": self._build_auto_page, "tgws": self._build_tgws_page,
            "diag": self._build_diag_page,
            "settings": (self._build_simple_settings_page if simple
                         else self._build_settings_page),
            "log": self._build_log_page,
        }
        # Only the visible dashboard is constructed before the first frame.
        self.pages["control"] = self._page_builders["control"]()

    def _ensure_page(self, key):
        if key not in self.pages:
            built = self._page_builders[key]()
            if isinstance(built, GeneratorType):
                page = next(built)
                self.pages[key] = page
                self._page_jobs[key] = built
                self.after(1, lambda: self._advance_page(key, page, built))
            else:
                self.pages[key] = built
                if key == "log":
                    self._render_log()
            if self._status_snapshot is not None:
                self._apply_status(*self._status_snapshot)
        return self.pages[key]

    def _advance_page(self, key, page, builder):
        if self._closing or self.pages.get(key) is not page:
            builder.close()
            return
        try:
            next(builder)
        except StopIteration:
            self._page_jobs.pop(key, None)
            if self._status_snapshot is not None:
                self._apply_status(*self._status_snapshot)
            return
        self.after(1, lambda: self._advance_page(key, page, builder))

    def _show_page(self, key):
        self._current_page = key
        self._ensure_page(key)
        for page in self.pages.values():
            page.grid_remove()
        page = self.pages[key]
        page.grid(row=0, column=0, sticky="nsew")
        # При переходе в раздел начинаем с заголовка, а не с прежней позиции.
        canvas = getattr(page, "_parent_canvas", None)
        if canvas is not None:
            canvas.yview_moveto(0)
        for k, (b, bar) in self.nav_buttons.items():
            active = (k == key)
            b.configure(fg_color=CARD_BG if active else "transparent",
                        text_color=ACCENT if active else TEXT)
            bar.configure(fg_color=ACCENT if active else "transparent")
        if key == "log" and self._log_dirty:
            self._render_log()
        if key == "diag" and not getattr(self, "_diag_loaded", False):
            self._diag_loaded = True
            self.on_diag_run()

    # -- конструкторы ----------------------------------------------------- #
    def _page(self):
        return ctk.CTkScrollableFrame(self.container, fg_color=WIN_BG,
                                      scrollbar_button_color=CARD_BG)

    def _title(self, parent, text, subtitle=None):
        ctk.CTkLabel(parent, text=text, font=(FONT_DISPLAY, 25), text_color=TEXT,
                     anchor="w").pack(fill="x", padx=16, pady=(12, 2))
        if subtitle:
            ctk.CTkLabel(parent, text=subtitle, font=(FONT, 12), text_color=MUTED,
                         anchor="w", justify="left", wraplength=760).pack(
                fill="x", padx=16, pady=(0, 10))

    def _section(self, parent, text):
        ctk.CTkLabel(parent, text=text, font=(FONT_DISPLAY, 13),
                     text_color=MUTED, anchor="w").pack(
            fill="x", padx=20, pady=(22, 7))

    def _card(self, parent):
        f = ctk.CTkFrame(parent, corner_radius=8, fg_color=CARD_BG,
                         border_width=1, border_color=BORDER)
        f.pack(fill="x", padx=14, pady=5)
        f.grid_columnconfigure(1, weight=1)
        return f

    def _card_row(self, parent, icon, title, subtitle):
        f = self._card(parent)
        f.grid_columnconfigure(1, weight=1, minsize=140)
        heading = ctk.CTkLabel(f, text=title, font=(FONT_DISPLAY, 14),
                               text_color=TEXT, anchor="w", justify="left", wraplength=240)
        heading.grid(row=0, column=1, sticky="sw", padx=(20, 16), pady=(18, 4))
        detail = ctk.CTkLabel(f, text=subtitle, font=(FONT, 12), text_color=MUTED,
                              anchor="w", justify="left", wraplength=240)
        detail.grid(row=1, column=1, sticky="nw", padx=(20, 16), pady=(0, 18))
        def resize(event):
            width = max(100, f.grid_bbox(1, 0)[2] - 36)
            heading.configure(wraplength=width)
            detail.configure(wraplength=width)
        f.bind("<Configure>", resize)
        return f

    def _btn(self, parent, text, command, accent=False, width=150):
        return ctk.CTkButton(
            parent, text=text, command=command, width=width, height=36,
            corner_radius=6, font=(FONT, 13),
            border_width=0 if accent else 1, border_color=BORDER,
            fg_color=ACCENT if accent else CARD_HOVER,
            hover_color=ACCENT_HOVER if accent else BTN_HOVER,
            text_color="#ffffff" if accent else TEXT)

    def _switch(self, parent, command=None):
        return ctk.CTkSwitch(parent, text="", command=command,
                             progress_color=ACCENT, fg_color=SWITCH_OFF,
                             button_color=SWITCH_KNOB,
                             button_hover_color=SWITCH_KNOB_HOVER,
                             border_width=2, border_color=SWITCH_BORDER)

    def _cfg_switch(self, parent, key, default=False, on_msg=None, off_msg=None):
        """Тумблер, привязанный к булеву ключу конфига: сам сохраняет значение
        и (опционально) пишет в журнал. Начальное положение — из конфига."""
        sw = self._switch(parent)

        def toggle():
            val = bool(sw.get())
            self.cfg[key] = val
            zc.update_config({key: self.cfg[key]})
            if on_msg or off_msg:
                self.log_msg(on_msg if val else off_msg)

        sw.configure(command=toggle)
        if self.cfg.get(key, default):
            sw.select()
        return sw

    def _seg(self, parent, values, command=None, variable=None):
        return ctk.CTkSegmentedButton(
            parent, values=values, command=command, variable=variable,
            font=(FONT, 12), text_color=TEXT, selected_color=SEG_SEL,
            selected_hover_color=SEG_SEL_HOVER, fg_color=FIELD_BG,
            unselected_color=FIELD_BG, unselected_hover_color=BTN_HOVER)

    def _menu(self, parent, values, variable, command, width=160):
        # button_color: (светлая=нейтральный серый, тёмная=акцент). Стрелка
        # рисуется цветом text_color=TEXT — на нейтральном сером она чёткая, а на
        # синей акцентной кнопке в светлой теме выглядела «сломанной».
        return ctk.CTkOptionMenu(
            parent, values=values, variable=variable, command=command,
            width=width, height=36, font=(FONT, 13), corner_radius=8,
            fg_color=FIELD_BG, text_color=TEXT, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT,
            button_color=(MENU_BTN_LIGHT, ACCENT),
            button_hover_color=(MENU_BTN_HOVER_LIGHT, ACCENT_HOVER))

    def _bg(self, fn):
        """Запустить функцию в фоновом daemon-потоке (обёртка для читаемости)."""
        threading.Thread(target=fn, daemon=True).start()

    # keycode -> виртуальное событие (позиции клавиш US QWERTY, от раскладки
    # не зависят): 67=C, 86=V, 88=X. A(65) обрабатывается отдельно.
    _CLIP_KEYS = {67: "<<Copy>>", 86: "<<Paste>>", 88: "<<Cut>>"}

    def _clipboard_on_key(self, e):
        """Обработчик Ctrl+C/V/X/A по keycode. На нелатинской раскладке (русской
        и др.) Tk не ловит их по символу — физическая клавиша шлёт кириллицу,
        а keycode остаётся тем же. -> 'break', если событие обработано."""
        if not (e.state & 0x4):              # нужен Control
            return None
        w, kc = e.widget, e.keycode
        ev = self._CLIP_KEYS.get(kc)
        if ev:
            w.event_generate(ev)
            return "break"
        if kc == 65:                         # A — выделить всё
            try:
                w.tag_add("sel", "1.0", "end")            # Text/Textbox
            except Exception:
                try:
                    w.select_range(0, "end")              # Entry
                except Exception:
                    pass
            return "break"
        return None

    def _enable_clipboard(self, widget):
        """Включить копирование/вставку/вырезание/выделение по keycode для поля
        ввода — чтобы работали и на русской (любой нелатинской) раскладке."""
        try:
            widget.bind("<Key>", self._clipboard_on_key)
        except Exception:
            pass

    def _cfgw(self, attr, **kw):
        """Настроить виджет по имени атрибута, если он существует и жив.
        Нужно, потому что в простом режиме часть страниц (и их виджетов)
        не строится, а фоновые обновления статуса общие для обоих режимов."""
        w = getattr(self, attr, None)
        if w is None:
            return
        try:
            w.configure(**kw)
        except Exception:
            pass

    # -- общие блоки страниц ------------------------------------------------ #
    def _init_strategy_var(self):
        """Создать strategy_var с последним выбранным пресетом (или general).
        Нужен обоим режимам: в простом выпадайки нет, но запуск читает его."""
        names = [pr["name"] for pr in self.presets]
        self.strategy_var = ctk.StringVar()
        last = self.cfg.get("strategy")
        if last in names:
            self.strategy_var.set(last)
        elif "general" in names:
            self.strategy_var.set("general")
        elif names:
            self.strategy_var.set(names[0])
        return names

    def _build_dashboard(self, p, big=False):
        card = self._card(p)
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=0)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 20))
        top.grid_columnconfigure(1, weight=1)
        self.ctl_dot = ctk.CTkLabel(top, text="●", width=48, height=48,
                                    corner_radius=12, fg_color=FIELD_BG,
                                    font=(FONT, 26), text_color=MUTED)
        self.ctl_dot.grid(row=0, column=0, rowspan=2, padx=(0, 16))
        self.ctl_status_title = ctk.CTkLabel(top, text="Проверка подключения…",
                                             font=(FONT_DISPLAY, 24), text_color=TEXT,
                                             anchor="w")
        self.ctl_status_title.grid(row=0, column=1, sticky="w")
        self.ctl_status_sub = ctk.CTkLabel(top, text="", font=(FONT, 12),
                                           text_color=MUTED, anchor="w", justify="left",
                                           wraplength=500)
        self.ctl_status_sub.grid(row=1, column=1, sticky="w", pady=(5, 0))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 24))
        self.btn_start = self._btn(actions, "Запустить обход", self.on_start,
                                   accent=True, width=180)
        self.btn_start.configure(height=42)
        self.btn_start.pack(side="left")
        self.btn_stop = self._btn(actions, "Остановить", self.on_stop, width=130)
        self.btn_stop.configure(height=42)
        self.btn_stop.pack(side="left", padx=10)
        self.health_summary_lbl = ctk.CTkLabel(actions, text=self._health_summary,
                                                font=(FONT, 11), text_color=MUTED, anchor="e")
        self.health_summary_lbl.pack(side="right")
        self.btn_health_check = self._btn(actions, "Проверить связь", self.on_health_check, width=140)
        self.btn_health_check.pack(side="right", padx=(0, 12))
        ctk.CTkFrame(card, height=1, fg_color=BORDER).grid(row=2, column=0, sticky="ew")
        health = ctk.CTkFrame(card, fg_color="transparent")
        health.grid(row=3, column=0, sticky="ew", padx=16, pady=16)
        health.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="health")
        self.health_widgets = {}
        for col, (key, label) in enumerate((("discord", "Discord"), ("youtube", "YouTube"),
                                             ("google", "Google"), ("telegram", "Telegram"))):
            cell = ctk.CTkFrame(health, fg_color=FIELD_BG, corner_radius=8)
            cell.grid(row=0, column=col, sticky="nsew", padx=4)
            ctk.CTkLabel(cell, text=label, font=(FONT, 12), text_color=MUTED,
                         anchor="w").pack(fill="x", padx=12, pady=(10, 2))
            values = ctk.CTkFrame(cell, fg_color="transparent")
            values.pack(fill="x", padx=12, pady=(0, 10))
            dot = ctk.CTkLabel(values, text="●", font=(FONT, 12), width=14, text_color=MUTED)
            dot.pack(side="left", padx=(0, 5))
            value = ctk.CTkLabel(values, text="Не проверено", font=(FONT_DISPLAY, 13), text_color=TEXT)
            value.pack(side="left")
            if key == "telegram":
                self.dash_proxy_dot, self.dash_proxy_lbl = dot, value
            else:
                self.health_widgets[key] = (dot, value, label)
        self._render_health()
        ctk.CTkLabel(card, text="Задержка TLS-соединения с сайтом; не задержка голосового чата.",
                     font=(FONT, 11), text_color=MUTED).grid(row=4, column=0, sticky="w", padx=24, pady=(0, 12))

    def _add_games_excl_card(self, parent):
        """Карточка «Не трогать Steam / Dota 2» с тумблером (общая для полной
        страницы «Свои сайты» и простого режима)."""
        c = self._card_row(parent, "🎮", "Не трогать Steam / Dota 2",
                           "Включите, если при обходе в Dota 2 не грузятся гайды/"
                           "сборки/гильдия (контент Steam идёт через те же сети, "
                           "что и обход). Касается и других игр Steam.")
        self.games_excl_switch = self._switch(c, self._on_games_excl_toggle)
        self.games_excl_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20),
                                    pady=12, sticky="e")
        if zc.game_exclusions_present():
            self.games_excl_switch.select()

    def _add_sites_editor(self, parent, height=300):
        """Редактор своих доменов: поле + подсказка + кнопки + счётчик (общий
        для полной страницы и простого режима)."""
        c = self._card(parent)
        self.sites_box = ctk.CTkTextbox(c, height=height, font=("Consolas", 13),
                                        fg_color=LOG_BG, text_color=LOG_FG,
                                        border_width=0, wrap="none")
        self.sites_box.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        self._enable_clipboard(self.sites_box)
        ctk.CTkLabel(c, text="По одному сайту в строке (например, rutracker.org). "
                     "Можно вставлять и ссылки целиком — лишнее уберётся, www. и "
                     "дубли отбросятся.", font=(FONT, 11), text_color=MUTED,
                     anchor="w", justify="left", wraplength=720).pack(
            fill="x", padx=14, pady=(0, 8))

        c = self._card(parent)
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=0, columnspan=3, padx=12, pady=12, sticky="w")
        self._btn(box, "💾  Сохранить и применить", self.on_sites_save,
                  accent=True, width=220).pack(side="left", padx=4)
        self._btn(box, "Сбросить изменения", self._sites_load, width=170).pack(
            side="left", padx=4)
        self.sites_count = ctk.CTkLabel(box, text="", font=(FONT, 12),
                                        text_color=MUTED)
        self.sites_count.pack(side="left", padx=14)
        self._sites_load()

    # -- страница: Главная (простой режим) --------------------------------- #
    def _build_simple_page(self):
        p = self._page()
        self._title(p, "Главная", "Подключение, доступность сервисов и быстрые действия.")
        self._init_strategy_var()
        self._build_dashboard(p, big=True)
        self._section(p, "Быстрые действия")
        c = self._card(p)
        c.grid_columnconfigure(0, weight=1)
        c.grid_columnconfigure(1, weight=0)
        ctk.CTkLabel(c, text="Telegram", font=(FONT_DISPLAY, 17), text_color=TEXT,
                     anchor="w").grid(row=0, column=0, sticky="w", padx=24, pady=(20, 4))
        ctk.CTkLabel(c, text="Подключите встроенный прокси для сообщений, фото и видео.",
                     font=(FONT, 12), text_color=MUTED, anchor="w", justify="left",
                     wraplength=520).grid(row=1, column=0, sticky="w", padx=24)
        actions = ctk.CTkFrame(c, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="w", padx=24, pady=(16, 20))
        self._btn(actions, "Открыть прокси в Telegram", self.on_tg_open, accent=True,
                  width=190).pack(side="left")
        self._btn(actions, "Скопировать ссылку", self.on_tg_copy, width=180).pack(side="left", padx=10)
        c = self._card(p)
        c.grid_columnconfigure(0, weight=1)
        c.grid_columnconfigure(1, weight=0)
        ctk.CTkLabel(c, text="Не получается подключиться?", font=(FONT_DISPLAY, 17),
                     text_color=TEXT, anchor="w").grid(row=0, column=0, sticky="w",
                                                      padx=24, pady=(20, 4))
        ctk.CTkLabel(c, text="Авто-поиск проверит стратегии и включит лучшую из найденных.",
                     font=(FONT, 12), text_color=MUTED, anchor="w", justify="left",
                     wraplength=520).grid(row=1, column=0, sticky="w", padx=24)
        actions = ctk.CTkFrame(c, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=24, pady=(16, 20))
        self.simple_fix_btn = self._btn(actions, "Подобрать настройку", self.on_simple_fix, width=190)
        self.simple_fix_btn.pack(side="left")
        self._btn(actions, "Отчёт для поддержки", self.on_support_bundle, width=180).pack(side="left", padx=10)
        self.simple_fix_lbl = ctk.CTkLabel(c, text="", font=(FONT, 12), text_color=MUTED,
                                          anchor="w", wraplength=520)
        self.simple_fix_lbl.grid(row=3, column=0, sticky="w", padx=24, pady=(0, 8))
        return p

    def _build_simple_settings_page(self):
        p = self._page()
        self._title(p, "Настройки",
                    "Оформление, запуск с Windows и обновления. Больше параметров — "
                    "в полном режиме (переключатель внизу слева).")

        self._section(p, "Оформление")
        c = self._card_row(p, "🌗", "Тема", "Тёмная / светлая / системная")
        self.appearance_var = ctk.StringVar(
            value={v: k for k, v in APPEARANCE.items()}.get(
                self.cfg.get("appearance", "light"), "Тёмная"))
        self._seg(c, list(APPEARANCE.keys()), command=self._on_appearance_change,
                  variable=self.appearance_var).grid(
            row=0, column=2, rowspan=2, padx=14, pady=12)

        c = self._card_row(p, "🎨", "Акцентный цвет", "Цвет кнопок и выделения")
        self.theme_var = ctk.StringVar(
            value=self.cfg.get("accent_name") if self.cfg.get("accent_name") in THEMES
            else "Синяя")
        self._menu(c, list(THEMES.keys()), self.theme_var, self._on_theme_change).grid(
            row=0, column=2, rowspan=2, padx=14, pady=12)

        self._section(p, "Запуск с Windows")
        c = self._card_row(p, "🚀", "Запускаться вместе с Windows",
                           "Приложение, обход и Telegram-прокси включатся сами "
                           "при входе в систему")
        self.full_autostart_switch = self._switch(c, self._on_full_autostart_toggle)
        self.full_autostart_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20),
                                        pady=12, sticky="e")
        self._sync_autostart_switch()

        self._section(p, "Обновления")
        c = self._card_row(p, "⬆", f"Версия {zc.APP_VERSION}",
                           "Проверить и установить новую версию с GitHub")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        self.upd_label = ctk.CTkLabel(box, text="", font=(FONT, 11), text_color=MUTED)
        self.upd_label.pack(side="left", padx=(0, 8))
        self._btn(box, "Проверить", self.on_check_update, accent=True,
                  width=120).pack(side="left", padx=4)

        self._section(p, "Антивирус")
        c = self._card_row(p, "🛡", "Windows Defender",
                           "Добавить папку в исключения — меньше ложных срабатываний AV")
        self._btn(c, "Добавить в исключения", self.on_add_defender_exclusion,
                  accent=True, width=200).grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        return p

    def on_simple_fix(self):
        """«Подобрать и включить» простого режима: авто-поиск с автоприменением
        лучшей стратегии; страница поиска при этом остаётся скрытой."""
        if self.auto_running:
            return
        self._auto_autoapply = True
        self._ensure_page("auto")
        self._cfgw("simple_fix_btn", state="disabled")
        self._cfgw("simple_fix_lbl", text="подбираю…")
        self.on_auto_start()
        if not self.auto_running:        # поиск не стартовал (нет пресетов и т.п.)
            self._cfgw("simple_fix_btn", state="normal")
            self._cfgw("simple_fix_lbl", text="")

    # -- режим интерфейса: простой / полный --------------------------------- #
    def _simple_mode(self):
        return self.cfg.get("ui_mode", "advanced") == "simple"

    def _on_mode_change(self, value):
        self._set_ui_mode("simple" if value == "Простой" else "advanced")

    def _set_ui_mode(self, mode, first_run=False):
        if not first_run and self.cfg.get("ui_mode", "advanced") == mode:
            return
        self.cfg["ui_mode"] = mode
        zc.update_config({"ui_mode": self.cfg["ui_mode"]})
        self._current_page = "control"
        self._rebuild_ui()
        self.log_msg("Режим интерфейса: "
                     + ("простой" if mode == "simple" else "полный"))
        if not first_run:
            return
        # первичная настройка после выбора режима в мастере
        if mode == "simple":
            if messagebox.askyesno(
                    "Первичная настройка",
                    "Настроить всё автоматически?\n\n"
                    "Программа проверит стратегии обхода, выберет рабочую и "
                    "сразу запустит её. Займёт пару минут."):
                self.on_simple_fix()
        elif messagebox.askyesno(
                "Добро пожаловать в Zapret GUI",
                "Запустить авто-поиск рабочих стратегий?\n\n"
                "Программа подберёт оптимальную стратегию обхода и составит "
                "список запасных. Если активная стратегия перестанет работать, "
                "приложение само переключится на другую рабочую.\n\n"
                "Поиск займёт пару минут."):
            self._show_page("auto")
            self.after(400, self.on_auto_start)

    def _ask_mode_dialog(self):
        win = ctk.CTkToplevel(self, fg_color=WIN_BG)
        win.title("Добро пожаловать")
        win.geometry("560x330")
        win.resizable(False, False)
        try:
            win.transient(self)
            win.grab_set()
        except Exception:
            pass
        ctk.CTkLabel(win, text="Как вам удобнее?", font=(FONT_DISPLAY, 24),
                     text_color=TEXT).pack(pady=(28, 4))
        ctk.CTkLabel(win, text="Режим можно сменить в любой момент — "
                     "переключатель внизу слева.",
                     font=(FONT, 12), text_color=MUTED).pack(pady=(0, 16))

        def pick(mode):
            try:
                win.destroy()
            except Exception:
                pass
            self._set_ui_mode(mode, first_run=True)

        for mode, txt, accent in (
                ("simple", "🏠  Простой режим  ·  рекомендуется\n"
                           "Одна кнопка «Запустить» — всё настроится само", True),
                ("advanced", "🛠  Полный режим\n"
                             "Пресеты, служба, DNS, диагностика, журнал", False)):
            ctk.CTkButton(
                win, text=txt, command=lambda m=mode: pick(m),
                width=440, height=64, corner_radius=12, font=(FONT, 14),
                fg_color=ACCENT if accent else CARD_HOVER,
                hover_color=ACCENT_HOVER if accent else BTN_HOVER,
                text_color=ON_ACCENT if accent else TEXT).pack(pady=7)
        win.protocol("WM_DELETE_WINDOW", lambda: pick("simple"))

    # -- страница: Управление --------------------------------------------- #
    def _build_control_page(self):
        p = self._page()
        self._title(p, "Управление Zapret",
                    "Выберите пресет и запустите обход. Пресеты хранятся в "
                    "presets.json. Тонкая настройка — в разделе «Настройки».")

        # --- плитка-дашборд: статус + здоровье + Старт/Стоп (сигнатура) ---
        self._section(p, "Состояние")
        self._build_dashboard(p)

        self._section(p, "Пресет обхода блокировок")
        c = self._card_row(p, "⭐", "Текущий пресет", "Выберите стратегию обхода")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        names = self._init_strategy_var()
        self.strategy_menu = self._menu(box, names or ["—"], self.strategy_var,
                                        self._on_strategy_pick, width=290)
        self.strategy_menu.pack(side="left", padx=4)
        self._btn(box, "Аргументы", self.show_args, width=110).pack(side="left", padx=4)
        self.preset_hint = ctk.CTkLabel(c, text="", font=(FONT, 11), text_color=MUTED,
                                        anchor="w")
        self.preset_hint.grid(row=2, column=1, sticky="w", padx=(20, 16), pady=(0, 14))
        self._update_preset_hint()

        self._section(p, "Автозапуск при старте Windows (служба)")
        c = self._card_row(p, "🔁", "Служба zapret",
                           "Обход стартует автоматически при включении ПК")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        self.svc_label = ctk.CTkLabel(box, text="…", font=(FONT, 12), text_color=MUTED)
        self.svc_label.pack(side="left", padx=(0, 10))
        self._btn(box, "Установить", self.on_install_service, accent=True,
                  width=120).pack(side="left", padx=4)
        self._btn(box, "Удалить", self.on_remove_service, width=110).pack(side="left", padx=4)

        self._section(p, "Параметры обхода")
        c = self._card_row(p, "🎮", "Игровой фильтр", "Расширяет диапазон портов для игр")
        self.game_seg = self._seg(c, ["Выкл", "TCP+UDP", "TCP", "UDP"],
                                  command=self._on_game_seg)
        self.game_seg.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        self.game_seg.set({"off": "Выкл", "all": "TCP+UDP", "tcp": "TCP",
                           "udp": "UDP"}[zc.get_game_mode()])

        c = self._card_row(p, "🚀", "Автозапуск обхода",
                           "Запускать обход при старте приложения")
        self.autostart_switch = self._cfg_switch(
            c, "autostart_bypass",
            on_msg="Автозапуск обхода: включён", off_msg="Автозапуск обхода: выключен")
        self.autostart_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20), pady=12, sticky="e")

        c = self._card_row(p, "🩺", "Авто-восстановление",
                           "Перезапускать обход, если он упал или перестал работать")
        self.recovery_switch = self._cfg_switch(
            c, "auto_recovery",
            on_msg="Авто-восстановление: включено", off_msg="Авто-восстановление: выключено")
        self.recovery_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20), pady=12, sticky="e")

        c = self._card_row(p, "🔒", "Шифрованный DNS (DoH)",
                           "Системный DNS через DoH (часть блокировок — по DNS)")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        _doh = zc.doh_status()
        self.doh_provider = ctk.StringVar(
            value={"cloudflare": "Cloudflare", "google": "Google"}.get(_doh["provider"], "Cloudflare"))
        self._seg(box, ["Cloudflare", "Google"], command=self._on_doh_provider_change,
                  variable=self.doh_provider).pack(side="left", padx=6)
        self.doh_switch = self._switch(box, self._on_doh_toggle)
        self.doh_switch.pack(side="left", padx=10)
        if _doh["enabled"]:
            self.doh_switch.select()

        c = self._card_row(p, "🌐", "IPSet-фильтр", "Текущее состояние списка IP")
        self.ipset_label = ctk.CTkLabel(c, text="…", font=(FONT, 12), text_color=MUTED)
        self.ipset_label.grid(row=0, column=2, rowspan=2, padx=(0, 8), pady=12, sticky="e")
        self._btn(c, "Обновить", self.on_update_ipset, width=110).grid(
            row=0, column=3, rowspan=2, padx=14, pady=12)

        c = self._card_row(p, "📃", "Списки доменов",
                           "Встроенные списки сайтов (Discord, YouTube и др.)")
        self.lists_label = ctk.CTkLabel(c, text="", font=(FONT, 12), text_color=MUTED)
        self.lists_label.grid(row=0, column=2, rowspan=2, padx=(0, 8), pady=12, sticky="e")
        self._btn(c, "Обновить", self.on_update_lists, width=110).grid(
            row=0, column=3, rowspan=2, padx=14, pady=12)
        self._refresh_lists_label()

        self._section(p, "Инструменты")
        c = self._card(p)
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=0, columnspan=3, padx=12, pady=12, sticky="w")
        self._btn(box, "Диагностика", self.on_diagnostics).pack(side="left", padx=4)
        self._btn(box, "Тест соединения", self.on_test).pack(side="left", padx=4)
        self._btn(box, "Сохранить отчёт", self.on_support_bundle, width=160).pack(
            side="left", padx=4)
        self._btn(box, "Папка логов", self.on_open_logs, width=130).pack(side="left", padx=4)
        self._btn(box, "Вернуть последнюю рабочую", self.on_restore_last_working,
                  width=210).pack(side="left", padx=4)
        box2 = ctk.CTkFrame(c, fg_color="transparent")
        box2.grid(row=1, column=0, columnspan=3, padx=12, pady=(0, 12), sticky="w")
        self._btn(box2, "Экспорт настроек", self.on_export_settings, width=160).pack(
            side="left", padx=4)
        self._btn(box2, "Импорт настроек", self.on_import_settings, width=160).pack(
            side="left", padx=4)
        return p

    # -- страница: Свои сайты --------------------------------------------- #
    def _build_sites_page(self):
        p = self._page()
        self._title(p, "Свои сайты для обхода",
                    "Добавьте сюда домены сайтов, которые нужно пробивать (например, "
                    "rutracker.org). Они дополняют встроенные списки. По одному домену "
                    "в строке — подпапки и поддомены учитываются автоматически.")

        self._section(p, "Список доменов")
        self._add_sites_editor(p, height=300)

        self._section(p, "Исключения из обхода")
        self._add_games_excl_card(p)
        return p

    def _on_games_excl_toggle(self):
        on = bool(self.games_excl_switch.get())
        changed = zc.set_game_exclusions(on)
        self.log_msg("Steam/Dota 2 " + ("исключены из обхода." if on
                     else "снова обрабатываются обходом."))
        if changed and ((self.proc and self.proc.poll() is None)
                        or zc.service_running()):
            self.log_msg("Перезапуск обхода для применения исключений…")
            self._bg(self._watchdog_restart)

    def _sites_load(self):
        domains = zc.read_user_domains()
        self.sites_box.delete("1.0", "end")
        if domains:
            self.sites_box.insert("1.0", "\n".join(domains) + "\n")
        self.sites_count.configure(text=f"сейчас сохранено: {len(domains)}")

    def on_sites_save(self):
        raw = self.sites_box.get("1.0", "end")
        lines = [ln for ln in raw.splitlines()]
        clean = zc.write_user_domains(lines)
        # перечитать и показать нормализованный результат
        self.sites_box.delete("1.0", "end")
        if clean:
            self.sites_box.insert("1.0", "\n".join(clean) + "\n")
        self.sites_count.configure(text=f"сохранено: {len(clean)}")
        self.log_msg(f"[Свои сайты] сохранено доменов: {len(clean)}")

        running = bool(self.proc and self.proc.poll() is None) or zc.service_running()
        if not running:
            messagebox.showinfo("Свои сайты",
                                f"Сохранено доменов: {len(clean)}.\n"
                                "Изменения применятся при следующем запуске обхода.")
            return
        if messagebox.askyesno("Свои сайты",
                               f"Сохранено доменов: {len(clean)}.\n"
                               "Перезапустить обход, чтобы применить сейчас?"):
            self.log_msg("[Свои сайты] перезапуск обхода для применения списка…")
            self._bg(self._watchdog_restart)

    # -- страница: Авто-поиск --------------------------------------------- #
    def _build_auto_page(self):
        p = self._page()
        self._title(p, "Авто-поиск стратегии",
                    "Умный подбор: стратегии пробуются по приоритету — последняя "
                    "рабочая, запасные из пула, похожие по типу десинка, затем "
                    "остальные. В быстром режиме поиск останавливается, как только "
                    "найдено несколько рабочих.")

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
        box2 = ctk.CTkFrame(c, fg_color="transparent")
        box2.grid(row=1, column=0, columnspan=3, padx=12, pady=(0, 10), sticky="w")
        self.fast_var = ctk.BooleanVar(value=self.cfg.get("auto_fast", True))
        ctk.CTkCheckBox(box2, text="Быстрый режим (остановиться на первых рабочих)",
                        variable=self.fast_var, font=(FONT, 13), fg_color=ACCENT,
                        hover_color=ACCENT_HOVER, command=self._on_fast_toggle).pack(
            side="left", padx=12)
        ctk.CTkLabel(box2, text=f"выкл. — проверить все {len(self.presets)} и собрать полный пул",
                     font=(FONT, 11), text_color=MUTED).pack(side="left", padx=14)

        c = self._card(p)
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=0, columnspan=3, padx=12, pady=12, sticky="ew")
        self.btn_auto_start = self._btn(box, "🔍  Начать поиск", self.on_auto_start,
                                        accent=True, width=160)
        self.btn_auto_start.pack(side="left", padx=4)
        self.btn_auto_stop = self._btn(box, "■  Остановить", self.on_auto_stop, width=130)
        self.btn_auto_stop.pack(side="left", padx=4)
        self.btn_auto_stop.configure(state="disabled")
        self.auto_bar = ctk.CTkProgressBar(box, width=240, progress_color=ACCENT,
                                           fg_color=FIELD_BG)
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
        self._apply_tree_tags()

        c = self._card(p)
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=0, padx=12, pady=12, sticky="w")
        self.btn_apply_best = self._btn(box, "✔  Применить и запустить", self.on_apply_best,
                                        accent=True, width=200)
        self.btn_apply_best.pack(side="left", padx=4)
        self.btn_apply_best.configure(state="disabled")
        self.btn_install_best = self._btn(box, "Установить как службу",
                                          self.on_install_best, width=200)
        self.btn_install_best.pack(side="left", padx=4)
        self.btn_install_best.configure(state="disabled")
        return p

    def _apply_tree_tags(self):
        # тема-зависимые теги строк таблицы (re-применяются при смене темы)
        if not hasattr(self, "tree"):
            return
        try:
            self.tree.tag_configure("best",
                                    background=_pick(("#d8f1e2", "#173d17")),
                                    foreground=_pick(("#137a43", "#9af0bf")))
            self.tree.tag_configure("good", foreground=_pick(GREEN))
            self.tree.tag_configure("partial", foreground=_pick(YELLOW))
            self.tree.tag_configure("bad", foreground=_pick(RED))
        except Exception:           # таблица могла быть пересоздана (смена акцента)
            pass

    # -- страница: Telegram ----------------------------------------------- #
    def _build_tgws_page(self):
        p = self._page()
        self._title(p, "Telegram-прокси",
                    "Встроенный MTProto-прокси для Telegram (WebSocket-мост). "
                    "Отдельная программа не нужна — всё работает внутри приложения. "
                    "Запустите прокси и добавьте ссылку в Telegram.")
        yield p
        yield
        self._section(p, "Статус")
        c = self._card(p)
        self.tg_dot = ctk.CTkLabel(c, text="●", font=(FONT, 24), text_color=MUTED)
        self.tg_dot.grid(row=0, column=0, rowspan=2, padx=(16, 12), pady=14)
        self.tg_title = ctk.CTkLabel(c, text="Проверка…", font=(FONT, 14, "bold"),
                                     text_color=TEXT, anchor="w")
        self.tg_title.grid(row=0, column=1, sticky="sw", pady=(14, 0))
        self.tg_sub = ctk.CTkLabel(c, text="", font=(FONT, 11), text_color=MUTED, anchor="w")
        self.tg_sub.grid(row=1, column=1, sticky="nw", pady=(0, 14))

        yield
        self._section(p, "Управление")
        yield
        c = self._card_row(p, "✈", "Встроенный прокси",
                           f"Слушает {zc.TG_DEFAULT_HOST}:{zc.tg_get_port()}")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        self.btn_tg_start = self._btn(box, "▶  Запустить", self.on_tg_start, accent=True)
        self.btn_tg_start.pack(side="left", padx=4)
        self.btn_tg_stop = self._btn(box, "■  Остановить", self.on_tg_stop)
        self.btn_tg_stop.pack(side="left", padx=4)

        yield
        self._section(p, "Ссылка для Telegram")
        c = self._card(p)
        self.tg_link_var = ctk.StringVar(value=zc.tg_proxy_url())
        tg_link_entry = ctk.CTkEntry(c, textvariable=self.tg_link_var, font=(FONT, 12),
                                     height=36, fg_color=FIELD_BG, text_color=TEXT,
                                     border_width=0)
        tg_link_entry.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=12)
        self._enable_clipboard(tg_link_entry)
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

        yield
        self._section(p, "Настройки прокси")
        yield
        c = self._card_row(p, "⚙", "Порт и секрет",
                           "Порт локального прокси и MTProto-секрет")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        self.tg_port_var = ctk.StringVar(value=str(zc.tg_get_port()))
        tg_port_entry = ctk.CTkEntry(box, textvariable=self.tg_port_var, width=80,
                                     height=36, font=(FONT, 13), justify="center",
                                     fg_color=FIELD_BG, text_color=TEXT,
                                     border_color=BORDER, border_width=1)
        tg_port_entry.pack(side="left", padx=4)
        self._enable_clipboard(tg_port_entry)
        self._btn(box, "Применить", self.on_tg_apply_port, width=110).pack(side="left", padx=4)
        self._btn(box, "Сменить секрет", self.on_tg_regen, width=150).pack(side="left", padx=4)

        yield
        c = self._card_row(p, "☁", "Запасной Cloudflare-прокси",
                           "Резерв через публичные Cloudflare-воркеры. Их общий пул "
                           "часто отдаёт 429 и вызывает обрывы — если Telegram и так "
                           "работает, выключите, чтобы убрать моргание.")
        self.cfproxy_switch = self._switch(c, self._on_cfproxy_toggle)
        self.cfproxy_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20), pady=12, sticky="e")
        if zc.tg_get_cfproxy():
            self.cfproxy_switch.select()

        yield
        self._section(p, "Статистика")
        c = self._card(p)
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=0, columnspan=3, padx=16, pady=14, sticky="w")
        self.pstat_conn = ctk.CTkLabel(box, text="Соединения: —", font=(FONT, 13),
                                       text_color=TEXT, anchor="w")
        self.pstat_conn.pack(anchor="w")
        self.pstat_traffic = ctk.CTkLabel(box, text="Трафик: —", font=(FONT, 13),
                                          text_color=MUTED, anchor="w")
        self.pstat_traffic.pack(anchor="w", pady=(2, 0))
        self._btn(c, "Обновить", self._refresh_proxy_stats, width=110).grid(
            row=0, column=3, padx=14, pady=12)

        yield
        self._section(p, "Диагностика прокси")
        yield
        c = self._card_row(p, "📜", "Лог прокси",
                           "Журнал соединений прокси — для разбора обрывов и сбросов")
        self._btn(c, "Открыть лог", self.on_open_proxy_log, width=140).grid(
            row=0, column=2, rowspan=2, padx=14, pady=12)
        return p

    def _refresh_proxy_stats(self):
        def worker():
            s = zc.tg_proxy_stats()
            self.post(lambda: self._apply_proxy_stats(s))
        self._bg(worker)

    def _apply_proxy_stats(self, s):
        if not hasattr(self, "pstat_conn"):
            return
        if not s:
            self.pstat_conn.configure(text="Соединения: нет данных (запустите прокси)")
            self.pstat_traffic.configure(text="Трафик: —")
            return
        self.pstat_conn.configure(
            text=f"Соединения: всего {s.get('total','?')} · активных {s.get('active','?')} "
                 f"· WS {s.get('ws','?')} · CF {s.get('cf','?')} · TCP {s.get('tcp_fb','?')}")
        self.pstat_traffic.configure(
            text=f"Трафик: ↑ {s.get('up','?')}   ↓ {s.get('down','?')}   ·   "
                 f"ошибок {s.get('err','0')}")

    def _proxy_stats_auto(self):
        if self._closing:
            return
        if zc.tg_proxy_running():
            self._refresh_proxy_stats()
        self.after(10000, self._proxy_stats_auto)

    def on_open_proxy_log(self):
        path = zc.tg_proxy_log_path()
        if not os.path.exists(path):
            messagebox.showinfo("Лог прокси",
                                "Лог пока пуст. Запустите прокси и попользуйтесь "
                                "Telegram — события появятся здесь:\n" + path)
            return
        try:
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("Лог прокси", str(e))

    # -- страница: Диагностика -------------------------------------------- #
    def _build_diag_page(self):
        p = self._page()
        self._title(p, "Диагностика и совместимость",
                    "Проверка окружения: права, драйвер WinDivert, конфликты с другими "
                    "обходами и сетевым ПО, порты, DNS, доступность сайтов. Рядом с "
                    "проблемой — кнопка быстрого исправления.")

        self._section(p, "Действия")
        c = self._card(p)
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=0, columnspan=3, padx=12, pady=12, sticky="w")
        self.btn_diag_run = self._btn(box, "🔄  Проверить заново", self.on_diag_run,
                                      accent=True, width=190)
        self.btn_diag_run.pack(side="left", padx=4)
        self._btn(box, "Остановить конфликты", lambda: self.on_diag_fix("stop_conflicts"),
                  width=190).pack(side="left", padx=4)
        self._btn(box, "Сбросить WinDivert", lambda: self.on_diag_fix("reset_windivert"),
                  width=180).pack(side="left", padx=4)
        box2 = ctk.CTkFrame(c, fg_color="transparent")
        box2.grid(row=1, column=0, columnspan=3, padx=12, pady=(0, 12), sticky="w")
        self._btn(box2, "Перезапустить обход", self.on_diag_restart, width=190).pack(
            side="left", padx=4)
        self._btn(box2, "⚠ Остановить все winws", self.on_stop_all_winws,
                  width=210).pack(side="left", padx=4)
        self._btn(box2, "Сохранить отчёт", self.on_support_bundle, width=170).pack(
            side="left", padx=4)

        self._section(p, "Результаты проверки")
        self.diag_list = ctk.CTkFrame(p, fg_color="transparent")
        self.diag_list.pack(fill="x", padx=10, pady=0)
        ctk.CTkLabel(self.diag_list, text="Нажмите «Проверить заново».",
                     font=(FONT, 12), text_color=MUTED).pack(anchor="w", padx=8, pady=8)
        return p

    def on_diag_run(self):
        if getattr(self, "_diag_busy", False):
            return
        self._diag_busy = True
        try:
            self.btn_diag_run.configure(state="disabled", text="Проверка…")
        except Exception:
            pass
        for w in self.diag_list.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.diag_list, text="Идёт проверка окружения…",
                     font=(FONT, 12), text_color=MUTED).pack(anchor="w", padx=8, pady=8)

        def worker():
            try:
                items = zc.diagnose()
            except Exception as e:
                items = [{"title": "Ошибка диагностики", "status": "bad",
                          "detail": str(e), "fix": None}]
            self.post(lambda: self._render_diag(items))

        self._bg(worker)

    def _render_diag(self, items):
        self._diag_busy = False
        try:
            self.btn_diag_run.configure(state="normal", text="🔄  Проверить заново")
        except Exception:
            pass
        for w in self.diag_list.winfo_children():
            w.destroy()
        colors = {"ok": GREEN, "warn": YELLOW, "bad": RED}
        n_bad = sum(1 for it in items if it["status"] == "bad")
        n_warn = sum(1 for it in items if it["status"] == "warn")
        self._set_diag_badge(n_bad)
        summary = ("Всё в порядке." if not n_bad and not n_warn
                   else f"Проблемы: {n_bad} критич., {n_warn} предупр.")
        ctk.CTkLabel(self.diag_list, text=summary, font=(FONT, 13, "bold"),
                     text_color=(RED if n_bad else (YELLOW if n_warn else GREEN))).pack(
            anchor="w", padx=8, pady=(2, 8))
        for it in items:
            row = ctk.CTkFrame(self.diag_list, corner_radius=10, fg_color=CARD_BG)
            row.pack(fill="x", padx=4, pady=4)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text="●", font=(FONT, 18),
                         text_color=colors.get(it["status"], MUTED)).grid(
                row=0, column=0, rowspan=2, padx=(16, 12), pady=12)
            ctk.CTkLabel(row, text=it["title"], font=(FONT, 14, "bold"),
                         text_color=TEXT, anchor="w").grid(
                row=0, column=1, sticky="sw", pady=(12, 0))
            ctk.CTkLabel(row, text=it["detail"], font=(FONT, 11), text_color=MUTED,
                         anchor="w", justify="left", wraplength=560).grid(
                row=1, column=1, sticky="nw", pady=(0, 12))
            if it.get("fix"):
                self._btn(row, "Исправить", lambda k=it["fix"]: self.on_diag_fix(k),
                          accent=True, width=120).grid(row=0, column=2, rowspan=2,
                                                       padx=14, pady=12)

    def on_diag_fix(self, key):
        self.log_msg(f"[Диагностика] исправление: {key}")
        if key == "relaunch_admin":
            self._relaunch_as_admin()
            return

        def worker():
            try:
                msg = zc.apply_fix(key)
            except Exception as e:
                msg = f"ошибка: {e}"
            self.log_msg(f"[Диагностика] {msg}")
            self.post(self.on_diag_run)

        self._bg(worker)

    def on_diag_restart(self):
        if self.auto_running or self._stop_busy or self._recovery_busy:
            return
        token = self.runtime.request()
        self._invalidate_health()
        self.log_msg("[Диагностика] перезапуск обхода…")
        self._bg(lambda: self._watchdog_restart(token))

    def on_stop_all_winws(self):
        """Аварийно завершить все winws.exe, включая чужие экземпляры."""
        if self._stop_busy:
            return
        if not messagebox.askyesno(
                "Аварийная остановка",
                "Будут принудительно завершены все процессы winws.exe, включая "
                "запущенные другими программами. Продолжить?"):
            return

        self._startup_cancelled = True
        token = self.runtime.request("stopped")
        self.auto_cancel = True
        self._auto_autoapply = False
        self._stop_busy = True
        self._invalidate_health()
        self.log_msg("[Диагностика] аварийная остановка всех winws.exe…")

        def worker():
            try:
                with self.runtime.transition(token):
                    zc.stop_process(self.runtime.trial)
                    self.runtime.trial = None
                    try:
                        if zc.service_running():
                            zc.set_service_running(False)
                    finally:
                        self._stop_local_winws()
                    msg = zc.stop_all_winws()
                    zc.update_config({}, remove=("svc_stopped_for_search",))
                    self.cfg.pop("svc_stopped_for_search", None)
                self.log_msg(f"[Диагностика] {msg}")
            except Superseded:
                pass
            except Exception as exc:
                self.log_msg(f"[ОШИБКА аварийной остановки] {exc}")
            finally:
                self._stop_busy = False
                self.post(self.refresh_status)

        self._bg(worker)

    # -- страница: Настройки приложения ----------------------------------- #
    def _build_settings_page(self):
        p = self._page()
        self._title(p, "Настройки приложения",
                    "Параметры самого приложения: обновления, оформление, трей, антивирус.")

        yield p
        yield
        self._section(p, "Обновления")
        yield
        c = self._card_row(p, "⬆", f"Версия {zc.APP_VERSION}",
                           "Проверить и установить новую версию с GitHub")
        box = ctk.CTkFrame(c, fg_color="transparent")
        box.grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        self.upd_label = ctk.CTkLabel(box, text="", font=(FONT, 11), text_color=MUTED)
        self.upd_label.pack(side="left", padx=(0, 8))
        self._btn(box, "Проверить", self.on_check_update, accent=True,
                  width=120).pack(side="left", padx=4)

        yield
        c = self._card_row(p, "🔄", "Автопроверка обновлений",
                           "Проверять новые версии при запуске")
        self.update_switch = self._switch(c, self._on_update_toggle)
        self.update_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20), pady=12, sticky="e")
        if zc.get_update_enabled():
            self.update_switch.select()

        yield
        self._section(p, "Оформление")
        yield
        c = self._card_row(p, "🌗", "Тема", "Тёмная / светлая / системная")
        self.appearance_var = ctk.StringVar(
            value={v: k for k, v in APPEARANCE.items()}.get(
                self.cfg.get("appearance", "light"), "Тёмная"))
        self._seg(c, list(APPEARANCE.keys()), command=self._on_appearance_change,
                  variable=self.appearance_var).grid(
            row=0, column=2, rowspan=2, padx=14, pady=12)

        yield
        c = self._card_row(p, "🎨", "Акцентный цвет", "Цвет кнопок и выделения")
        self.theme_var = ctk.StringVar(
            value=self.cfg.get("accent_name") if self.cfg.get("accent_name") in THEMES
            else "Синяя")
        self._menu(c, list(THEMES.keys()), self.theme_var, self._on_theme_change).grid(
            row=0, column=2, rowspan=2, padx=14, pady=12)

        yield
        self._section(p, "Поведение")
        yield
        c = self._card_row(p, "📥", "Сворачивать в трей",
                           "При закрытии окна прятать в трей (обход продолжит работать)")
        self.tray_switch = self._cfg_switch(c, "minimize_to_tray", default=True)
        self.tray_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20), pady=12, sticky="e")

        yield
        c = self._card_row(p, "🚀", "Полный автозапуск при включении ПК",
                           "Приложение, обход и Telegram-прокси стартуют при входе "
                           "в систему (свернётся в трей)")
        self.full_autostart_switch = self._switch(c, self._on_full_autostart_toggle)
        self.full_autostart_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20),
                                        pady=12, sticky="e")
        self._sync_autostart_switch()

        yield
        self._section(p, "Списки и обход")
        yield
        c = self._card_row(p, "📃", "Автообновление списков и IPSet",
                           "Раз в неделю подтягивать свежие списки сайтов и IP-набор "
                           "(ipset-all) из upstream — чтобы обход не устаревал")
        self.lists_auto_switch = self._cfg_switch(c, "lists_auto_update")
        self.lists_auto_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20), pady=12, sticky="e")

        yield
        c = self._card_row(p, "🔁", "Авто-переподбор при сбое",
                           "Если все запасные стратегии перестали работать — "
                           "автоматически запустить авто-поиск и применить лучшую")
        self.research_switch = self._cfg_switch(c, "auto_research_on_fail")
        self.research_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20), pady=12, sticky="e")

        yield
        c = self._card_row(p, "🔔", "Уведомления",
                           "Всплывающие сообщения о событиях обхода (переключение, "
                           "восстановление, обновление списков)")
        self.notif_switch = self._cfg_switch(c, "notifications", default=True)
        self.notif_switch.grid(row=0, column=2, rowspan=2, padx=(0, 20), pady=12, sticky="e")

        yield
        self._section(p, "Антивирус")
        yield
        c = self._card_row(p, "🛡", "Windows Defender",
                           "Добавить папку в исключения — меньше ложных срабатываний AV")
        self._btn(c, "Добавить в исключения", self.on_add_defender_exclusion,
                  accent=True, width=200).grid(row=0, column=2, rowspan=2, padx=14, pady=12)
        return p

    # -- страница: Журнал ------------------------------------------------- #
    def _build_log_page(self):
        p = ctk.CTkFrame(self.container, fg_color=WIN_BG)
        p.grid_rowconfigure(2, weight=1)
        p.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(p, text="Журнал", font=(FONT_DISPLAY, 25), text_color=TEXT,
                     anchor="w").grid(row=0, column=0, sticky="w", padx=16, pady=(12, 6))

        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        bar.grid_columnconfigure(0, weight=1)
        self.log_filter_var = ctk.StringVar()
        self.log_filter_var.trace_add("write", lambda *a: self._render_log())
        log_filter_entry = ctk.CTkEntry(bar, textvariable=self.log_filter_var, height=34,
                                        font=(FONT, 12), placeholder_text="Фильтр по тексту…",
                                        fg_color=FIELD_BG, text_color=TEXT,
                                        border_color=BORDER, border_width=1)
        log_filter_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._enable_clipboard(log_filter_entry)
        self.log_count_lbl = ctk.CTkLabel(bar, text="", font=(FONT, 11), text_color=MUTED)
        self.log_count_lbl.grid(row=0, column=1, padx=(0, 8))
        self._btn(bar, "Копировать", self.on_copy_log, width=120).grid(row=0, column=2, padx=4)
        self._btn(bar, "Очистить", self.clear_log, width=110).grid(row=0, column=3, padx=(4, 0))

        self.logbox = ctk.CTkTextbox(p, font=(FONT_MONO, 12), fg_color=LOG_BG,
                                     text_color=LOG_FG, wrap="none")
        self.logbox.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 12))
        self.logbox.configure(state="disabled")
        self.log_msg("Готово. Выберите пресет и нажмите «Запустить».")
        return p

    def _log_matches(self, line):
        flt = self.log_filter_var.get().strip().lower() if hasattr(self, "log_filter_var") else ""
        return (not flt) or (flt in line.lower())

    def _render_log(self):
        self._log_dirty = False
        # перерисовать журнал из буфера с учётом фильтра
        if not hasattr(self, "logbox"):
            return
        shown = [ln for ln in self._log_lines if self._log_matches(ln)]
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        if shown:
            self.logbox.insert("1.0", "\n".join(shown) + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
        try:
            total = len(self._log_lines)
            self.log_count_lbl.configure(
                text=f"{len(shown)}/{total}" if len(shown) != total else f"{total} строк")
        except Exception:
            pass

    def on_copy_log(self):
        try:
            self.clipboard_clear()
            self.clipboard_append("\n".join(self._log_lines))
            self.log_msg(f"[журнал] скопировано строк: {len(self._log_lines)}")
        except Exception as e:
            self.log_msg(f"[журнал] не удалось скопировать: {e}")

    # -- журнал / очередь ------------------------------------------------- #
    def log_msg(self, text):
        self.log_queue.put(str(text))

    def post(self, fn):
        self.ui_queue.put(fn)

    def _notify(self, title, message):
        """Всплывающее уведомление Windows через значок в трее (если включено)."""
        if not self.cfg.get("notifications", True):
            return
        if self.tray is not None:
            try:
                self.tray.notify(message, title)
            except Exception:
                pass

    def _poll_ui(self):
        if self._closing:
            return
        lines = []
        for _ in range(200):
            try:
                lines.append(self.log_queue.get_nowait().rstrip())
            except queue.Empty:
                break
        if lines:
            self._log_lines.extend(lines)
            trimmed = len(self._log_lines) > 5000
            if trimmed:
                self._log_lines = self._log_lines[-4000:]
            self._log_dirty = True
            if self._current_page == "log" and hasattr(self, "logbox"):
                if trimmed:
                    self._render_log()
                else:
                    shown = [line for line in lines if self._log_matches(line)]
                    if shown:
                        self.logbox.configure(state="normal")
                        self.logbox.insert("end", "\n".join(shown) + "\n")
                        self.logbox.see("end")
                        self.logbox.configure(state="disabled")
                    self._cfgw("log_count_lbl", text=f"{len(self._log_lines)} строк")
                    self._log_dirty = False
            if self._logf:
                try:
                    prefix = time.strftime("%H:%M:%S ")
                    self._logf.write("".join(prefix + line + "\n" for line in lines))
                    self._logf.flush()
                except OSError:
                    pass
        deadline = time.perf_counter() + 0.008
        for _ in range(30):
            if time.perf_counter() >= deadline:
                break
            try:
                fn = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as exc:
                self.log_msg(f"[Интерфейс] {exc}")
        self.after(10 if not self.ui_queue.empty() or not self.log_queue.empty() else 100, self._poll_ui)

    def clear_log(self):
        self._log_lines = []
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")
        try:
            self.log_count_lbl.configure(text="0 строк")
        except Exception:
            pass

    # -- статус ----------------------------------------------------------- #
    def _auto_refresh(self):
        self.refresh_status()
        self.after(3000, self._auto_refresh)

    def refresh_status(self):
        if self._status_busy:
            return
        self._status_busy = True

        def worker():
            try:
                installed = zc.service_installed()
                svc_run = zc.service_running() if installed else False
                running = bool(self.proc and self.proc.poll() is None) or svc_run
                ipset = zc.get_ipset_status()
                tg = zc.tg_proxy_running()
                if svc_run:
                    self.runtime.request("service", expected=self._initial_runtime_token)
                self.post(lambda: self._apply_status(running, installed, svc_run, ipset, tg))
            except Exception as exc:
                self.log_msg(f"[Статус] {exc}")
            finally:
                self._status_busy = False

        self._bg(worker)

    def _apply_status(self, running, installed, svc_run, ipset, tg):
        self._status_snapshot = (running, installed, svc_run, ipset, tg)
        self._status_busy = False
        if running:
            self.ctl_dot.configure(text_color=GREEN)
            self.ctl_status_title.configure(text="Обход включён")
            sub = "Обход блокировок активен"
            # показать, какая стратегия реально работает — раньше по интерфейсу
            # этого не было видно и легко было запустить не тот пресет
            name = self.active_preset_name or self.cfg.get("strategy") or ""
            if name:
                sub += f"   ·   {name}"
            self.side_status.configure(text="●  Zapret работает", text_color=GREEN)
        else:
            self.ctl_dot.configure(text_color=MUTED)
            self.ctl_status_title.configure(text="Обход выключен")
            sub = "Запустите обход или сначала проверьте связь."
            self.side_status.configure(text="●  Обход выключен", text_color=MUTED)
        if installed:
            sub += f"   ·   служба: {'работает' if svc_run else 'установлена'}"
        self.ctl_status_sub.configure(text=sub)
        busy = self.auto_running or self._stop_busy or self._start_busy
        self.btn_start.configure(state="disabled" if running or busy else "normal")
        can_stop = running or self.auto_running or self._start_busy or self.runtime.desired != "stopped"
        self.btn_stop.configure(state="normal" if can_stop and not self._stop_busy else "disabled")
        self._cfgw("ipset_label", text=f"IPSet: {ipset}")
        if installed:
            self._cfgw("svc_label", text="работает" if svc_run else "остановлена",
                       text_color=GREEN if svc_run else MUTED)
        else:
            self._cfgw("svc_label", text="не установлена", text_color=MUTED)

        if tg:
            self._cfgw("tg_dot", text_color=GREEN)
            self._cfgw("tg_title", text="Telegram-прокси работает")
            self._cfgw("tg_sub", text=f"Слушает {zc.TG_DEFAULT_HOST}:{zc.tg_get_port()}")
        else:
            self._cfgw("tg_dot", text_color=RED)
            self._cfgw("tg_title", text="Telegram-прокси остановлен")
            self._cfgw("tg_sub", text="Прокси не запущен")
        # индикатор прокси на дашборде
        self._cfgw("dash_proxy_dot", text_color=GREEN if tg else MUTED)
        self._cfgw("dash_proxy_lbl", text="Включён" if tg else "Выключен")

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
        name = self.strategy_var.get()
        # логируем каждый выбор — иначе по журналу не понять, почему запуск/служба
        # использовали не ту стратегию, что подобрал авто-поиск
        if name and name != "—" and self.cfg.get("strategy") != name:
            self.log_msg(f"Выбран пресет: «{name}»")
        self.cfg["strategy"] = name
        zc.update_config({"strategy": self.cfg["strategy"]})
        self._update_preset_hint()

    def _update_preset_hint(self):
        preset = self.preset_by_name.get(self.strategy_var.get())
        if preset:
            self._cfgw("preset_hint", text="Метки: " + " · ".join(zc.preset_tags(preset)))

    def on_start(self):
        if self.auto_running or self._stop_busy or self._start_busy:
            return
        if self.proc and self.proc.poll() is None:
            self.log_msg("Обход уже запущен.")
            return
        preset = self._selected_preset()
        if not preset:
            return
        mode = zc.get_game_mode()
        args = zc.build_args_str(preset["args"], mode)
        if not args:
            return
        token = self.runtime.request("manual")
        self._recovery_failed.clear()
        self._start_busy = True
        self._invalidate_health()
        self._cfgw("btn_start", state="disabled", text="Запускаю…")
        def worker():
            try:
                with self.runtime.transition(token):
                    if zc.service_running() or zc.winws_running():
                        self.runtime.set_desired(token, "service" if zc.service_running() else "stopped")
                        raise RuntimeError("Обход уже запущен службой или другой программой.")
                    self.runtime.check(token)
                    self.active_args, self.active_preset_name = args, preset["name"]
                    zc.enable_tcp_timestamps()
                    self.runtime.check(token)
                    self._spawn_winws(args, preset["name"])
                    self.cfg["strategy"] = preset["name"]
                    zc.update_config({"strategy": preset["name"]})
                self.log_msg(f"Запущена стратегия «{preset['name']}».")
            except Superseded:
                pass
            except Exception as exc:
                self.log_msg(f"[Запуск] {exc}")
            finally:
                self._start_busy = False
                self.post(lambda: self._cfgw("btn_start", state="normal", text="Запустить обход"))
                self.post(self.refresh_status)
                self.post(self.on_health_check)
        self._bg(worker)

    def _spawn_winws(self, args, name=None):
        """Запустить winws с чтением вывода в журнал и запомнить, что работает
        (active_args/active_preset_name нужны watchdog'у и статусу)."""
        self.proc = zc.start_winws_logged(args)
        self.active_args = args
        if name:
            self.active_preset_name = name
        threading.Thread(target=self._read_output, args=(self.proc,),
                         daemon=True).start()

    def _stop_local_winws(self, clear=True):
        """Остановить только winws, запущенный этим экземпляром GUI."""
        zc.stop_process(self.proc)
        self.proc = None
        if clear:
            self.active_args = None
            self.active_preset_name = None

    def _managed_bypass_running(self):
        """Состояние обхода, которым может управлять именно этот GUI."""
        return bool(self.proc and self.proc.poll() is None) or zc.service_running()

    def _read_output(self, proc):
        try:
            for line in proc.stdout:
                if line:
                    self.log_msg(line)
        except Exception:
            pass
        try:                       # дождаться реального кода выхода (не «None»)
            code = proc.wait(timeout=3)
        except Exception:
            code = proc.poll()
        self.log_msg(f"--- winws.exe завершился (код {code}) ---")
        self.post(self.refresh_status)

    def on_stop(self):
        self._startup_cancelled = True
        if self._stop_busy:
            return
        token = self.runtime.request("stopped")
        self.auto_cancel = True
        self._auto_autoapply = False
        self._stop_busy = True
        self._invalidate_health()
        self.log_msg("--- Остановка обхода ---")
        def worker():
            try:
                with self.runtime.transition(token):
                    zc.stop_process(self.runtime.trial)
                    self.runtime.trial = None
                    try:
                        if zc.service_running():
                            zc.set_service_running(False)
                    finally:
                        self._stop_local_winws()
                    zc.update_config({}, remove=("svc_stopped_for_search",))
                    self.cfg.pop("svc_stopped_for_search", None)
                self.log_msg("Обход остановлен.")
            except Superseded:
                pass
            except Exception as exc:
                self.log_msg(f"[ОШИБКА остановки] {exc}")
            finally:
                self._stop_busy = False
                self.post(self.refresh_status)
        self._bg(worker)

    def on_install_service(self):
        if self.auto_running or self._stop_busy:
            return
        preset = self._selected_preset()
        if not preset:
            return
        if not messagebox.askyesno("Служба", f"Установить пресет «{preset['name']}» "
                                   "как службу автозапуска?"):
            return
        mode = zc.get_game_mode()
        token = self.runtime.request("service")
        self.log_msg(f"--- Установка службы из «{preset['name']}» ---")

        def worker():
            try:
                with self.runtime.transition(token):
                    self._stop_local_winws()
                    self.runtime.check(token)
                    ok, log = zc.install_service(preset["name"], preset["args"], mode,
                                                 cancelled=lambda: not self.runtime.valid(token))
                    self.runtime.check(token)
            except Superseded:
                return
            except Exception as exc:
                self.log_msg(f"[Служба] {exc}")
                return
            if log:
                self.log_msg(log)
            if ok:
                # синхронизировать выбор с установленной службой — иначе watchdog
                # и статус показывают не ту стратегию, что реально работает
                self.active_preset_name = preset["name"]
                self.cfg["strategy"] = preset["name"]
                zc.update_config({"strategy": self.cfg["strategy"]})
                self.post(lambda: self.strategy_var.set(preset["name"]))
            self.log_msg("Служба установлена." if ok else "[ОШИБКА] Служба не установлена.")
            self.post(self.refresh_status)

        self._bg(worker)

    def on_remove_service(self):
        if self.auto_running or self._stop_busy:
            return
        if not zc.service_installed():
            messagebox.showinfo("Zapret", "Служба не установлена.")
            return
        self.log_msg("--- Удаление службы ---")
        token = self.runtime.request("stopped")

        def worker():
            try:
                with self.runtime.transition(token):
                    zc.remove_service()
                self.log_msg("Служба удалена.")
            except Exception as exc:
                self.log_msg(f"[Служба] {exc}")
            self.post(self.refresh_status)

        self._bg(worker)

    # -- настройки / инструменты ------------------------------------------ #
    def _on_game_seg(self, value):
        mode = {"Выкл": "off", "TCP+UDP": "all", "TCP": "tcp", "UDP": "udp"}[value]
        zc.set_game_mode(mode)
        self.log_msg(f"Игровой фильтр: {mode}. Перезапустите обход, чтобы применить.")

    def _on_update_toggle(self):
        en = bool(self.update_switch.get())
        zc.set_update_enabled(en)
        self.log_msg("Проверка обновлений: " + ("включена" if en else "выключена"))

    # -- первый запуск ---------------------------------------------------- #
    def _first_run_wizard(self):
        if self.cfg.get("first_run_done"):
            return
        self.cfg["first_run_done"] = True
        zc.update_config({"first_run_done": self.cfg["first_run_done"]})
        self._ask_mode_dialog()

    # -- авто-восстановление (watchdog) ----------------------------------- #
    def _startup_service_restore(self):
        if self._closing or getattr(self, "_startup_cancelled", False) or not self.cfg.get("svc_stopped_for_search"):
            return
        token = self.runtime.request("service")
        def worker():
            try:
                with self.runtime.transition(token):
                    zc.set_service_running(True)
                    self.runtime.check(token)
                    self.cfg.pop("svc_stopped_for_search", None)
                    zc.update_config({}, remove=("svc_stopped_for_search",))
            except Exception as exc:
                self.log_msg(f"[Восстановление службы] {exc}")
            self.post(self.refresh_status)
        self._bg(worker)

    def _autostart_bypass(self):
        if self._closing or getattr(self, "_startup_cancelled", False):
            return
        if zc.service_running():
            self.log_msg("Обход уже обеспечивает служба zapret.")
            return
        if self.cfg.get("svc_stopped_for_search") and zc.service_installed():
            return   # службу сейчас вернёт _startup_service_restore
        if not zc.winws_running():
            self.log_msg("Автозапуск обхода…")
            self.on_start()

    def _autostart_proxy(self):
        if zc.tg_proxy_running():
            return
        self.log_msg("Автозапуск Telegram-прокси…")
        self._tg_start_verified(ok_msg="Telegram-прокси запущен.")

    def _autostart_full(self):
        # запуск при входе в систему: обход + прокси + сворачивание в трей
        self.log_msg("Полный автозапуск (вход в систему)…")
        if self.tray is not None:
            self.after(300, self.withdraw)
        self._autostart_bypass()
        self._autostart_proxy()

    def _watchdog_loop(self):
        failures, healthy = {}, 0
        network = None
        episode_token = self.runtime.token
        while not self._closing:
            token = self.runtime.token
            if token is not episode_token:
                failures, healthy = {}, 0
                episode_token = token
            if not self.runtime.wait(token, zc.WATCHDOG_INTERVAL):
                continue
            try:
                if not self.cfg.get("auto_recovery") or self.auto_running or self.runtime.busy or self._stop_busy:
                    failures, healthy = {}, 0
                    continue
                if self.runtime.desired == "stopped":
                    continue
                current_network = zc.network_identity()
                if current_network != network:
                    token = self.runtime.request(expected=token)
                    if token is None:
                        continue
                    self.post(self._invalidate_health)
                    network = current_network
                    self._recovery_failed.clear()
                    failures, healthy = {}, 0
                    episode_token = token
                if not self._managed_bypass_running():
                    self._recover(False, token)
                    continue
                services = self.cfg.get("recovery_services") or ["discord", "youtube"]
                hosts = {key: zc.AUTO_QUICK_HOST[key] for key in services if key in zc.AUTO_QUICK_HOST}
                results = zc.check_hosts(list(hosts.values()), 3, 1)
                if not self.runtime.valid(token):
                    continue
                failures = {key: 0 if results[host][0] else failures.get(key, 0) + 1
                            for key, host in hosts.items()}
                if all(results[h][0] for h in hosts.values()):
                    healthy += 1
                    if healthy >= 2:
                        self._recovery_failed.clear()
                else:
                    healthy = 0
                if any(n >= zc.WATCHDOG_FAIL_THRESHOLD for n in failures.values()):
                    if not any(results[h][0] for h in hosts.values()):
                        controls = zc.check_hosts(["www.microsoft.com", "www.cloudflare.com"], 3, 1)
                        if not any(ok for ok, _ in controls.values()):
                            self.log_msg("[watchdog] Нет подтверждения доступа к интернету; перебор приостановлен.")
                            continue
                    self._recover(True, token)
                    failures = {}
            except Superseded:
                continue
            except Exception as exc:
                self.log_msg(f"[watchdog] Ошибка: {exc}")

    def _recovery_hosts(self):
        services = self.cfg.get("recovery_services") or ["discord", "youtube"]
        return [h for key in services if key in zc.AUTO_TARGETS for h in zc.AUTO_TARGETS[key]]

    def _watchdog_restart(self, token=None):
        token = token or self.runtime.token
        with self.runtime.transition(token):
            if self.runtime.desired == "stopped":
                return False
            if self.runtime.desired == "service":
                zc.set_service_running(False)
                self.runtime.check(token)
                zc.set_service_running(True)
            elif self.active_args:
                self._stop_local_winws(clear=False)
                self.runtime.check(token)
                self._spawn_winws(self.active_args, self.active_preset_name)
            else:
                return False
        if not self.runtime.wait(token, zc.FULL_WAIT):
            return False
        results = zc.check_hosts(self._recovery_hosts(), 3, 1)
        self.runtime.check(token)
        return self._managed_bypass_running() and all(ok for ok, _ in results.values())

    def _recover(self, switch, token=None):
        self._recovery_busy = True
        self.post(self._invalidate_health)
        try:
            return self._recover_impl(switch, token)
        finally:
            self._recovery_busy = False
            self.post(self.on_health_check)

    def _recover_impl(self, switch, token=None):
        token = token or self.runtime.token
        if self.runtime.desired == "stopped":
            return
        self.runtime.check(token)
        if switch:
            original = self.active_preset_name or self.cfg.get("strategy")
            pool = self.cfg.get("recovery_pool", []) or []
            candidates = [n for n in pool if n in self.preset_by_name
                          and n != original and n not in self._recovery_failed]
            if self.cfg.get("auto_research_on_fail"):
                candidates += [p["name"] for p in self.presets if p["name"] not in candidates
                               and p["name"] != original and p["name"] not in self._recovery_failed]
            if original not in self.preset_by_name:
                self.log_msg("[watchdog] Неизвестна исходная стратегия; автоматическая смена отложена.")
                return
            for name in candidates:
                self.runtime.check(token)
                committed = False
                try:
                    if self._switch_to(name, token, persist=False):
                        results = zc.measure_hosts(self._recovery_hosts(), 3, 3,
                                                   lambda: not self.runtime.valid(token))
                        self.runtime.check(token)
                        if results and not any(r.successes for r in results.values()):
                            controls = zc.check_hosts(["www.microsoft.com", "www.cloudflare.com"], 3, 1)
                            self.runtime.check(token)
                            if not any(ok for ok, _ in controls.values()):
                                self.log_msg("[watchdog] Связь пропала во время проверки; перебор приостановлен.")
                                return  # finally restores the original strategy, without blacklisting.
                        if self._managed_bypass_running() and all(r.reliable for r in results.values()):
                            self.cfg["strategy"] = name
                            zc.update_config({"strategy": name})
                            self.post(lambda n=name: self.strategy_var.set(n))
                            committed = True
                            self._notify("Обход восстановлен", f"Проверена стратегия «{name}».")
                            return
                except Superseded:
                    raise
                except Exception as exc:
                    self.log_msg(f"[watchdog] {name}: {exc}")
                finally:
                    if self.runtime.valid(token) and not committed:
                        if not self._switch_to(original, token, persist=False):
                            raise RuntimeError("Не удалось восстановить исходную стратегию")
                self._recovery_failed.add(name)
            self.log_msg("[watchdog] Подходящая замена не найдена; исходная стратегия сохранена.")
            return
        if getattr(self, "_restart_exhausted", None) is token:
            return
        for delay in (5, 15, 45):
            if not self.runtime.wait(token, delay):
                return
            try:
                if self._watchdog_restart(token):
                    self._notify("Обход восстановлен", "Запуск и доступность сервисов подтверждены.")
                    return
            except Superseded:
                return
            except Exception as exc:
                self.log_msg(f"[watchdog] Перезапуск: {exc}")
        self._restart_exhausted = token
        self._notify("Не удалось восстановить обход", "Три попытки не удались. Проверьте подключение и журнал.")

    def _switch_to(self, name, token=None, persist=True):
        token = token or self.runtime.token
        preset = self.preset_by_name.get(name)
        if not preset or self.runtime.desired == "stopped":
            return False
        args = zc.build_args_str(preset["args"], zc.get_game_mode())
        self.post(self._invalidate_health)
        with self.runtime.transition(token):
            if self.runtime.desired == "service":
                ok, log = zc.install_service(name, preset["args"], zc.get_game_mode(),
                                             cancelled=lambda: not self.runtime.valid(token))
                if not ok:
                    raise RuntimeError(log)
                self.active_args, self.active_preset_name = args, name
            else:
                self._stop_local_winws(clear=False)
                self.runtime.check(token)
                self._spawn_winws(args, name)
            self.runtime.check(token)
            if persist:
                self.cfg["strategy"] = name
                zc.update_config({"strategy": name})
                self.post(lambda: self.strategy_var.set(name))
        if not self.runtime.wait(token, zc.FULL_WAIT):
            return False
        self.post(self.refresh_status)
        if persist:
            self.post(self.on_health_check)
        return self._managed_bypass_running()

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

        self._bg(worker)

    def on_open_logs(self):
        try:
            os.makedirs(zc.LOGS, exist_ok=True)
            os.startfile(zc.LOGS)
        except Exception as e:
            self.log_msg(str(e))

    def _on_appearance_change(self, value):
        mode = APPEARANCE.get(value, "light")
        self.cfg["appearance"] = mode
        zc.update_config({"appearance": self.cfg["appearance"]})
        ctk.set_appearance_mode(mode)
        # у акцента свой вариант под светлую/тёмную — пересчитать под новый режим
        # и пересобрать интерфейс (акценты хранятся как одиночные цвета режима)
        _apply_accent(self.cfg.get("accent_name", "Синяя"))
        self._rebuild_ui()
        self.log_msg(f"Тема: {value.lower()}.")

    def _on_theme_change(self, value):
        # акцент применяется СРАЗУ, без перезапуска — пересобираем интерфейс
        _apply_accent(value)
        self.cfg["accent_name"] = value
        zc.update_config({"accent_name": self.cfg["accent_name"]})
        self._rebuild_ui()
        self.log_msg(f"Акцент: {value}.")

    def _rebuild_ui(self):
        """Пересобрать сайдбар и страницы под новый акцент (на месте, без
        перезапуска процесса). Динамика восстанавливается после пересборки."""
        for builder in self._page_jobs.values():
            builder.close()
        self._page_jobs.clear()
        cur = getattr(self, "_current_page", "control")
        for attr in ("_sidebar", "container"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
        self.pages = {}
        self.nav_buttons = {}
        self.nav_badges = {}
        # убрать ссылки на виджеты разрушенных страниц: наборы страниц в простом
        # и полном режиме разные, и _cfgw не должен попадать в «мёртвые» виджеты
        for attr in ("ipset_label", "svc_label", "lists_label", "tg_dot",
                     "tg_title", "tg_sub", "dash_proxy_dot", "dash_proxy_lbl",
                     "pstat_conn", "pstat_traffic", "upd_label",
                     "simple_fix_btn", "simple_fix_lbl"):
            if hasattr(self, attr):
                delattr(self, attr)
        for attr in ("logbox", "log_filter_var", "log_count_lbl", "tree"):
            if hasattr(self, attr):
                delattr(self, attr)
        self._diag_loaded = False        # пересобранная диагностика авто-обновится
        self._init_ttk_style()
        self._build_layout()
        if self.auto_running or getattr(self, "_search_results", []):
            self._ensure_page("auto")
            counts = {key: len(zc.AUTO_TARGETS[key]) for key in self._auto_services}
            for result in self._search_results:
                self._auto_add_row(result.name, result.per_service, result.total, result.latency_ms, counts)
            self.btn_auto_start.configure(state="disabled" if self.auto_running else "normal")
            self.btn_auto_stop.configure(state="normal" if self.auto_running else "disabled")
        self._show_page(cur)
        if self._status_snapshot is not None:
            self._apply_status(*self._status_snapshot)
        self.refresh_status()
        self._set_diag_badge(getattr(self, "_diag_bad_count", 0))   # вернуть бейдж

    def on_add_defender_exclusion(self):
        self.log_msg("Добавляю папку в исключения Windows Defender…")

        def worker():
            ok, msg = zc.add_defender_exclusion(zc.BASE)
            self.log_msg(("Defender: " if ok else "[!] Defender: ") + msg)

        self._bg(worker)

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
    def _sync_autostart_switch(self):
        """Подтянуть реальное состояние задачи автозапуска в тумблер
        (PowerShell ~0.5с — в фоне, не блокируя интерфейс)."""
        def worker():
            on = zc.autostart_enabled()

            def apply():
                try:
                    (self.full_autostart_switch.select() if on
                     else self.full_autostart_switch.deselect())
                except Exception:
                    pass
            self.post(apply)

        self._bg(worker)

    def _repair_autostart_bg(self):
        """Самопочинка задачи автозапуска: если она есть, но указывает на старый
        путь (после обновления/переноса) — перерегистрировать под текущий exe.
        Раньше это приходилось делать вручную (выкл/вкл тумблер)."""
        if self._closing:
            return

        def worker():
            try:
                if zc.repair_autostart():
                    self.log_msg("Автозапуск с Windows: задача обновлена под текущий "
                                 "путь приложения (после обновления/переноса).")
                    self.post(self._sync_autostart_switch)
            except Exception:
                pass

        self._bg(worker)

    def _on_full_autostart_toggle(self):
        on = bool(self.full_autostart_switch.get())
        self.log_msg("Настройка полного автозапуска…")

        def worker():
            if on:
                ok, msg = zc.enable_autostart()
                if ok:
                    self.log_msg("Полный автозапуск включён: приложение, обход и "
                                 "Telegram-прокси будут стартовать при входе в систему.")
                else:
                    self.log_msg(f"[!] Автозапуск: {msg}")
                    self.post(lambda: self.full_autostart_switch.deselect())
            else:
                zc.disable_autostart()
                self.log_msg("Полный автозапуск выключен.")

        self._bg(worker)

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
        dot = _pick(GREEN if running else MUTED)
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
                    lambda i: "Остановить обход" if self._managed_bypass_running()
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
        if self._managed_bypass_running():
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

        self._bg(worker)

    # -- здоровье обхода -------------------------------------------------- #
    def on_health_check(self):
        if self._health_busy or self._closing or self.auto_running or self._stop_busy or self.runtime.busy or self._recovery_busy:
            return
        self._health_busy = True
        self._health_token = self.runtime.token
        self._health_state = "Проверяется"
        self._render_health()
        self._bg(self._health_worker)

    def _health_worker(self):
        token = self._health_token
        try:
            hosts = {k: zc.AUTO_QUICK_HOST[k] for k in ("discord", "youtube", "google")}
            results = zc.check_hosts(list(hosts.values()), 3, attempts=1)
            out = {k: results[h] for k, h in hosts.items()}
            self.post(lambda: self._finish_health(token, out))
        except Exception as exc:
            self.post(lambda detail=str(exc): self._finish_health(token, None, detail))

    def _finish_health(self, token, out, error=None):
        self._health_busy = False
        if not self.runtime.valid(token) or self.auto_running or self.runtime.busy or self._recovery_busy:
            self._invalidate_health()
            return
        if error:
            self._apply_health_failure(error)
        else:
            self._apply_health(out)

    def _invalidate_health(self):
        self._health_state = "Устарело"
        self._health_summary = "Нужна новая проверка"
        self._render_health()

    def _render_health(self):
        self._cfgw("health_summary_lbl", text=self._health_summary)
        self._cfgw("btn_health_check", state="disabled" if self._health_busy else "normal",
                   text="Проверяю…" if self._health_busy else "Проверить связь")
        for key, (dot, value, _) in getattr(self, "health_widgets", {}).items():
            ok, ms = self._health_snapshot.get(key, (False, None))
            fresh = self._health_state == "Готово"
            dot.configure(text_color=(GREEN if ok else RED) if fresh else MUTED)
            text = (f"{round(ms)} мс" if ms is not None else "Доступен") if ok else "Недоступен"
            value.configure(text=text if fresh else self._health_state)

    def _apply_health(self, out):
        self._health_busy = False
        self._health_snapshot = dict(out)
        self._health_state = "Готово"
        self._health_checked_at = time.time()
        available = sum(ok for ok, _ in out.values())
        self._health_summary = f"TLS · {available}/{len(out)} · {time.strftime('%H:%M')}"
        self._render_health()
        self.refresh_status()

    def _apply_health_failure(self, detail):
        self._health_busy = False
        self._health_state = "Ошибка проверки"
        self._health_summary = "Проверка связи не удалась"
        self._render_health()
        self.log_msg(f"[Проверка связи] {detail}")
        self.refresh_status()

    def _health_auto(self):
        if self._closing:
            return
        self.on_health_check()
        self.after(20000, self._health_auto)

    def _startup_lists_check(self):
        """Раз в неделю (если включено) подтянуть свежие списки и IPSet из upstream."""
        if self._closing or not zc.lists_update_due():
            return
        self.log_msg("[Списки] плановое автообновление (списки + IPSet)…")
        self.on_update_lists(silent=True, restart_if_running=True, include_ipset=True)

    def _set_diag_badge(self, n_bad):
        # красная точка на пункте «Диагностика» при критичных проблемах
        self._diag_bad_count = n_bad
        b = self.nav_badges.get("diag")
        if b is not None:
            try:
                b.configure(text="●" if n_bad else "")
            except Exception:
                pass

    def _startup_diag_badge(self):
        # фоновая проверка при запуске для бейджа (на странице ещё ничего не строим)
        if self._closing:
            return

        def worker():
            try:
                n_bad = sum(1 for it in zc.diagnose() if it["status"] == "bad")
            except Exception:
                n_bad = 0
            self.post(lambda: self._set_diag_badge(n_bad))

        self._bg(worker)

    def _refresh_lists_label(self):
        ts = zc.lists_last_update_ts()
        if ts:
            txt = "обновлено " + time.strftime("%d.%m.%Y", time.localtime(ts))
        else:
            txt = "встроенные"
        try:
            self.lists_label.configure(text=txt)
        except Exception:
            pass

    def on_update_lists(self, silent=False, restart_if_running=False, include_ipset=False):
        if self.auto_running or self._recovery_busy:
            self.log_msg("Обновление списков отложено до завершения проверки стратегий.")
            return
        token = self.runtime.token
        if not silent:
            self.log_msg("Обновление списков доменов из upstream…")

        def worker():
            ok, msg = zc.update_lists()
            self.log_msg(msg)
            # планово обновляем и ipset-all (если фильтр не выключен вручную)
            ipset_ok = False
            if include_ipset and zc.ipset_enabled():
                iok, imsg = zc.update_ipset()
                self.log_msg(imsg)
                ipset_ok = iok
            self.post(self._refresh_lists_label)
            self.post(self.refresh_status)
            if ok or ipset_ok:
                self._notify("Списки обновлены",
                             "Списки сайтов" + (" и IPSet" if ipset_ok else "")
                             + " для обхода обновлены.")
                if restart_if_running and (
                        (self.proc and self.proc.poll() is None) or zc.service_running()):
                    self.log_msg("Перезапуск обхода для применения обновлений…")
                    restart_token = self.runtime.request(expected=token)
                    if restart_token is not None:
                        self.post(self._invalidate_health)
                        self._watchdog_restart(restart_token)

        self._bg(worker)

    def on_diagnostics(self):
        # кнопка из «Инструментов» открывает страницу диагностики и запускает проверку
        self._show_page("diag")
        self.on_diag_run()

    def on_test(self):
        self.log_msg("Проверка доступности Discord, YouTube и Google…")
        self.on_health_check()

    def _startup_update_check(self):
        if not zc.get_update_enabled():
            return

        def worker():
            info = zc.check_update()

            def show():
                if info.get("error"):
                    return
                if info.get("available"):
                    self._cfgw("upd_label", text=f"есть {info['latest']}")
                    self.log_msg(f"[Обновление] доступна версия {info['latest']} — "
                                 "раздел «Настройки» → «Проверить».")
                    if self._simple_mode():
                        # в простом режиме страницы настроек нет — предложить сразу
                        self._show_update_result(info)
                else:
                    self._cfgw("upd_label", text=f"актуально ({info.get('current','')})")
            self.post(show)

        self._bg(worker)

    def on_check_update(self):
        self._cfgw("upd_label", text="проверка…")
        self.log_msg("Проверка обновлений приложения…")

        def worker():
            info = zc.check_update()
            self.post(lambda: self._show_update_result(info))

        self._bg(worker)

    def _show_update_result(self, info):
        if info.get("error"):
            self._cfgw("upd_label", text="ошибка")
            self.log_msg(f"[Обновление] ошибка: {info['error']}")
            return
        if not info.get("available"):
            self._cfgw("upd_label", text=f"актуально ({info['current']})")
            self.log_msg(f"[Обновление] установлена последняя версия: {info['current']}")
            return
        self._cfgw("upd_label", text=f"есть {info['latest']}")
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
        if not info.get("digest"):
            messagebox.showwarning("Обновление", msg + "Нет контрольной суммы. Откройте релиз для ручной установки.")
            self._open_link(zc.GITHUB_RELEASES_PAGE)
            return
        if messagebox.askyesno("Обновление", msg + "Скачать и установить сейчас?"):
            self._do_update(info["url"], info.get("size", 0), info.get("digest", ""))

    def _do_update(self, url, size=0, digest=""):
        if self.auto_running or getattr(self, "_update_busy", False):
            self.log_msg("Дождитесь завершения текущего поиска или обновления.")
            return
        self._update_busy = True
        self.log_msg("Скачивание обновления…")
        self._cfgw("upd_label", text="скачивание…")

        def worker():
            dest = os.path.join(os.environ.get("TEMP", zc.BASE), "ZapretControl_update.zip")
            last = [0]

            def prog(fr):
                pct = int(fr * 100)
                if pct >= last[0] + 10:
                    last[0] = pct
                    self.log_msg(f"  скачано {pct}%")

            try:
                zc.download_update(url, dest, progress_cb=prog, expected_size=size, expected_digest=digest)
                self.log_msg("Загрузка завершена. Установка и перезапуск…")
                zc.apply_update(dest, expected_digest=digest)
                self.post(self._quit_for_update)
            except Exception as e:
                self._update_busy = False
                self.log_msg(f"[ОШИБКА] обновление: {e}")
                self.post(lambda: self._cfgw("upd_label", text="ошибка"))

        self._bg(worker)

    def _quit_for_update(self):
        self.log_msg("Закрываю приложение для применения обновления…")
        self._closing = True
        zc.tg_proxy_stop()
        if self.tray is not None:
            self.tray.stop()
        if self._logf:
            self._logf.close()
        self.destroy()

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
    def _tg_start_verified(self, ok_msg="Прокси запущен.", on_ok=None):
        """Запустить встроенный прокси в фоне и честно доложить результат
        в журнал (общий код для кнопки, автозапуска и «Открыть в Telegram»)."""
        def worker():
            try:
                zc.tg_proxy_start()
                time.sleep(1.3)
            except Exception as e:
                self.log_msg(f"[ОШИБКА] Telegram-прокси: {e}")
                return
            if zc.tg_proxy_running():
                self.log_msg(ok_msg)
                if on_ok:
                    self.post(on_ok)
            else:
                self.log_msg("[ОШИБКА] прокси не запустился: "
                             + (zc.tg_last_error() or "возможно, порт занят"))
            self.post(self.refresh_status)

        self._bg(worker)

    def on_tg_start(self):
        self.log_msg(f"Запуск встроенного Telegram-прокси на "
                     f"{zc.TG_DEFAULT_HOST}:{zc.tg_get_port()}…")
        self._tg_start_verified(
            ok_msg="Прокси запущен. Нажмите «Открыть в Telegram» или «Скопировать».")

    def on_tg_stop(self):
        self.log_msg("Остановка Telegram-прокси…")

        def worker():
            zc.tg_proxy_stop()
            self.log_msg("Telegram-прокси остановлен.")
            self.post(self.refresh_status)

        self._bg(worker)

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
        self.log_msg(f"Порт прокси: {port}.")
        if zc.tg_proxy_running():
            self._tg_restart()      # применить новый порт сразу

    def on_tg_regen(self):
        zc.tg_regenerate_secret()
        self.tg_link_var.set(zc.tg_proxy_url())
        self.log_msg("Секрет прокси обновлён — обновите ссылку в Telegram.")
        if zc.tg_proxy_running():
            self._tg_restart()      # применить новый секрет сразу

    def _tg_restart(self):
        """Перезапустить встроенный прокси (для применения настроек на лету)."""
        def worker():
            if zc.tg_proxy_running():
                zc.tg_proxy_stop()
                time.sleep(0.6)
                zc.tg_proxy_start()
                self.log_msg("Прокси перезапущен.")
            self.post(self.refresh_status)
        self._bg(worker)

    def _on_cfproxy_toggle(self):
        on = bool(self.cfproxy_switch.get())
        zc.tg_set_cfproxy(on)
        self.log_msg(("Запасной Cloudflare-прокси включён." if on
                      else "Запасной Cloudflare-прокси выключен (меньше обрывов, "
                           "если прямые соединения работают)."))
        self._tg_restart()

    def _on_doh_toggle(self):
        on = bool(self.doh_switch.get())
        legacy_mode = None
        cfg = zc.load_config()
        if not on and not cfg.get("doh_snapshot") and cfg.get("doh_prev"):
            choice = messagebox.askyesnocancel(
                "Восстановление DNS", "Старая версия сохранила адреса DNS, но не способ их получения.\n\n"
                "Да — получать DNS автоматически (DHCP).\nНет — вернуть сохранённые адреса вручную.\n"
                "Отмена — оставить настройки без изменений.")
            if choice is None:
                self._restore_doh_controls()
                return
            legacy_mode = "automatic" if choice else "static"
        prov = {"Cloudflare": "cloudflare", "Google": "google"}[self.doh_provider.get()]
        self.log_msg(("Включаю" if on else "Выключаю") + " шифрованный DNS (DoH)…")
        self.doh_switch.configure(state="disabled")

        def worker():
            try:
                if on:
                    zc.doh_enable(prov)
                    self.log_msg(f"DoH включён ({prov}): системный DNS переведён на провайдера.")
                else:
                    zc.doh_disable(legacy_mode=legacy_mode)
                    self.log_msg("DoH выключен: прежний DNS восстановлен.")
            except Exception as e:
                self.log_msg(f"[ОШИБКА] DNS: {e}")
                self.post(self._restore_doh_controls)
                return
            self.post(self._restore_doh_controls)

        self._bg(worker)

    def _restore_doh_controls(self):
        status = zc.doh_status()
        if status["enabled"]:
            self.doh_switch.select()
        else:
            self.doh_switch.deselect()
        self.doh_provider.set({"cloudflare": "Cloudflare", "google": "Google"}.get(
            status["provider"], "Cloudflare"))
        self.doh_switch.configure(state="normal")

    def _on_doh_provider_change(self, _value=None):
        # если DoH уже включён — сразу переключить DNS на нового провайдера
        if not self.doh_switch.get():
            return
        prov = {"Cloudflare": "cloudflare", "Google": "google"}[self.doh_provider.get()]
        self.log_msg(f"Смена DNS-провайдера на {prov}…")
        self.doh_switch.configure(state="disabled")

        def worker():
            try:
                zc.doh_enable(prov)
                self.log_msg(f"DNS-провайдер изменён: {prov}.")
            except Exception as e:
                self.log_msg(f"[ОШИБКА] DNS: {e}")
            finally:
                self.post(self._restore_doh_controls)

        self._bg(worker)

    def on_tg_copy(self):
        link = zc.tg_proxy_url()
        try:
            self.clipboard_clear()
            self.clipboard_append(link)
            self.log_msg("Ссылка прокси скопирована в буфер обмена.")
        except Exception as e:
            self.log_msg(f"[ОШИБКА] копирование: {e}")

    def _open_link(self, link):
        try:
            os.startfile(link)
        except Exception:
            try:
                import webbrowser
                webbrowser.open(link)
            except Exception as e:
                self.log_msg(f"[ОШИБКА] открытие ссылки: {e}")

    def on_tg_open(self):
        link = zc.tg_proxy_url()
        if zc.tg_proxy_running():
            self._open_link(link)
            return
        # прокси не запущен — запускаем его сами и открываем ссылку после
        self.log_msg("Прокси не запущен — запускаю и открываю Telegram…")
        self._tg_start_verified(on_ok=lambda: self._open_link(link))

    # -- авто-поиск (двухфазный) ------------------------------------------ #
    def on_auto_start(self):
        if self.auto_running or self._stop_busy:
            return
        services = [s for s in ("discord", "youtube", "google") if self.svc_vars[s].get()]
        if not services:
            messagebox.showwarning("Авто-поиск", "Выберите хотя бы один сервис.")
            return
        if not self.presets:
            messagebox.showwarning("Авто-поиск", "Не найдено ни одного пресета.")
            return
        self._previous_search = (list(getattr(self, "_search_results", [])),
                                 list(getattr(self, "_auto_services", [])), self.auto_total_targets)
        self.auto_total_targets = sum(len(zc.AUTO_TARGETS[s]) for s in services)
        self.auto_best = None
        self._auto_full_pass = []
        self.auto_cancel = False
        self._auto_completed = False
        self._search_results = []
        self._auto_token = self.runtime.request()
        self._auto_services = list(services)
        self._invalidate_health()
        self.auto_running = True
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.btn_auto_start.configure(state="disabled")
        self.btn_auto_stop.configure(state="normal")
        self.btn_apply_best.configure(state="disabled")
        self.btn_install_best.configure(state="disabled")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.auto_bar.set(0)
        self.auto_fast = bool(self.fast_var.get())
        self._auto_thread = threading.Thread(target=self._auto_worker, args=(list(self.presets), services), daemon=True)
        self._auto_thread.start()

    def _on_fast_toggle(self):
        self.cfg["auto_fast"] = bool(self.fast_var.get())
        zc.update_config({"auto_fast": self.cfg["auto_fast"]})

    def on_auto_stop(self):
        if self.auto_running:
            self.auto_cancel = True
            self.auto_phase_lbl.configure(text="останавливаю…")
            self.log_msg("Авто-поиск: запрошена остановка…")

    def _auto_sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._search_cancelled():
                return False
            time.sleep(0.1)
        return not self._search_cancelled()

    def _search_cancelled(self):
        return self.auto_cancel or self._closing or not self.runtime.valid(self._auto_token)

    def _auto_worker(self, presets, services):
        token = self._auto_token
        mode = zc.get_game_mode()
        original_args, original_name = self.active_args, self.active_preset_name
        svc_was_running = False
        own_running = False
        interrupted = False
        context = None
        counts = {s: len(zc.AUTO_TARGETS[s]) for s in services}
        targets = [(s, h) for s in services for h in zc.AUTO_TARGETS[s]]
        try:
            with self.runtime.transition(token):
                svc_was_running = zc.service_running()
                own_running = bool(self.proc and self.proc.poll() is None)
                if zc.winws_running() and not (own_running or svc_was_running):
                    raise RuntimeError("Найден winws.exe другой программы; поиск отменён.")
                if svc_was_running:
                    zc.update_config({"svc_stopped_for_search": True})
                    self.cfg["svc_stopped_for_search"] = True
                    zc.set_service_running(False)
                interrupted = True
                context = zc.recovery_context(presets, services)
                self._stop_local_winws(clear=False)
                trial_counts = {1: 0, 3: 0}

                def trial(preset, hosts, samples):
                    self.runtime.check(token)
                    if self._search_cancelled():
                        raise Superseded("Поиск отменён")
                    trial_counts[samples] += 1
                    step = trial_counts[samples]
                    progress = (0.3 * step / len(presets) if samples == 1
                                else 0.3 + 0.7 * step / len(presets))
                    label = "Быстрая проверка" if samples == 1 else "Повторная проверка"
                    self.post(lambda f=progress, t=f"{label} {step}/{len(presets)} · {preset['name']}":
                              self._auto_prog(f, t))
                    try:
                        args = zc.build_args_str(preset["args"], mode)
                        self.runtime.trial = zc.start_winws_silent(args)
                        if not self._auto_sleep(zc.QUICK_WAIT if samples == 1 else zc.FULL_WAIT):
                            raise Superseded("Поиск отменён")
                        if self.runtime.trial.poll() is not None:
                            raise RuntimeError("winws завершился до проверки")
                        probes = zc.measure_hosts(hosts, zc.QUICK_TIMEOUT if samples == 1 else zc.FULL_TIMEOUT,
                                                  samples, self._search_cancelled)
                        if self.runtime.trial.poll() is not None:
                            raise RuntimeError("winws завершился во время проверки")
                        return probes
                    except Superseded:
                        raise
                    except (OSError, RuntimeError) as exc:
                        self.log_msg(f"[{preset['name']}] {exc}")
                        from zapret_measurements import ProbeResult
                        return {h: ProbeResult(samples, 0, None, str(exc), time.time()) for h in hosts}
                    finally:
                        zc.stop_process(self.runtime.trial)
                        self.runtime.trial = None

                def publish(result):
                    self._search_results.append(result)
                    self.post(lambda r=result: self._auto_add_row(
                        r.name, r.per_service, r.total, r.latency_ms, counts))
                ordered = zc.prioritize_presets(presets, self.cfg.get("strategy"), self.cfg.get("recovery_pool"))
                results = run_search(ordered, targets, [zc.AUTO_QUICK_HOST[s] for s in services],
                                     trial, self._search_cancelled, self.auto_fast, publish)
                if results is not None and not self._search_cancelled():
                    final_context = zc.recovery_context(presets, services)
                    if any(context.get(k) != final_context.get(k) for k in context if k != "checked_at"):
                        raise RuntimeError("Условия проверки изменились. Повторите поиск.")
                    self._auto_context = final_context
                    self._search_results = results
                    self._auto_completed = True
        except Superseded:
            self.auto_cancel = True
        except Exception as exc:
            self.auto_cancel = True
            self.log_msg(f"[Авто-поиск] {exc}")
        finally:
            try:
                with self.runtime.lock:
                    zc.stop_process(self.runtime.trial)
                    self.runtime.trial = None
                    if self.runtime.valid(token):
                        if svc_was_running:
                            zc.set_service_running(True)
                            zc.update_config({}, remove=("svc_stopped_for_search",))
                            self.cfg.pop("svc_stopped_for_search", None)
                        elif interrupted and own_running and original_args:
                            self._spawn_winws(original_args, original_name)
            except Exception as exc:
                self.auto_cancel = True
                self._auto_completed = False
                self.log_msg(f"[Восстановление после поиска] {exc}")
            if not self._auto_completed:
                self.auto_cancel = True
            self.post(self._auto_done)

    def _auto_prog(self, frac, text):
        self.auto_bar.set(max(0.0, min(1.0, frac)))
        self.auto_phase_lbl.configure(text=text)
        self._cfgw("simple_fix_lbl", text=text)   # зеркало на простой странице

    def _auto_add_row(self, name, per, total, avg_lat, counts):
        def cell(s):
            return f"{per.get(s, 0)}/{counts[s]}" if s in counts else "—"

        ms = "—" if avg_lat is None else str(round(avg_lat))
        total_str = f"{total}/{self.auto_total_targets}"
        tag = "good" if total == self.auto_total_targets else ("partial" if total > 0 else "bad")
        values = (name, cell("discord"), cell("youtube"), cell("google"), total_str, ms)
        if self.tree.exists(name):
            self.tree.item(name, values=values, tags=(tag,))
        else:
            self.tree.insert("", "end", iid=name, values=values, tags=(tag,))
        # дублируем в журнал точный результат фазы 2 — чтобы лог совпадал с
        # таблицей (раньше в лог попадал только быстрый отсев фазы 1, и цифры
        # выглядели противоречиво)
        self.log_msg(f"  фаза 2 (точно): {name} — {total_str}"
                     + (f" (~{ms} мс)" if avg_lat else ""))
    def _auto_done(self):
        self.auto_running = False
        if not self.runtime.valid(self._auto_token) or not self._auto_completed:
            self.auto_cancel = True
        if self._closing:
            return
        if not self.auto_cancel:
            complete = sorted((r for r in self._search_results if r.total == self.auto_total_targets),
                              key=lambda r: r.rank, reverse=True)
            pool = [r.name for r in complete]
            changes = {"recovery_pool": pool, "recovery_services": self._auto_services,
                       "recovery_context": self._auto_context,
                       **({"auto_recovery": True, "last_working_strategy": pool[0]} if pool else {})}
            try:
                zc.update_config(changes)
            except Exception as exc:
                self.log_msg(f"[Авто-поиск] Не удалось сохранить результаты: {exc}")
                self.auto_cancel = True
                self._auto_completed = False
            else:
                self.cfg.update(changes)
                self._recovery_failed.clear()
        if self.auto_cancel and getattr(self, "_previous_search", None) is not None:
            self._search_results, self._auto_services, self.auto_total_targets = self._previous_search
            self._ensure_page("auto")
            for item in self.tree.get_children():
                self.tree.delete(item)
            counts = {s: len(zc.AUTO_TARGETS[s]) for s in self._auto_services}
            for result in self._search_results:
                self._auto_add_row(result.name, result.per_service, result.total, result.latency_ms, counts)
        self._previous_search = None
        results = sorted(self._search_results, key=lambda r: r.rank, reverse=True)
        best = next((r for r in results if r.total > 0), None)
        self.auto_best = (best.name, best.total, best.latency_ms) if best else None
        self._auto_full_pass = [(r.name, r.latency_ms if r.latency_ms is not None else 1e9)
                                for r in results if r.total == self.auto_total_targets]
        self._ensure_page("auto")
        self.btn_apply_best.configure(state="normal" if self.auto_best else "disabled")
        self.btn_install_best.configure(state="normal" if self.auto_best else "disabled")
        if self.auto_cancel:
            self._auto_autoapply = False
            self.btn_auto_start.configure(state="normal")
            self.btn_auto_stop.configure(state="disabled")
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="normal")
            self.auto_phase_lbl.configure(text="Поиск отменён")
            self._cfgw("simple_fix_btn", state="normal")
            self._cfgw("simple_fix_lbl", text="Поиск отменён")
            self.log_msg("Авто-поиск отменён. Запасные стратегии сохранены.")
            self.refresh_status()
            return
        self.btn_auto_start.configure(state="normal")
        self.btn_auto_stop.configure(state="disabled")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="normal")
        self.auto_bar.set(1.0)
        self.auto_phase_lbl.configure(text="готово")
        self._cfgw("simple_fix_btn", state="normal")
        self._cfgw("simple_fix_lbl",
                   text=(f"готово: {self.auto_best[0]}" if self.auto_best
                         else "рабочая стратегия не нашлась"))
        # пул запасных рабочих стратегий (для авто-восстановления), лучшие первыми
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
                            round(avg) if avg is not None else "?"))
            for item in self.tree.get_children():
                if self.tree.item(item, "values")[0] == name:
                    self.tree.item(item, tags=("best",))
                    self.tree.see(item)
                    break
        else:
            self.log_msg("=== Авто-поиск завершён: рабочих стратегий не найдено ===")
        # авто-применение (когда поиск запущен watchdog'ом из-за деградации)
        if getattr(self, "_auto_autoapply", False):
            self._auto_autoapply = False
            if self.auto_best:
                name = self.auto_best[0]
                self.log_msg(f"[watchdog] применяю найденную стратегию «{name}»")
                token = self._auto_token
                self._bg(lambda n=name: self._switch_to(n, token))
            else:
                self._notify("Авто-поиск", "Рабочая стратегия не найдена.")
        self.refresh_status()

    def on_apply_best(self):
        if self.auto_running or not self.auto_best:
            return
        name = self.auto_best[0]
        self.strategy_var.set(name)
        self._on_strategy_pick()
        self._show_page("control")
        # реально применить: раньше кнопка только меняла выбор, и работающий
        # обход/служба оставались на старой стратегии (видно было по логам)
        if self._managed_bypass_running():
            token = self.runtime.request("service" if zc.service_running() else "manual")
            self.log_msg(f"Применяю «{name}» к работающему обходу…")
            self._bg(lambda: self._switch_to(name, token))
        else:
            self.on_start()

    def on_restore_last_working(self):
        name = self.cfg.get("last_working_strategy")
        if name not in self.preset_by_name:
            messagebox.showinfo("Zapret", "Нет полностью проверенной стратегии для восстановления.")
            return
        self.strategy_var.set(name)
        self._on_strategy_pick()
        self.log_msg(f"Возвращаю последнюю полностью проверенную стратегию: «{name}».")
        if self._managed_bypass_running():
            token = self.runtime.request("service" if zc.service_running() else "manual")
            self._bg(lambda: self._switch_to(name, token))
        else:
            self.on_start()

    def on_install_best(self):
        if self.auto_running or not self.auto_best:
            return
        self.strategy_var.set(self.auto_best[0])
        self._show_page("control")
        self.on_install_service()

    # -------------------------------------------------------------------- #
    def _real_quit(self):
        if self._closing:
            return
        stop_manual = False
        if (self.proc and self.proc.poll() is None) or (self.auto_running and self.runtime.desired == "manual"):
            stop_manual = messagebox.askyesno("Выход", "Обход запущен. Остановить при выходе?")
        self.auto_cancel = True
        self._closing = True
        if stop_manual:
            self.runtime.request("stopped")
        if not self.auto_running:
            self.runtime.close()
        errors = []
        # Waiting for processes and service transitions must not block Tk.
        def cleanup():
            thread = getattr(self, "_auto_thread", None)
            if thread:
                thread.join()
            self.runtime.close()
            try:
                with self.runtime.lock:
                    zc.stop_process(self.runtime.trial)
                    self.runtime.trial = None
                    if stop_manual:
                        self._stop_local_winws()
            except Exception as exc:
                errors.append(str(exc))
            try:
                zc.tg_proxy_stop()
            except Exception as exc:
                errors.append(str(exc))
            if self.tray is not None:
                try:
                    self.tray.stop()
                except Exception as exc:
                    errors.append(str(exc))
        shutdown = threading.Thread(target=cleanup, daemon=True)
        shutdown.start()

        def finish():
            if shutdown.is_alive():
                self.after(100, finish)
                return
            if errors:
                messagebox.showwarning("Завершение работы", "Не всё удалось остановить:\n" + "\n".join(errors))
            try:
                if self._logf:
                    self._logf.close()
            except OSError:
                pass
            self.destroy()
        finish()


_SINGLETON = None


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        # Exercises the packaged GUI/crypto stack without starting bypass or changing settings.
        import json
        report = {"version": zc.APP_VERSION, "ok": False}
        root = None
        try:
            import tgproxy.tg_ws_proxy
            from tgproxy import __version__ as proxy_version
            from zapret_measurements import ProbeResult
            root = ctk.CTk()
            root.withdraw()
            root.update_idletasks()
            zc._verify_tls_context()
            bundled = os.path.join(zc._meipass(), "presets.json")
            with open(bundled, encoding="utf-8") as source:
                presets = zc.validate_presets(json.load(source))
            report.update(ok=True, proxy_version=proxy_version, presets=len(presets),
                          probe=ProbeResult(1, 1, 0, None, 0).reliable,
                          proxy_entrypoint=callable(tgproxy.tg_ws_proxy._run))
        except Exception as exc:
            report["error"] = str(exc)
        finally:
            if root is not None:
                root.destroy()
            with open(sys.argv[2], "w", encoding="utf-8") as target:
                json.dump(report, target)
        raise SystemExit(0 if report["ok"] else 1)
    zc.install_crash_logging()   # необработанные исключения -> logs/crash.log
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
        zc.refresh_defaults()   # досыл свежих апстрим-списков при обновлении версии
        # исключить Telegram из десинка winws (до автозапуска обхода), чтобы
        # обход не рвал соединения встроенного Telegram-прокси
        zc.ensure_telegram_bypass_exclude()
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
    app = ZapretApp(autostart=("--autostart" in sys.argv))
    if copied:
        app.log_msg(f"Развёрнуто встроенных файлов: {len(copied)} (папка: {zc.BASE})")
    app.mainloop()


if __name__ == "__main__":
    main()
