"""Юнит-тесты чистых функций ядра (без сети/сабпроцессов).
Гоняются в CI до сборки — гейт, чтобы не выпустить сломанный билд."""
import os
import ssl
import sys

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


# --- проверка соединения авто-поиска верифицирует сертификат -------------- #
def test_verify_tls_context_checks_cert_and_hostname():
    # Гарантия против регресса: если проверку сертификата/имени хоста отключат
    # (CERT_NONE), авто-поиск снова начнёт считать заглушку цензора «рабочей»
    # стратегией и рекомендовать её как лучшую (баг general (ALT)).
    ctx = zc._verify_tls_context()
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


# --- version compare ----------------------------------------------------- #
def test_version_tuple_ordering():
    assert zc._version_tuple("v2.30.0") > zc._version_tuple("v2.29.9")
    assert zc._version_tuple("2.4.0") > zc._version_tuple("2.3.99")
    assert zc._version_tuple("v1.0") == zc._version_tuple("1.0.0")[:2]


def test_version_tuple_prerelease_and_release():
    # релиз новее любой своей пре-релизной беты (бета -> 0 в позиции суффикса)
    assert zc._version_tuple("2.40.1") > zc._version_tuple("2.40.0")
    assert zc._version_tuple("v2.41.0") > zc._version_tuple("v2.40.9")
    # мусор/пусто не падает и не «выигрывает» у нормальной версии
    assert zc._version_tuple("") == (0,)
    assert zc._version_tuple("2.40.0") > zc._version_tuple("")


# --- отбор кандидатов авто-поиска (фаза 1 -> фаза 2) ---------------------- #
def test_select_candidates_prefers_full_pass_by_latency():
    # полностью пробившие (score==2) идут первыми, отсортированы по задержке
    phase1 = [("A", 2, 120.0), ("B", 1, 30.0), ("C", 2, 40.0), ("D", 0, None)]
    assert zc.select_candidates(phase1, full_score=2, max_cand=6) == ["C", "A"]


def test_select_candidates_falls_back_to_partial():
    # полностью рабочих нет -> берём частичные по убыванию score, затем задержке
    phase1 = [("A", 1, 90.0), ("B", 1, 30.0), ("C", 0, None)]
    assert zc.select_candidates(phase1, full_score=3, max_cand=6) == ["B", "A"]


def test_select_candidates_caps_and_handles_empty():
    phase1 = [(f"P{i}", 2, float(i)) for i in range(10)]
    assert zc.select_candidates(phase1, 2, max_cand=3) == ["P0", "P1", "P2"]
    assert zc.select_candidates([], 2) == []
    assert zc.select_candidates([("X", 0, None)], 2) == []   # ноль не берём


# --- выбор лучшей стратегии (фаза 2) ------------------------------------- #
def test_result_is_better_by_coverage_then_latency():
    # больше покрытие важнее задержки
    assert zc.result_is_better(("A", 8, 200.0), ("B", 7, 10.0)) is True
    # при равном покрытии — меньше задержка
    assert zc.result_is_better(("A", 8, 90.0), ("B", 8, 91.0)) is True
    assert zc.result_is_better(("A", 8, 91.0), ("B", 8, 90.0)) is False
    # первый валидный результат всегда лучше «ничего»
    assert zc.result_is_better(("A", 5, None), None) is True
    # нулевое/пустое покрытие не может стать лучшим (баг «0/3 как рабочая»)
    assert zc.result_is_better(("A", 0, 5.0), ("B", 1, 500.0)) is False
    assert zc.result_is_better(None, ("B", 1, 500.0)) is False
    # строго лучше: равные не вытесняют (стабильность «первого найденного»)
    assert zc.result_is_better(("A", 8, 90.0), ("B", 8, 90.0)) is False


# --- аргументы службы (экранирование) ------------------------------------ #
def test_quote_for_service_escapes_spaces_and_colons():
    # значение с пробелом/двоеточием -> в экранированных кавычках \"
    assert zc.quote_for_service('--wf-l3=ipv4') == '--wf-l3=ipv4'      # без спец — как есть
    assert zc.quote_for_service('--hostlist=C:\\a b.txt') == '--hostlist=\\"C:\\a b.txt\\"'
    assert zc.quote_for_service('--new') == '--new'
    assert zc.quote_for_service('plain') == 'plain'
    assert zc.quote_for_service('has space') == '\\"has space\\"'


# --- игровой фильтр / id пресета ----------------------------------------- #
def test_game_filter_values():
    assert zc.game_filter_values("off") == ("12", "12")
    assert zc.game_filter_values("all") == ("1024-65535", "1024-65535")
    assert zc.game_filter_values("tcp") == ("1024-65535", "12")
    assert zc.game_filter_values("udp") == ("12", "1024-65535")
    assert zc.game_filter_values("bogus") == ("12", "12")     # неизвестное -> выкл


def test_preset_id_sanitizes():
    assert zc._preset_id("general (ALT12)") == "general_alt12"
    assert zc._preset_id("FAKE TLS AUTO") == "fake_tls_auto"
    assert zc._preset_id("!!!") == "preset"                   # пусто -> запасное имя


# --- сравнение путей автозапуска ----------------------------------------- #
def test_same_path_normalizes_case_and_slashes():
    assert zc._same_path("C:\\Zapret GUI\\app.exe", "c:/zapret gui/app.exe") is True
    assert zc._same_path("C:\\a\\app.exe", "C:\\b\\app.exe") is False
    assert zc._same_path("", "x") is False


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
