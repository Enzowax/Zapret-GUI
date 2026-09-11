"""Локальный предпросмотр без запуска обхода, сети и изменения настроек.

python utils/preview_ui.py --screenshots   # обновить снимки обеих тем
python utils/preview_ui.py                 # открыть интерактивный макет
"""
import argparse
import ctypes
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
loader = importlib.machinery.SourceFileLoader("zapret_ui_preview", str(ROOT / "zapret_app.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
ui = importlib.util.module_from_spec(spec)
loader.exec_module(ui)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs")
    args = parser.parse_args()
    config = {"first_run_done": True, "ui_mode": "simple", "appearance": "light",
              "accent_name": "Синяя", "tg_secret": "0" * 32}

    def update_config(changes, remove=()):
        config.update(changes)
        for key in remove:
            config.pop(key, None)
        return dict(config)

    with patch.object(ui.zc, "load_config", lambda: dict(config)), \
            patch.object(ui.zc, "update_config", update_config), \
            patch.object(ui.zc, "current_log_path", lambda: "NUL"), \
            patch.object(ui.zc, "service_running", lambda: False), \
            patch.object(ui.zc, "service_installed", lambda: False), \
            patch.object(ui.zc, "tg_proxy_stop", lambda: None), \
            patch.object(ui.ZapretApp, "_bg", lambda *a: None), \
            patch.object(ui.ZapretApp, "_setup_tray", lambda *a: None), \
            patch.object(ui.ZapretApp, "on_start", lambda *a: None), \
            patch.object(ui.ZapretApp, "on_stop", lambda *a: None), \
            patch.object(ui.ZapretApp, "on_health_check", lambda *a: None), \
            patch.object(ui.ZapretApp, "on_simple_fix", lambda *a: None), \
            patch.object(ui.ZapretApp, "on_tg_open", lambda *a: None), \
            patch.object(ui.ZapretApp, "on_tg_copy", lambda *a: None):
        app = ui.ZapretApp()
        app.title("Zapret GUI — предпросмотр интерфейса")
        app.protocol("WM_DELETE_WINDOW", app.destroy)

        def state():
            app._apply_status(False, False, False, "встроенный список", False)
            app._apply_health({"discord": (True, 42), "youtube": (False, None), "google": (True, 15)})

        def capture():
            from PIL import ImageGrab
            try:
                # Проверить построение всех страниц в обоих режимах и темах.
                for mode, theme, filename in (("simple", "light", "screenshot-simple.png"),
                                               ("advanced", "light", "screenshot-light.png"),
                                               ("advanced", "dark", "screenshot-dark.png")):
                    app.cfg.update(ui_mode=mode, appearance=theme)
                    ui.ctk.set_appearance_mode(theme)
                    ui._apply_accent("Синяя")
                    app._current_page = "control"
                    app._rebuild_ui()
                    for key in app._page_builders:
                        app._ensure_page(key)
                    while app._page_jobs:
                        app.update()
                    state()
                    app._rebuild_ui()
                    assert app.health_widgets["discord"][1].cget("text") == "42 мс"
                    assert app.health_widgets["youtube"][1].cget("text") == "Недоступен"
                    app.geometry("920x620")
                    app.update()
                    app.geometry("1120x780")
                    app.update()
                    app._show_page("control")
                    app.update()
                    hwnd = ctypes.windll.user32.GetParent(app.winfo_id())
                    args.output_dir.mkdir(parents=True, exist_ok=True)
                    ImageGrab.grab(window=hwnd).save(args.output_dir / filename)
                    print(f"UI OK: {mode}, {theme}, 920x620 and 1120x780", flush=True)
            finally:
                app._real_quit()

        state()
        if args.screenshots:
            app.after(500, capture)
        app.mainloop()


if __name__ == "__main__":
    main()
