"""各モールの CSV / TSV を共通スキーマへ取り込むための解析ユーティリティ。

モールごとに文字コード（楽天・Yahoo は Shift_JIS が多い）も列名も区切り文字も
違うため、「読めるところまで機械的に判定 → 残りは画面で列を対応付け」という
方針を取る。標準ライブラリのみ。
"""

import csv
import io
import re
import unicodedata
from datetime import datetime

# Excel の「Unicode テキスト」は UTF-16、楽天・Yahoo は CP932 が多い。
ENCODING_CANDIDATES = ("utf-8-sig", "cp932", "euc-jp", "utf-8")

DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日",
    "%Y%m%d", "%y/%m/%d", "%m/%d/%Y", "%d.%m.%Y",
)
MONTH_FORMATS = ("%Y-%m", "%Y/%m", "%Y年%m月")

_NUM_STRIP = str.maketrans("", "", "¥￥$, 　円個点回%")


def decode(raw):
    """バイト列を文字列へ。判定できた文字コード名も返す。"""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16"
    for enc in ENCODING_CANDIDATES:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        # CP932 は大半のバイト列を「読めてしまう」ので、文字化けの気配を見る。
        if enc in ("cp932", "euc-jp") and text.count("�"):
            continue
        return text, enc
    return raw.decode("cp932", errors="replace"), "cp932 (一部変換できず)"


def sniff_delimiter(text):
    head = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(head, delimiters=",\t;").delimiter
    except csv.Error:
        # 1 行目の出現数で決め打ち。区切りが判らないときはカンマ。
        first = head.splitlines()[0] if head.splitlines() else ""
        return max(",\t;", key=first.count) if first else ","


def read_table(raw, header_row=0):
    """アップロードされたファイルを (見出し, 行) に分解する。

    header_row は見出しが 1 行目にない CSV（楽天 RMS など）向けの読み飛ばし数。
    """
    text, encoding = decode(raw)
    delimiter = sniff_delimiter(text)
    rows = [r for r in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return {"encoding": encoding, "delimiter": delimiter, "headers": [], "rows": []}

    if header_row >= len(rows):
        header_row = 0
    headers = [h.strip().lstrip("﻿") for h in rows[header_row]]
    headers = _dedupe(headers)
    body = []
    for r in rows[header_row + 1:]:
        if len(r) < len(headers):
            r = r + [""] * (len(headers) - len(r))
        body.append(dict(zip(headers, r)))
    return {
        "encoding": encoding,
        "delimiter": delimiter,
        "headers": headers,
        "rows": body,
    }


def _dedupe(headers):
    """同名列があると dict 化で潰れるので、後ろに連番を付ける。"""
    seen, out = {}, []
    for i, h in enumerate(headers):
        h = h or f"列{i + 1}"
        if h in seen:
            seen[h] += 1
            h = f"{h} ({seen[h]})"
        else:
            seen[h] = 0
        out.append(h)
    return out


def parse_number(value):
    """「¥1,234」「1,234円」「(500)」「１２３」などを float にする。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = unicodedata.normalize("NFKC", str(value)).strip()
    if not s or s in ("-", "--", "―", "ー", "N/A", "n/a"):
        return 0.0
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").translate(_NUM_STRIP)
    if not s or s in ("-", "+", "."):
        return 0.0
    try:
        n = float(s)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        if not m:
            return 0.0
        n = float(m.group())
    return -n if negative else n


def parse_int(value):
    return int(round(parse_number(value)))


def parse_date(value):
    """様々な日付表記を YYYY-MM-DD に正規化する。解釈できなければ None。"""
    if value is None:
        return None
    s = unicodedata.normalize("NFKC", str(value)).strip()
    if not s:
        return None
    # ISO 8601（Amazon の purchase-date など）は T 以降を落とす。
    s = re.split(r"[T ]", s)[0].strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    for fmt in MONTH_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-01")
        except ValueError:
            pass
    m = re.match(r"^(\d{4})\D(\d{1,2})\D(\d{1,2})", s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def _resolve(spec, row, computed):
    """マッピング 1 項目を評価する。

    spec は次のいずれか:
      "列名"                              … その列の値
      {"const": 1000}                     … 固定値
      {"rate_of": "gross", "percent": 10} … 他項目に対する率（手数料率など）
    """
    if spec in (None, "", {}):
        return 0.0
    if isinstance(spec, dict):
        if "const" in spec:
            return parse_number(spec["const"])
        if "rate_of" in spec:
            base = computed.get(spec["rate_of"], 0.0)
            return base * parse_number(spec.get("percent", 0)) / 100.0
        return 0.0
    return parse_number(row.get(spec, 0))


SALES_NUMERIC = ("gross", "fees", "shipping", "cogs", "refunds")
ADS_NUMERIC = ("cost", "ad_sales")


def build_rows(rows, mapping, kind, channel, aggregate=True):
    """画面で指定されたマッピングを適用し、DB へ入れる行と警告を返す。"""
    built, errors = [], []
    date_col = mapping.get("date")
    if not date_col:
        return [], ["日付の列が指定されていません。"]

    for i, row in enumerate(rows, start=1):
        date = parse_date(row.get(date_col))
        if not date:
            if len(errors) < 20:
                raw = str(row.get(date_col, ""))[:40]
                errors.append(f"{i} 行目: 日付を解釈できないため除外しました（{raw!r}）")
            continue

        if kind == "sales":
            out = {"channel": channel, "date": date, "note": ""}
            # 率指定（手数料 10% など）が売上を参照できるよう gross から順に計算する。
            for field in SALES_NUMERIC:
                out[field] = _resolve(mapping.get(field), row, out)
            orders_spec = mapping.get("orders")
            if orders_spec:
                out["orders"] = parse_int(_resolve(orders_spec, row, out))
            else:
                # 注文明細をそのまま取り込む場合、1 行 = 1 注文とみなす。
                out["orders"] = 1
        else:
            out = {"channel": channel, "date": date, "note": "",
                   "campaign": str(row.get(mapping.get("campaign"), "")).strip()
                   if mapping.get("campaign") else ""}
            for field in ADS_NUMERIC:
                out[field] = _resolve(mapping.get(field), row, out)
            for field in ("clicks", "impressions"):
                out[field] = parse_int(_resolve(mapping.get(field), row, out))
        built.append(out)

    if aggregate:
        built = _aggregate(built, kind)
    return built, errors


def _aggregate(rows, kind):
    """1 注文 1 行のような明細を、日付（広告はキャンペーン別）にまとめる。"""
    merged = {}
    for r in rows:
        key = (r["date"],) if kind == "sales" else (r["date"], r["campaign"])
        if key not in merged:
            merged[key] = dict(r)
            continue
        target = merged[key]
        fields = (("orders",) + SALES_NUMERIC) if kind == "sales" \
            else (ADS_NUMERIC + ("clicks", "impressions"))
        for f in fields:
            target[f] += r[f]
    return [merged[k] for k in sorted(merged)]


def guess_mapping(headers, kind):
    """よくある列名から初期マッピングを推測して、画面の入力を減らす。"""
    hints = {
        "date": ["日付", "注文日", "受注日", "売上日", "purchase-date", "date",
                 "注文日時", "受注日時", "Date", "レポート期間", "集計期間",
                 "対象期間", "期間", "納品日", "出荷日", "計上日", "取引日", "年月"],
        "orders": ["注文数", "受注件数", "件数", "注文件数", "orders", "数量"],
        "gross": ["売上", "売上高", "商品代金", "小計", "合計金額", "item-price",
                  "商品合計", "総売上", "sales", "売上金額", "請求金額", "販売金額",
                  "取引金額", "税込金額"],
        "fees": ["手数料", "販売手数料", "システム利用料", "fee", "commission",
                 "決済手数料", "利用料"],
        "shipping": ["送料", "配送料", "shipping", "発送料", "運賃"],
        "cogs": ["原価", "仕入", "仕入額", "原価合計", "仕入金額", "cost"],
        "refunds": ["返金", "返品", "キャンセル", "refund"],
        "campaign": ["キャンペーン", "キャンペーン名", "campaign", "広告グループ"],
        "cost": ["広告費", "費用", "消化金額", "コスト", "spend", "cost", "clickCost"],
        "ad_sales": ["広告経由売上", "広告売上", "売上", "attributedSales",
                     "コンバージョン売上"],
        "clicks": ["クリック", "クリック数", "clicks"],
        "impressions": ["表示回数", "インプレッション", "impressions"],
    }
    fields = ("date", "orders", "gross", "fees", "shipping", "cogs", "refunds") \
        if kind == "sales" else \
        ("date", "campaign", "cost", "ad_sales", "clicks", "impressions")

    lowered = [(h, unicodedata.normalize("NFKC", h).lower()) for h in headers]
    mapping, used = {}, set()
    for field in fields:
        for hint in hints[field]:
            h_norm = unicodedata.normalize("NFKC", hint).lower()
            for original, low in lowered:
                if original in used:
                    continue
                if low == h_norm or h_norm in low:
                    mapping[field] = original
                    used.add(original)
                    break
            if field in mapping:
                break
    return mapping
