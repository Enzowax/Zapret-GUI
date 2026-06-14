# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec: самодостаточный ZapretControl.exe.
Внутрь зашиваются bin/, lists/, нужные utils, presets.json, пресеты *.bat и TgWsProxy.
При первом запуске exe разворачивает их рядом с собой (zapret_core.ensure_runtime)
и сверяет целостность бинарников (zapret_core.verify_runtime).

Сборка:  pyinstaller --noconfirm --distpath . --workpath build ZapretControl.spec
"""
import os
import sys
import glob
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(SPECPATH)
# важно: чтобы локальные пакеты (tgproxy, zapret_core) находились при сборке
# и через `pyinstaller` (CI), и через `python -m PyInstaller` (локально)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

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

# иконки (окно/трей)
for _ic in ("icon.ico", "icon.png"):
    _p = os.path.join(ROOT, "assets", _ic)
    if os.path.exists(_p):
        datas.append((_p, "assets"))

ICON_FILE = os.path.join(ROOT, "assets", "icon.ico")
if not os.path.exists(ICON_FILE):
    ICON_FILE = None

# Telegram-прокси теперь вшит как Python-пакет tgproxy (см. hiddenimports),
# отдельный TgWsProxy.exe больше не нужен.

ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")
pil_datas, pil_binaries, pil_hidden = collect_all("PIL")
pystray_datas, pystray_binaries, pystray_hidden = collect_all("pystray")
# cryptography нужен встроенному TG-прокси (tgproxy/_aes.py) на Windows
crypto_datas, crypto_binaries, crypto_hidden = collect_all("cryptography")
datas += ctk_datas + pil_datas + pystray_datas + crypto_datas

a = Analysis(
    ["zapret_app.pyw"],
    pathex=[ROOT],
    binaries=ctk_binaries + pil_binaries + pystray_binaries + crypto_binaries,
    datas=datas,
    hiddenimports=(ctk_hidden + pil_hidden + pystray_hidden + crypto_hidden
                   + ["darkdetect", "zapret_core",
                      "cryptography.hazmat.primitives.ciphers"]
                   + collect_submodules("tgproxy")
                   + ["tgproxy", "tgproxy.tg_ws_proxy", "tgproxy.config",
                      "tgproxy.bridge", "tgproxy.raw_websocket", "tgproxy.pool",
                      "tgproxy.balancer", "tgproxy.fake_tls", "tgproxy.stats",
                      "tgproxy.utils", "tgproxy._aes"]),
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
    uac_admin=True, icon=ICON_FILE,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=True, upx_exclude=[], name="ZapretControl",
)
