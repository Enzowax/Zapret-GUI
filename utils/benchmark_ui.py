"""Reproducible Tk timings; no network, processes or persistent configuration."""
import argparse
from contextlib import ExitStack
import io
import json
from pathlib import Path
import statistics
import time
from unittest.mock import patch

from preview_ui import ui


def benchmark():
    result = {}
    for mode in ("simple", "advanced"):
        cfg = {"first_run_done": True, "ui_mode": mode, "appearance": "light", "tg_secret": "a" * 32}
        with ExitStack() as stack:
            for name, replacement in {
                "load_config": lambda: dict(cfg), "update_config": lambda *a, **k: dict(cfg),
                "current_log_path": lambda: "NUL", "service_running": lambda: False,
                "service_installed": lambda: False,
            }.items():
                stack.enter_context(patch.object(ui.zc, name, replacement))
            stack.enter_context(patch.object(ui.ZapretApp, "_bg", lambda *a: None))
            stack.enter_context(patch.object(ui.ZapretApp, "_setup_tray", lambda *a: None))
            start = time.perf_counter()
            app = ui.ZapretApp()
            app.withdraw()
            app.update_idletasks()
            created = (time.perf_counter() - start) * 1000
            try:
                start = time.perf_counter()
                app._show_page("settings")
                app.update_idletasks()
                settings = (time.perf_counter() - start) * 1000
                while app._page_jobs:
                    app.update()
                settings_complete = (time.perf_counter() - start) * 1000
                switches = []
                for _ in range(5):
                    start = time.perf_counter()
                    app._show_page("control")
                    app._show_page("settings")
                    app.update_idletasks()
                    switches.append((time.perf_counter() - start) * 1000)
                start = time.perf_counter()
                app._rebuild_ui()
                app.update_idletasks()
                rebuild = (time.perf_counter() - start) * 1000
                app._show_page("log")
                app._logf.close()
                app._logf = io.StringIO()
                for i in range(1000):
                    app.log_msg(f"Benchmark message {i}")
                start = time.perf_counter()
                app._poll_ui()
                log_tick = (time.perf_counter() - start) * 1000
                result[mode] = {"create_ms": round(created, 2), "settings_cold_ms": round(settings, 2),
                                "settings_complete_ms": round(settings_complete, 2),
                                "switch_pair_median_ms": round(statistics.median(switches), 2),
                                "theme_rebuild_ms": round(rebuild, 2), "log_tick_ms": round(log_tick, 2),
                                "log_remaining": app.log_queue.qsize()}
            finally:
                app._closing = True
                for timer in app.tk.call("after", "info"):
                    app.tk.call("after", "cancel", timer)
                app.destroy()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = benchmark()
    args.output.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(json.dumps(data, indent=2))
