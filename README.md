# Zapret GUI

Современное приложение (CustomTkinter) для обхода блокировок **Discord, YouTube
и Telegram** на Windows. Надстройка над DPI-движком `winws.exe` + WinDivert из
открытого проекта [zapret](https://github.com/Flowseal/zapret-discord-youtube).

> ⚙️ Не VPN. Локальный обход DPI: правит сетевые пакеты, чтобы цензор не мог
> опознать и заблокировать соединение.

## Возможности
- Запуск/остановка обхода выбранным **пресетом**; служба автозапуска Windows.
- **Авто-поиск** лучшей стратегии (двухфазный, асинхронные проверки хостов).
- **Telegram-прокси** — встроенный MTProto-WS-прокси (отдельный exe не нужен):
  запуск/остановка, ссылка `tg://` для добавления в Telegram.
- **Самообновление** через GitHub Releases этого репозитория.
- Пресеты как данные (`presets.json`), контроль целостности бинарников (SHA-256).

## Установка
Скачайте `ZapretControl.zip` из [Releases](../../releases/latest), распакуйте
папку `ZapretControl` куда удобно и запустите `ZapretControl.exe` из неё. Всё
нужное (winws, WinDivert, списки, пресеты, TgWsProxy) уже внутри. Права
администратора запрашиваются автоматически.

> Формат **onedir** (exe + папка `_internal`) выбран намеренно: нет распаковки
> во временную папку при каждом запуске — быстрее старт и нет ошибок загрузки
> Python DLL при самообновлении.

> ⚠️ Windows Defender/SmartScreen может ругаться (как и на сам zapret) — это
> ложное срабатывание. «Подробнее → Выполнить в любом случае» / добавить в
> исключения.

## Сборка из исходников
```bat
pip install customtkinter pyinstaller
build_exe.bat
```
`build_exe.bat` обновляет `presets.json` из `.bat` и собирает `ZapretControl.exe`.

## Обновления / релизы
Приложение проверяет последний релиз (`releases/latest`) и предлагает обновиться
(качает `ZapretControl.zip`, проверяет размер, заменяет установку и перезапускается).
Чтобы выпустить новую версию: поднять `APP_VERSION` в `zapret_core.py`, запустить
`build_exe.bat`, создать релиз с тегом `vX.Y.Z` и приложить `ZapretControl.zip`.

## Структура
| Файл | Назначение |
|------|------------|
| `zapret_app.pyw` | интерфейс (CustomTkinter) |
| `zapret_core.py` | логика: пути, пресеты, процессы/служба, обновления, проверки |
| `presets.json` | декларативные пресеты (стратегии как данные) |
| `bin/`, `lists/` | движок и списки (из проекта zapret) |
| `ZapretControl.spec`, `build_exe.bat` | сборка |

## Лицензии и благодарности
- Движок `winws`/WinDivert и стратегии — проект zapret (bol-van) и сборка
  Flowseal/zapret-discord-youtube.
- Встроенный Telegram-прокси — пакет `tgproxy/`, взят из открытого проекта
  [Flowseal/tg-ws-proxy](https://github.com/Flowseal/tg-ws-proxy) (лицензия MIT,
  см. `tgproxy/LICENSE`). Используется только ядро прокси (без отдельного exe).

Это самостоятельный GUI; он не связан и не использует код платных сборок.
