"""Real owned child processes; never launch winws or alter Windows services."""
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import pytest
import zapret_core as zc


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows process integration")


def child():
    return subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=zc.CREATE_NO_WINDOW)


def cleanup(proc):
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)
    proc.stdin.close()


def test_real_stop_only_terminates_owned_child():
    first, other = child(), child()
    try:
        assert zc.stop_process(first, timeout=3)
        assert first.poll() is not None
        assert other.poll() is None
        assert not zc.stop_process(first)
    finally:
        cleanup(first)
        cleanup(other)


def test_real_timeout_uses_kill_and_waits_for_exit():
    proc = child()
    killed = []
    def kill():
        killed.append(True)
        proc.kill()
    # Simulate a termination request that failed to stop the real child.
    wrapped = SimpleNamespace(poll=proc.poll, wait=proc.wait, terminate=lambda: None, kill=kill)
    try:
        assert zc.stop_process(wrapped, timeout=0.5)
        assert killed == [True]
        assert proc.poll() is not None
    finally:
        cleanup(proc)


def test_generated_updater_preserves_external_settings(tmp_path, monkeypatch):
    install, stage = tmp_path / "install & data", tmp_path / "stage"
    install.mkdir()
    preserved = {"utils/app_config.json": b'{"strategy":"custom"}',
                 "presets.json": b'{"presets":[]}', "lists/list-user.txt": b"example.org\n"}
    for name, contents in preserved.items():
        path = install / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(contents)
    (install / "ZapretControl.exe").write_bytes(b"old fixture")
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("ZapretControl/ZapretControl.exe", b"new fixture")
        zipped.writestr("ZapretControl/_internal/release-notes/changes.md", b"new notes")
    with monkeypatch.context() as patch:
        patch.setattr(zc.sys, "frozen", True, raising=False)
        patch.setattr(zc.sys, "executable", str(install / "ZapretControl.exe"))
        patch.setattr(zc.tempfile, "mkdtemp", lambda **kw: str(stage))
        patch.setattr(zc.subprocess, "Popen", lambda *a, **kw: None)
        zc.apply_update(archive, expected_digest="sha256:" + zc._sha256(archive))
    helper = stage / "update.ps1"
    lines = helper.read_text(encoding="utf-8-sig").splitlines()
    # Exercise actual copying and scoped cleanup, without waiting for pytest
    # to exit or attempting to launch the fixture bytes as an application.
    lifecycle = ("Wait-Process -Id ", "Start-Process -FilePath ")
    assert sum(line.startswith(lifecycle) for line in lines) == 2
    helper.write_text("\n".join(line for line in lines if not line.startswith(lifecycle)),
                      encoding="utf-8-sig")
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper)],
                            capture_output=True, timeout=30, creationflags=zc.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    assert (install / "ZapretControl.exe").read_bytes() == b"new fixture"
    assert (install / "_internal/release-notes/changes.md").read_bytes() == b"new notes"
    assert all((install / name).read_bytes() == contents for name, contents in preserved.items())
    assert not (stage / "ZapretControl").exists()
