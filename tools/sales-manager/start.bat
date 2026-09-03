@echo off
chcp 65001 > nul
cd /d "%~dp0"

where py > nul 2>&1
if %errorlevel%==0 (
    py -3 server.py %*
    goto :end
)
where python > nul 2>&1
if %errorlevel%==0 (
    python server.py %*
    goto :end
)

echo.
echo Python が見つかりませんでした。
echo https://www.python.org/downloads/windows/ からインストールし、
echo インストール時に "Add python.exe to PATH" にチェックを入れてください。
echo.

:end
pause
