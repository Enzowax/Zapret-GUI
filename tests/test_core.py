"""Юнит-тесты чистых функций ядра (без сети/сабпроцессов).
Гоняются в CI до сборки — гейт, чтобы не выпустить сломанный билд."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import zapret_core as zc  # noqa: E402


# --- normalize_domain ---------------------------------------------------- #
def test_normalize_domain_strips_scheme_path_www():
    assert zc.normalize_domain("https://www.RuTracker.org/forum/?x=1") == "rutracker.org"
    assert zc.normalize_domain("http://example.com:8080/path") == "example.com"
    assert zc.normalize_domain("discord.com") == "discord.com"
    assert zc.normalize_domain("user@host.net") == "host.net"


def test_normalize_domain_rejects_garbage():
    assert zc.normalize_domain("не домен") == ""
    assert zc.normalize_domain("   ") == ""
    assert zc.normalize_domain("# comment") == ""
    assert zc.normalize_domain("nodot") == ""


def test_normalize_domain_idn_to_punycode():
    assert zc.normalize_domain("рутрекер.рф") == "xn--e1aaowadjh.xn--p1ai"


# --- strategy signature / prioritize ------------------------------------- #
def test_strategy_signature_extracts_features():
    sig = zc.strategy_signature("--dpi-desync=fake,multisplit --dpi-desync-fooling=ts "
                                "--dpi-desync-split-seqovl=681 --ip-id=zero")
    assert "d:fake" in sig and "d:multisplit" in sig
    assert "f:ts" in sig and "seqovl" in sig and "ipid" in sig


def test_prioritize_presets_order():
    presets = [{"name": n, "args": "--dpi-desync=fake"} for n in
               ["a", "b", "last", "poolA", "poolB"]]
    order = [p["name"] for p in zc.prioritize_presets(
        presets, last_name="last", pool=["poolA", "poolB"])]
    assert order[0] == "last"            # последний рабочий — первым
    assert order[1:3] == ["poolA", "poolB"]   # затем пул в его порядке
    assert len(order) == len(presets)    # ничего не потеряли


# --- version compare ----------------------------------------------------- #
def test_version_tuple_ordering():
    assert zc._version_tuple("v2.30.0") > zc._version_tuple("v2.29.9")
    assert zc._version_tuple("2.4.0") > zc._version_tuple("2.3.99")
    assert zc._version_tuple("v1.0") == zc._version_tuple("1.0.0")[:2]


# --- tokenize / substitute / build_args ---------------------------------- #
def test_tokenize_respects_quotes():
    toks = zc.tokenize('--a=1 --b="c d" --e')
    assert toks == ["--a=1", "--b=c d", "--e"]


def test_build_args_substitutes_placeholders():
    args = zc.build_args_str('--wf-tcp=80,%GameFilterTCP% --x="%BIN%f.bin"', "off")
    joined = " ".join(args)
    assert "%GameFilterTCP%" not in joined and "%BIN%" not in joined
    assert "--wf-tcp=80,12" in joined


# --- tg_proxy_stats (temp file) ------------------------------------------ #
def test_tg_proxy_stats_parses_last_line(tmp_path, monkeypatch):
    p = tmp_path / "tg_proxy.log"
    p.write_text(
        "INFO start\n"
        "INFO stats: total=10 ws=3 cf=7 up=1.0KB down=2.0MB\n"
        "INFO stats: total=22 ws=9 cf=12 tcp_fb=1 up=5.0KB down=9.0MB | ws_bl: none\n",
        encoding="utf-8")
    monkeypatch.setattr(zc, "TG_PROXY_LOG", str(p))
    s = zc.tg_proxy_stats()
    assert s["total"] == "22" and s["ws"] == "9" and s["tcp_fb"] == "1"
    assert s["down"] == "9.0MB"


def test_tg_proxy_stats_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(zc, "TG_PROXY_LOG", str(tmp_path / "nope.log"))
    assert zc.tg_proxy_stats() is None


# --- ipset_enabled (temp file) ------------------------------------------- #
def test_ipset_enabled_detects_placeholder(tmp_path, monkeypatch):
    f = tmp_path / "ipset-all.txt"
    monkeypatch.setattr(zc, "IPSET_FILE", str(f))
    f.write_text("203.0.113.113/32\n", encoding="utf-8")   # заглушка = выкл
    assert zc.ipset_enabled() is False
    f.write_text("1.2.3.0/24\n4.5.6.0/24\n", encoding="utf-8")
    assert zc.ipset_enabled() is True


# --- game (Steam/Dota) exclusions toggle --------------------------------- #
def test_game_exclusions_toggle(tmp_path, monkeypatch):
    f = tmp_path / "list-exclude-user.txt"
    monkeypatch.setattr(zc, "LIST_EXCLUDE_USER", str(f))
    monkeypatch.setattr(zc, "LISTS", str(tmp_path) + os.sep)
    f.write_text(zc._EXCLUDE_PLACEHOLDER + "\n", encoding="utf-8")
    assert zc.game_exclusions_present() is False
    assert zc.set_game_exclusions(True) is True
    assert zc.game_exclusions_present() is True
    assert zc.set_game_exclusions(True) is False          # идемпотентно
    body = f.read_text(encoding="utf-8")
    assert "dota2.com" in body and "steamstatic.com" in body
    assert zc._EXCLUDE_PLACEHOLDER not in body            # заглушка убрана
    assert zc.set_game_exclusions(False) is True
    assert zc.game_exclusions_present() is False
    assert f.read_text(encoding="utf-8").strip()          # файл не пустой


# --- user domains round-trip --------------------------------------------- #
def test_user_domains_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "list-general-user.txt"
    monkeypatch.setattr(zc, "USER_LIST_FILE", str(f))
    monkeypatch.setattr(zc, "LISTS", str(tmp_path) + os.sep)
    written = zc.write_user_domains(
        ["https://rutracker.org/x", "WWW.Rutracker.org", "modrinth.com", "junk"])
    assert "rutracker.org" in written and "modrinth.com" in written
    assert written.count("rutracker.org") == 1          # дубли убраны
    assert "rutracker.org" in zc.read_user_domains()
