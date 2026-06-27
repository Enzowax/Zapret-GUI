# -*- coding: utf-8 -*-
"""
zapret_core — вся логика обхода без GUI: пути, пресеты (как данные), разбор
аргументов, управление процессом/службой, настройки, асинхронные проверки
соединения, контроль целостности и TgWsProxy.

Работает и как обычный скрипт, и внутри собранного PyInstaller .exe.

Фаза 1: стратегии хранятся декларативно в presets.json (а не парсятся из .bat
на лету); встроенные бинарники проверяются по SHA-256 при запуске .exe.
"""

import os
import re
import sys
import ssl
import json
import time
import shutil
import socket
import ctypes
import hashlib
import zipfile
import asyncio
import threading
import subprocess
import urllib.request


# --------------------------------------------------------------------------- #
#  Базовые пути (с учётом запуска из .exe)
# --------------------------------------------------------------------------- #
def _writable_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        test = os.path.join(path, ".write_test.tmp")
        with open(test, "w") as f:
            f.write("x")
        os.remove(test)
        return True
    except Exception:
        return False


def _compute_base():
    """Рабочая папка: рядом со скриптом, а для .exe — рядом с exe
    (или %LOCALAPPDATA%\\ZapretControl, если рядом с exe писать нельзя)."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        if _writable_dir(exe_dir):
            return exe_dir
        return os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ZapretControl")
    return os.path.dirname(os.path.abspath(__file__))


BASE = _compute_base()

BIN = os.path.join(BASE, "bin") + os.sep
LISTS = os.path.join(BASE, "lists") + os.sep
UTILS = os.path.join(BASE, "utils")
LOGS = os.path.join(BASE, "logs")
WINWS = os.path.join(BIN, "winws.exe")

GAME_FLAG = os.path.join(UTILS, "game_filter.enabled")
UPDATE_FLAG = os.path.join(UTILS, "check_updates.enabled")
IPSET_FILE = os.path.join(LISTS, "ipset-all.txt")
CONFIG_FILE = os.path.join(UTILS, "app_config.json")
PRESETS_JSON = os.path.join(BASE, "presets.json")

SERVICE_NAME = "zapret"
IPSET_URL = ("https://raw.githubusercontent.com/Flowseal/zapret-discord-youtube/"
             "refs/heads/main/.service/ipset-service.txt")

# Пользовательский список доменов для обхода (уже подключён во всех стратегиях
# как --hostlist=...list-general-user.txt). Сюда пишем сайты, добавленные
# пользователем (например, rutracker.org).
USER_LIST_FILE = os.path.join(LISTS, "list-general-user.txt")

# Автообновление дефолтных списков доменов из upstream (Flowseal).
LIST_RAW_BASE = ("https://raw.githubusercontent.com/Flowseal/"
                 "zapret-discord-youtube/refs/heads/main/lists/")
LIST_UPDATE_FILES = ("list-general.txt", "list-exclude.txt", "list-google.txt")
LISTS_UPDATE_INTERVAL_DAYS = 7

# Файл пользовательских исключений IP для winws (уже подключён во всех стратегиях
# как --ipset-exclude=...ipset-exclude-user.txt).
IPSET_EXCLUDE_USER_FILE = os.path.join(LISTS, "ipset-exclude-user.txt")
# Лог встроенного Telegram-прокси.
TG_PROXY_LOG = os.path.join(LOGS, "tg_proxy.log")
# Официальные диапазоны Telegram (core.telegram.org/resources/cidr.txt, IPv4).
# Исключаются из DPI-десинка winws: Telegram обслуживает встроенный прокси,
# обходу (Discord/YouTube) трогать его соединения незачем — это вызывает обрывы.
TELEGRAM_IP_RANGES = [
    "91.105.192.0/23", "91.108.4.0/22", "91.108.8.0/22", "91.108.12.0/22",
    "91.108.16.0/22", "91.108.20.0/22", "91.108.56.0/22",
    "95.161.64.0/20", "149.154.160.0/20", "185.76.151.0/24",
]

# --- версия приложения и источник обновлений (GitHub) ---
APP_VERSION = "2.22.0"
GITHUB_OWNER = "Enzowax"
GITHUB_REPO = "Zapret-GUI"
GITHUB_API_LATEST = (f"https://api.github.com/repos/{GITHUB_OWNER}/"
                     f"{GITHUB_REPO}/releases/latest")
GITHUB_RELEASES_PAGE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

CREATE_NO_WINDOW = 0x08000000
CONFLICTING_SERVICES = ["GoodbyeDPI", "discordfix_zapret", "winws1", "winws2"]
# Сторонние DPI-обходы (процессы), которые мешают нашему winws/WinDivert.
# winws.exe и ZapretControl.exe сюда НЕ входят — это мы сами.
DPI_CONFLICT_PROCS = ["goodbyedpi.exe", "byedpi.exe", "ciadpi.exe",
                      "spoofdpi.exe", "zapret.exe"]
# Сетевые «оптимизаторы»/драйверы, известные конфликтами с WinDivert.
NIC_CONFLICT = {
    "SmartByteNetworkService": "SmartByte (Dell)",
    "KillerNetworkService": "Killer Networking",
    "Killer Analytics Service": "Killer Analytics",
    "RivetNetworking": "Rivet/Killer",
    "cFosSpeed": "cFosSpeed",
}

# Цели авто-поиска. Проверяется TLS-рукопожатие с нужным SNI.
AUTO_TARGETS = {
    "discord": ["discord.com", "gateway.discord.gg", "cdn.discordapp.com"],
    "youtube": ["www.youtube.com", "i.ytimg.com", "redirector.googlevideo.com"],
    "google":  ["www.google.com", "www.gstatic.com"],
}
AUTO_QUICK_HOST = {"discord": "discord.com", "youtube": "www.youtube.com",
                   "google": "www.google.com"}
AUTO_SERVICE_LABELS = {"discord": "Discord", "youtube": "YouTube", "google": "Google"}

QUICK_WAIT = 2.0
QUICK_TIMEOUT = 2.0
FULL_WAIT = 3.5
FULL_TIMEOUT = 3.0

# авто-восстановление (watchdog)
WATCHDOG_INTERVAL = 45          # сек между проверками
WATCHDOG_HEALTH_HOSTS = ["discord.com", "www.youtube.com"]
WATCHDOG_FAIL_THRESHOLD = 2     # подряд неудачных проверок до перезапуска

# встроенный Telegram-прокси (вшитый пакет tgproxy)
TG_DEFAULT_HOST = "127.0.0.1"
TG_DEFAULT_PORT = 1443

# подпапки и файлы, которые разворачиваются из .exe рядом с ним при первом запуске
RUNTIME_SUBDIRS = ("bin", "lists", "utils")
RUNTIME_ROOT_FILES = ("presets.json",)
# файлы, чья целостность критична (проверяются по SHA-256)
INTEGRITY_SUBDIRS = ("bin",)
# апстрим-дефолты (списки/утилиты), которые ДОСЫЛАЮТСЯ при обновлении сборки:
# при смене версии один раз перезаписываются свежими из .exe. Пользовательские
# *-user.txt и управляемый через тумблер ipset-all.txt здесь НЕ перечислены —
# их трогать нельзя.
REFRESH_DEFAULT_FILES = (
    os.path.join("lists", "list-general.txt"),
    os.path.join("lists", "list-google.txt"),
    os.path.join("lists", "list-exclude.txt"),
    os.path.join("lists", "ipset-exclude.txt"),
    os.path.join("utils", "targets.txt"),
    os.path.join("utils", "test zapret.ps1"),
)


# --------------------------------------------------------------------------- #
#  Развёртывание и контроль целостности встроенных файлов (для .exe)
# --------------------------------------------------------------------------- #
def _meipass():
    return getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else None


def ensure_runtime():
    """Однократно распаковать встроенные bin/lists/utils, presets.json,
    пресеты *.bat и TgWsProxy рядом с exe. Существующее не перезаписывается."""
    src = _meipass()
    if not src or not os.path.isdir(src):
        return []
    copied = []

    def copy_if_absent(sp, dp):
        if os.path.isfile(sp) and not os.path.exists(dp):
            try:
                os.makedirs(os.path.dirname(dp), exist_ok=True)
                shutil.copy2(sp, dp)
                copied.append(dp)
            except Exception:
                pass

    os.makedirs(BASE, exist_ok=True)
    for sub in RUNTIME_SUBDIRS:
        s = os.path.join(src, sub)
        if os.path.isdir(s):
            for name in os.listdir(s):
                copy_if_absent(os.path.join(s, name), os.path.join(BASE, sub, name))
    for name in os.listdir(src):
        if name.lower().endswith(".bat"):
            copy_if_absent(os.path.join(src, name), os.path.join(BASE, name))
    for name in RUNTIME_ROOT_FILES:
        copy_if_absent(os.path.join(src, name), os.path.join(BASE, name))
    return copied


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_runtime():
    """Сверить критичные бинарники на диске со встроенными в .exe и
    перезаписать повреждённые/изменённые. Возвращает список исправленных."""
    src = _meipass()
    if not src or not os.path.isdir(src):
        return []
    fixed = []
    for sub in INTEGRITY_SUBDIRS:
        s = os.path.join(src, sub)
        d = os.path.join(BASE, sub)
        if not os.path.isdir(s):
            continue
        for name in os.listdir(s):
            sp, dp = os.path.join(s, name), os.path.join(d, name)
            if not os.path.isfile(sp):
                continue
            try:
                if (not os.path.isfile(dp)) or _sha256(sp) != _sha256(dp):
                    shutil.copy2(sp, dp)
                    fixed.append(dp)
            except Exception:
                pass
    return fixed


def refresh_defaults():
    """Досылает обновлённые апстрим-дефолты (домен-списки/утилиты) из встроенных
    в .exe на уже существующую установку при обновлении версии.

    В отличие от bin это не «целостность», а «свежие апстрим-данные»: перезапись
    выполняется один раз на новую версию (метка defaults_version в конфиге),
    чтобы не затирать ручные правки пользователя при каждом запуске. Файл
    обновляется только если действительно отличается. Пользовательские
    *-user.txt и управляемый тумблером ipset-all.txt не затрагиваются."""
    src = _meipass()
    if not src or not os.path.isdir(src):
        return []
    cfg = load_config()
    if cfg.get("defaults_version") == APP_VERSION:
        return []
    refreshed = []
    for rel in REFRESH_DEFAULT_FILES:
        sp, dp = os.path.join(src, rel), os.path.join(BASE, rel)
        if not os.path.isfile(sp):
            continue
        try:
            if (not os.path.isfile(dp)) or _sha256(sp) != _sha256(dp):
                os.makedirs(os.path.dirname(dp), exist_ok=True)
                shutil.copy2(sp, dp)
                refreshed.append(dp)
        except Exception:
            pass
    cfg["defaults_version"] = APP_VERSION
    save_config(cfg)
    return refreshed


# --------------------------------------------------------------------------- #
#  Скрытый запуск команд
# --------------------------------------------------------------------------- #
def _startupinfo():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si


def run_hidden(cmd, shell=False, timeout=30):
    return subprocess.run(
        cmd, shell=shell, capture_output=True, text=True,
        startupinfo=_startupinfo(), creationflags=CREATE_NO_WINDOW,
        timeout=timeout, encoding="utf-8", errors="replace",
    )


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, ""
    else:
        exe, params = sys.executable, f'"{os.path.abspath(sys.argv[0])}"'
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, BASE, 1)


# --------------------------------------------------------------------------- #
#  Разбор стратегий (.bat -> аргументы) и пресеты как данные
# --------------------------------------------------------------------------- #
def natural_key(name):
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]


def list_strategies():
    """Список .bat-стратегий (для импорта/конвертации в presets.json)."""
    result = []
    try:
        names = os.listdir(BASE)
    except Exception:
        return result
    for name in names:
        if not name.lower().endswith(".bat"):
            continue
        if name.lower().startswith("service"):
            continue
        try:
            with open(os.path.join(BASE, name), encoding="utf-8", errors="replace") as f:
                if "winws.exe" not in f.read().lower():
                    continue
        except Exception:
            continue
        result.append(name)
    result.sort(key=natural_key)
    return result


def extract_winws_argstring(bat_path):
    with open(bat_path, encoding="utf-8", errors="replace") as f:
        raw = f.read().splitlines()
    start_i = None
    for i, ln in enumerate(raw):
        if "winws.exe" in ln.lower():
            start_i = i
            break
    if start_i is None:
        return None
    parts = []
    i = start_i
    while i < len(raw):
        ln = raw[i].rstrip()
        cont = ln.endswith("^")
        if cont:
            ln = ln[:-1]
        parts.append(ln)
        if not cont:
            break
        i += 1
    full = " ".join(parts)
    idx = full.lower().find("winws.exe")
    after = full[idx + len("winws.exe"):].lstrip()
    if after.startswith('"'):
        after = after[1:]
    return after.strip()


def game_filter_values(mode):
    return {
        "off": ("12", "12"),
        "all": ("1024-65535", "1024-65535"),
        "tcp": ("1024-65535", "12"),
        "udp": ("12", "1024-65535"),
    }.get(mode, ("12", "12"))


def substitute(argstr, gf_tcp, gf_udp):
    s = argstr
    s = s.replace("%BIN%", BIN).replace("%LISTS%", LISTS)
    s = s.replace("%GameFilterTCP%", gf_tcp).replace("%GameFilterUDP%", gf_udp)
    s = s.replace("%GameFilter%", gf_tcp)
    return s


def tokenize(s):
    tokens, cur, in_q = [], "", False
    for ch in s:
        if ch == '"':
            in_q = not in_q
        elif ch.isspace() and not in_q:
            if cur:
                tokens.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        tokens.append(cur)
    return tokens


def build_args_str(argstr, mode):
    """Из строки аргументов с плейсхолдерами -> список аргументов winws.exe."""
    if not argstr:
        return None
    gf_tcp, gf_udp = game_filter_values(mode)
    return tokenize(substitute(argstr, gf_tcp, gf_udp))


def build_args(bat_path, mode):
    """Совместимость: список аргументов из .bat-файла."""
    return build_args_str(extract_winws_argstring(bat_path), mode)


def strategy_signature(argstr):
    """Набор признаков стратегии (тип десинка и т.п.) для оценки «похожести».
    Похожие по сигнатуре стратегии чаще работают/ломаются вместе при смене DPI."""
    s = (argstr or "").lower()
    feats = set()
    for m in re.findall(r"--dpi-desync=([a-z0-9,]+)", s):
        for part in m.split(","):
            if part:
                feats.add("d:" + part)
    for m in re.findall(r"--dpi-desync-fooling=([a-z0-9,]+)", s):
        feats.add("f:" + m)
    if "--dpi-desync-split-seqovl" in s:
        feats.add("seqovl")
    if "--dpi-desync-fake-quic" in s or "quic" in s:
        feats.add("quic")
    if "--ip-id=" in s:
        feats.add("ipid")
    return feats


def prioritize_presets(presets, last_name=None, pool=None):
    """Упорядочить пресеты для авто-поиска по приоритету: последний рабочий →
    запасной пул (recovery_pool) → похожие по сигнатуре десинка → остальные.
    Стабильная сортировка сохраняет исходный порядок при равных рангах."""
    pool = list(pool or [])
    by_name = {p["name"]: p for p in presets}
    last = by_name.get(last_name)
    last_sig = strategy_signature(last["args"]) if last else set()

    def rank(p):
        name = p["name"]
        if last_name and name == last_name:
            return (0, 0)
        if name in pool:
            return (1, pool.index(name))
        overlap = len(strategy_signature(p["args"]) & last_sig) if last_sig else 0
        return (2, -overlap)

    return sorted(presets, key=rank)


def _preset_id(name):
    return re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "preset"


def generate_presets_json(force=False):
    """Сконвертировать .bat-стратегии в декларативный presets.json (однократно)."""
    if os.path.exists(PRESETS_JSON) and not force:
        return False
    presets = []
    for fname in list_strategies():
        argstr = extract_winws_argstring(os.path.join(BASE, fname))
        if not argstr:
            continue
        base = os.path.splitext(fname)[0]
        presets.append({
            "id": _preset_id(base),
            "name": base,
            "source_bat": fname,
            "description": "",
            "label": "recommended" if fname == "general.bat" else None,
            "engine": "winws1",
            "args": argstr,
        })
    presets.sort(key=lambda p: (p["name"] != "general", natural_key(p["name"])))
    try:
        with open(PRESETS_JSON, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "presets": presets}, f,
                      ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_presets():
    """Список пресетов: из presets.json, иначе — построить из .bat в памяти."""
    try:
        with open(PRESETS_JSON, encoding="utf-8") as f:
            presets = json.load(f).get("presets", [])
        if presets:
            return presets
    except Exception:
        pass
    presets = []
    for fname in list_strategies():
        argstr = extract_winws_argstring(os.path.join(BASE, fname))
        if not argstr:
            continue
        base = os.path.splitext(fname)[0]
        presets.append({
            "id": _preset_id(base), "name": base, "source_bat": fname,
            "description": "", "label": "recommended" if fname == "general.bat" else None,
            "engine": "winws1", "args": argstr,
        })
    presets.sort(key=lambda p: (p["name"] != "general", natural_key(p["name"])))
    return presets


def quote_for_service(tok):
    q = '\\"'
    needs = lambda v: (":" in v) or (" " in v)
    if tok.startswith("--") and "=" in tok:
        k, v = tok.split("=", 1)
        if needs(v):
            v = q + v + q
        return k + "=" + v
    if needs(tok):
        return q + tok + q
    return tok


# --------------------------------------------------------------------------- #
#  Настройки
# --------------------------------------------------------------------------- #
def get_game_mode():
    if not os.path.exists(GAME_FLAG):
        return "off"
    try:
        txt = open(GAME_FLAG, encoding="utf-8", errors="replace").read().strip().lower()
    except Exception:
        return "off"
    return txt if txt in ("all", "tcp", "udp") else "udp"


def set_game_mode(mode):
    os.makedirs(UTILS, exist_ok=True)
    if mode == "off":
        if os.path.exists(GAME_FLAG):
            os.remove(GAME_FLAG)
    else:
        with open(GAME_FLAG, "w", encoding="utf-8") as f:
            f.write(mode)


def get_update_enabled():
    return os.path.exists(UPDATE_FLAG)


def set_update_enabled(enabled):
    os.makedirs(UTILS, exist_ok=True)
    if enabled:
        with open(UPDATE_FLAG, "w", encoding="utf-8") as f:
            f.write("ENABLED")
    elif os.path.exists(UPDATE_FLAG):
        os.remove(UPDATE_FLAG)


def get_ipset_status():
    try:
        with open(IPSET_FILE, encoding="utf-8", errors="replace") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except FileNotFoundError:
        return "нет файла"
    if not lines:
        return "any (все IP)"
    if len(lines) == 1 and lines[0].strip() == "203.0.113.113/32":
        return "none (выкл.)"
    return f"loaded ({len(lines)} строк)"


def ipset_enabled():
    """IPSet-фильтр в рабочем состоянии (не заглушка 'none (выкл.)').
    Используется, чтобы плановое автообновление не включало то, что выключено."""
    return not get_ipset_status().startswith("none")


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(UTILS, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def export_settings(path):
    """Сохранить конфиг и пресеты в один файл (бэкап/перенос)."""
    data = {"app": "ZapretGUI", "version": APP_VERSION, "config": load_config()}
    try:
        with open(PRESETS_JSON, encoding="utf-8") as f:
            data["presets"] = json.load(f)
    except Exception:
        pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True


def import_settings(path):
    """Восстановить конфиг и пресеты из файла. Возвращает (ok, сообщение)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return False, "неверный формат файла"
    n = 0
    if isinstance(data.get("config"), dict):
        save_config(data["config"])
        n += 1
    if isinstance(data.get("presets"), dict) and data["presets"].get("presets"):
        try:
            with open(PRESETS_JSON, "w", encoding="utf-8") as f:
                json.dump(data["presets"], f, ensure_ascii=False, indent=2)
            n += 1
        except Exception:
            pass
    if not n:
        return False, "в файле нет настроек/пресетов"
    return True, "настройки импортированы"


# --------------------------------------------------------------------------- #
#  Процесс / служба
# --------------------------------------------------------------------------- #
def winws_running():
    try:
        out = run_hidden(["tasklist", "/FI", "IMAGENAME eq winws.exe"]).stdout
        return "winws.exe" in out.lower()
    except Exception:
        return False


def service_installed():
    try:
        return run_hidden(["sc", "query", SERVICE_NAME]).returncode == 0
    except Exception:
        return False


def service_running():
    try:
        return "RUNNING" in run_hidden(["sc", "query", SERVICE_NAME]).stdout.upper()
    except Exception:
        return False


def enable_tcp_timestamps():
    try:
        out = run_hidden(["netsh", "interface", "tcp", "show", "global"]).stdout.lower()
        if "timestamps" in out and "enabled" in out:
            return
        run_hidden(["netsh", "interface", "tcp", "set", "global", "timestamps=enabled"])
    except Exception:
        pass


def remove_windivert():
    run_hidden(["net", "stop", "WinDivert"])
    run_hidden(["sc", "delete", "WinDivert"])
    run_hidden(["net", "stop", "WinDivert14"])
    run_hidden(["sc", "delete", "WinDivert14"])


def kill_winws_only():
    run_hidden(["taskkill", "/IM", "winws.exe", "/F"])


def start_winws_silent(args):
    return subprocess.Popen(
        [WINWS] + args, cwd=BIN,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        startupinfo=_startupinfo(), creationflags=CREATE_NO_WINDOW,
    )


def start_winws_logged(args):
    return subprocess.Popen(
        [WINWS] + args, cwd=BIN,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        startupinfo=_startupinfo(), creationflags=CREATE_NO_WINDOW, bufsize=1,
    )


def install_service(display_name, argstr, mode):
    """Создать службу автозапуска из строки аргументов. -> (ok, log)."""
    args = build_args_str(argstr, mode)
    if not args:
        return False, "Не удалось разобрать аргументы пресета."
    logs = []
    kill_winws_only()
    run_hidden(["net", "stop", SERVICE_NAME])
    run_hidden(["sc", "delete", SERVICE_NAME])
    enable_tcp_timestamps()

    svc_args = " ".join(quote_for_service(t) for t in args)
    binpath = f'\\"{WINWS}\\" {svc_args}'
    cmd = (f'sc create {SERVICE_NAME} binPath= "{binpath}" '
           f'DisplayName= "zapret" start= auto')
    res = run_hidden(cmd, shell=True)
    logs.append((res.stdout or res.stderr).strip())
    run_hidden(["sc", "description", SERVICE_NAME, "Zapret DPI bypass software"])
    sres = run_hidden(["sc", "start", SERVICE_NAME])
    logs.append((sres.stdout or sres.stderr).strip())

    run_hidden(["reg", "add", r"HKLM\System\CurrentControlSet\Services\zapret",
                "/v", "zapret-discord-youtube", "/t", "REG_SZ", "/d", display_name, "/f"])
    return service_installed(), "\n".join(x for x in logs if x)


def remove_service():
    run_hidden(["net", "stop", SERVICE_NAME])
    run_hidden(["sc", "delete", SERVICE_NAME])
    kill_winws_only()
    remove_windivert()


def update_ipset():
    try:
        if os.path.exists(IPSET_FILE):
            backup = IPSET_FILE + ".backup"
            try:
                if os.path.exists(backup):
                    os.remove(backup)
                os.replace(IPSET_FILE, backup)
            except Exception:
                pass
        req = urllib.request.Request(IPSET_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        with open(IPSET_FILE, "wb") as f:
            f.write(data)
        n = len([x for x in data.decode("utf-8", "replace").splitlines() if x.strip()])
        return True, f"IPSet обновлён: {n} строк."
    except Exception as e:
        return False, f"Ошибка обновления IPSet: {e}"


# --------------------------------------------------------------------------- #
#  Свои домены для обхода (list-general-user.txt) и автообновление списков
# --------------------------------------------------------------------------- #
def normalize_domain(raw):
    """Привести введённую строку к голому домену: убрать схему, путь, порт,
    www., привести к нижнему регистру. Вернёт '' если это не похоже на домен."""
    s = (raw or "").strip().lower()
    if not s or s[0] in "#;":
        return ""
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    s = s.split("@")[-1].split(":", 1)[0].strip().strip(".")
    if s.startswith("www."):
        s = s[4:]
    if not s or "." not in s or " " in s:
        return ""
    if not re.match(r"^[a-z0-9.*_-]+$", s):
        try:                                   # IDN (кириллица и т.п.) -> punycode
            s = s.encode("idna").decode("ascii")
        except Exception:
            return ""
    return s


def read_user_domains():
    """Список доменов из list-general-user.txt (без комментариев/пустых строк)."""
    try:
        with open(USER_LIST_FILE, encoding="utf-8", errors="replace") as f:
            raw = f.read().splitlines()
    except FileNotFoundError:
        return []
    except Exception:
        return []
    out = []
    for ln in raw:
        d = ln.strip()
        if d and not d.startswith("#"):
            out.append(d)
    return out


def write_user_domains(domains):
    """Нормализовать, убрать дубли и записать пользовательские домены.
    Возвращает итоговый отсортированный список того, что записано."""
    seen, clean = set(), []
    for d in domains:
        nd = normalize_domain(d)
        if nd and nd not in seen:
            seen.add(nd)
            clean.append(nd)
    clean.sort()
    os.makedirs(LISTS, exist_ok=True)
    with open(USER_LIST_FILE, "w", encoding="utf-8") as f:
        if clean:
            f.write("\n".join(clean) + "\n")
    return clean


def update_lists():
    """Скачать свежие дефолтные списки доменов из upstream (Flowseal).
    Пользовательские *-user.txt и ipset не трогаются. -> (ok_any, сообщение)."""
    parts, ok_any = [], False
    for name in LIST_UPDATE_FILES:
        try:
            req = urllib.request.Request(
                LIST_RAW_BASE + name,
                headers={"Cache-Control": "no-cache", "User-Agent": "ZapretGUI"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            if not data.strip():
                parts.append(f"{name}: пусто")
                continue
            with open(os.path.join(LISTS, name), "wb") as f:
                f.write(data)
            n = len([x for x in data.decode("utf-8", "replace").splitlines()
                     if x.strip() and not x.strip().startswith("#")])
            parts.append(f"{name.replace('list-', '').replace('.txt', '')}: {n}")
            ok_any = True
        except Exception as e:
            parts.append(f"{name}: ошибка ({e})")
    if ok_any:
        cfg = load_config()
        cfg["lists_last_update"] = int(time.time())
        save_config(cfg)
    return ok_any, ("Списки обновлены — " if ok_any else "Не удалось обновить — ") + "; ".join(parts)


def lists_last_update_ts():
    try:
        return int(load_config().get("lists_last_update", 0))
    except Exception:
        return 0


def lists_update_due():
    """True, если включено автообновление и прошёл интервал."""
    cfg = load_config()
    if not cfg.get("lists_auto_update"):
        return False
    last = int(cfg.get("lists_last_update", 0) or 0)
    return (time.time() - last) >= LISTS_UPDATE_INTERVAL_DAYS * 86400


def ensure_telegram_bypass_exclude():
    """Добавить диапазоны Telegram в ipset-exclude-user.txt, чтобы winws не
    десинкал соединения встроенного Telegram-прокси. Идемпотентно.
    -> True, если файл был изменён (значит обход стоит перезапустить)."""
    try:
        existing = []
        if os.path.exists(IPSET_EXCLUDE_USER_FILE):
            with open(IPSET_EXCLUDE_USER_FILE, encoding="utf-8", errors="replace") as f:
                existing = [ln.strip() for ln in f if ln.strip()]
        have = set(existing)
        added = [r for r in TELEGRAM_IP_RANGES if r not in have]
        if not added:
            return False
        os.makedirs(LISTS, exist_ok=True)
        with open(IPSET_EXCLUDE_USER_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(existing + added) + "\n")
        return True
    except Exception:
        return False


def run_diagnostics():
    out = []
    out.append(("RUNNING" in run_hidden(["sc", "query", "BFE"]).stdout.upper(),
                "Base Filtering Engine"))
    g = run_hidden(["netsh", "interface", "tcp", "show", "global"]).stdout.lower()
    out.append(("timestamps" in g and "enabled" in g, "TCP timestamps"))
    out.append((os.path.exists(os.path.join(BIN, "WinDivert64.sys")),
                "WinDivert64.sys в bin\\"))
    found = [s for s in CONFLICTING_SERVICES
             if run_hidden(["sc", "query", s]).returncode == 0]
    out.append((not found, "Конфликтующие обходы: "
                + (", ".join(found) if found else "нет")))
    out.append((winws_running(), "winws.exe запущен"))
    return out


# --------------------------------------------------------------------------- #
#  Расширенная диагностика и авто-починка (Фаза 7)
# --------------------------------------------------------------------------- #
def _port_in_use(port):
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", int(port)))
        return False
    except OSError:
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def diagnose():
    """Полная диагностика среды. -> список словарей:
    {title, status: ok|warn|bad, detail, fix: ключ для apply_fix() или None}."""
    items = []

    def add(title, status, detail="", fix=None):
        items.append({"title": title, "status": status, "detail": detail, "fix": fix})

    admin = is_admin()
    add("Права администратора", "ok" if admin else "bad",
        "есть" if admin else "winws не сможет работать — запустите от администратора")

    bfe = "RUNNING" in run_hidden(["sc", "query", "BFE"]).stdout.upper()
    add("Base Filtering Engine (BFE)", "ok" if bfe else "bad",
        "служба запущена" if bfe else "нужна для WinDivert — запустите службу",
        None if bfe else "start_bfe")

    sys_ok = os.path.exists(os.path.join(BIN, "WinDivert64.sys"))
    dll_ok = os.path.exists(os.path.join(BIN, "WinDivert.dll"))
    add("Файлы WinDivert", "ok" if (sys_ok and dll_ok) else "bad",
        "WinDivert64.sys и WinDivert.dll на месте" if (sys_ok and dll_ok)
        else "в bin\\ не хватает файлов драйвера")

    wd_exists = run_hidden(["sc", "query", "WinDivert"]).returncode == 0
    running = winws_running()
    if wd_exists and not running:
        add("Драйвер WinDivert", "warn",
            "служба драйвера зависла от прошлого запуска", "reset_windivert")
    else:
        add("Драйвер WinDivert", "ok",
            "загружен (обход работает)" if wd_exists
            else "не загружен (норма, когда обход выключен)")

    g = run_hidden(["netsh", "interface", "tcp", "show", "global"]).stdout.lower()
    ts = "timestamps" in g and "enabled" in g
    add("TCP timestamps", "ok" if ts else "warn",
        "включены" if ts else "выключены — нужны некоторым стратегиям",
        None if ts else "enable_ts")

    found_svc = [s for s in CONFLICTING_SERVICES
                 if run_hidden(["sc", "query", s]).returncode == 0]
    add("Конфликтующие службы обхода", "ok" if not found_svc else "warn",
        "не найдено" if not found_svc else "найдено: " + ", ".join(found_svc),
        None if not found_svc else "stop_conflicts")

    tl = run_hidden(["tasklist"]).stdout.lower()
    found_proc = sorted({p for p in DPI_CONFLICT_PROCS if p in tl})
    add("Сторонние DPI-программы", "ok" if not found_proc else "warn",
        "не запущены" if not found_proc else "запущены: " + ", ".join(found_proc),
        None if not found_proc else "stop_conflicts")

    nic = [label for svc, label in NIC_CONFLICT.items()
           if run_hidden(["sc", "query", svc]).returncode == 0]
    add("Сетевые оптимизаторы", "ok" if not nic else "warn",
        "не обнаружены" if not nic
        else "обнаружено: " + ", ".join(nic) + " — могут мешать WinDivert")

    port = tg_get_port()
    if tg_proxy_running():
        add(f"Порт Telegram-прокси ({port})", "ok", "занят нашим прокси")
    elif _port_in_use(port):
        add(f"Порт Telegram-прокси ({port})", "warn",
            "занят другим процессом — смените порт в разделе «Telegram»")
    else:
        add(f"Порт Telegram-прокси ({port})", "ok", "свободен")

    d = doh_status()
    add("Шифрованный DNS (DoH)", "ok" if d.get("enabled") else "warn",
        f"включён ({d.get('provider')})" if d.get("enabled")
        else "выключен — можно включить на «Управлении»")

    try:
        res = check_hosts(["discord.com", "www.youtube.com"], 3.0, attempts=1)
        okn = sum(1 for h in res if res[h][0])
    except Exception:
        okn = 0
    add("Доступность Discord/YouTube", "ok" if okn == 2 else ("warn" if okn == 1 else "bad"),
        f"{okn}/2 доступны по TLS")

    return items


def stop_conflicts():
    """Остановить конфликтующие службы и процессы сторонних обходов."""
    stopped = []
    for s in CONFLICTING_SERVICES:
        if run_hidden(["sc", "query", s]).returncode == 0:
            run_hidden(["net", "stop", s])
            run_hidden(["sc", "stop", s])
            stopped.append(s)
    for p in DPI_CONFLICT_PROCS:
        if run_hidden(["taskkill", "/IM", p, "/F"]).returncode == 0:
            stopped.append(p)
    return ("Остановлено: " + ", ".join(sorted(set(stopped)))) if stopped \
        else "Конфликтующих служб и процессов не найдено."


def apply_fix(key):
    """Выполнить авто-починку по ключу из diagnose()[...]['fix']. -> сообщение."""
    if key == "start_bfe":
        run_hidden(["sc", "config", "BFE", "start=", "auto"])
        run_hidden(["net", "start", "BFE"])
        return "BFE: запуск выполнен."
    if key == "reset_windivert":
        remove_windivert()
        return "Драйвер WinDivert сброшен."
    if key == "enable_ts":
        run_hidden(["netsh", "interface", "tcp", "set", "global", "timestamps=enabled"])
        return "TCP timestamps включены."
    if key == "stop_conflicts":
        return stop_conflicts()
    return ""


# --------------------------------------------------------------------------- #
#  Самообновление через GitHub Releases
# --------------------------------------------------------------------------- #
def _version_tuple(v):
    v = (v or "").strip().lstrip("vV")
    out = []
    for part in re.split(r"[.\-+]", v):
        out.append(int(part) if part.isdigit() else 0)
    return tuple(out) or (0,)


def check_update(timeout=10):
    """Проверить последний релиз на GitHub.
    -> {available, current, latest, url, notes} или {error}."""
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "ZapretGUI"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        latest = (data.get("tag_name") or "").strip()
        notes = data.get("body") or ""
        url, size = "", 0
        for a in data.get("assets", []):
            if a.get("name", "").lower().endswith(".zip"):
                url = a.get("browser_download_url", "")
                size = int(a.get("size") or 0)
                break
        available = bool(latest) and _version_tuple(latest) > _version_tuple(APP_VERSION)
        return {"available": available, "current": APP_VERSION,
                "latest": latest or "?", "url": url, "size": size, "notes": notes}
    except Exception as e:
        return {"error": str(e)}


def download_update(url, dest, progress_cb=None, timeout=180, expected_size=0):
    """Скачать архив обновления с проверкой целостности по размеру."""
    req = urllib.request.Request(url, headers={"User-Agent": "ZapretGUI"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or expected_size or 0)
        got = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress_cb and total:
                    progress_cb(got / total)
    if expected_size and os.path.getsize(dest) != expected_size:
        raise RuntimeError("Размер загрузки не совпал — повреждённый файл")
    return dest


def apply_update(zip_path):
    """Распаковать архив обновления и заменить установку (через .bat-хелпер).
    Доступно только в собранном приложении (onedir)."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Самообновление доступно только в собранном приложении")
    install_dir = BASE
    pid = os.getpid()
    temp_root = os.path.join(os.environ.get("TEMP", BASE),
                             f"zapret_upd_{int(time.time())}")
    os.makedirs(temp_root, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(temp_root)

    src = None
    for root, _dirs, files in os.walk(temp_root):
        if "ZapretControl.exe" in files:
            src = root
            break
    if not src:
        raise RuntimeError("В архиве обновления не найден ZapretControl.exe")

    bat = os.path.join(os.environ.get("TEMP", BASE), "zapret_gui_update.bat")
    script = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {pid}" | find "{pid}" >nul\r\n'
        "if not errorlevel 1 ( timeout /t 1 /nobreak >nul & goto wait )\r\n"
        f'robocopy "{src}" "{install_dir}" /E /IS /IT /R:2 /W:1 '
        "/NFL /NDL /NJH /NJS /NP >nul\r\n"
        f'start "" "{install_dir}\\ZapretControl.exe"\r\n'
        f'rmdir /s /q "{temp_root}" >nul 2>&1\r\n'
        f'del "{zip_path}" >nul 2>&1\r\n'
        'del "%~f0"\r\n'
    )
    with open(bat, "w", encoding="utf-8") as f:
        f.write(script)
    subprocess.Popen(["cmd", "/c", bat], creationflags=CREATE_NO_WINDOW,
                     startupinfo=_startupinfo())


# --------------------------------------------------------------------------- #
#  Асинхронные проверки соединения (TLS-рукопожатие к хостам)
# --------------------------------------------------------------------------- #
async def _check_host(host, timeout, attempts):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for _ in range(attempts):
        t0 = time.perf_counter()
        writer = None
        try:
            fut = asyncio.open_connection(host, 443, ssl=ctx, server_hostname=host)
            reader, writer = await asyncio.wait_for(fut, timeout=timeout)
            return True, (time.perf_counter() - t0) * 1000.0
        except Exception:
            continue
        finally:
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
    return False, None


async def _check_many(hosts, timeout, attempts):
    return await asyncio.gather(*[_check_host(h, timeout, attempts) for h in hosts])


def check_hosts(hosts, timeout, attempts=1):
    if not hosts:
        return {}
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        results = loop.run_until_complete(_check_many(hosts, timeout, attempts))
    finally:
        try:
            loop.close()
        except Exception:
            pass
    return dict(zip(hosts, results))


# --------------------------------------------------------------------------- #
#  Встроенный Telegram-прокси (вшитый пакет tgproxy, MIT, Flowseal/tg-ws-proxy)
# --------------------------------------------------------------------------- #
_tg_thread = None          # поток с asyncio-циклом прокси
_tg_async = None           # (loop, stop_event)
_tg_error = ""             # последняя ошибка запуска


def tg_get_secret():
    """32-символьный hex-секрет (стабильный, хранится в конфиге)."""
    cfg = load_config()
    sec = cfg.get("tg_secret")
    if not sec or len(sec) != 32:
        sec = os.urandom(16).hex()
        cfg["tg_secret"] = sec
        save_config(cfg)
    return sec


def tg_get_port():
    try:
        return int(load_config().get("tg_port", TG_DEFAULT_PORT))
    except Exception:
        return TG_DEFAULT_PORT


def set_tg_port(port):
    cfg = load_config()
    cfg["tg_port"] = int(port)
    save_config(cfg)


def tg_get_cfproxy():
    return bool(load_config().get("tg_cfproxy", True))


def tg_set_cfproxy(on):
    cfg = load_config()
    cfg["tg_cfproxy"] = bool(on)
    save_config(cfg)


def tg_regenerate_secret():
    cfg = load_config()
    cfg["tg_secret"] = os.urandom(16).hex()
    save_config(cfg)
    return cfg["tg_secret"]


def tg_proxy_url():
    return (f"tg://proxy?server={TG_DEFAULT_HOST}&port={tg_get_port()}"
            f"&secret=dd{tg_get_secret()}")


def tg_proxy_running():
    return bool(_tg_thread and _tg_thread.is_alive())


def tg_last_error():
    return _tg_error


def tg_proxy_log_path():
    return TG_PROXY_LOG


def _setup_proxy_logging():
    """Перехватить внутренний логгер прокси в файл logs/tg_proxy.log
    (приложение оконное — иначе логи прокси теряются). Идемпотентно."""
    import logging
    import logging.handlers
    lg = logging.getLogger("tg-mtproto-proxy")
    lg.setLevel(logging.INFO)
    if any(getattr(h, "_zapret", False) for h in lg.handlers):
        return
    try:
        os.makedirs(LOGS, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(
            TG_PROXY_LOG, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-5s  %(message)s"))
        h._zapret = True
        lg.addHandler(h)
        lg.propagate = False
    except Exception:
        pass


def tg_proxy_start():
    """Запустить встроенный MTProto-WS-прокси в фоновом потоке."""
    global _tg_thread, _tg_async, _tg_error
    if tg_proxy_running():
        return
    _tg_error = ""
    _setup_proxy_logging()
    from tgproxy.tg_ws_proxy import _run
    from tgproxy.config import proxy_config
    proxy_config.host = TG_DEFAULT_HOST
    proxy_config.port = tg_get_port()
    proxy_config.secret = tg_get_secret()
    # Запасной путь через публичные Cloudflare-воркеры (CF proxy). Их общий пул
    # часто отдаёт HTTP 429 (rate limit) и вызывает кратковременные обрывы. Если
    # прямые соединения к DC работают, фолбэк лучше отключить (тумблер в UI).
    proxy_config.fallback_cfproxy = bool(load_config().get("tg_cfproxy", True))

    def runner():
        global _tg_async, _tg_error
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ev = asyncio.Event()
        _tg_async = (loop, ev)
        try:
            loop.run_until_complete(_run(stop_event=ev))
        except Exception as exc:
            _tg_error = repr(exc)
        finally:
            try:
                loop.close()
            except Exception:
                pass
            _tg_async = None

    _tg_thread = threading.Thread(target=runner, daemon=True, name="tg-proxy")
    _tg_thread.start()


def tg_proxy_stop():
    global _tg_thread, _tg_async
    if _tg_async:
        loop, ev = _tg_async
        try:
            loop.call_soon_threadsafe(ev.set)
        except Exception:
            pass
        if _tg_thread:
            _tg_thread.join(timeout=5)
    _tg_thread = None


# --------------------------------------------------------------------------- #
#  Логи, single-instance, отчёт поддержки (Фаза 3)
# --------------------------------------------------------------------------- #
def current_log_path():
    os.makedirs(LOGS, exist_ok=True)
    return os.path.join(LOGS, f"zapret_{time.strftime('%Y-%m-%d')}.txt")


def acquire_single_instance(name="ZapretGUI_singleton_mutex"):
    """Создать именованный мьютекс. None — если копия уже запущена."""
    try:
        h = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return None
        return h
    except Exception:
        return 1  # не смогли проверить — разрешаем запуск


def make_support_bundle():
    """Собрать zip с диагностикой и логами для поддержки. -> путь."""
    os.makedirs(LOGS, exist_ok=True)
    path = os.path.join(LOGS, f"support_{time.strftime('%Y%m%d_%H%M%S')}.zip")
    diag = [
        f"app_version = {APP_VERSION}",
        f"frozen      = {getattr(sys, 'frozen', False)}",
        f"base        = {BASE}",
        f"admin       = {is_admin()}",
        f"winws_run   = {winws_running()}",
        f"service     = installed={service_installed()} running={service_running()}",
        f"tgws_run    = {tg_proxy_running()}",
        "",
        "diagnostics:",
    ]
    try:
        mark = {"ok": "  [OK]  ", "warn": "  [!]   ", "bad": "  [BAD] "}
        for it in diagnose():
            diag.append(mark.get(it["status"], "  [?]   ")
                        + f"{it['title']}: {it['detail']}")
    except Exception as e:
        diag.append(f"  diagnostics error: {e}")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("diagnostics.txt", "\n".join(diag))
        if os.path.isdir(LOGS):
            for name in os.listdir(LOGS):
                if name.endswith(".txt") or name.endswith(".log"):
                    try:
                        z.write(os.path.join(LOGS, name), f"logs/{name}")
                    except Exception:
                        pass
        try:
            z.write(CONFIG_FILE, "app_config.json")
        except Exception:
            pass
    return path


# --------------------------------------------------------------------------- #
#  Шифрованный DNS (DoH) — Фаза 4
# --------------------------------------------------------------------------- #
DOH_PROVIDERS = {
    "cloudflare": (["1.1.1.1", "1.0.0.1"], "https://cloudflare-dns.com/dns-query"),
    "google":     (["8.8.8.8", "8.8.4.4"], "https://dns.google/dns-query"),
}


def _ps(script, timeout=40):
    return run_hidden(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                       "-Command", script], timeout=timeout)


def doh_status():
    cfg = load_config()
    return {"enabled": bool(cfg.get("doh_enabled")),
            "provider": cfg.get("doh_provider", "cloudflare")}


def doh_enable(provider="cloudflare"):
    """Перевести системный DNS активных адаптеров на провайдера с DoH.
    При первом включении сохраняет прежний DNS; при смене провайдера на лету
    (когда DoH уже включён) прежний DNS НЕ перезахватывается."""
    ips, tmpl = DOH_PROVIDERS.get(provider, DOH_PROVIDERS["cloudflare"])
    ps_ips = ",".join(f"'{x}'" for x in ips)
    cfg = load_config()
    already = bool(cfg.get("doh_enabled"))

    register = (
        f"$ips=@({ps_ips}); $tmpl='{tmpl}'\n"
        "foreach($ip in $ips){ try{ Add-DnsClientDohServerAddress -ServerAddress $ip "
        "-DohTemplate $tmpl -AllowFallbackToUdp $false -AutoUpgrade $true -ErrorAction Stop }"
        "catch{ try{ Set-DnsClientDohServerAddress -ServerAddress $ip -DohTemplate $tmpl "
        "-AllowFallbackToUdp $false -AutoUpgrade $true -ErrorAction SilentlyContinue }catch{} } }\n"
    )

    if already:
        # только переустановить DNS на новый провайдер, prev не трогаем
        script = register + (
            "foreach($a in (Get-NetAdapter | Where-Object {$_.Status -eq 'Up'})){\n"
            "  Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ServerAddresses $ips "
            "-ErrorAction SilentlyContinue\n}\n"
            "Clear-DnsClientCache -ErrorAction SilentlyContinue\n"
        )
        _ps(script)
        cfg["doh_provider"] = provider
        cfg["doh_enabled"] = True
        save_config(cfg)
        return True

    # первое включение — захватить прежний DNS
    script = register + (
        "$prev=@{}\n"
        "foreach($a in (Get-NetAdapter | Where-Object {$_.Status -eq 'Up'})){\n"
        "  $cur=(Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue).ServerAddresses\n"
        "  $prev[[string]$a.ifIndex]=($cur -join ',')\n"
        "  Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ServerAddresses $ips "
        "-ErrorAction SilentlyContinue\n}\n"
        "Clear-DnsClientCache -ErrorAction SilentlyContinue\n"
        "$prev | ConvertTo-Json -Compress"
    )
    res = _ps(script)
    prev = {}
    try:
        line = (res.stdout or "").strip().splitlines()
        if line:
            data = json.loads(line[-1])
            if isinstance(data, dict):
                prev = {str(k): str(v) for k, v in data.items()}
    except Exception:
        prev = {}
    cfg["doh_enabled"] = True
    cfg["doh_provider"] = provider
    cfg["doh_prev"] = prev
    save_config(cfg)
    return True


def doh_disable():
    """Восстановить прежний DNS адаптеров."""
    cfg = load_config()
    prev = cfg.get("doh_prev", {}) or {}
    lines = []
    for idx, servers in prev.items():
        servers = (servers or "").strip()
        if servers:
            ipl = ",".join(f"'{s}'" for s in servers.split(",") if s.strip())
            lines.append(f"Set-DnsClientServerAddress -InterfaceIndex {idx} "
                         f"-ServerAddresses {ipl} -ErrorAction SilentlyContinue")
        else:
            lines.append(f"Set-DnsClientServerAddress -InterfaceIndex {idx} "
                         f"-ResetServerAddresses -ErrorAction SilentlyContinue")
    if not lines:
        lines.append("foreach($a in (Get-NetAdapter | Where-Object {$_.Status -eq 'Up'}))"
                     "{ Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex "
                     "-ResetServerAddresses -ErrorAction SilentlyContinue }")
    lines.append("Clear-DnsClientCache -ErrorAction SilentlyContinue")
    _ps("\n".join(lines))
    cfg["doh_enabled"] = False
    cfg["doh_prev"] = {}
    save_config(cfg)
    return True


# --------------------------------------------------------------------------- #
#  Откат прежнего Xbox-фикса (удалён — ломал Microsoft Store)
# --------------------------------------------------------------------------- #
LIST_EXCLUDE_USER = os.path.join(LISTS, "list-exclude-user.txt")
HOSTS_FILE = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "drivers", "etc", "hosts")
HOSTS_BEGIN = "# >>> ZapretGUI Xbox fix >>>"
HOSTS_END = "# <<< ZapretGUI Xbox fix <<<"
_XBOX_LEGACY_DOMAINS = [
    "microsoft.com", "microsoftonline.com", "live.com",
    "xboxlive.com", "xbox.com", "skype.com",
    "minecraft.net", "minecraftservices.com",
]
_EXCLUDE_PLACEHOLDER = "domain.example.abc"


def cleanup_xbox_legacy():
    """Откатить прежний Xbox-фикс: убрать блок из hosts и домены из
    list-exclude-user.txt. Вызывается один раз при старте."""
    # 1) убрать блок из hosts
    try:
        with open(HOSTS_FILE, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if HOSTS_BEGIN in text:
            pat = re.escape(HOSTS_BEGIN) + r".*?" + re.escape(HOSTS_END) + r"\r?\n?"
            with open(HOSTS_FILE, "w", encoding="utf-8") as f:
                f.write(re.sub(pat, "", text, flags=re.S))
            run_hidden(["ipconfig", "/flushdns"])
    except Exception:
        pass
    # 2) убрать наши домены из list-exclude-user.txt
    try:
        if os.path.exists(LIST_EXCLUDE_USER):
            existing = [l.strip() for l in
                        open(LIST_EXCLUDE_USER, encoding="utf-8", errors="replace")
                        .read().splitlines()]
            others = [l for l in existing if l and l not in _XBOX_LEGACY_DOMAINS]
            if not others:
                others = [_EXCLUDE_PLACEHOLDER]
            with open(LIST_EXCLUDE_USER, "w", encoding="utf-8") as f:
                f.write("\n".join(others) + "\n")
    except Exception:
        pass
    # 3) очистить флаг в конфиге
    try:
        cfg = load_config()
        if "xbox_fix" in cfg:
            cfg.pop("xbox_fix", None)
            save_config(cfg)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Антивирус (исключения Windows Defender) и перезапуск — Фаза 5
# --------------------------------------------------------------------------- #
def defender_exclusion_exists(path=None):
    path = path or BASE
    try:
        out = _ps(f"if((Get-MpPreference).ExclusionPath -contains '{path}')"
                  "{'YES'}else{'NO'}").stdout.strip()
        return out == "YES"
    except Exception:
        return False


def add_defender_exclusion(path=None):
    """Добавить папку и winws.exe в исключения Windows Defender (-> (ok, msg))."""
    path = path or BASE
    try:
        out = _ps(
            "try{"
            f"Add-MpPreference -ExclusionPath '{path}' -ErrorAction Stop;"
            f"Add-MpPreference -ExclusionProcess 'winws.exe' -ErrorAction SilentlyContinue;"
            "'OK'}catch{'FAIL:'+$_.Exception.Message}").stdout.strip()
        if out.startswith("OK"):
            return True, "папка и winws.exe добавлены в исключения Defender"
        return False, out.replace("FAIL:", "") or "не удалось (Defender отключён?)"
    except Exception as e:
        return False, str(e)


def remove_defender_exclusion(path=None):
    path = path or BASE
    try:
        _ps(f"Remove-MpPreference -ExclusionPath '{path}' -ErrorAction SilentlyContinue;"
            "Remove-MpPreference -ExclusionProcess 'winws.exe' -ErrorAction SilentlyContinue")
        return True
    except Exception:
        return False


def relaunch_app():
    """Перезапустить приложение (через .bat-хелпер: ждёт выхода и стартует заново)."""
    pid = os.getpid()
    if getattr(sys, "frozen", False):
        target = f'"{sys.executable}"'
    else:
        target = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
    bat = os.path.join(os.environ.get("TEMP", BASE), "zapret_relaunch.bat")
    script = (
        "@echo off\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {pid}" | find "{pid}" >nul\r\n'
        "if not errorlevel 1 ( timeout /t 1 /nobreak >nul & goto wait )\r\n"
        f'start "" {target}\r\n'
        'del "%~f0"\r\n'
    )
    with open(bat, "w", encoding="utf-8") as f:
        f.write(script)
    subprocess.Popen(["cmd", "/c", bat], creationflags=CREATE_NO_WINDOW,
                     startupinfo=_startupinfo())


# --------------------------------------------------------------------------- #
#  Полный автозапуск при входе в систему (задача планировщика) — Фаза 5
# --------------------------------------------------------------------------- #
AUTOSTART_TASK = "ZapretGUI_Autostart"


def autostart_enabled():
    """Существует ли задача автозапуска."""
    try:
        out = _ps(f"if(Get-ScheduledTask -TaskName '{AUTOSTART_TASK}' "
                  "-ErrorAction SilentlyContinue){'Y'}else{'N'}").stdout.strip()
        return out == "Y"
    except Exception:
        return False


def enable_autostart():
    """Создать задачу: запуск приложения при входе в систему с правами админа
    (без UAC-окна). Приложение получает аргумент --autostart. -> (ok, msg)."""
    if not getattr(sys, "frozen", False):
        return False, "автозапуск доступен только в собранном .exe"
    exe = sys.executable.replace("'", "''")
    script = (
        f"$a=New-ScheduledTaskAction -Execute '{exe}' -Argument '--autostart';"
        "$t=New-ScheduledTaskTrigger -AtLogOn;"
        "$p=New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive "
        "-RunLevel Highest;"
        "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero);"
        f"try{{Register-ScheduledTask -TaskName '{AUTOSTART_TASK}' -Action $a "
        "-Trigger $t -Principal $p -Settings $s -Force -ErrorAction Stop | Out-Null;'OK'}"
        "catch{'FAIL:'+$_.Exception.Message}"
    )
    res = _ps(script)
    out = (res.stdout or "").strip()
    if "OK" in out:
        return True, "задача автозапуска создана"
    return False, out.replace("FAIL:", "") or (res.stderr or "не удалось").strip()


def disable_autostart():
    try:
        _ps(f"Unregister-ScheduledTask -TaskName '{AUTOSTART_TASK}' "
            "-Confirm:$false -ErrorAction SilentlyContinue")
        return True
    except Exception:
        return False
