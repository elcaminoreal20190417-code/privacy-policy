"""デスクトップに起動用アイコン（ショートカット）を作る。

Windows / macOS / Linux のどれでも動く。追加ライブラリは不要。

    python3 create_shortcut.py            作成する
    python3 create_shortcut.py --remove   取り消す
"""

import os
import subprocess
import sys
import tempfile

APP_NAME = "売上・広告費 一元管理"
DESCRIPTION = "売上・広告費 一元管理ツール"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(BASE_DIR, "icon.ico")
# Linux のデスクトップ環境は .ico をうまく扱えないことが多いので PNG を使う。
ICON_PNG = os.path.join(BASE_DIR, "icon.png")


def ps_quote(text):
    """PowerShell のシングルクォート文字列に安全に埋め込む。"""
    return "'" + text.replace("'", "''") + "'"


def run_powershell(script):
    """UTF-8 (BOM 付き) の .ps1 に書き出して実行する。

    コマンドラインへ日本語を直接渡すとコードページの影響を受けるため、
    ファイル経由にして文字化けを避ける。
    """
    path = None
    try:
        with tempfile.NamedTemporaryFile("wb", suffix=".ps1", delete=False) as fh:
            fh.write(b"\xef\xbb\xbf" + script.encode("utf-8"))
            path = fh.name
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip())
        return result.stdout.strip()
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


# ---------- Windows ----------

def windows_link_path():
    # OneDrive にデスクトップが移動している場合も正しく取れるよう Windows に尋ねる。
    return run_powershell(
        "$d = [Environment]::GetFolderPath('Desktop')\n"
        f"Write-Output (Join-Path $d {ps_quote(APP_NAME + '.lnk')})\n"
    )


def windows_create():
    target = os.path.join(BASE_DIR, "start.bat")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$d = [Environment]::GetFolderPath('Desktop')\n"
        f"$link = Join-Path $d {ps_quote(APP_NAME + '.lnk')}\n"
        "$sc = (New-Object -ComObject WScript.Shell).CreateShortcut($link)\n"
        f"$sc.TargetPath = {ps_quote(target)}\n"
        f"$sc.WorkingDirectory = {ps_quote(BASE_DIR)}\n"
        f"$sc.IconLocation = {ps_quote(ICON)}\n"
        f"$sc.Description = {ps_quote(DESCRIPTION)}\n"
        "$sc.Save()\n"
        "Write-Output $link\n"
    )
    return run_powershell(script)


def windows_remove():
    link = windows_link_path()
    if os.path.exists(link):
        os.unlink(link)
        return link
    return None


# ---------- macOS ----------

def mac_link_path():
    return os.path.join(os.path.expanduser("~/Desktop"), APP_NAME + ".command")


def mac_create():
    link = mac_link_path()
    target = os.path.join(BASE_DIR, "start.command")
    # 実体を指す小さな起動スクリプトを置く（シンボリックリンクより Finder と相性がよい）
    with open(link, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n"
                 f"# {DESCRIPTION}\n"
                 f'exec {shell_quote(target)}\n')
    os.chmod(link, 0o755)
    return link


# ---------- Linux ----------

def linux_link_path():
    desktop = os.path.expanduser("~/Desktop")
    if not os.path.isdir(desktop):
        desktop = os.path.expanduser("~/デスクトップ")
    return os.path.join(desktop, "sales-manager.desktop")


def linux_create():
    link = linux_link_path()
    os.makedirs(os.path.dirname(link), exist_ok=True)
    with open(link, "w", encoding="utf-8") as fh:
        fh.write(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={APP_NAME}\n"
            f"Comment={DESCRIPTION}\n"
            f"Exec={shell_quote(sys.executable)} {shell_quote(os.path.join(BASE_DIR, 'server.py'))}\n"
            f"Path={BASE_DIR}\n"
            f"Icon={ICON_PNG}\n"
            "Terminal=true\n"
        )
    os.chmod(link, 0o755)
    return link


def shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


# ---------- 共通 ----------

def remove_generic(link):
    if link and os.path.exists(link):
        os.unlink(link)
        return link
    return None


def main():
    removing = "--remove" in sys.argv

    if sys.platform.startswith("win"):
        create, path_of = windows_create, windows_link_path
    elif sys.platform == "darwin":
        create, path_of = mac_create, mac_link_path
    else:
        create, path_of = linux_create, linux_link_path

    try:
        if removing:
            removed = windows_remove() if sys.platform.startswith("win") \
                else remove_generic(path_of())
            print(f"削除しました: {removed}" if removed
                  else "デスクトップにショートカットは見つかりませんでした。")
            return 0
        link = create()
    except Exception as exc:
        print(f"ショートカットを作成できませんでした: {exc}")
        print(f"このフォルダを開いて起動用ファイルを直接ダブルクリックしてください:\n  {BASE_DIR}")
        return 1

    print("デスクトップにアイコンを作りました。")
    print(f"  {link}")
    print("次回からは、このアイコンをダブルクリックすると起動します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
