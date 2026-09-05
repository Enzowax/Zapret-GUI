@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo === Сборка ZapretControl (onedir + ZIP) ===
:: presets.json — канонический источник стратегий (редактируется напрямую)

py -3.11 -m pytest tests -q
if errorlevel 1 exit /b 1
py -3.11 -m ruff check --select=E9,F63,F7,F82 zapret_core.py zapret_app.pyw tgproxy tests
if errorlevel 1 exit /b 1

py -3.11 -m PyInstaller --noconfirm --distpath "%~dp0dist" --workpath "%~dp0build" ZapretControl.spec
if errorlevel 1 exit /b 1

if not exist "%~dp0dist\ZapretControl\ZapretControl.exe" (
    echo [ОШИБКА] сборка не удалась — смотрите вывод выше.
    pause
    exit /b 1
)

echo Упаковка в ZapretControl.zip ...
powershell -NoProfile -Command "Compress-Archive -LiteralPath 'dist\ZapretControl' -DestinationPath 'ZapretControl.zip' -Force -ErrorAction Stop"
if errorlevel 1 exit /b 1

echo.
if exist "%~dp0ZapretControl.zip" (
    echo Готово: "%~dp0ZapretControl.zip"  (внутри папка ZapretControl)
) else (
    echo [ОШИБКА] не удалось создать ZIP.
)
pause
