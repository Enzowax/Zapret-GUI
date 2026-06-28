"""Тесты разбора share-ссылок и генерации Xray-конфига (модуль vpn)."""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vpn  # noqa: E402


def test_parse_vless_reality():
    link = ("vless://11111111-2222-3333-4444-555555555555@example.com:443"
            "?type=tcp&security=reality&pbk=PBK&sid=01ab&sni=www.microsoft.com"
            "&fp=chrome&flow=xtls-rprx-vision#Srv")
    p = vpn.parse_share_link(link)
    assert p["protocol"] == "vless" and p["address"] == "example.com" and p["port"] == 443
    assert p["security"] == "reality" and p["flow"] == "xtls-rprx-vision"
    cfg = vpn.build_xray_config(p)
    out = cfg["outbounds"][0]
    assert out["protocol"] == "vless"
    assert out["streamSettings"]["security"] == "reality"
    assert out["streamSettings"]["realitySettings"]["publicKey"] == "PBK"
    json.dumps(cfg)                                   # валидный JSON


def test_parse_vmess_ws_tls():
    raw = base64.b64encode(json.dumps({
        "v": "2", "ps": "VM", "add": "1.2.3.4", "port": "443", "id": "uuid-x",
        "aid": "0", "net": "ws", "host": "cdn.example.com", "path": "/ws",
        "tls": "tls", "sni": "cdn.example.com"}).encode()).decode()
    p = vpn.parse_share_link("vmess://" + raw)
    assert p["protocol"] == "vmess" and p["network"] == "ws" and p["security"] == "tls"
    cfg = vpn.build_xray_config(p)
    assert cfg["outbounds"][0]["streamSettings"]["wsSettings"]["path"] == "/ws"


def test_parse_trojan_defaults_tls():
    p = vpn.parse_share_link("trojan://pass@example.org:443?type=ws&path=/t#T")
    assert p["protocol"] == "trojan" and p["security"] == "tls"  # trojan -> tls по умолчанию


def test_parse_ss():
    body = base64.b64encode(b"aes-256-gcm:secret").decode()
    p = vpn.parse_share_link(f"ss://{body}@1.2.3.4:8388#SS")
    assert p["protocol"] == "shadowsocks" and p["method"] == "aes-256-gcm"
    assert p["password"] == "secret" and p["port"] == 8388


def test_parse_unknown_returns_none():
    assert vpn.parse_share_link("http://nope") is None
    assert vpn.parse_share_link("") is None


def test_config_has_socks_and_http_inbounds():
    p = vpn.parse_share_link("vless://id@h:443?security=none#x")
    cfg = vpn.build_xray_config(p)
    protos = [i["protocol"] for i in cfg["inbounds"]]
    assert "socks" in protos and "http" in protos
