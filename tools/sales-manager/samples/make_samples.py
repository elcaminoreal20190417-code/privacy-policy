"""動作確認用のサンプル CSV を生成する。

Shift_JIS のサンプル 3 本は、そのままだと文字コードが保てない経路で配布される
ことがあるため、Git には入れずこのスクリプトで作り直せるようにしてある。

    python3 samples/make_samples.py     （リポジトリ直下 or samples/ のどちらからでも可）
"""

import csv
import io
import os
import random

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def write(name, headers, rows, encoding="utf-8", delimiter=","):
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows(rows)
    path = os.path.join(OUT_DIR, name)
    with open(path, "wb") as fh:
        fh.write(buf.getvalue().encode(encoding))
    print(f"  {name}  ({encoding})")


def main():
    random.seed(7)  # 毎回同じ内容が出るように固定する
    days = [f"2026-05-{d:02d}" for d in range(1, 32)]
    print("サンプル CSV を生成します:")

    # Amazon: 注文明細のイメージ（1 行 1 注文 / UTF-8 / タブ区切り / ISO 日時）
    rows = []
    for day in days:
        for _ in range(random.randint(3, 9)):
            price = random.choice([2480, 3980, 5980, 8800])
            rows.append([day + "T10:23:11+09:00",
                         f"249-{random.randint(1000000, 9999999)}-1234567",
                         price, round(price * 0.15), 0])
    write("amazon_orders_2026-05.tsv",
          ["purchase-date", "order-id", "item-price", "販売手数料", "返金額"],
          rows, "utf-8", "\t")

    # 楽天: 日別集計 / Shift_JIS / 金額にカンマ
    rows = [[day.replace("-", "/"), random.randint(2, 7),
             f"{random.randint(20000, 90000):,}", f"{random.randint(2000, 9000):,}",
             random.choice([0, 550, 880])] for day in days]
    write("rakuten_daily_2026-05.csv",
          ["受注日", "受注件数", "売上金額", "システム利用料", "送料"], rows, "cp932")

    # Yahoo!: 月まとめ 1 行 / Shift_JIS / 「2026年5月」「￥312,400」表記
    write("yahoo_monthly_2026-05.csv",
          ["集計期間", "注文数", "売上高", "ストアポイント原資・手数料", "配送料", "返金"],
          [["2026年5月", 48, "￥312,400", "￥28,116", "￥12,000", "￥0"]], "cp932")

    # 公式サイト（自社 EC）: UTF-8 / 日別
    rows = [[day, random.randint(0, 4), random.randint(0, 45000), 0, 0] for day in days]
    write("official_site_2026-05.csv",
          ["日付", "注文件数", "売上", "決済手数料", "配送料"], rows)

    # 卸: 請求ベースで月に数本
    write("wholesale_2026-05.csv",
          ["納品日", "取引先", "請求金額", "原価"],
          [["2026/05/09", "A商事", "480,000", "312,000"],
           ["2026/05/21", "B流通", "265,000", "171,000"]])

    # Amazon 広告: キャンペーン別の日次
    rows = []
    for day in days:
        for campaign in ("スポンサープロダクト_主力", "スポンサーブランド_新商品"):
            cost = random.randint(800, 4200)
            rows.append([day, campaign, cost, round(cost * random.uniform(2.5, 7.5)),
                         random.randint(40, 260), random.randint(2000, 18000)])
    write("amazon_ads_2026-05.csv",
          ["日付", "キャンペーン名", "広告費", "広告経由売上", "クリック数", "表示回数"],
          rows)

    # 楽天広告 (RPP): Shift_JIS / 「消化金額」という独自の列名
    rows = [[day.replace("-", "/"), "RPP", f"￥{random.randint(1500, 6000):,}",
             f"￥{random.randint(8000, 40000):,}", random.randint(30, 180)]
            for day in days]
    write("rakuten_ads_2026-05.csv",
          ["日付", "メニュー", "消化金額", "経由売上", "クリック"], rows, "cp932")

    print("完了しました。")


if __name__ == "__main__":
    main()
