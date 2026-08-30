#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
00838B TPEx Official ETF Historical Price Diagnostic V4
=========================================================

目的：
1. 00838B 已確認為 TPEx ETF
2. 不再使用一般上櫃股票 stk_quote_result.php
3. 改測 TPEx 官方 ETF 歷史行情資料
4. 正確遍歷 tables[].data
5. 不假設 tables[0]
6. 不假設 aaData
7. 搜尋所有 table 的 00838B
8. 找到後輸出完整 table / fields / row
9. 不修改正式 fetch_prices.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import requests


SYMBOL = "00838B"

# TPEx 官方 ETF 歷史行情頁面所對應的資料服務
ENDPOINTS = [
    (
        "ETF historical day",
        "https://www.tpex.org.tw/web/stock/aftertrading/"
        "etf_hist/etf_hist_result.php",
    ),
    (
        "ETF historical price",
        "https://www.tpex.org.tw/web/stock/aftertrading/"
        "etf_hist/etf_hist_result.php",
    ),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.tpex.org.tw/",
}

TIMEOUT = 30


def roc_date(d: date) -> str:
    return f"{d.year - 1911:03d}/{d.month:02d}/{d.day:02d}"


def normalize(value) -> str:
    if value is None:
        return ""

    return (
        str(value)
        .replace("\xa0", " ")
        .replace("\u3000", " ")
        .strip()
        .upper()
    )


def symbol_matches(value) -> bool:
    """
    嚴格比對 00838B。

    不做模糊包含，避免把其他商品誤判成 00838B。
    """
    return normalize(value) == SYMBOL.upper()


def inspect_tables(payload):
    """
    正確處理 TPEx JSON：

        root
          └── tables[]
                ├── fields
                └── data[]

    不假設 tables[0]。
    """

    tables = payload.get("tables")

    print()
    print("TABLE STRUCTURE")
    print(f"tables type：{type(tables).__name__}")

    if not isinstance(tables, list):
        print("❌ root.tables 不是 list")

        print()
        print("ROOT PAYLOAD PREVIEW")
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )[:12000]
        )

        return []

    print(f"tables count：{len(tables)}")

    found = []

    for table_index, table in enumerate(tables):

        print()
        print("-" * 80)
        print(f"TABLE [{table_index}]")
        print("-" * 80)

        if not isinstance(table, dict):
            print(f"type：{type(table).__name__}")
            print("⚠️ table 不是 dict")
            continue

        print("keys：")
        for key in table.keys():
            print(f"  {key}")

        fields = table.get("fields")
        data = table.get("data")

        print()
        print(f"fields type：{type(fields).__name__}")
        print(f"data type：{type(data).__name__}")

        if isinstance(fields, list):
            print(f"fields count：{len(fields)}")

            for i, field in enumerate(fields):
                print(f"  FIELD[{i:02d}]：{field}")

        if not isinstance(data, list):
            print("⚠️ table.data 不是 list")
            continue

        print(f"rows：{len(data)}")

        if not data:
            print("⚠️ table.data = 0 rows")
            continue

        # ---------------------------------------------------------
        # 搜尋所有 row
        # ---------------------------------------------------------

        for row_index, row in enumerate(data):

            if not isinstance(row, list):
                continue

            # 不是只看 row[0]。
            # ETF endpoint 可能把代號放在不同欄位，
            # 因此整列搜尋。
            matched_positions = []

            for column_index, value in enumerate(row):
                if symbol_matches(value):
                    matched_positions.append(column_index)

            if not matched_positions:
                continue

            found.append(
                {
                    "table_index": table_index,
                    "row_index": row_index,
                    "matched_positions": matched_positions,
                    "fields": fields,
                    "row": row,
                }
            )

    return found


def fetch(endpoint_name: str, endpoint: str, d: date):
    tpex_date = roc_date(d)

    params_candidates = [
        {
            "l": "zh-tw",
            "o": "json",
            "d": tpex_date,
        },
        {
            "l": "zh-tw",
            "o": "json",
            "d": tpex_date,
            "s": "0,asc,0",
        },
    ]

    print()
    print("=" * 80)
    print(f"TEST DATE：{d.isoformat()}")
    print(f"TPEx DATE：{tpex_date}")
    print(f"ENDPOINT TYPE：{endpoint_name}")
    print(f"ENDPOINT：{endpoint}")
    print("=" * 80)

    for attempt, params in enumerate(params_candidates, start=1):

        print()
        print(f"REQUEST ATTEMPT：{attempt}")
        print(f"params：{params}")

        try:
            response = requests.get(
                endpoint,
                params=params,
                headers=HEADERS,
                timeout=TIMEOUT,
            )
        except Exception as exc:
            print(f"❌ REQUEST ERROR：{exc}")
            continue

        print()
        print("HTTP")
        print(f"status_code：{response.status_code}")
        print(
            "content_type："
            f"{response.headers.get('Content-Type', '')}"
        )
        print(f"content_length：{len(response.content)}")

        if response.status_code != 200:
            print("❌ HTTP STATUS 非 200")
            continue

        try:
            payload = response.json()
        except Exception as exc:
            print(f"❌ JSON decode failed：{exc}")
            print()
            print("RAW RESPONSE")
            print(response.text[:12000])
            continue

        print()
        print("JSON ROOT")
        print(f"type：{type(payload).__name__}")

        if not isinstance(payload, dict):
            print("❌ JSON root 不是 dict")
            continue

        print()
        print("ROOT KEYS")

        for key in payload.keys():
            print(f"  {key}")

        # ---------------------------------------------------------
        # 重要：
        #
        # 這裡只接受 tables[].data。
        # 不再把 aaData 當成唯一資料來源。
        # ---------------------------------------------------------

        found = inspect_tables(payload)

        if found:

            print()
            print("=" * 80)
            print(f"✅ GLOBAL SEARCH FOUND {SYMBOL}")
            print("=" * 80)

            for item in found:

                table_index = item["table_index"]
                row_index = item["row_index"]
                positions = item["matched_positions"]
                fields = item["fields"]
                row = item["row"]

                print()
                print(
                    f"TABLE INDEX：{table_index}"
                )
                print(
                    f"ROW INDEX：{row_index}"
                )
                print(
                    f"MATCHED COLUMN：{positions}"
                )

                print()
                print("RAW ROW")
                print(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                    )
                )

                print()
                print("ROW FIELDS")

                for i, value in enumerate(row):

                    if (
                        isinstance(fields, list)
                        and i < len(fields)
                    ):
                        label = fields[i]
                    else:
                        label = f"FIELD_{i}"

                    print(
                        f"  [{i:02d}] "
                        f"{label}：{value}"
                    )

            return True

        print()
        print(f"❌ {SYMBOL} not found in tables[].data")

        # 只在第一次 request 印 preview，
        # 避免 Action log 爆量。
        if attempt == 1:

            print()
            print("DATA STRUCTURE PREVIEW")

            tables = payload.get("tables")

            if isinstance(tables, list):

                for i, table in enumerate(tables[:5]):

                    if not isinstance(table, dict):
                        continue

                    print()
                    print(f"TABLE {i} PREVIEW")

                    data = table.get("data")

                    if isinstance(data, list):

                        print(
                            f"rows={len(data)}"
                        )

                        for row in data[:3]:
                            print(
                                json.dumps(
                                    row,
                                    ensure_ascii=False,
                                )
                            )

        # 第二組 params 不需要再被視為不同日期結果；
        # 若兩次都沒有找到，就讓 endpoint/date 結束。
        if attempt == len(params_candidates):
            return False

    return False


def main():

    print("=" * 80)
    print("00838B TPEx OFFICIAL ETF PRICE DIAGNOSTIC V4")
    print("=" * 80)

    print(f"測試商品：{SYMBOL}")
    print("資料來源：TPEx 官方")
    print("資料類型：ETF 歷史行情")
    print()
    print("Yahoo：NO")
    print("Universe：NO")
    print("正式價格管線：NO")
    print("正式 fetch_prices.py：NO TOUCH")

    today = date(2026, 8, 28)

    success = []

    # 最近 10 個日曆日
    test_dates = [
        today - timedelta(days=i)
        for i in range(10)
    ]

    for endpoint_name, endpoint in ENDPOINTS:

        print()
        print()
        print("#" * 80)
        print(f"ENDPOINT TEST：{endpoint_name}")
        print("#" * 80)

        for d in test_dates:

            try:
                ok = fetch(
                    endpoint_name,
                    endpoint,
                    d,
                )
            except Exception as exc:

                print()
                print(
                    f"❌ UNHANDLED TEST ERROR：{exc}"
                )

                ok = False

            if ok:

                success.append(
                    (
                        endpoint_name,
                        endpoint,
                        d.isoformat(),
                    )
                )

    print()
    print()
    print("=" * 80)
    print("FINAL RESULT")
    print("=" * 80)

    print(
        f"HTTP / JSON 測試日期："
        f"{len(test_dates)}"
    )

    print(
        f"找到 {SYMBOL} 的結果："
        f"{len(success)}"
    )

    if success:

        print()
        print("✅ SUCCESS")

        for endpoint_name, endpoint, d in success:

            print()
            print(f"日期：{d}")
            print(f"類型：{endpoint_name}")
            print(f"Endpoint：{endpoint}")

        print()
        print(
            "✅ 已找到 TPEx 官方 ETF "
            "歷史行情資料。"
        )

        print()
        print(
            "下一步才可以依實際 row/fields "
            "修改正式價格管線。"
        )

        return 0

    print()
    print(
        f"❌ 目前候選 ETF endpoint "
        f"仍沒有找到 {SYMBOL}"
    )

    print()
    print("目前已確認：")
    print("1. 00838B 是 TPEx ETF")
    print("2. 不應使用 stk_wn1430_result.php")
    print("3. 不應使用一般上櫃股票 stk_quote_result.php")
    print("4. TPEx 官方另有 ETF 歷史行情資料頁")
    print("5. 本測試只認 tables[].data")
    print("6. 正式 fetch_prices.py 尚未修改")

    print()
    print(
        "❌ 若本測試失敗，下一步只追查 "
        "ETF 歷史行情頁面的實際 API endpoint，"
        "不再修改正式價格管線。"
    )

    return 1


if __name__ == "__main__":
    sys.exit(main())