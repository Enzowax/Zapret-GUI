"""Regression coverage for W1-W4, R1-R5 and S1-S7 from the audit.

No real service, network, DNS or process changes are allowed in these tests.
"""
import asyncio
import importlib.machinery
import importlib.util
from pathlib import Path
from types import SimpleNamespace, MethodType
import threading
import time
import queue
import io

import pytest
import zapret_core as zc
REAL_STOP_PROCESS = zc.stop_process
import zapret_measurements as measurements
from zapret_measurements import ProbeResult
from zapret_runtime import RuntimeController, Superseded
from zapret_search import SearchResult, run_search

loader = importlib.machinery.SourceFileLoader("regression_ui", str(Path(__file__).parents[1] / "zapret_app.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
ui = importlib.util.module_from_spec(spec)
loader.exec_module(ui)


class Widget:
    def __init__(self, *args, **kwargs):
        self.values = dict(kwargs)

    def configure(self, **kwargs):
        self.values.update(kwargs)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def app_stub(**overrides):
    app = SimpleNamespace(
        cfg={"strategy": "old", "recovery_pool": ["old", "new"], "auto_recovery": True},
        runtime=RuntimeController("manual"), proc=SimpleNamespace(poll=lambda: None),
        active_args=["--old"], active_preset_name="old", _closing=False, _stop_busy=False,
        auto_running=False, auto_cancel=False, auto_fast=False, _auto_completed=False,
        auto_best=None, auto_total_targets=3, _auto_full_pass=[], _search_results=[],
        _auto_autoapply=False, _recovery_failed=set(), _recovery_busy=False,
        _health_busy=False, _health_summary="Not checked", _health_checked_at=None,
        _health_state="Не проверено", _health_snapshot={},
        health_widgets={k: (Widget(), Widget(), k) for k in zc.AUTO_QUICK_HOST},
        events=[], callbacks=[], recoveries=[], _cfgw=lambda *args, **kw: None,
        refresh_status=lambda: None, _notify=lambda *args: None,
        _ensure_page=lambda *args: None,
        _card=lambda *args: Widget(), _btn=lambda *args, **kwargs: Widget(**kwargs),
        on_start=lambda: None, strategy_var=Widget(), tree=Widget(),
    )
    for name in ("btn_apply_best", "btn_install_best", "btn_auto_start", "btn_auto_stop",
                 "btn_start", "btn_stop", "auto_phase_lbl", "auto_bar"):
        setattr(app, name, Widget())
    for name in ("_health_worker", "_apply_health", "_apply_health_failure", "on_health_check",
                 "_finish_health", "_invalidate_health", "_render_health", "_health_auto",
                 "_auto_done", "_auto_prog", "_auto_worker", "_recover", "_recover_impl",
                 "_recovery_hosts", "_switch_to", "_watchdog_restart", "_search_cancelled",
                 "_auto_add_row", "on_stop", "_managed_bypass_running", "_poll_ui"):
        setattr(app, name, MethodType(getattr(ui.ZapretApp, name), app))
    app.post = app.callbacks.append
    app.log_msg = app.events.append
    app.after = lambda *args: None
    app._bg = lambda fn: fn()
    app._auto_sleep = lambda seconds: not app._search_cancelled()
    app._stop_local_winws = lambda **kw: setattr(app, "proc", None)
    app._spawn_winws = lambda args, name=None: setattr(app, "proc", SimpleNamespace(poll=lambda: None))
    app.presets = [{"name": n, "args": "--" + n} for n in ("old", "new")]
    app.preset_by_name = {p["name"]: p for p in app.presets}
    app._auto_token = app.runtime.token
    app._auto_services = ["discord"]
    app.__dict__.update(overrides)
    app._auto_token = app.runtime.token
    return app


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(zc, "get_game_mode", lambda: "off")
    monkeypatch.setattr(zc, "service_running", lambda: False)
    monkeypatch.setattr(zc, "service_installed", lambda: False)
    monkeypatch.setattr(zc, "winws_running", lambda: False)
    monkeypatch.setattr(zc, "network_identity", lambda: "network-a")
    monkeypatch.setattr(zc, "recovery_context", lambda *args: {"version": 1, "network": "a"})
    monkeypatch.setattr(zc, "load_config", lambda: {})
    monkeypatch.setattr(zc, "update_config", lambda changes, remove=(): dict(changes))
    monkeypatch.setattr(zc, "run_hidden", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(zc, "start_winws_silent", lambda *a: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(zc, "stop_process", lambda *a: True)
    monkeypatch.setattr(zc, "set_service_running", lambda *a: None)
    monkeypatch.setattr(zc, "check_hosts", lambda hosts, *a, **kw: {h: (False, None) for h in hosts})
    monkeypatch.setattr(zc, "measure_hosts", lambda hosts, timeout, samples, *a: {
        h: ProbeResult(samples, samples, 1.0, None, 1) for h in hosts})


def run_watchdog(app, cycles):
    ticks = 0
    def wait(token, seconds):
        nonlocal ticks
        ticks += 1
        if ticks > cycles:
            app._closing = True
            return False
        return app.runtime.valid(token)
    app.runtime.wait = wait
    ui.ZapretApp._watchdog_loop(app)


def test_W2_health_error_clears_green_values():
    app = app_stub()
    app._apply_health({"discord": (True, 42)})
    app._apply_health_failure("resolver failed")
    dot, value, _ = app.health_widgets["discord"]
    assert dot.values["text_color"] != ui.GREEN
    assert value.values["text"] == "Ошибка проверки"
    assert not app._health_busy


def test_W3_rebuilt_cards_render_snapshot(monkeypatch):
    app = app_stub()
    app._apply_health({"discord": (True, 42)})
    monkeypatch.setattr(ui.ctk, "CTkFrame", Widget)
    monkeypatch.setattr(ui.ctk, "CTkLabel", Widget)
    ui.ZapretApp._build_dashboard(app, Widget())
    assert app.health_widgets["discord"][1].values["text"] == "42 мс"
    assert "1/1" in app._health_summary


def test_W4_old_health_result_rejected_after_switch():
    app = app_stub()
    app.on_health_check()
    app.runtime.request("manual")
    app.callbacks.pop(0)()
    assert app._health_state == "Устарело"
    assert not app._health_snapshot
    assert not app._health_busy


def test_W4_search_pauses_health():
    app = app_stub(auto_running=True)
    app.on_health_check()
    assert not app.callbacks
    assert not app._health_busy


def test_R1_partial_outage_triggers_recovery(monkeypatch):
    app = app_stub()
    monkeypatch.setattr(zc, "check_hosts", lambda hosts, *a: {h: ("youtube" in h, 10) for h in hosts})
    app._recover = lambda switch, token: app.recoveries.append(switch)
    run_watchdog(app, 2)
    assert app.recoveries == [True]


def test_R2_crashed_desired_service_is_restarted():
    app = app_stub(proc=None, runtime=RuntimeController("service"))
    app._recover = lambda switch, token: app.recoveries.append(switch)
    run_watchdog(app, 1)
    assert app.recoveries == [False]


def test_R2_installed_but_stopped_service_is_not_started():
    app = app_stub(proc=None, runtime=RuntimeController("stopped"))
    app._recover = lambda switch, token: app.recoveries.append(switch)
    run_watchdog(app, 2)
    assert not app.recoveries


def test_R3_stop_during_probe_cannot_recover(monkeypatch):
    app = app_stub()
    def check(hosts, *args):
        app.on_stop()
        return {h: (False, None) for h in hosts}
    monkeypatch.setattr(zc, "check_hosts", check)
    app._recover = lambda switch, token: app.recoveries.append(switch)
    run_watchdog(app, 2)
    assert not app.recoveries
    assert app.runtime.desired == "stopped"


def test_S1_foreign_process_preserves_pool(monkeypatch):
    app = app_stub(proc=None)
    monkeypatch.setattr(zc, "winws_running", lambda: True)
    app._auto_worker(app.presets, ["discord"])
    app.callbacks[-1]()
    assert app.cfg["recovery_pool"] == ["old", "new"]
    assert app.auto_cancel


def test_S2_cancelled_search_restores_manual_process():
    app = app_stub(auto_cancel=True)
    original = app.proc
    app._auto_worker(app.presets, ["discord"])
    assert app.proc is not None and app.proc is not original
    assert app.cfg["recovery_pool"] == ["old", "new"]


def test_S3_service_restore_failure_keeps_marker(monkeypatch):
    app = app_stub(proc=None, runtime=RuntimeController("service"), auto_cancel=True)
    monkeypatch.setattr(zc, "service_running", lambda: True)
    def transition(running):
        if running:
            raise RuntimeError("start failed")
    monkeypatch.setattr(zc, "set_service_running", transition)
    app._auto_worker(app.presets, ["discord"])
    assert app.cfg["svc_stopped_for_search"] is True
    assert not app._auto_completed


def test_S4_closed_search_never_spawns_trials(monkeypatch):
    app = app_stub(_closing=True)
    calls = []
    monkeypatch.setattr(zc, "start_winws_silent", lambda *a: calls.append(a))
    app._auto_worker(app.presets, ["discord"])
    assert not calls


def test_S5_failed_sample_is_not_hidden_by_fast_success(monkeypatch):
    calls = []
    class Writer:
        def close(self):
            pass
        async def wait_closed(self):
            pass
    async def connect(*args, **kw):
        calls.append(True)
        if len(calls) == 1:
            raise TimeoutError()
        return None, Writer()
    monkeypatch.setattr(measurements.asyncio, "open_connection", connect)
    result = asyncio.run(measurements.probe("host", 1, 3, object()))
    assert result.attempts == 3 and result.successes == 2
    assert not result.reliable and result.error == "TimeoutError"


def test_S5_three_samples_are_three_connections(monkeypatch):
    calls = []
    async def connect(*args, **kw):
        calls.append(True)
        return None, SimpleNamespace(close=lambda: None, wait_closed=lambda: asyncio.sleep(0))
    monkeypatch.setattr(measurements.asyncio, "open_connection", connect)
    result = asyncio.run(measurements.probe("host", 1, 3, object()))
    assert result.reliable and len(calls) == 3


def test_W1_context_prepared_once_before_timing(monkeypatch):
    contexts = []
    def context():
        contexts.append(True)
        time.sleep(0.06)
        return object()
    async def connect(*a, **k):
        return None, SimpleNamespace(close=lambda: None, wait_closed=lambda: asyncio.sleep(0))
    monkeypatch.setattr(measurements.asyncio, "open_connection", connect)
    results = measurements.measure_hosts(["a", "b", "c"], context_factory=context)
    assert len(contexts) == 1
    assert max(r.latency_ms for r in results.values()) < 50


def test_S6_no_starvation_and_fast_stop_requires_full_results():
    presets = [{"name": str(i)} for i in range(6)]
    seen = []
    def trial(preset, hosts, samples):
        seen.append((preset["name"], samples))
        success = samples == 1 or preset["name"] != "0"
        return {h: ProbeResult(samples, samples if success else 0, 0.0, None, 0) for h in hosts}
    results = run_search(presets, [("discord", "host")], ["host"], trial, lambda: False, True, lambda r: None)
    assert len(results) == 4
    assert [name for name, samples in seen if samples == 3] == ["0", "1", "2", "3"]
    seen.clear()
    assert len(run_search(presets, [("discord", "host")], ["host"], trial, lambda: False, False, lambda r: None)) == 6


def test_R5_healthy_cycles_reset_episode(monkeypatch):
    app = app_stub(_recovery_failed={"new"})
    monkeypatch.setattr(zc, "check_hosts", lambda hosts, *a: {h: (True, 1) for h in hosts})
    run_watchdog(app, 2)
    assert not app._recovery_failed


def test_R4_failed_respawn_retries_and_retains_intent():
    app = app_stub(proc=None)
    delays = []
    app.runtime.wait = lambda token, delay: delays.append(delay) or True
    app._watchdog_restart = lambda token: False
    app._recover(False)
    assert delays == [5, 15, 45]
    assert app.runtime.desired == "manual"
    app._recover(False)
    assert delays == [5, 15, 45]


def test_stop_invalidates_queued_transition_without_waiting_for_lock():
    runtime = RuntimeController("manual")
    old = runtime.token
    ready, release = threading.Event(), threading.Event()
    def hold():
        with runtime.transition(old):
            ready.set()
            release.wait(2)
    thread = threading.Thread(target=hold)
    thread.start()
    assert ready.wait(1)
    runtime.request("stopped")
    assert not runtime.valid(old)
    release.set()
    thread.join(2)
    with pytest.raises(Superseded):
        with runtime.transition(old):
            pytest.fail("stale transition ran")


def test_recovery_rolls_back_candidate_that_breaks_other_service(monkeypatch):
    app = app_stub()
    switches = []
    def switch(name, *a, **kw):
        switches.append(name)
        app.active_preset_name = name
        return True
    app._switch_to = switch
    monkeypatch.setattr(zc, "measure_hosts", lambda hosts, *a: {
        h: ProbeResult(3, 0 if "youtube" in h else 3, 10, None, 0) for h in hosts})
    app._recover(True)
    assert switches == ["new", "old"]
    assert app.cfg["strategy"] == "old"
    assert app._recovery_failed == {"new"}


def test_offline_does_not_poison_recovery_pool():
    app = app_stub()
    app._recover = lambda switch, token: app.recoveries.append(switch)
    run_watchdog(app, 3)
    assert not app.recoveries
    assert not app._recovery_failed


def test_log_queue_is_bounded_per_tick_and_callbacks_still_run():
    app = app_stub(_current_page="control", log_queue=queue.Queue(), ui_queue=queue.Queue(),
                   _log_lines=[], _logf=io.StringIO())
    for i in range(1000):
        app.log_queue.put(str(i))
    completed = []
    app.ui_queue.put(lambda: completed.append(True))
    ui.ZapretApp._poll_ui(app)
    assert len(app._log_lines) == 200
    assert app.log_queue.qsize() == 800
    assert completed == [True]
    assert len(app._logf.getvalue().splitlines()) == 200


def test_queued_start_is_cancelled_before_any_process_is_launched(monkeypatch):
    app = app_stub(proc=None, _start_busy=False)
    app._selected_preset = lambda: app.presets[0]
    pending = []
    app._bg = pending.append
    calls = []
    app._spawn_winws = lambda *a: calls.append(a)
    ui.ZapretApp.on_start(app)
    assert app._start_busy and not calls
    app.on_stop()
    for action in pending:
        action()
    assert not calls and app.runtime.desired == "stopped"


def test_background_network_change_cannot_supersede_user_stop():
    runtime = RuntimeController("manual")
    observed = runtime.token
    stopped = runtime.request("stopped")
    assert runtime.request(expected=observed) is None
    assert runtime.valid(stopped)


def test_deferred_page_stops_after_rebuild():
    app = app_stub(pages={}, _page_jobs={})
    old_page = object()
    changes = []
    def builder():
        yield
        changes.append("stale widgets changed")
    gen = builder()
    next(gen)
    ui.ZapretApp._advance_page(app, "settings", old_page, gen)
    assert not changes


def test_late_start_observation_cannot_change_stop_intent():
    runtime = RuntimeController("manual")
    observed = runtime.token
    runtime.request("stopped")
    runtime.set_desired(observed, "service")
    assert runtime.desired == "stopped"


@pytest.mark.parametrize("searching,starting", [(True, False), (False, True)])
def test_stop_button_stays_available_during_background_work(searching, starting):
    app = app_stub(auto_running=searching, _start_busy=starting, tray=None,
                   ctl_dot=Widget(), ctl_status_title=Widget(), ctl_status_sub=Widget(),
                   side_status=Widget())
    app.runtime.request("stopped" if searching else "manual")
    ui.ZapretApp._apply_status(app, False, False, False, "none", False)
    assert app.btn_stop.values["state"] == "normal"
    assert app.btn_start.values["state"] == "disabled"


@pytest.mark.parametrize("callback", ["_autostart_bypass", "_startup_service_restore"])
def test_stop_cancels_pending_startup_actions(callback, monkeypatch):
    app = app_stub(proc=None)
    app._bg = lambda fn: None  # Stop cleanup has not yet removed the search marker.
    app.cfg["svc_stopped_for_search"] = True
    calls = []
    app.on_start = lambda: calls.append("start")
    app.on_stop()
    app._bg = lambda fn: fn()
    monkeypatch.setattr(zc, "set_service_running", lambda run: calls.append(run))
    getattr(ui.ZapretApp, callback)(app)
    assert not calls
    assert app.runtime.desired == "stopped"


def test_failed_stop_keeps_owned_process_reference(monkeypatch):
    proc = SimpleNamespace(poll=lambda: None,
                           terminate=lambda: (_ for _ in ()).throw(PermissionError("denied")))
    app = app_stub(proc=proc)
    # Restore the real function hidden by the no-system-actions fixture.
    monkeypatch.setattr(zc, "stop_process", REAL_STOP_PROCESS)
    with pytest.raises(RuntimeError, match="denied"):
        ui.ZapretApp._stop_local_winws(app)
    assert app.proc is proc


@pytest.mark.parametrize("failed_save", [False, True])
def test_unsuccessful_search_preserves_previous_visible_results(monkeypatch, failed_save):
    old = SearchResult("old", {"discord": 1}, 1, 3, 3, 0)
    new = SearchResult("new", {"discord": 0}, 0, 0, 3, None)
    app = app_stub(_search_results=[old], auto_total_targets=1, _auto_services=["discord"])
    app.svc_vars = {s: SimpleNamespace(get=lambda s=s: s == "discord")
                    for s in ("discord", "youtube", "google")}
    app.fast_var = SimpleNamespace(get=lambda: True)
    app.tree.get_children = lambda: []
    monkeypatch.setattr(ui.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    ui.ZapretApp.on_auto_start(app)
    app._search_results = [new]
    app._auto_completed = failed_save
    app.auto_cancel = not failed_save
    app._auto_context = {}
    if failed_save:
        monkeypatch.setattr(zc, "update_config", lambda *a, **kw:
                            (_ for _ in ()).throw(PermissionError("disk denied")))
    app._auto_done()
    assert app._search_results == [old]
    assert app.auto_best == ("old", 1, 0)
    assert app.cfg["recovery_pool"] == ["old", "new"]
    assert app.auto_cancel


def test_cancelled_measurement_is_not_reliable(monkeypatch):
    cancelled = False
    closed = []
    class Writer:
        def close(self):
            closed.append(True)
        async def wait_closed(self):
            pass
    async def connect(*args, **kwargs):
        nonlocal cancelled
        cancelled = True
        return None, Writer()
    monkeypatch.setattr(asyncio, "open_connection", connect)
    result = asyncio.run(measurements.probe("host", 1, 3, object(), lambda: cancelled))
    assert closed == [True]
    assert result.attempts == 1 and result.successes == 1
    assert not result.reliable


def test_trial_crashing_during_measurement_cannot_win_search(monkeypatch):
    app = app_stub(proc=None)
    child = SimpleNamespace(code=None)
    child.poll = lambda: child.code
    def start(*args):
        child.code = None
        return child
    def measure(hosts, timeout, samples, *args):
        child.code = 1
        return {h: ProbeResult(samples, samples, 0, None, 0) for h in hosts}
    monkeypatch.setattr(zc, "start_winws_silent", start)
    monkeypatch.setattr(zc, "measure_hosts", measure)
    app._auto_worker(app.presets, ["discord"])
    assert app._auto_completed
    assert app._search_results and all(r.total == 0 for r in app._search_results)


def test_network_loss_during_candidate_restores_original_without_blacklisting(monkeypatch):
    app = app_stub()
    switches = []
    app._switch_to = lambda name, *args, **kw: switches.append(name) or True
    monkeypatch.setattr(zc, "measure_hosts", lambda hosts, *a: {
        h: ProbeResult(3, 0, None, "TimeoutError", 0) for h in hosts})
    app._recover(True)
    assert switches == ["new", "old"]
    assert not app._recovery_failed


def test_removed_recovery_service_does_not_keep_failure_counter(monkeypatch):
    app = app_stub()
    app.cfg["recovery_services"] = ["discord", "youtube"]
    app._recover = lambda switch, token: app.recoveries.append(switch)
    def check(hosts, *args):
        if "www.microsoft.com" in hosts:
            return {h: (False, None) for h in hosts}
        if zc.AUTO_QUICK_HOST["discord"] in hosts:
            # Keep Discord for two failed cycles, then deselect it.
            check.calls += 1
            if check.calls == 2:
                app.cfg["recovery_services"] = ["youtube"]
            return {h: (False, None) for h in hosts}
        return {h: (True, 0) for h in hosts}
    check.calls = 0
    monkeypatch.setattr(zc, "check_hosts", check)
    run_watchdog(app, 3)
    assert not app.recoveries


def test_quit_defers_process_cleanup_and_destroys_only_after_it_finishes(monkeypatch):
    work, scheduled, stopped, destroyed = [], [], [], []
    app = app_stub(tray=None, _logf=io.StringIO())
    app.after = lambda delay, callback: scheduled.append(callback)
    app._stop_local_winws = lambda: stopped.append(True)
    app.destroy = lambda: destroyed.append(True)
    monkeypatch.setattr(ui.messagebox, "askyesno", lambda *a: True)
    monkeypatch.setattr(zc, "tg_proxy_stop", lambda: None)
    alive = [True]
    monkeypatch.setattr(ui.threading, "Thread", lambda **kw: SimpleNamespace(
        start=lambda: work.append(kw["target"]), is_alive=lambda: alive[0]))
    ui.ZapretApp._real_quit(app)
    assert work and scheduled and not stopped and not destroyed
    work[0]()
    alive[0] = False
    scheduled.pop(0)()
    assert stopped == [True] and destroyed == [True]
    assert app.runtime.closed
