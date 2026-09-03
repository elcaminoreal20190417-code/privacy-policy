"""売上・広告費 一元管理ツール — ローカル HTTP サーバ。

標準ライブラリのみで動く。127.0.0.1 にだけ待ち受け、データは同梱の SQLite に
保存する。外部への送信は一切行わない。

    python3 server.py            # http://127.0.0.1:8787 を開く
    python3 server.py --port 9000 --no-browser
"""

import argparse
import csv
import io
import json
import mimetypes
import os
import secrets
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import db
import importer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
MAX_UPLOAD = 64 * 1024 * 1024  # 64 MiB
ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")

# プレビューしたファイルを確定まで保持する。ローカル単独利用なのでメモリ上でよい。
_pending = {}
_pending_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status
        self.message = message


def _stash(raw):
    token = secrets.token_urlsafe(12)
    with _pending_lock:
        if len(_pending) > 8:  # 取り込みは 1 件ずつ。古いものから捨てる。
            for key in list(_pending)[:-4]:
                _pending.pop(key, None)
        _pending[token] = raw
    return token


def _take(token):
    with _pending_lock:
        raw = _pending.get(token)
    if raw is None:
        raise ApiError("アップロードの有効期限が切れました。もう一度ファイルを選んでください。")
    return raw


class Handler(BaseHTTPRequestHandler):
    server_version = "SalesManager/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 共通 ----------

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    def _host_ok(self):
        """DNS リバインディング対策。ローカル名以外の Host は受け付けない。"""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ALLOWED_HOSTS or host.startswith("127.")

    def _send(self, status, body, content_type="application/json; charset=utf-8",
              extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload, ensure_ascii=False, default=float),
                   "application/json; charset=utf-8")

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            raise ApiError("ファイルが大きすぎます（上限 64MB）。", 413)
        return self.rfile.read(length) if length else b""

    def _json_body(self):
        raw = self._body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError("リクエストの形式が不正です。")

    def _query(self):
        return parse_qs(urlparse(self.path).query)

    def _filters(self):
        q = self._query()
        chans = [c for c in q.get("channels", [""])[0].split(",") if c]
        return chans, (q.get("from", [""])[0] or None), (q.get("to", [""])[0] or None)

    # ---------- ルーティング ----------

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        if not self._host_ok():
            self._send(403, "forbidden", "text/plain; charset=utf-8")
            return
        path = urlparse(self.path).path
        # 独自ヘッダはクロスオリジンのフォームからは付けられないため、
        # 更新系リクエストの簡易 CSRF 対策として要求する。
        if method in ("POST", "DELETE") and \
                self.headers.get("X-Requested-With") != "sales-manager":
            self._send(403, json.dumps({"error": "forbidden"}), "application/json")
            return
        try:
            if path.startswith("/api/"):
                self._api(method, path)
            elif method == "GET":
                self._static(path)
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")
        except ApiError as exc:
            self._json({"error": exc.message}, exc.status)
        except BrokenPipeError:
            pass
        except Exception as exc:  # 画面側に理由を返したいので握りつぶさず表示する
            import traceback
            traceback.print_exc()
            self._json({"error": f"サーバ側でエラーが発生しました: {exc}"}, 500)

    def _static(self, path):
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not target.startswith(STATIC_DIR) or not os.path.isfile(target):
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith(("text/", "application/javascript")):
            ctype += "; charset=utf-8"
        with open(target, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def _api(self, method, path):
        conn = db.connect()
        try:
            route = (method, path)
            if route == ("GET", "/api/bootstrap"):
                self._json({
                    "channels": db.channels(conn),
                    "presets": db.list_presets(conn),
                    "bounds": db.date_bounds(conn),
                    "fields": {
                        "sales": ["date", "orders", "gross", "fees", "shipping",
                                  "cogs", "refunds"],
                        "ads": ["date", "campaign", "cost", "ad_sales", "clicks",
                                "impressions"],
                    },
                })
            elif route == ("GET", "/api/summary"):
                chans, dfrom, dto = self._filters()
                self._json(db.summary(conn, chans, dfrom, dto))
            elif route == ("GET", "/api/timeseries"):
                chans, dfrom, dto = self._filters()
                gran = self._query().get("granularity", ["month"])[0]
                gran = gran if gran in ("day", "month") else "month"
                self._json({"granularity": gran,
                            "points": db.timeseries(conn, chans, dfrom, dto, gran)})
            elif route == ("GET", "/api/batches"):
                self._json({"batches": db.list_batches(conn)})
            elif route == ("GET", "/api/export.csv"):
                self._export(conn)
            elif route == ("POST", "/api/preview"):
                self._preview(conn)
            elif route == ("POST", "/api/commit"):
                self._commit(conn)
            elif route == ("POST", "/api/manual"):
                self._manual(conn)
            elif route == ("POST", "/api/channel"):
                name = (self._json_body().get("name") or "").strip()
                if not name:
                    raise ApiError("チャネル名を入力してください。")
                db.add_channel(conn, name)
                self._json({"channels": db.channels(conn)})
            elif route == ("POST", "/api/preset"):
                payload = self._json_body()
                name = (payload.get("name") or "").strip()
                if not name:
                    raise ApiError("設定名を入力してください。")
                db.save_preset(conn, name, payload.get("kind", "sales"),
                               payload.get("channel", ""), payload.get("mapping", {}))
                self._json({"presets": db.list_presets(conn)})
            elif route == ("DELETE", "/api/preset"):
                db.delete_preset(conn, int(self._query().get("id", ["0"])[0]))
                self._json({"presets": db.list_presets(conn)})
            elif route == ("DELETE", "/api/batch"):
                removed = db.delete_batch(conn, int(self._query().get("id", ["0"])[0]))
                self._json({"removed": removed, "batches": db.list_batches(conn)})
            else:
                self._json({"error": "not found"}, 404)
        finally:
            conn.close()

    # ---------- 取り込み ----------

    def _preview(self, conn):
        raw = self._body()
        if not raw:
            raise ApiError("ファイルが空です。")
        q = self._query()
        kind = q.get("kind", ["sales"])[0]
        header_row = int(q.get("header_row", ["0"])[0] or 0)
        table = importer.read_table(raw, header_row=header_row)
        if not table["headers"]:
            raise ApiError("列が読み取れませんでした。CSV / TSV 形式か確認してください。")
        self._json({
            "token": _stash(raw),
            "encoding": table["encoding"],
            "delimiter": "タブ" if table["delimiter"] == "\t" else table["delimiter"],
            "headers": table["headers"],
            "row_count": len(table["rows"]),
            "sample": table["rows"][:8],
            "guess": importer.guess_mapping(table["headers"], kind),
        })

    def _commit(self, conn):
        payload = self._json_body()
        kind = payload.get("kind", "sales")
        channel = (payload.get("channel") or "").strip()
        if not channel:
            raise ApiError("チャネルを選択してください。")
        raw = _take(payload.get("token", ""))
        table = importer.read_table(raw, header_row=int(payload.get("header_row") or 0))
        rows, errors = importer.build_rows(
            table["rows"], payload.get("mapping", {}), kind, channel,
            aggregate=bool(payload.get("aggregate", True)),
        )
        if not rows:
            raise ApiError("取り込める行がありませんでした。" + (" / ".join(errors[:3])))

        replaced = 0
        if payload.get("replace"):
            # 同じ月を month 単位で入れ直すときの二重計上を防ぐ。
            dates = [r["date"] for r in rows]
            replaced = db.delete_range(conn, kind, channel, min(dates), max(dates))

        db.add_channel(conn, channel)
        batch_id = db.create_batch(conn, kind, channel,
                                   payload.get("filename", "アップロード"), len(rows))
        if kind == "sales":
            db.insert_sales(conn, rows, batch_id)
        else:
            db.insert_ads(conn, rows, batch_id)

        if payload.get("preset_name"):
            db.save_preset(conn, payload["preset_name"].strip(), kind, channel,
                           payload.get("mapping", {}))
        self._json({
            "imported": len(rows), "replaced": replaced, "warnings": errors,
            "batch_id": batch_id, "presets": db.list_presets(conn),
            "channels": db.channels(conn),
        })

    def _manual(self, conn):
        payload = self._json_body()
        kind = payload.get("kind", "sales")
        channel = (payload.get("channel") or "").strip()
        date = importer.parse_date(payload.get("date"))
        if not channel or not date:
            raise ApiError("チャネルと日付は必須です。")
        num = importer.parse_number
        if kind == "sales":
            row = {"channel": channel, "date": date,
                   "orders": importer.parse_int(payload.get("orders", 0)),
                   "note": (payload.get("note") or "")[:200]}
            for f in ("gross", "fees", "shipping", "cogs", "refunds"):
                row[f] = num(payload.get(f, 0))
            db.insert_sales(conn, [row])
        else:
            row = {"channel": channel, "date": date,
                   "campaign": (payload.get("campaign") or "")[:120],
                   "clicks": importer.parse_int(payload.get("clicks", 0)),
                   "impressions": importer.parse_int(payload.get("impressions", 0)),
                   "note": (payload.get("note") or "")[:200]}
            for f in ("cost", "ad_sales"):
                row[f] = num(payload.get(f, 0))
            db.insert_ads(conn, [row])
        db.add_channel(conn, channel)
        self._json({"ok": True, "channels": db.channels(conn)})

    def _export(self, conn):
        chans, dfrom, dto = self._filters()
        gran = self._query().get("granularity", ["month"])[0]
        what = self._query().get("what", ["channel"])[0]

        buf = io.StringIO()
        writer = csv.writer(buf)
        if what == "trend":
            writer.writerow(["期間", "売上", "返金", "純売上", "手数料", "送料", "原価",
                             "広告費", "広告経由売上", "粗利", "粗利率(%)",
                             "広告費率(%)", "ACoS(%)", "ROAS(%)", "注文数"])
            rows = db.timeseries(conn, chans, dfrom, dto, gran)
            key = "bucket"
        else:
            writer.writerow(["チャネル", "売上", "返金", "純売上", "手数料", "送料", "原価",
                             "広告費", "広告経由売上", "粗利", "粗利率(%)",
                             "広告費率(%)", "ACoS(%)", "ROAS(%)", "注文数"])
            data = db.summary(conn, chans, dfrom, dto)
            rows = data["channels"] + [data["total"]]
            key = "channel"
        for r in rows:
            writer.writerow([
                r[key], round(r["gross"]), round(r["refunds"]), round(r["net"]),
                round(r["fees"]), round(r["shipping"]), round(r["cogs"]),
                round(r["ad_cost"]), round(r["ad_sales"]), round(r["profit"]),
                round(r["margin"], 1), round(r["ad_ratio"], 1), round(r["acos"], 1),
                round(r["roas"], 1), r["orders"],
            ])
        # Excel で開いたときに文字化けしないよう BOM 付き UTF-8 で返す。
        body = buf.getvalue().encode("utf-8-sig")
        self._send(200, body, "text/csv; charset=utf-8", {
            "Content-Disposition": 'attachment; filename="sales-manager-export.csv"'
        })


def pick_port(preferred):
    for port in range(preferred, preferred + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("空きポートが見つかりませんでした。--port で指定してください。")


def main():
    parser = argparse.ArgumentParser(description="売上・広告費 一元管理ツール")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true",
                        help="起動時にブラウザを開かない")
    args = parser.parse_args()

    db.init()
    port = pick_port(args.port)
    url = f"http://127.0.0.1:{port}/"
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("=" * 56)
    print("  売上・広告費 一元管理ツール")
    print(f"  {url}")
    print(f"  データ: {db.DB_PATH}")
    print("  終了するには Ctrl+C")
    print("=" * 56)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n終了しました。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
