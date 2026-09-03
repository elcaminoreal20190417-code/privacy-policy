"""SQLite storage for the multi-channel sales / ad-cost manager.

Standard library only. The database file lives next to this module under
``data/`` so the whole tool can be copied to another PC as-is.
"""

import json
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "sales.db")

# 既定チャネル。UI から自由に増やせるが、最初から並んでいると入力が早い。
DEFAULT_CHANNELS = ["Amazon", "楽天", "Yahoo", "公式サイト", "卸"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel    TEXT    NOT NULL,
    date       TEXT    NOT NULL,
    orders     INTEGER NOT NULL DEFAULT 0,
    gross      REAL    NOT NULL DEFAULT 0,
    fees       REAL    NOT NULL DEFAULT 0,
    shipping   REAL    NOT NULL DEFAULT 0,
    cogs       REAL    NOT NULL DEFAULT 0,
    refunds    REAL    NOT NULL DEFAULT 0,
    note       TEXT    NOT NULL DEFAULT '',
    batch_id   INTEGER,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sales_date    ON sales(date);
CREATE INDEX IF NOT EXISTS idx_sales_channel ON sales(channel);
CREATE INDEX IF NOT EXISTS idx_sales_batch   ON sales(batch_id);

CREATE TABLE IF NOT EXISTS ads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel     TEXT    NOT NULL,
    date        TEXT    NOT NULL,
    campaign    TEXT    NOT NULL DEFAULT '',
    cost        REAL    NOT NULL DEFAULT 0,
    ad_sales    REAL    NOT NULL DEFAULT 0,
    clicks      INTEGER NOT NULL DEFAULT 0,
    impressions INTEGER NOT NULL DEFAULT 0,
    note        TEXT    NOT NULL DEFAULT '',
    batch_id    INTEGER,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ads_date    ON ads(date);
CREATE INDEX IF NOT EXISTS idx_ads_channel ON ads(channel);
CREATE INDEX IF NOT EXISTS idx_ads_batch   ON ads(batch_id);

CREATE TABLE IF NOT EXISTS batches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    channel    TEXT NOT NULL,
    filename   TEXT NOT NULL,
    row_count  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS presets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL,
    channel    TEXT NOT NULL,
    mapping    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    name     TEXT PRIMARY KEY,
    sort_key INTEGER NOT NULL DEFAULT 100
);
"""

SALES_FIELDS = ("orders", "gross", "fees", "shipping", "cogs", "refunds")
ADS_FIELDS = ("cost", "ad_sales", "clicks", "impressions")


def now():
    return datetime.now().isoformat(timespec="seconds")


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init():
    conn = connect()
    with conn:
        conn.executescript(SCHEMA)
        for i, name in enumerate(DEFAULT_CHANNELS):
            conn.execute(
                "INSERT OR IGNORE INTO channels(name, sort_key) VALUES (?, ?)",
                (name, i),
            )
    conn.close()


def channels(conn):
    """設定済みチャネル + 実データに現れるチャネルを、並び順つきで返す。"""
    rows = conn.execute("SELECT name, sort_key FROM channels").fetchall()
    known = {r["name"]: r["sort_key"] for r in rows}
    for table in ("sales", "ads"):
        for r in conn.execute(f"SELECT DISTINCT channel FROM {table}"):
            known.setdefault(r["channel"], 999)
    return [n for n, _ in sorted(known.items(), key=lambda kv: (kv[1], kv[0]))]


def add_channel(conn, name):
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO channels(name, sort_key) VALUES (?, 500)", (name,)
        )


def create_batch(conn, kind, channel, filename, row_count):
    with conn:
        cur = conn.execute(
            "INSERT INTO batches(kind, channel, filename, row_count, created_at)"
            " VALUES (?,?,?,?,?)",
            (kind, channel, filename, row_count, now()),
        )
    return cur.lastrowid


def insert_sales(conn, rows, batch_id=None):
    stamp = now()
    with conn:
        conn.executemany(
            "INSERT INTO sales(channel, date, orders, gross, fees, shipping, cogs,"
            " refunds, note, batch_id, created_at)"
            " VALUES (:channel,:date,:orders,:gross,:fees,:shipping,:cogs,:refunds,"
            ":note,:batch_id,:created_at)",
            [dict(r, batch_id=batch_id, created_at=stamp) for r in rows],
        )
    return len(rows)


def insert_ads(conn, rows, batch_id=None):
    stamp = now()
    with conn:
        conn.executemany(
            "INSERT INTO ads(channel, date, campaign, cost, ad_sales, clicks,"
            " impressions, note, batch_id, created_at)"
            " VALUES (:channel,:date,:campaign,:cost,:ad_sales,:clicks,:impressions,"
            ":note,:batch_id,:created_at)",
            [dict(r, batch_id=batch_id, created_at=stamp) for r in rows],
        )
    return len(rows)


def delete_range(conn, kind, channel, date_from, date_to):
    """再取込み前の重複除去用。同一チャネル・同一期間の既存行を消す。"""
    table = "sales" if kind == "sales" else "ads"
    with conn:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE channel = ? AND date >= ? AND date <= ?",
            (channel, date_from, date_to),
        )
    return cur.rowcount


def delete_batch(conn, batch_id):
    with conn:
        removed = 0
        for table in ("sales", "ads"):
            cur = conn.execute(f"DELETE FROM {table} WHERE batch_id = ?", (batch_id,))
            removed += cur.rowcount
        conn.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
    return removed


def list_batches(conn, limit=100):
    rows = conn.execute(
        "SELECT id, kind, channel, filename, row_count, created_at FROM batches"
        " ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _where(channels_filter, date_from, date_to):
    clauses, params = [], []
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)
    if channels_filter:
        clauses.append("channel IN (%s)" % ",".join("?" * len(channels_filter)))
        params.extend(channels_filter)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def summary(conn, channels_filter=None, date_from=None, date_to=None):
    """チャネル別の売上・広告費・粗利を集計して返す。"""
    where, params = _where(channels_filter, date_from, date_to)
    sales = {
        r["channel"]: dict(r)
        for r in conn.execute(
            "SELECT channel, SUM(orders) orders, SUM(gross) gross, SUM(fees) fees,"
            " SUM(shipping) shipping, SUM(cogs) cogs, SUM(refunds) refunds"
            f" FROM sales{where} GROUP BY channel",
            params,
        )
    }
    ads = {
        r["channel"]: dict(r)
        for r in conn.execute(
            "SELECT channel, SUM(cost) cost, SUM(ad_sales) ad_sales,"
            " SUM(clicks) clicks, SUM(impressions) impressions"
            f" FROM ads{where} GROUP BY channel",
            params,
        )
    }

    out = []
    for name in sorted(set(sales) | set(ads)):
        s = sales.get(name, {})
        a = ads.get(name, {})
        row = {
            "channel": name,
            "orders": int(s.get("orders") or 0),
            "gross": float(s.get("gross") or 0),
            "fees": float(s.get("fees") or 0),
            "shipping": float(s.get("shipping") or 0),
            "cogs": float(s.get("cogs") or 0),
            "refunds": float(s.get("refunds") or 0),
            "ad_cost": float(a.get("cost") or 0),
            "ad_sales": float(a.get("ad_sales") or 0),
            "clicks": int(a.get("clicks") or 0),
            "impressions": int(a.get("impressions") or 0),
        }
        out.append(derive(row))

    total = {k: 0 for k in (
        "orders", "gross", "fees", "shipping", "cogs", "refunds",
        "ad_cost", "ad_sales", "clicks", "impressions",
    )}
    for row in out:
        for k in total:
            total[k] += row[k]
    total["channel"] = "合計"
    return {"channels": out, "total": derive(total)}


def derive(row):
    """売上・費用から派生指標（純売上・粗利・広告費率・ACoS・ROAS）を計算する。"""
    net = row["gross"] - row["refunds"]
    profit = net - row["fees"] - row["shipping"] - row["cogs"] - row["ad_cost"]
    row["net"] = net
    row["profit"] = profit
    row["margin"] = (profit / net * 100) if net else 0.0
    row["ad_ratio"] = (row["ad_cost"] / net * 100) if net else 0.0
    row["acos"] = (row["ad_cost"] / row["ad_sales"] * 100) if row["ad_sales"] else 0.0
    row["roas"] = (row["ad_sales"] / row["ad_cost"] * 100) if row["ad_cost"] else 0.0
    row["aov"] = (net / row["orders"]) if row["orders"] else 0.0
    return row


def timeseries(conn, channels_filter=None, date_from=None, date_to=None,
               granularity="month"):
    """期間ごと（日/月）の売上・広告費・粗利の推移。"""
    expr = "substr(date, 1, 7)" if granularity == "month" else "date"
    where, params = _where(channels_filter, date_from, date_to)

    buckets = {}
    for r in conn.execute(
        f"SELECT {expr} bucket, SUM(gross) gross, SUM(refunds) refunds,"
        " SUM(fees) fees, SUM(shipping) shipping, SUM(cogs) cogs, SUM(orders) orders"
        f" FROM sales{where} GROUP BY bucket",
        params,
    ):
        buckets[r["bucket"]] = {
            "bucket": r["bucket"],
            "gross": float(r["gross"] or 0),
            "refunds": float(r["refunds"] or 0),
            "fees": float(r["fees"] or 0),
            "shipping": float(r["shipping"] or 0),
            "cogs": float(r["cogs"] or 0),
            "orders": int(r["orders"] or 0),
            "ad_cost": 0.0,
            "ad_sales": 0.0,
            "clicks": 0,
            "impressions": 0,
        }
    for r in conn.execute(
        f"SELECT {expr} bucket, SUM(cost) cost, SUM(ad_sales) ad_sales,"
        " SUM(clicks) clicks, SUM(impressions) impressions"
        f" FROM ads{where} GROUP BY bucket",
        params,
    ):
        b = buckets.setdefault(r["bucket"], {
            "bucket": r["bucket"], "gross": 0.0, "refunds": 0.0, "fees": 0.0,
            "shipping": 0.0, "cogs": 0.0, "orders": 0, "ad_cost": 0.0,
            "ad_sales": 0.0, "clicks": 0, "impressions": 0,
        })
        b["ad_cost"] = float(r["cost"] or 0)
        b["ad_sales"] = float(r["ad_sales"] or 0)
        b["clicks"] = int(r["clicks"] or 0)
        b["impressions"] = int(r["impressions"] or 0)

    return [derive(b) for b in sorted(buckets.values(), key=lambda x: x["bucket"])]


def date_bounds(conn):
    row = conn.execute(
        "SELECT MIN(d) lo, MAX(d) hi FROM ("
        " SELECT MIN(date) d FROM sales UNION ALL SELECT MAX(date) FROM sales"
        " UNION ALL SELECT MIN(date) FROM ads UNION ALL SELECT MAX(date) FROM ads)"
    ).fetchone()
    return {"min": row["lo"], "max": row["hi"]}


def save_preset(conn, name, kind, channel, mapping):
    with conn:
        conn.execute(
            "INSERT INTO presets(name, kind, channel, mapping, created_at)"
            " VALUES (?,?,?,?,?)"
            " ON CONFLICT(name) DO UPDATE SET kind=excluded.kind,"
            " channel=excluded.channel, mapping=excluded.mapping",
            (name, kind, channel, json.dumps(mapping, ensure_ascii=False), now()),
        )


def list_presets(conn):
    rows = conn.execute(
        "SELECT id, name, kind, channel, mapping FROM presets ORDER BY name"
    ).fetchall()
    return [dict(r, mapping=json.loads(r["mapping"])) for r in rows]


def delete_preset(conn, preset_id):
    with conn:
        conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
