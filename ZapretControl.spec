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
from PyInstaller.utils.hooks import collect_all, collect_submodules

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

# Telegram-прокси теперь вшит как Python-пакет tgproxy (см. hiddenimports),
# отдельный TgWsProxy.exe больше не нужен.

ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")
pil_datas, pil_binaries, pil_hidden = collect_all("PIL")
pystray_datas, pystray_binaries, pystray_hidden = collect_all("pystray")
datas += ctk_datas + pil_datas + pystray_datas

a = Analysis(
    ["zapret_app.pyw"],
    pathex=[ROOT],
    binaries=ctk_binaries + pil_binaries + pystray_binaries,
    datas=datas,
    hiddenimports=(ctk_hidden + pil_hidden + pystray_hidden
                   + ["darkdetect", "zapret_core"]
                   + collect_submodules("tgproxy")),
    hookspath=[], runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)

# onedir: exe + папка _internal (без распаковки DLL во временную папку при запуске)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ZapretControl",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    console=False, disable_windowed_traceback=False,
    uac_admin=True, icon=None,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=True, upx_exclude=[], name="ZapretControl",
)
