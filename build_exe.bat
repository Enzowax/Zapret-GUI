@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo === Сборка ZapretControl.exe (PyInstaller) ===

:: обновить presets.json из .bat перед сборкой
py -3 -c "import zapret_core as zc; zc.generate_presets_json(force=True); print('presets.json updated')"

py -3 -m PyInstaller --noconfirm --distpath "%~dp0." --workpath "%~dp0build" ZapretControl.spec

echo.
if exist "%~dp0ZapretControl.exe" (
    echo Готово: "%~dp0ZapretControl.exe"
) else (
    echo [ОШИБКА] exe не собрался — смотрите вывод выше.
)
pause
