"""Встроенный VPN-клиент на ядре Xray-core (XTLS).

Два режима:
  • «прокси»  — xray поднимает локальный SOCKS5/HTTP; можно включить системный
                прокси Windows, тогда трафик приложений идёт через VPN без драйвера;
  • «туннель» — xray + tun2socks (драйвер wintun) заворачивают ВЕСЬ системный
                трафик в VPN (полноценный VPN-туннель, нужны права администратора).

Ядро (xray.exe), tun2socks и wintun.dll скачиваются автоматически при первом
запуске в папку BASE/vpn — в репозиторий тяжёлые бинарники не кладём.

ЭКСПЕРИМЕНТ («проба пера»): туннель-режим best-effort, отлаживается на реальном
сервере/ссылке пользователя.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import urllib.parse
import urllib.request
import zipfile

import zapret_core as zc

VPN_DIR = os.path.join(zc.BASE, "vpn")
XRAY_EXE = os.path.join(VPN_DIR, "xray.exe")
TUN2SOCKS_EXE = os.path.join(VPN_DIR, "tun2socks.exe")
WINTUN_DLL = os.path.join(VPN_DIR, "wintun.dll")
XRAY_CONFIG = os.path.join(VPN_DIR, "config.json")

XRAY_URL = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-windows-64.zip"
TUN2SOCKS_URL = "https://github.com/xjasonlyu/tun2socks/releases/latest/download/tun2socks-windows-amd64.zip"
WINTUN_URL = "https://www.wintun.net/builds/wintun-0.14.1.zip"

SOCKS_PORT = 10808
HTTP_PORT = 10809
TUN_NAME = "ZapretVPN"
TUN_ADDR = "10.10.0.2"
TUN_MASK = "255.255.255.0"
TUN_GW = "10.10.0.1"
TUN_DNS = "1.1.1.1"

_xray_proc = None
_tun_proc = None
_mode = None          # None | "proxy" | "tunnel"
_server_ip = None     # IP сервера (для удаления маршрута)


# --------------------------------------------------------------------------- #
#  Разбор share-ссылок (vless / vmess / trojan / ss)
# --------------------------------------------------------------------------- #
def _b64d(s):
    s = s.strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s).decode("utf-8", "replace")


_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://")


def _extract_links(text):
    return [ln.strip() for ln in text.replace("\r", "\n").split("\n")
            if ln.strip().startswith(_SCHEMES)]


# UA известных клиентов: многие подписочные панели отдают реальные конфиги
# только распознанным приложениям (иначе — заглушку «не поддерживается»).
# v2rayTun первым: ряд панелей именно ему отдают полноценный Xray-JSON.
SUB_USER_AGENTS = ["v2rayTun/2.9.0", "Streisand", "Happ/1.16.0", "Hiddify/2.5.0",
                   "v2rayNG/1.9.5", "v2rayN/7.0", "clash-verge/1.5.0"]

_PLACEHOLDER_WORDS = ("поддерж", "support", "unsupport", "traffic", "трафик",
                      "expire", "истек", "осталось", "subscription", "подписк")


def _is_placeholder(name):
    n = (name or "").lower()
    return any(w in n for w in _PLACEHOLDER_WORDS)


def _b64d_safe(text):
    try:
        return _b64d(text)
    except Exception:
        return ""


def _http_get(url, ua):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return ""


def _outbound_addr(ob):
    s = ob.get("settings", {})
    vn = s.get("vnext") or s.get("servers") or [{}]
    a = vn[0] if vn else {}
    return "%s:%s" % (a.get("address", "?"), a.get("port", "?"))


def _servers_from_json(data):
    """Xray-JSON подписки (формат v2rayTun): массив конфигов или один конфиг.
    Берём прокси-outbound каждого. Плейсхолдер-имя заменяем на адрес сервера."""
    cfgs = data if isinstance(data, list) else [data]
    out = []
    for cfg in cfgs:
        if not isinstance(cfg, dict):
            continue
        remark = cfg.get("remarks") or cfg.get("name") or ""
        for ob in cfg.get("outbounds", []):
            if ob.get("protocol") in ("vless", "vmess", "trojan", "shadowsocks"):
                name = remark if remark and not _is_placeholder(remark) else _outbound_addr(ob)
                ob = dict(ob)
                ob["tag"] = "proxy"
                out.append({"name": str(name)[:60], "outbound": ob})
                break
    return out


def _servers_from_links(links):
    out, seen = [], set()
    for lk in links:
        if lk in seen:
            continue
        seen.add(lk)
        p = parse_share_link(lk)
        if p:
            out.append({"name": (p.get("name") or p.get("address") or "server")[:60],
                        "link": lk})
    return out


def import_servers(text):
    """Из ввода — ссылка(и), base64-блоб, или URL подписки — вернуть список
    серверов {name, link} либо {name, outbound}."""
    text = (text or "").strip()
    if not text:
        return []
    if (text.startswith(("http://", "https://")) and "\n" not in text
            and not text.startswith(_SCHEMES)):
        return _import_subscription(text)
    links = _extract_links(text) or _extract_links(_b64d_safe(text))
    return _servers_from_links(links)


def _import_subscription(url):
    """Скачать подписку, пробуя UA известных клиентов; распознать и Xray-JSON,
    и base64/текст со ссылками. Вернуть набор с НАИБОЛЬШИМ числом реальных
    серверов (некоторые панели полный список отдают только определённому UA)."""
    best, fallback = [], []
    for ua in SUB_USER_AGENTS:
        body = _http_get(url, ua).strip()
        if not body:
            continue
        servers = []
        if body[:1] in "[{":                       # Xray-JSON (v2rayTun)
            try:
                servers = _servers_from_json(json.loads(body))
            except Exception:
                servers = []
        if not servers:                            # base64 / текст со ссылками
            links = _extract_links(body) or _extract_links(_b64d_safe(body))
            servers = _servers_from_links(links)
        real = [s for s in servers if not _is_placeholder(s["name"])]
        if len(real) > len(best):
            best = real
        fallback = fallback or servers
        if len(best) > 1:                          # нашли полноценный список
            break
    return best or fallback


def parse_share_link(link):
    """vless://… | vmess://… | trojan://… | ss://… -> dict или None."""
    link = (link or "").strip()
    try:
        if link.startswith("vless://"):
            return _parse_vless(link)
        if link.startswith("vmess://"):
            return _parse_vmess(link)
        if link.startswith("trojan://"):
            return _parse_trojan(link)
        if link.startswith("ss://"):
            return _parse_ss(link)
    except Exception:
        return None
    return None


def _common_stream(q):
    return {
        "network": q.get("type", "tcp"),
        "security": q.get("security", "none"),
        "sni": q.get("sni") or q.get("peer") or q.get("host", ""),
        "fp": q.get("fp", "chrome"),
        "alpn": q.get("alpn", ""),
        "pbk": q.get("pbk", ""),
        "sid": q.get("sid", ""),
        "spx": q.get("spx", ""),
        "path": urllib.parse.unquote(q.get("path", "")),
        "host": q.get("host", ""),
        "serviceName": urllib.parse.unquote(q.get("serviceName", "")),
    }


def _parse_vless(link):
    u = urllib.parse.urlparse(link)
    q = dict(urllib.parse.parse_qsl(u.query))
    d = {"protocol": "vless", "name": urllib.parse.unquote(u.fragment) or "VLESS",
         "address": u.hostname, "port": u.port or 443, "id": u.username,
         "encryption": q.get("encryption", "none"), "flow": q.get("flow", "")}
    d.update(_common_stream(q))
    return d


def _parse_trojan(link):
    u = urllib.parse.urlparse(link)
    q = dict(urllib.parse.parse_qsl(u.query))
    d = {"protocol": "trojan", "name": urllib.parse.unquote(u.fragment) or "Trojan",
         "address": u.hostname, "port": u.port or 443, "password": u.username}
    d.update(_common_stream(q))
    if d["security"] == "none":
        d["security"] = "tls"          # trojan почти всегда поверх TLS
    return d


def _parse_vmess(link):
    data = json.loads(_b64d(link[len("vmess://"):]))
    net = data.get("net", "tcp")
    tls = "tls" if str(data.get("tls", "")).lower() in ("tls", "true", "1") else "none"
    return {"protocol": "vmess", "name": data.get("ps", "VMess"),
            "address": data.get("add"), "port": int(data.get("port", 443)),
            "id": data.get("id"), "aid": int(data.get("aid", 0) or 0),
            "network": net, "security": tls,
            "sni": data.get("sni") or data.get("host", ""),
            "path": data.get("path", ""), "host": data.get("host", ""),
            "fp": data.get("fp", "chrome"), "alpn": data.get("alpn", ""),
            "serviceName": data.get("path", "") if net == "grpc" else ""}


def _parse_ss(link):
    body = link[len("ss://"):]
    name = "Shadowsocks"
    if "#" in body:
        body, frag = body.split("#", 1)
        name = urllib.parse.unquote(frag)
    if "@" in body:                      # ss://base64(method:pass)@host:port
        userinfo, hostport = body.rsplit("@", 1)
        method, password = _b64d(userinfo).split(":", 1)
    else:                                # ss://base64(method:pass@host:port)
        dec = _b64d(body)
        userinfo, hostport = dec.rsplit("@", 1)
        method, password = userinfo.split(":", 1)
    host, port = hostport.split(":")
    return {"protocol": "shadowsocks", "name": name, "address": host,
            "port": int(port.split("/")[0]), "method": method, "password": password,
            "network": "tcp", "security": "none"}


# --------------------------------------------------------------------------- #
#  Генерация конфигурации Xray
# --------------------------------------------------------------------------- #
def _stream_settings(p):
    net = p.get("network", "tcp")
    sec = p.get("security", "none")
    st = {"network": net, "security": sec}
    if net == "ws":
        st["wsSettings"] = {"path": p.get("path", "/"),
                            "headers": {"Host": p.get("host") or p.get("sni", "")}}
    elif net == "grpc":
        st["grpcSettings"] = {"serviceName": p.get("serviceName", "")}
    elif net == "tcp" and p.get("host"):
        st["tcpSettings"] = {"header": {"type": "http",
                             "request": {"headers": {"Host": [p.get("host")]}}}}
    if sec == "tls":
        tls = {"serverName": p.get("sni", ""), "fingerprint": p.get("fp", "chrome")}
        if p.get("alpn"):
            tls["alpn"] = p["alpn"].split(",")
        st["tlsSettings"] = tls
    elif sec == "reality":
        st["realitySettings"] = {"serverName": p.get("sni", ""),
                                 "fingerprint": p.get("fp", "chrome"),
                                 "publicKey": p.get("pbk", ""),
                                 "shortId": p.get("sid", ""),
                                 "spiderX": p.get("spx", "")}
    return st


def _build_outbound(p):
    proto = p["protocol"]
    if proto in ("vless", "vmess"):
        user = {"id": p["id"]}
        if proto == "vless":
            user["encryption"] = p.get("encryption", "none")
            if p.get("flow"):
                user["flow"] = p["flow"]
        else:
            user["alterId"] = p.get("aid", 0)
            user["security"] = "auto"
        settings = {"vnext": [{"address": p["address"], "port": p["port"],
                               "users": [user]}]}
    elif proto == "trojan":
        settings = {"servers": [{"address": p["address"], "port": p["port"],
                                 "password": p["password"]}]}
    elif proto == "shadowsocks":
        settings = {"servers": [{"address": p["address"], "port": p["port"],
                                 "method": p["method"], "password": p["password"]}]}
    else:
        raise ValueError("unsupported protocol: " + proto)
    return {"tag": "proxy", "protocol": proto, "settings": settings,
            "streamSettings": _stream_settings(p)}


def _config_with_outbound(out, socks_port=SOCKS_PORT, http_port=HTTP_PORT):
    out = dict(out)
    out["tag"] = "proxy"
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {"tag": "socks", "listen": "127.0.0.1", "port": socks_port,
             "protocol": "socks", "settings": {"udp": True, "auth": "noauth"},
             "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]}},
            {"tag": "http", "listen": "127.0.0.1", "port": http_port,
             "protocol": "http"},
        ],
        "outbounds": [
            out,
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
    }


def build_xray_config(p, socks_port=SOCKS_PORT, http_port=HTTP_PORT):
    return _config_with_outbound(_build_outbound(p), socks_port, http_port)


def _server_outbound(server):
    """server: dict {link} | {outbound} | строка-ссылка -> (outbound, name, address)
    или (None, '', '')."""
    if isinstance(server, dict) and server.get("outbound"):
        ob = server["outbound"]
        s = ob.get("settings", {})
        vn = s.get("vnext") or s.get("servers") or [{}]
        addr = (vn[0] if vn else {}).get("address", "")
        return ob, server.get("name") or addr, addr
    link = server.get("link") if isinstance(server, dict) else server
    p = parse_share_link(link)
    if not p or not p.get("address"):
        return None, "", ""
    name = (server.get("name") if isinstance(server, dict) else None) or p.get("name") or p["address"]
    return _build_outbound(p), name, p["address"]


# --------------------------------------------------------------------------- #
#  Авто-загрузка бинарников
# --------------------------------------------------------------------------- #
def _download_zip(url, dest_dir, want_files):
    """Скачать zip и распаковать нужные файлы (по имени) в dest_dir."""
    try:
        os.makedirs(dest_dir, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "ZapretGUI"})
        tmp = os.path.join(dest_dir, "_dl.zip")
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            f.write(r.read())
        with zipfile.ZipFile(tmp) as z:
            for member in z.namelist():
                base = os.path.basename(member)
                if base in want_files:
                    with z.open(member) as src, open(os.path.join(dest_dir, base), "wb") as out:
                        out.write(src.read())
        os.remove(tmp)
        return True, "ok"
    except Exception as e:
        return False, f"не удалось скачать {os.path.basename(url)}: {e}"


def ensure_xray():
    if os.path.exists(XRAY_EXE):
        return True, "ok"
    return _download_zip(XRAY_URL, VPN_DIR, {"xray.exe", "geoip.dat", "geosite.dat"})


def ensure_tunnel_bins():
    if not os.path.exists(TUN2SOCKS_EXE):
        ok, msg = _download_zip(TUN2SOCKS_URL, VPN_DIR, {"tun2socks-windows-amd64.exe",
                                                         "tun2socks.exe"})
        if not ok:
            return False, msg
        # переименовать к ожидаемому имени
        for n in ("tun2socks-windows-amd64.exe",):
            p = os.path.join(VPN_DIR, n)
            if os.path.exists(p) and not os.path.exists(TUN2SOCKS_EXE):
                os.replace(p, TUN2SOCKS_EXE)
    if not os.path.exists(WINTUN_DLL):
        ok, msg = _download_zip(WINTUN_URL, VPN_DIR, {"wintun.dll"})
        if not ok:
            return False, msg
    return True, "ok"


# --------------------------------------------------------------------------- #
#  Системный прокси Windows
# --------------------------------------------------------------------------- #
def set_system_proxy(port=HTTP_PORT):
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                           0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, f"127.0.0.1:{port}")
        winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ,
                          "localhost;127.*;10.*;192.168.*;<local>")
        winreg.CloseKey(k)
        _refresh_inet()
        return True
    except Exception:
        return False


def unset_system_proxy():
    """Снять системный прокси, НО только если он указывает на наш локальный порт —
    чтобы не затереть собственные настройки прокси пользователя."""
    try:
        import winreg
        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        kr = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ)
        try:
            server = winreg.QueryValueEx(kr, "ProxyServer")[0]
        except Exception:
            server = ""
        winreg.CloseKey(kr)
        if "127.0.0.1" not in str(server):
            return False                      # прокси не наш — не трогаем
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(k)
        _refresh_inet()
        return True
    except Exception:
        return False


def cleanup_leftovers():
    """Защитная очистка при старте приложения: если предыдущая сессия рухнула
    с включённым VPN — снять наш системный прокси и убить осиротевшие процессы,
    чтобы не остался сломанный интернет."""
    try:
        unset_system_proxy()
        zc.run_hidden(["taskkill", "/IM", "tun2socks.exe", "/F"])
        # xray.exe не трогаем тут вслепую — вдруг это не наш; но прокси сняли
    except Exception:
        pass


def _refresh_inet():
    try:
        import ctypes
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, 39, 0, 0)   # SETTINGS_CHANGED
        wininet.InternetSetOptionW(0, 37, 0, 0)   # REFRESH
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Запуск / остановка
# --------------------------------------------------------------------------- #
def _popen(args):
    return subprocess.Popen(args, cwd=VPN_DIR, creationflags=zc.CREATE_NO_WINDOW,
                            startupinfo=zc._startupinfo(),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _start_xray():
    global _xray_proc
    _xray_proc = _popen([XRAY_EXE, "run", "-c", XRAY_CONFIG])


def vpn_running():
    return _xray_proc is not None and _xray_proc.poll() is None


def vpn_status():
    return {"running": vpn_running(), "mode": _mode,
            "socks": SOCKS_PORT, "http": HTTP_PORT,
            "xray": os.path.exists(XRAY_EXE)}


def vpn_start(server, mode="proxy", system_proxy=False):
    """server: dict сервера ({name,link} или {name,outbound}) либо строка-ссылка.
    mode: 'proxy' (локальный SOCKS/HTTP, опц. системный прокси) или 'tunnel'
    (полный системный туннель через tun2socks). -> (ok, msg)."""
    global _mode, _server_ip
    if vpn_running():
        vpn_stop()
    out, name, addr = _server_outbound(server)
    if not out:
        return False, "Не удалось разобрать сервер (vless/vmess/trojan/ss)."
    ok, msg = ensure_xray()
    if not ok:
        return False, msg
    if mode == "tunnel":
        ok, msg = ensure_tunnel_bins()
        if not ok:
            return False, msg
    os.makedirs(VPN_DIR, exist_ok=True)
    with open(XRAY_CONFIG, "w", encoding="utf-8") as f:
        json.dump(_config_with_outbound(out), f, ensure_ascii=False, indent=2)
    _start_xray()
    try:
        _server_ip = socket.gethostbyname(addr) if addr else None
    except Exception:
        _server_ip = None
    _mode = mode
    if mode == "proxy":
        if system_proxy:
            set_system_proxy(HTTP_PORT)
        return True, (f"VPN-прокси «{name}» запущен · "
                      f"SOCKS5 127.0.0.1:{SOCKS_PORT} · HTTP :{HTTP_PORT}"
                      + (" · системный прокси включён" if system_proxy else ""))
    ok, msg = _start_tunnel()
    if not ok:
        vpn_stop()
        return False, msg
    return True, f"VPN-туннель «{name}» запущен (весь трафик через VPN)."


def _start_tunnel():
    global _tun_proc
    try:
        _tun_proc = _popen([TUN2SOCKS_EXE, "-device", "wintun://" + TUN_NAME,
                            "-proxy", f"socks5://127.0.0.1:{SOCKS_PORT}",
                            "-loglevel", "warning"])
        import time as _t
        _t.sleep(2.0)            # дать tun2socks создать адаптер
        # настроить адаптер и маршруты
        zc.run_hidden(["netsh", "interface", "ip", "set", "address",
                       TUN_NAME, "static", TUN_ADDR, TUN_MASK, TUN_GW])
        zc.run_hidden(["netsh", "interface", "ip", "set", "dns",
                       TUN_NAME, "static", TUN_DNS])
        gw = _default_gateway()
        if _server_ip and gw:    # сервер — мимо туннеля, чтобы не зациклить
            zc.run_hidden(["route", "add", _server_ip, "mask", "255.255.255.255",
                           gw, "metric", "5"])
        # весь трафик — в туннель (двумя /1, чтобы перебить дефолт)
        zc.run_hidden(["route", "add", "0.0.0.0", "mask", "128.0.0.0", TUN_GW, "metric", "1"])
        zc.run_hidden(["route", "add", "128.0.0.0", "mask", "128.0.0.0", TUN_GW, "metric", "1"])
        return True, "ok"
    except Exception as e:
        return False, f"туннель не поднялся: {e}"


def _teardown_tunnel():
    try:
        zc.run_hidden(["route", "delete", "0.0.0.0", "mask", "128.0.0.0"])
        zc.run_hidden(["route", "delete", "128.0.0.0", "mask", "128.0.0.0"])
        if _server_ip:
            zc.run_hidden(["route", "delete", _server_ip])
    except Exception:
        pass


def _default_gateway():
    try:
        out = zc.run_hidden(["powershell", "-NoProfile", "-Command",
                             "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
                             "Sort-Object RouteMetric | Select-Object -First 1)."
                             "NextHop"]).stdout.strip()
        return out.splitlines()[0].strip() if out else None
    except Exception:
        return None


def vpn_stop():
    global _xray_proc, _tun_proc, _mode
    if _mode == "tunnel":
        _teardown_tunnel()
    unset_system_proxy()
    for proc, name in ((_tun_proc, "tun2socks.exe"), (_xray_proc, "xray.exe")):
        try:
            if proc and proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
    zc.run_hidden(["taskkill", "/IM", "tun2socks.exe", "/F"])
    zc.run_hidden(["taskkill", "/IM", "xray.exe", "/F"])
    _xray_proc = None
    _tun_proc = None
    _mode = None
