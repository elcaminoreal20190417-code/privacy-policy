@echo off
chcp 65001 > nul
cd /d "%~dp0"

where py > nul 2>&1
if %errorlevel%==0 (
    py -3 create_shortcut.py %*
    goto :end
)
where python > nul 2>&1
if %errorlevel%==0 (
    python create_shortcut.py %*
    goto :end
)

echo.
echo Python が見つかりませんでした。先に Python をインストールしてください。
echo https://www.python.org/downloads/windows/
echo インストール時に "Add python.exe to PATH" にチェックを入れてください。
echo.

:end
pause
