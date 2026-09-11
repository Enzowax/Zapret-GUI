import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import zapret_core as zc


@pytest.fixture
def files(tmp_path, monkeypatch):
    for attr, name in (("CONFIG_FILE", "config.json"), ("PRESETS_JSON", "presets.json")):
        monkeypatch.setattr(zc, attr, str(tmp_path / name))
    monkeypatch.setattr(zc, "LISTS", str(tmp_path))
    zc.save_config({"strategy": "safe", "tg_secret": "a" * 32})
    return tmp_path


@pytest.mark.parametrize("presets", [None, {"presets": [{}]}, {"presets": "wrong"},
    {"presets": [{"name": "x", "args": []}]},
    {"presets": [{"name": "x", "args": "--ok"}, {"name": "x", "args": "--other"}]}])
def test_invalid_import_never_partially_replaces_settings(files, presets):
    source = files / "import.json"
    before = Path(zc.CONFIG_FILE).read_bytes()
    source.write_text(json.dumps({"config": {"strategy": "bad"}, "presets": presets}))
    assert not zc.import_settings(source)[0]
    assert Path(zc.CONFIG_FILE).read_bytes() == before


def test_transaction_rolls_back_second_file_failure(files, monkeypatch):
    one, two = files / "a", files / "b"
    one.write_bytes(b"old-a")
    two.write_bytes(b"old-b")
    write = zc._atomic_write
    def fail(path, data):
        if path == str(two) and data == b"new-b":
            raise OSError("disk full")
        write(path, data)
    monkeypatch.setattr(zc, "_atomic_write", fail)
    with pytest.raises(OSError):
        zc._write_transaction({str(one): b"new-a", str(two): b"new-b"})
    assert one.read_bytes() == b"old-a"
    assert two.read_bytes() == b"old-b"


def test_broken_config_preserved_and_permission_errors_propagate(files, monkeypatch):
    Path(zc.CONFIG_FILE).write_bytes(b"{broken")
    assert zc.load_config() == {}
    backups = list(files.glob("*.broken"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"{broken"
    import builtins
    original = builtins.open
    def denied(path, *a, **kw):
        if path == zc.CONFIG_FILE:
            raise PermissionError("denied")
        return original(path, *a, **kw)
    monkeypatch.setattr(builtins, "open", denied)
    with pytest.raises(PermissionError):
        zc.update_config({"strategy": "lost"})


@pytest.mark.parametrize("payload", [b"", b"<html>unavailable</html>", b"a.com\nwrong row", b"\xff"])
def test_bad_list_keeps_entire_previous_set(files, monkeypatch, payload):
    for name in zc.LIST_UPDATE_FILES:
        (files / name).write_bytes(b"old.example\n")
    calls = iter([b"new.example\n", payload, b"new.example\n"])
    monkeypatch.setattr(zc.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(next(calls)))
    assert not zc.update_lists()[0]
    assert all((files / name).read_bytes() == b"old.example\n" for name in zc.LIST_UPDATE_FILES)
    assert "lists_last_update" not in zc.load_config()


def test_hostlist_exact_match_prefix_and_success_transaction(files, monkeypatch):
    data = b"# comment\n^dns.google\nexample.com\n"
    monkeypatch.setattr(zc.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(data))
    assert zc.update_lists()[0]
    assert all((files / n).read_bytes() == data for n in zc.LIST_UPDATE_FILES)
    assert zc.load_config()["lists_last_update"] > 0


def test_missing_update_digest_does_not_download(files, monkeypatch):
    monkeypatch.setattr(zc.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("download started"))
    with pytest.raises(RuntimeError):
        zc.download_update("https://example.com", files / "update.zip")
    assert not (files / "update.zip").exists()


def test_release_checksum_sidecar_is_used(monkeypatch):
    digest = hashlib.sha256(b"zip").hexdigest()
    root = "https://github.com/Enzowax/Zapret-GUI/releases/download/v99.0.0/"
    release = {"tag_name": "v99.0.0", "assets": [
        {"name": "ZapretControl.zip", "browser_download_url": root + "ZapretControl.zip"},
        {"name": "ZapretControl.zip.sha256", "browser_download_url": root + "ZapretControl.zip.sha256"}]}
    replies = iter([json.dumps(release).encode(), (digest + "  ZapretControl.zip\n").encode()])
    monkeypatch.setattr(zc.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(next(replies)))
    assert zc.check_update()["digest"] == "sha256:" + digest


SNAPSHOT = {"7": {"guid": "00000000-0000-0000-0000-000000000007",
                  "automatic": True, "servers": ["192.168.1.1"]}}


def test_doh_invalid_snapshot_does_not_mutate_dns(files, monkeypatch):
    calls = []
    def ps(script):
        calls.append(script)
        return SimpleNamespace(returncode=0, stdout="broken", stderr="")
    monkeypatch.setattr(zc, "_ps", ps)
    with pytest.raises(RuntimeError):
        zc.doh_enable()
    assert len(calls) == 1
    assert "Set-DnsClient" not in calls[0]
    assert "doh_pending" not in zc.load_config()


def test_dns_snapshot_normalizes_braced_interface_guid(monkeypatch):
    scripts = []
    response = SimpleNamespace(returncode=0, stdout=json.dumps({
        "8": {"guid": "dc3bb1f0-b584-4777-972a-cae4fd60a50f", "servers": ["192.168.0.1"],
              "automatic": True}}), stderr="")
    monkeypatch.setattr(zc, "_ps_checked", lambda script, action: scripts.append(script) or response)
    assert zc._dns_snapshot()["8"]["guid"] == "dc3bb1f0-b584-4777-972a-cae4fd60a50f"
    assert "$guid=$a.InterfaceGuid.ToString().Trim('{}')" in scripts[0]


def test_dns_apply_matches_normalized_interface_guid(monkeypatch):
    scripts = []
    monkeypatch.setattr(zc, "_ps_checked", lambda script, action: scripts.append(script))
    zc._dns_apply(SNAPSHOT)
    assert "$_.InterfaceGuid.ToString().Trim('{}') -eq '00000000-0000-0000-0000-000000000007'" in scripts[0]


def test_doh_snapshot_is_durable_before_mutation(files, monkeypatch):
    monkeypatch.setattr(zc, "_dns_snapshot", lambda: SNAPSHOT)
    monkeypatch.setattr(zc, "_ps_checked", lambda *a: None)
    def apply(snapshot, ips=None):
        assert zc.load_config()["doh_pending"]
        assert zc.load_config()["doh_snapshot"] == SNAPSHOT
    monkeypatch.setattr(zc, "_dns_apply", apply)
    assert zc.doh_enable()
    assert zc.load_config()["doh_enabled"]


def test_doh_failed_rollback_preserves_recovery_snapshot(files, monkeypatch):
    monkeypatch.setattr(zc, "_dns_snapshot", lambda: SNAPSHOT)
    monkeypatch.setattr(zc, "_ps_checked", lambda *a: None)
    calls = []
    def apply(snapshot, ips=None):
        calls.append(ips)
        raise RuntimeError("adapter unavailable")
    monkeypatch.setattr(zc, "_dns_apply", apply)
    with pytest.raises(RuntimeError, match="adapter unavailable"):
        zc.doh_enable()
    assert len(calls) == 2 and calls[1] is None
    assert zc.load_config()["doh_pending"]
    assert zc.load_config()["doh_snapshot"] == SNAPSHOT


def test_doh_provider_change_keeps_original_and_adds_adapter(files, monkeypatch):
    zc.update_config({"doh_enabled": True, "doh_snapshot": SNAPSHOT})
    current = {"7": {**SNAPSHOT["7"], "automatic": False, "servers": ["1.1.1.1"]},
               "8": {**SNAPSHOT["7"], "guid": "00000000-0000-0000-0000-000000000008"}}
    monkeypatch.setattr(zc, "_dns_snapshot", lambda: current)
    monkeypatch.setattr(zc, "_ps_checked", lambda *a: None)
    monkeypatch.setattr(zc, "_dns_apply", lambda *a: None)
    zc.doh_enable("google")
    assert zc.load_config()["doh_snapshot"] == {"7": SNAPSHOT["7"], "8": current["8"]}


def test_doh_restore_selects_correct_mode_and_ipv4_only(monkeypatch):
    scripts = []
    monkeypatch.setattr(zc, "_ps_checked", lambda script, action: scripts.append(script))
    zc._dns_apply(SNAPSHOT)
    assert "-ResetServerAddresses" in scripts[-1]
    assert "-AddressFamily IPv4" in scripts[-1]
    assert "InterfaceGuid" in scripts[-1]
    zc._dns_apply({"7": {**SNAPSHOT["7"], "automatic": False}})
    assert "-ResetServerAddresses" not in scripts[-1]
    assert "'192.168.1.1'" in scripts[-1]


def test_presets_keep_profile_order_and_add_effective_exclusions():
    args = '--filter-tcp=443 --hostlist="%LISTS%list-google.txt" --dpi-desync=fake --new --filter-udp=443 --dpi-desync=split'
    result = zc.build_args_str(args, "off")
    split = result.index("--new")
    assert result[:split][-1] == "--hostlist-exclude=" + str(Path(zc.LISTS) / "list-exclude-user.txt")
    assert result[split + 1:] == ["--filter-udp=443", "--dpi-desync=split"]


@pytest.mark.parametrize("retry,expected", [("120", 120), ("999999", 300), ("broken", 60), ("-2", 1)])
def test_cf_worker_429_backoff_expires(monkeypatch, retry, expected):
    from tgproxy.pool import _CfWorkerPool
    from tgproxy.raw_websocket import WsHandshakeError
    import tgproxy.pool as pool_module
    now = [100.0]
    monkeypatch.setattr(pool_module.time, "time", lambda: now[0])
    pool = _CfWorkerPool()
    pool.report_failure("worker.example", WsHandshakeError(429, "limit", {"retry-after": retry}))
    assert pool.available_domains(["worker.example"]) == []
    now[0] += expected + 1
    assert pool.available_domains(["worker.example"]) == ["worker.example"]


def test_domain_filter_preserves_telegram_and_hides_worker():
    import logging
    from tgproxy.utils import DomainCensorFilter
    record = logging.LogRecord("proxy", logging.INFO, "", 0, "%s -> %s", ("worker.example.com", "web.telegram.org"), None)
    assert DomainCensorFilter().filter(record)
    assert "worker.example.com" not in record.getMessage()
    assert "web.telegram.org" in record.getMessage()


def test_legacy_dns_requires_explicit_mode_before_migration(files, monkeypatch):
    zc.update_config({"doh_enabled": True, "doh_prev": {"7": ["192.168.1.1"]}})
    monkeypatch.setattr(zc, "_dns_snapshot", lambda: SNAPSHOT)
    applied = []
    monkeypatch.setattr(zc, "_dns_apply", lambda snapshot: applied.append(snapshot))
    with pytest.raises(RuntimeError):
        zc.doh_disable()
    assert not applied
    assert zc.doh_disable(legacy_mode="automatic")
    assert applied[0]["7"]["automatic"]
    assert not zc.load_config()["doh_enabled"]


@pytest.mark.parametrize("dc", [-2, 2, -4, 4])
def test_media_flag_follows_encrypted_dc_sign(dc):
    import os
    import struct
    from tgproxy._aes import Cipher, algorithms, modes
    from tgproxy.tg_ws_proxy import _try_handshake
    from tgproxy.utils import PROTO_TAG_SECURE
    secret = bytes.fromhex("a" * 32)
    raw = bytearray(os.urandom(64))
    raw[56:60] = PROTO_TAG_SECURE
    raw[60:62] = struct.pack("<h", dc)
    cipher = Cipher(algorithms.AES(hashlib.sha256(bytes(raw[8:40]) + secret).digest()),
                    modes.CTR(bytes(raw[40:56]))).encryptor()
    encrypted = cipher.update(bytes(raw))
    wire = bytes(raw[:56]) + encrypted[56:]
    result = _try_handshake(wire, secret)
    assert result[0] == abs(dc)
    assert result[1] is (dc < 0)
