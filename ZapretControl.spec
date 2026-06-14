# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec: самодостаточный ZapretControl.exe.
Внутрь зашиваются bin/, lists/, нужные utils, presets.json, пресеты *.bat и TgWsProxy.
При первом запуске exe разворачивает их рядом с собой (zapret_core.ensure_runtime)
и сверяет целостность бинарников (zapret_core.verify_runtime).

Сборка:  pyinstaller --noconfirm --distpath . --workpath build ZapretControl.spec
"""
import os
import glob
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(SPECPATH)

datas = []

for folder in ("bin", "lists"):
    base = os.path.join(ROOT, folder)
    for f in glob.glob(os.path.join(base, "**", "*"), recursive=True):
        if os.path.isfile(f):
            datas.append((f, os.path.relpath(os.path.dirname(f), ROOT)))

for name in ("test zapret.ps1", "targets.txt"):
    p = os.path.join(ROOT, "utils", name)
    if os.path.exists(p):
        datas.append((p, "utils"))

# декларативные пресеты (единственный источник стратегий)
if os.path.exists(os.path.join(ROOT, "presets.json")):
    datas.append((os.path.join(ROOT, "presets.json"), "."))

# TgWsProxy — из корня проекта или с рабочего стола
for cand in (os.path.join(ROOT, "TgWsProxy_windows.exe"),
             os.path.join(os.path.expanduser("~"), "Desktop", "TgWsProxy_windows.exe")):
    if os.path.exists(cand):
        datas.append((cand, "."))
        break

ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")
datas += ctk_datas

a = Analysis(
    ["zapret_app.pyw"],
    pathex=[ROOT],
    binaries=ctk_binaries,
    datas=datas,
    hiddenimports=ctk_hidden + ["darkdetect", "zapret_core"],
    hookspath=[], runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="ZapretControl",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    uac_admin=True, icon=None,
)
