#!/bin/sh
# 売上・広告費 一元管理ツールを起動する（macOS / Linux）
cd "$(dirname "$0")" || exit 1

if command -v python3 > /dev/null 2>&1; then
    exec python3 server.py "$@"
fi

echo "Python 3 が見つかりませんでした。"
echo "macOS: https://www.python.org/downloads/macos/ からインストールしてください。"
exit 1
