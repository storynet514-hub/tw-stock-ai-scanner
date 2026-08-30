#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
00838B TPEx Official Price Diagnostic V3

目的：
1. 使用 TPEx 正確的「上櫃股票行情」官方 endpoint
2. 不使用 stk_wn1430_result.php
3. 直接讀取 aaData
4. 搜尋 00838B
5. 驗證收盤價、成交量等欄位
6. 不修改正式價格管線
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import requests


SYMBOL = "00838B"

ENDPOINT = (
    "https://www.tpex.org.tw/web/stock/aftertrading/"
    "daily_close_quotes/stk_quote_result.php"
)

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


def fetch(d: date):
    tpex_date = roc_date(d)

    params = {
        "l": "zh-tw",
        "o": "json",
        "d": tpex_date,
        "s": "0,asc,0",
    }

    print()
    print("=" * 80)
    print(f"TEST DATE：{d.isoformat()}")
    print(f"TPEx DATE：{tpex_date}")
    print("=" * 80)

    print(f"ENDPOINT：{ENDPOINT}")

    try:
        response = requests.get(
            ENDPOINT,
            params=params,
            headers=HEADERS,
            timeout=TIMEOUT,
        )
    except Exception as exc:
        print(f"❌ REQUEST ERROR：{exc}")
        return False

    print()
    print("HTTP")
    print(f"status_code：{response.status_code}")
    print(f"content_type：{response.headers.get('Content-Type', '')}")
    print(f"content_length：{len(response.content)}")

    if response.status_code != 200:
        print("❌ HTTP STATUS 非 200")
        return False

    try:
        payload = response.json()
    except Exception as exc:
        print(f"❌ JSON decode failed：{exc}")
        print(response.text[:3000])
        return False

    print()
    print("JSON ROOT")
    print(f"type：{type(payload).__name__}")

    if not isinstance(payload, dict):
        print("❌ JSON root 不是 dict")
        return False

    print()
    print("ROOT KEYS")
    for key in payload.keys():
        print(f"  {key}")

    aa_data = payload.get("aaData")

    print()
    print("DATA EXTRACTION")
    print("來源結構：aaData")
    print(f"type：{type(aa_data).__name__}")

    if not isinstance(aa_data, list):
        print("❌ aaData 不是 list")
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:5000])
        return False

    print(f"rows：{len(aa_data)}")

    if not aa_data:
        print("❌ aaData = 0 rows")
        return False

    print()
    print("GLOBAL SYMBOL SEARCH")
    print("=" * 80)

    found = []

    for row_index, row in enumerate(aa_data):
        if not isinstance(row, list):
            continue

        if not row:
            continue

        symbol = str(row[0]).strip()

        if symbol.upper() == SYMBOL.upper():
            found.append((row_index, row))

    if not found:
        print(f"❌ {SYMBOL} 不在 aaData")

        print()
        print("前 30 筆代號：")

        for row in aa_data[:30]:
            if isinstance(row, list) and row:
                print(f"  {str(row[0]).strip()}")

        return False

    print(f"✅ 找到 {SYMBOL}")

    for row_index, row in found:
        print()
        print(f"ROW INDEX：{row_index}")
        print("RAW ROW：")
        print(json.dumps(row, ensure_ascii=False))

        print()
        print("PRICE FIELDS")

        labels = [
            "代號",
            "名稱",
            "收盤",
            "漲跌",
            "開盤",
            "最高",
            "最低",
            "成交股數",
            "成交金額",
            "成交筆數",
            "最後買價",
            "最後買量",
            "最後賣價",
            "最後賣量",
            "發行股數",
            "次日漲停價",
            "次日跌停價",
        ]

        for i, value in enumerate(row):
            label = labels[i] if i < len(labels) else f"FIELD_{i}"
            print(f"  [{i:02d}] {label}：{value}")

        if len(row) >= 8:
            close_price = str(row[2]).strip()
            volume = str(row[7]).strip()

            print()
            print("VALIDATION")

            if close_price and close_price not in ("-", "--"):
                print(f"  ✅ close_price = {close_price}")
            else:
                print("  ❌ close_price 無有效資料")
                return False

            if volume and volume not in ("-", "--"):
                print(f"  ✅ volume = {volume}")
            else:
                print("  ❌ volume 無有效資料")
                return False

    return True


def main():
    print("=" * 80)
    print("00838B TPEx OFFICIAL PRICE DIAGNOSTIC V3")
    print("=" * 80)

    print(f"測試商品：{SYMBOL}")
    print("資料來源：TPEx 官方")
    print("資料類型：上櫃股票行情")
    print(f"Endpoint：{ENDPOINT}")
    print()
    print("Yahoo：NO")
    print("Universe：NO")
    print("正式價格管線：NO")

    today = date(2026, 8, 28)

    success_dates = []

    # 測試最近 10 個日曆日
    for offset in range(10):
        d = today - timedelta(days=offset)

        try:
            ok = fetch(d)
        except Exception as exc:
            print(f"❌ TEST ERROR：{exc}")
            ok = False

        if ok:
            success_dates.append(d.isoformat())

    print()
    print("=" * 80)
    print("FINAL RESULT")
    print("=" * 80)

    print(f"HTTP / JSON 測試日期：10")
    print(f"找到 {SYMBOL} 的日期：{len(success_dates)}")

    if success_dates:
        print()
        print("✅ SUCCESS")
        for d in success_dates:
            print(f"  {d}")

        print()
        print("✅ 已確認 TPEx 官方價格資料可取得 00838B")
        print()
        print("下一步才修改正式 fetch_prices.py。")

        return 0

    print()
    print(f"❌ 最近測試期間仍沒有找到 {SYMBOL}")
    print()
    print("這次才需要繼續追查 TPEx 資料分類。")

    return 1


if __name__ == "__main__":
    sys.exit(main())
