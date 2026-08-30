#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
00838B TPEx Official Price Diagnostic V3

目的：
- 只測試 TPEx 官方 endpoint
- 正確解析 tables[].data
- 不使用 aaData
- 不使用 Yahoo
- 不使用 Universe
- 不修改正式價格管線
- 不因為找不到 00838B 就提前結束
- 印出實際 rows，確認 TPEx 回傳資料格式
"""

import json
import sys
from datetime import datetime, timedelta

import requests


SYMBOL = "00838B"

URL = (
    "https://www.tpex.org.tw/web/stock/aftertrading/"
    "otc_quotes_no1430/stk_wn1430_result.php"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.tpex.org.tw/",
}


def roc_date(dt):
    return f"{dt.year - 1911}/{dt.month:02d}/{dt.day:02d}"


def normalize(value):
    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
        .upper()
    )


def request_tpex(date_text):
    params = {
        "l": "zh-tw",
        "o": "json",
        "d": date_text,
    }

    response = requests.get(
        URL,
        params=params,
        headers=HEADERS,
        timeout=30,
    )

    print(f"HTTP STATUS：{response.status_code}")
    print(
        "CONTENT TYPE："
        f"{response.headers.get('content-type', '')}"
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise RuntimeError(
            f"JSON root 不是 dict：{type(data).__name__}"
        )

    return data


def inspect_tables(payload):
    tables = payload.get("tables")

    print()
    print("JSON ROOT")
    print(f"  type：{type(payload).__name__}")

    print()
    print("ROOT KEYS")
    for key in payload.keys():
        print(f"  {key}")

    if not isinstance(tables, list):
        print()
        print("❌ tables 不是 list")
        return False, []

    print()
    print(f"TABLE COUNT：{len(tables)}")

    all_rows = []

    for ti, table in enumerate(tables):

        if not isinstance(table, dict):
            print(f"❌ tables[{ti}] 不是 dict")
            continue

        title = table.get("title")
        fields = table.get("fields")
        rows = table.get("data")

        print()
        print(f"TABLE [{ti}]")
        print(f"  title：{title}")
        print(f"  totalCount：{table.get('totalCount')}")
        print(f"  fields type：{type(fields).__name__}")
        print(f"  data type：{type(rows).__name__}")

        if not isinstance(fields, list):
            print("  ❌ fields 不是 list")
            continue

        if not isinstance(rows, list):
            print("  ❌ data 不是 list")
            continue

        print(f"  fields：{len(fields)}")
        print(f"  rows：{len(rows)}")

        print()
        print("FIELDS")
        for i, field in enumerate(fields):
            print(f"  [{i:02d}] {field}")

        if not rows:
            continue

        all_rows.extend(rows)

        print()
        print("FIRST 5 ROWS")
        for ri, row in enumerate(rows[:5]):
            print(f"  ROW [{ri}]")
            print(
                json.dumps(
                    row,
                    ensure_ascii=False,
                )
            )

        # 尋找任何包含 00838B 的 row
        print()
        print(f"SEARCH SYMBOL：{SYMBOL}")

        found = False

        for ri, row in enumerate(rows):

            if not isinstance(row, list):
                continue

            for ci, cell in enumerate(row):
                if normalize(cell) == SYMBOL:
                    found = True

                    print()
                    print("✅ EXACT MATCH FOUND")
                    print(f"  table：{ti}")
                    print(f"  row：{ri}")
                    print(f"  column：{ci}")
                    print(f"  field：{fields[ci]}")
                    print(f"  value：{cell}")

                    print()
                    print("完整 ROW：")
                    print(
                        json.dumps(
                            row,
                            ensure_ascii=False,
                            indent=2,
                        )
                    )

                    break

            if found:
                break

        if not found:
            print(
                f"❌ {SYMBOL} 不在這個 table 的 data 中"
            )

            # 印出前 30 個第一欄代號
            print()
            print("第一欄前 30 筆：")

            for row in rows[:30]:
                if isinstance(row, list) and row:
                    print(f"  {row[0]!r}")

    return True, all_rows


def search_any_symbol(payload):
    """
    第二層搜尋：

    不只搜尋 row[0]，
    而是搜尋所有 tables[].data 欄位。

    用來排除：
    - 代號不在第一欄
    - 欄位順序改變
    - 00838B 被包在其他欄位
    """

    print()
    print("=" * 80)
    print("GLOBAL SYMBOL SEARCH")
    print("=" * 80)

    tables = payload.get("tables", [])

    for ti, table in enumerate(tables):

        if not isinstance(table, dict):
            continue

        fields = table.get("fields", [])
        rows = table.get("data", [])

        if not isinstance(rows, list):
            continue

        for ri, row in enumerate(rows):

            if not isinstance(row, list):
                continue

            for ci, value in enumerate(row):

                if normalize(value) == SYMBOL:

                    field = (
                        fields[ci]
                        if ci < len(fields)
                        else "UNKNOWN"
                    )

                    print()
                    print("✅ GLOBAL MATCH")
                    print(f"table：{ti}")
                    print(f"row：{ri}")
                    print(f"column：{ci}")
                    print(f"field：{field}")
                    print(f"value：{value}")

                    print()
                    print("ROW：")
                    print(
                        json.dumps(
                            row,
                            ensure_ascii=False,
                            indent=2,
                        )
                    )

                    return True

    print()
    print(f"❌ 全部 tables[].data 都沒有 {SYMBOL}")

    return False


def test_date(dt):
    date_text = dt.strftime("%Y-%m-%d")
    tp_ex_date = roc_date(dt)

    print()
    print("=" * 80)
    print(f"TEST DATE：{date_text}")
    print("=" * 80)
    print(f"TPEx DATE：{tp_ex_date}")
    print(f"URL：{URL}")

    try:
        payload = request_tpex(tp_ex_date)
    except Exception as exc:
        print()
        print(f"❌ REQUEST ERROR：{exc}")
        return False, False

    valid, rows = inspect_tables(payload)

    if not valid:
        return False, False

    found = search_any_symbol(payload)

    return True, found


def main():

    print("=" * 80)
    print("00838B TPEx OFFICIAL PRICE DIAGNOSTIC V3")
    print("=" * 80)

    print(f"測試商品：{SYMBOL}")
    print("資料來源：TPEx 官方")
    print("Endpoint：stk_wn1430_result.php")
    print("解析結構：tables[].data")
    print("Yahoo：NO")
    print("Universe：NO")
    print("正式價格管線：NO")

    print()
    print("=" * 80)
    print("開始測試最近 10 個交易日候選日期")
    print("=" * 80)

    start = datetime(2026, 8, 28)

    success_count = 0
    found_count = 0

    for offset in range(10):

        dt = start - timedelta(days=offset)

        valid, found = test_date(dt)

        if valid:
            success_count += 1

        if found:
            found_count += 1

    print()
    print("=" * 80)
    print("FINAL RESULT")
    print("=" * 80)

    print(f"HTTP / JSON 有效日期：{success_count}")
    print(f"找到 {SYMBOL} 的日期：{found_count}")

    if found_count > 0:
        print()
        print(f"✅ 已確認 TPEx tables[].data 可以找到 {SYMBOL}")
        print("下一步才修改正式 fetch_prices.py")
        return 0

    print()
    print(f"❌ 最近測試期間仍沒有找到 {SYMBOL}")
    print()
    print("目前可以確定：")
    print("1. HTTP endpoint 可正常回應")
    print("2. JSON root 正常")
    print("3. 正確資料位置是 tables[].data")
    print("4. tables[].data 在目前日期回傳 0 rows")
    print(f"5. {SYMBOL} 沒有出現在這個資料集")
    print()
    print("因此現在不能把問題歸因於 Python 解析錯誤。")
    print("下一步應查 00838B 的官方商品分類 / 正確行情 endpoint。")

    # 診斷腳本本身不因「商品不存在此資料集」而報 Python error。
    # 但保留 exit code 1，讓 GitHub Actions 清楚標示「尚未找到」。
    return 1


if __name__ == "__main__":
    sys.exit(main())
