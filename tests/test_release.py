"""Регрессии обновления: настройки, файлы, интеграция upstream и отмена поиска."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

import pytest
import zapret_core as zc

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(zc, "CONFIG_FILE", str(tmp_path / "app_config.json"))
    return tmp_path / "app_config.json"


def test_config_changes_preserve_proxy_secret_and_parallel_writes(config):
    secret = zc.tg_get_secret()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: zc.update_config({f"key{i}": i}), range(20)))
    zc.update_config({"appearance": "light"})
    assert zc.tg_get_secret() == secret
    assert all(zc.load_config()[f"key{i}"] == i for i in range(20))
    zc.update_config({}, remove=("key1",))
    assert "key1" not in zc.load_config()


def test_atomic_write_keeps_previous_file_on_failure(config, monkeypatch):
    zc.save_config({"strategy": "general"})
    def fail(*args):
        raise OSError("disk error")
    monkeypatch.setattr(zc.os, "replace", fail)
    with pytest.raises(OSError):
        zc.update_config({"strategy": "broken"})
    assert zc.load_config() == {"strategy": "general"}
    assert list(config.parent.iterdir()) == [config]


def test_config_rejects_non_object_and_repairs_invalid_proxy_values(config):
    config.write_text("[]", encoding="utf-8")
    assert zc.load_config() == {}
    zc.save_config({"tg_secret": "z" * 32, "tg_port": 99999})
    assert len(bytes.fromhex(zc.tg_get_secret())) == 16
    assert zc.tg_get_port() == zc.TG_DEFAULT_PORT
    with pytest.raises(ValueError):
        zc.set_tg_port(0)


@pytest.mark.parametrize("payload", [b"", b"<html>error</html>", b"999.1.1.1/24"])
def test_ipset_invalid_download_keeps_working_file(tmp_path, monkeypatch, payload):
    path = tmp_path / "ipset.txt"
    path.write_bytes(b"1.2.3.0/24\n")
    monkeypatch.setattr(zc, "IPSET_FILE", str(path))
    monkeypatch.setattr(zc.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(payload))
    assert zc.update_ipset()[0] is False
    assert path.read_bytes() == b"1.2.3.0/24\n"


def test_ipset_network_failure_and_success(tmp_path, monkeypatch):
    path = tmp_path / "ipset.txt"
    path.write_bytes(b"1.2.3.0/24\n")
    monkeypatch.setattr(zc, "IPSET_FILE", str(path))
    def fail(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(zc.urllib.request, "urlopen", fail)
    assert zc.update_ipset()[0] is False
    assert path.exists()
    monkeypatch.setattr(zc.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"8.8.8.0/24\n"))
    assert zc.update_ipset()[0] is True
    assert path.read_bytes() == b"8.8.8.0/24\n"
    assert Path(str(path) + ".backup").read_bytes() == b"1.2.3.0/24\n"


def test_service_uses_argument_list_and_checks_start(monkeypatch):
    calls = []
    monkeypatch.setattr(zc, "WINWS", 'C:\\A & B\\winws.exe')
    monkeypatch.setattr(zc, "kill_winws_only", lambda: None)
    monkeypatch.setattr(zc, "enable_tcp_timestamps", lambda: None)
    monkeypatch.setattr(zc, "service_running", lambda: True)
    monkeypatch.setattr(zc, "service_installed", lambda: False)
    def run(cmd, **kwargs):
        assert isinstance(cmd, list)
        assert not kwargs.get("shell")
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr(zc, "run_hidden", run)
    assert zc.install_service("general", '--hostlist="C:\\a & b\\list.txt"', "off")[0]
    create = next(c for c in calls if c[:2] == ["sc", "create"])
    assert create[4] == subprocess.list2cmdline([zc.WINWS] + zc.build_args_str('--hostlist="C:\\a & b\\list.txt"', 'off'))
    assert not any(call[:2] == ["taskkill", "/IM"] for call in calls)
    monkeypatch.setattr(zc, "service_running", lambda: False)
    assert zc.install_service("general", "--new", "off")[0] is False


def test_service_create_failure_does_not_claim_success(monkeypatch):
    calls = []
    monkeypatch.setattr(zc, "kill_winws_only", lambda: None)
    monkeypatch.setattr(zc, "enable_tcp_timestamps", lambda: None)
    def run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=1, stdout="", stderr="denied")
    monkeypatch.setattr(zc, "run_hidden", run)
    assert zc.install_service("general", "--new", "off")[0] is False
    assert ["sc", "start", zc.SERVICE_NAME] not in calls


def test_stop_process_only_terminates_the_tracked_child():
    class Child:
        def __init__(self):
            self.terminated = self.waited = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            self.waited = timeout

    child = Child()
    assert zc.stop_process(child, timeout=2) is True
    assert child.terminated and child.waited == 2
    assert zc.stop_process(None) is False


def test_stop_all_winws_terminates_every_matching_process(monkeypatch):
    calls = []
    monkeypatch.setattr(zc, "run_hidden", lambda cmd: calls.append(cmd) or SimpleNamespace(
        returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(zc, "winws_running", lambda: False)

    assert zc.stop_all_winws() == "Все процессы winws.exe принудительно остановлены."
    assert calls == [["taskkill", "/IM", "winws.exe", "/F"]]


def test_stop_all_winws_reports_absence_after_taskkill(monkeypatch):
    calls = []
    monkeypatch.setattr(zc, "run_hidden", lambda cmd: calls.append(cmd) or SimpleNamespace(
        returncode=128, stdout="not found", stderr=""))
    monkeypatch.setattr(zc, "winws_running", lambda: False)

    assert zc.stop_all_winws() == "Процессы winws.exe не найдены."
    assert calls == [["taskkill", "/IM", "winws.exe", "/F"]]


def test_apply_fix_routes_global_winws_stop(monkeypatch):
    monkeypatch.setattr(zc, "is_admin", lambda: True)
    monkeypatch.setattr(zc, "stop_all_winws", lambda: "done")
    assert zc.apply_fix("stop_all_winws") == "done"


def test_doh_updates_config_only_after_verified_powershell(config, monkeypatch):
    zc.save_config({"doh_enabled": False, "doh_provider": "cloudflare"})
    failed = SimpleNamespace(returncode=1, stdout="", stderr="access denied")
    monkeypatch.setattr(zc, "_ps", lambda script: failed)
    with pytest.raises(RuntimeError, match="access denied"):
        zc.doh_enable("google")
    assert zc.doh_status() == {"enabled": False, "provider": "cloudflare"}

    ok = SimpleNamespace(returncode=0, stdout=json.dumps({"7": {
        "guid": "00000000-0000-0000-0000-000000000007", "servers": ["192.168.1.1"],
        "automatic": True}}), stderr="")
    monkeypatch.setattr(zc, "_ps", lambda script: ok)
    assert zc.doh_enable("google") is True
    cfg = zc.load_config()
    assert cfg["doh_enabled"] and cfg["doh_provider"] == "google"
    assert cfg["doh_snapshot"]["7"]["servers"] == ["192.168.1.1"]
    assert cfg["doh_snapshot"]["7"]["automatic"] is True


def test_doh_disable_refuses_to_forget_state_when_restore_fails(config, monkeypatch):
    zc.save_config({"doh_enabled": True, "doh_provider": "cloudflare",
                    "doh_snapshot": {"5": {"guid": "00000000-0000-0000-0000-000000000005",
                                           "servers": ["192.168.1.1"], "automatic": True}}})
    monkeypatch.setattr(zc, "_ps", lambda script: SimpleNamespace(
        returncode=1, stdout="", stderr="adapter failed"))
    with pytest.raises(RuntimeError, match="adapter failed"):
        zc.doh_disable()
    assert zc.doh_status()["enabled"] is True


def test_upstream_files_match_recorded_hashes_and_presets_resolve():
    manifest = json.loads((ROOT / "upstream-versions.json").read_text(encoding="utf-8"))
    for name, expected in manifest["sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    presets = zc.load_presets()
    assert len({p["id"] for p in presets}) == len(presets)
    for preset in presets:
        for mode in ("off", "all", "tcp", "udp"):
            for arg in zc.build_args_str(preset["args"], mode):
                assert "%" not in arg, (preset["name"], arg)
                if "=" in arg:
                    value = arg.split("=", 1)[1]
                    if str(ROOT) in value:
                        assert Path(value).is_file(), (preset["name"], arg)


def test_upgrade_refreshes_presets_and_preserves_custom_data(config, tmp_path, monkeypatch):
    bundle, install = tmp_path / "bundle", tmp_path / "install"
    bundle.mkdir()
    install.mkdir()
    old = {"presets": [{"name": "general", "args": "--old"},
                       {"name": "custom", "args": "--custom"}]}
    new = {"presets": [{"name": "general", "args": "--new"},
                       {"name": "general (EXP)", "args": "--exp"}]}
    (bundle / "presets.json").write_text(json.dumps(new), encoding="utf-8")
    dest = install / "presets.json"
    dest.write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(zc, "_meipass", lambda: str(bundle))
    monkeypatch.setattr(zc, "BASE", str(install))
    monkeypatch.setattr(zc, "PRESETS_JSON", str(dest))
    zc.save_config({"tg_secret": "a" * 32, "defaults_version": "2.41.0"})
    assert str(dest) in zc.refresh_defaults()
    assert [p["name"] for p in zc.load_presets()] == ["general", "general (EXP)", "custom"]
    assert json.loads(Path(str(dest) + ".backup").read_text()) == old
    assert zc.load_config()["tg_secret"] == "a" * 32
    assert zc.refresh_defaults() == []


def test_download_checks_sha256(tmp_path, monkeypatch):
    data = b"archive"
    class Response(io.BytesIO):
        headers = {}
    monkeypatch.setattr(zc.urllib.request, "urlopen", lambda *a, **k: Response(data))
    dest = tmp_path / "update.zip"
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    zc.download_update("https://example.com/file", dest, expected_size=len(data), expected_digest=digest)
    with pytest.raises(RuntimeError):
        zc.download_update("https://example.com/file", dest, expected_digest="sha256:" + "0" * 64)


def test_update_selects_application_zip(monkeypatch):
    release = {"tag_name": "v99.0.0", "assets": [
        {"name": "sources.zip", "browser_download_url": "wrong"},
        {"name": "ZapretControl.zip", "browser_download_url": "right", "digest": "sha256:" + "a" * 64}]}
    monkeypatch.setattr(zc.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(release).encode()))
    assert zc.check_update()["url"] == "right"
    assert zc.check_update()["digest"] == "sha256:" + "a" * 64


@pytest.mark.parametrize("name", ["../escape", "ZapretControl/../escape", "ZapretControl/C:bad",
                                  "ZapretControl/utils/app_config.json"])
def test_update_rejects_unsafe_archive(tmp_path, monkeypatch, name):
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(name, "bad")
    monkeypatch.setattr(zc.sys, "frozen", True, raising=False)
    monkeypatch.setattr(zc.tempfile, "mkdtemp", lambda **k: str(tmp_path / "staging"))
    with pytest.raises(RuntimeError):
        zc.apply_update(archive, expected_digest="sha256:" + zc._sha256(archive))
    assert not (tmp_path / "escape").exists()


def test_update_targets_executable_directory_not_data_directory(tmp_path, monkeypatch):
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("ZapretControl/ZapretControl.exe", b"fixture")
        z.writestr("ZapretControl/_internal/data", b"fixture")
    monkeypatch.setattr(zc.sys, "frozen", True, raising=False)
    monkeypatch.setattr(zc.sys, "executable", str(tmp_path / "install' & %/ZapretControl.exe"))
    monkeypatch.setattr(zc, "BASE", str(tmp_path / "data"))
    monkeypatch.setattr(zc.tempfile, "mkdtemp", lambda **k: str(tmp_path / "staging"))
    calls = []
    monkeypatch.setattr(zc.subprocess, "Popen", lambda *a, **k: calls.append(a))
    zc.apply_update(archive, expected_digest="sha256:" + zc._sha256(archive))
    script = (tmp_path / "staging/update.ps1").read_text(encoding="utf-8-sig")
    assert "install'' & %" in script
    assert "$LASTEXITCODE -ge 8" in script
    assert calls[0][0][0] == "powershell"


@pytest.mark.parametrize("digest", ["", "sha256:" + "0" * 64])
def test_apply_update_requires_matching_digest_before_staging(tmp_path, monkeypatch, digest):
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("ZapretControl/ZapretControl.exe", b"fixture")
        z.writestr("ZapretControl/_internal/data", b"fixture")
    monkeypatch.setattr(zc.sys, "frozen", True, raising=False)
    monkeypatch.setattr(zc.tempfile, "mkdtemp", lambda **k: pytest.fail("unverified archive staged"))
    with pytest.raises(RuntimeError, match="SHA-256"):
        zc.apply_update(archive, expected_digest=digest)


def test_support_bundle_removes_secrets(config, tmp_path, monkeypatch):
    secret = "abcdef12" * 4
    zc.save_config({"tg_secret": secret, "appearance": "light",
                    "doh_prev": {"7": ["192.168.1.1"]}})
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "tg_proxy.log").write_text(f"Secret: {secret}\ntg://proxy?secret=dd{secret}\n", encoding="utf-8")
    monkeypatch.setattr(zc, "LOGS", str(logs))
    for name in ("is_admin", "winws_running", "service_installed", "service_running", "tg_proxy_running"):
        monkeypatch.setattr(zc, name, lambda: False)
    monkeypatch.setattr(zc, "diagnose", lambda: [])
    with zipfile.ZipFile(zc.make_support_bundle()) as z:
        assert all(secret.encode() not in z.read(name) for name in z.namelist())
        assert b"192.168.1.1" not in z.read("app_config.json")


def test_preset_tags_are_short_and_derived_from_args():
    assert zc.preset_tags({"name": "general (EXP)",
                           "args": "--filter-tcp=443 --filter-udp=443 --dpi-desync=fake-quic"}) == [
        "TCP", "UDP", "QUIC", "desync", "экспериментальный"]
    assert zc.preset_tags({"name": "minimal", "args": ""}) == ["универсальный"]


def test_recovery_does_not_cycle_back_to_a_failed_strategy(monkeypatch):
    from test_health_runtime import app_stub
    app = app_stub(_recovery_failed={"new"})
    switched = []
    app._switch_to = lambda name, *a, **kw: switched.append(name)
    app._recover(True)
    assert switched == []


def test_proxy_immediate_stop_cancels_pending_tasks(config, monkeypatch):
    import tgproxy.tg_ws_proxy as proxy
    cancelled = []
    async def background():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    async def run(stop_event):
        asyncio.create_task(background())
        await asyncio.sleep(0)
        await stop_event.wait()
    monkeypatch.setattr(proxy, "_run", run)
    monkeypatch.setattr(zc, "_setup_proxy_logging", lambda: None)
    for _ in range(3):
        zc.tg_proxy_start()
        zc.tg_proxy_stop()
        assert not zc.tg_proxy_running()
        assert not zc.tg_last_error()
    assert len(cancelled) == 3


def test_cancelled_search_does_not_overwrite_recovery_pool(config):
    loader = importlib.machinery.SourceFileLoader("test_zapret_ui", str(ROOT / "zapret_app.pyw"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    ui = importlib.util.module_from_spec(spec)
    loader.exec_module(ui)
    zc.save_config({"recovery_pool": ["working"]})
    widget = SimpleNamespace(configure=lambda **k: None)
    app = SimpleNamespace(auto_running=True, auto_cancel=True, auto_best=None,
                          _auto_autoapply=True, _cfgw=lambda *a, **k: None,
                          log_msg=lambda *a: None, refresh_status=lambda: None)
    for name in ("btn_apply_best", "btn_install_best", "btn_auto_start", "btn_auto_stop",
                 "btn_start", "btn_stop", "auto_phase_lbl"):
        setattr(app, name, widget)
    from zapret_runtime import RuntimeController
    app.runtime = RuntimeController()
    app._auto_token = app.runtime.token
    app._search_results = []
    app._auto_completed = False
    app._closing = False
    app._ensure_page = lambda *args: None
    ui.ZapretApp._auto_done(app)
    assert zc.load_config()["recovery_pool"] == ["working"]
    assert app._auto_autoapply is False
    assert app.auto_running is False
