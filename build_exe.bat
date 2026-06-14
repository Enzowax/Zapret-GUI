@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo === Сборка ZapretControl (onedir + ZIP) ===
:: presets.json — канонический источник стратегий (редактируется напрямую)

py -3 -m PyInstaller --noconfirm --distpath "%~dp0dist" --workpath "%~dp0build" ZapretControl.spec

if not exist "%~dp0dist\ZapretControl\ZapretControl.exe" (
    echo [ОШИБКА] сборка не удалась — смотрите вывод выше.
    pause
    exit /b 1
)

echo Упаковка в ZapretControl.zip ...
powershell -NoProfile -Command "Compress-Archive -Path '%~dp0dist\ZapretControl' -DestinationPath '%~dp0ZapretControl.zip' -Force"

echo.
if exist "%~dp0ZapretControl.zip" (
    echo Готово: "%~dp0ZapretControl.zip"  (внутри папка ZapretControl)
) else (
    echo [ОШИБКА] не удалось создать ZIP.
)
pause
