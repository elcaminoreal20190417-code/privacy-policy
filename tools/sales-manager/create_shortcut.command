#!/bin/sh
# デスクトップに起動用アイコンを作る（macOS / Linux）
cd "$(dirname "$0")" || exit 1

if command -v python3 > /dev/null 2>&1; then
    python3 create_shortcut.py "$@"
    echo ""
    echo "このウィンドウは閉じて構いません。"
    exit 0
fi

echo "Python 3 が見つかりませんでした。先にインストールしてください。"
exit 1
