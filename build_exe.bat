@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo === Сборка ZapretControl.exe (PyInstaller) ===
:: presets.json — канонический источник стратегий (редактируется напрямую)

py -3 -m PyInstaller --noconfirm --distpath "%~dp0." --workpath "%~dp0build" ZapretControl.spec

echo.
if exist "%~dp0ZapretControl.exe" (
    echo Готово: "%~dp0ZapretControl.exe"
) else (
    echo [ОШИБКА] exe не собрался — смотрите вывод выше.
)
pause
