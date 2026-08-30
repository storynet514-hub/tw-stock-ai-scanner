#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
00838B TPEx ETF Historical Price Diagnostic

目的：
1. 不使用 stk_wn1430_result.php
2. 專門測試 TPEx ETF「歷史行情」資料
3. 確認 00838B 是否能由官方 ETF 歷史行情取得
4. 確認實際 JSON / CSV endpoint 與資料結構
5. 本測試不修改任何正式價格資料
"""

from __future__ import annotations

import csv
import io
import json
import sys
from datetime import date, timedelta

import requests


SYMBOL = "00838B"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": "https://www.tpex.org.tw/",
}

TIMEOUT = 30


def roc_date(d: date) -> str:
    return f"{d.year - 1911:03d}/{d.month:02d}/{d.day:02d}"


def print_separator():
    print("=" * 80)


def request_url(url: str, params=None):
    print()
    print("-" * 80)
    print("URL")
    print(url)

    if params:
        print("PARAMS")
        for k, v in params.items():
            print(f"  {k} = {v}")

    try:
        r = requests.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=TIMEOUT,
        )
    except Exception as e:
        print(f"❌ REQUEST ERROR: {e}")
        return None

    print(f"HTTP STATUS: {r.status_code}")
    print(f"CONTENT TYPE: {r.headers.get('Content-Type', '')}")
    print(f"CONTENT LENGTH: {len(r.content)}")

    if r.status_code != 200:
        print("❌ HTTP 非 200")
        return None

    return r


def normalize(v) -> str:
    if v is None:
        return ""

    return (
        str(v)
        .replace("\ufeff", "")
        .replace("\xa0", " ")
        .strip()
    )


def inspect_json(r):
    try:
        obj = r.json()
    except Exception as e:
        print(f"❌ JSON decode failed: {e}")
        print(r.text[:2000])
        return False

    print()
    print("JSON ROOT")
    print(f"type: {type(obj).__name__}")

    if isinstance(obj, dict):
        print("ROOT KEYS")
        for k in obj.keys():
            print(f"  {k}")

    found = []

    def walk(x, path="root"):
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(v, (str, int, float)):
                    if normalize(v).upper() == SYMBOL:
                        found.append((path + "." + str(k), v))
                else:
                    walk(v, path + "." + str(k))

        elif isinstance(x, list):
            for i, v in enumerate(x):
                if isinstance(v, (str, int, float)):
                    if normalize(v).upper() == SYMBOL:
                        found.append((f"{path}[{i}]", v))
                else:
                    walk(v, f"{path}[{i}]")

    walk(obj)

    print()
    print("GLOBAL SYMBOL SEARCH")
    print("=" * 80)

    if found:
        print(f"✅ 找到 {SYMBOL}")
        for path, value in found[:20]:
            print(f"  {path} = {value}")
        return True

    print(f"❌ 沒有找到 {SYMBOL}")

    # 顯示 JSON 結構，方便下一步定位
    print()
    print("JSON PREVIEW")
    print(json.dumps(obj, ensure_ascii=False, indent=2)[:10000])

    return False


def inspect_csv(r):
    raw = r.content

    for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        print("❌ CSV encoding 無法解析")
        return False

    print()
    print("CSV PREVIEW")
    print(text[:3000])

    try:
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
    except Exception as e:
        print(f"❌ CSV parse error: {e}")
        return False

    print()
    print(f"CSV ROWS: {len(rows)}")

    found = []

    for row_index, row in enumerate(rows):
        normalized = [normalize(x) for x in row]

        if SYMBOL in [x.upper() for x in normalized]:
            found.append((row_index, normalized))

    print()
    print("CSV SYMBOL SEARCH")
    print("=" * 80)

    if found:
        print(f"✅ 找到 {SYMBOL}")
        for idx, row in found:
            print(f"ROW {idx}")
            print(row)
        return True

    print(f"❌ CSV 沒有找到 {SYMBOL}")

    print()
    print("前 10 筆")
    for row in rows[:10]:
        print(row)

    return False


def main():
    print_separator()
    print("00838B TPEx ETF HISTORICAL PRICE DIAGNOSTIC")
    print_separator()

    print(f"測試商品：{SYMBOL}")
    print("資料來源：TPEx 官方 ETF 歷史行情")
    print("正式價格管線：NO")
    print("Universe：NO")
    print("Yahoo：NO")

    # TPEx 官方 ETF 歷史行情頁面
    page_url = (
        "https://www.tpex.org.tw/web/etf/historical/"
        "etf_statistics.php"
    )

    print()
    print_separator()
    print("STEP 1：ETF HISTORICAL PAGE")
    print_separator()

    r = request_url(
        page_url,
        params={"l": "zh-tw"},
    )

    if r is not None:
        print()
        print("PAGE TITLE / CONTENT CHECK")

        text = r.text

        keywords = [
            "ETF歷史行情",
            "日報表",
            "CSV",
            "00838B",
        ]

        for keyword in keywords:
            if keyword in text:
                print(f"  ✅ {keyword}")
            else:
                print(f"  ❌ {keyword}")

        print()
        print("PAGE PREVIEW")
        print(text[:5000])

    print()
    print_separator()
    print("STEP 2：SEARCH POSSIBLE TPEx ETF DATA ENDPOINTS")
    print_separator()

    # 這些是候選 endpoint。
    # 不直接假設其中任何一個一定正確。
    candidates = [
        (
            "ETF historical JSON candidate",
            "https://www.tpex.org.tw/web/etf/historical/"
            "etf_statistics_result.php",
            {
                "l": "zh-tw",
                "d": "115/08",
                "stkno": SYMBOL,
            },
        ),
        (
            "ETF historical JSON candidate 2",
            "https://www.tpex.org.tw/web/etf/historical/"
            "etf_statistics_result.php",
            {
                "l": "zh-tw",
                "d": "115/08",
                "stkno": "",
            },
        ),
        (
            "ETF historical candidate 3",
            "https://www.tpex.org.tw/web/etf/historical/"
            "etf_statistics.php",
            {
                "l": "zh-tw",
                "d": "115/08",
                "stkno": SYMBOL,
            },
        ),
    ]

    success = False

    for name, url, params in candidates:
        print()
        print(f">>> {name}")

        r = request_url(url, params)

        if r is None:
            continue

        content_type = r.headers.get("Content-Type", "").lower()

        if "json" in content_type:
            if inspect_json(r):
                success = True
        else:
            if inspect_csv(r):
                success = True

    print()
    print_separator()
    print("FINAL RESULT")
    print_separator()

    if success:
        print(f"✅ 已找到 {SYMBOL} 的官方 ETF 歷史資料")
        print()
        print("下一步：")
        print("將依實際 endpoint / response 結構修改正式價格管線。")
        return 0

    print(f"❌ 目前候選 endpoint 尚未找到 {SYMBOL}")
    print()
    print("但已確認：")
    print("1. 00838B 是 TPEx ETF")
    print("2. 不應使用 stk_wn1430_result.php")
    print("3. 應使用 TPEx ETF 歷史行情資料源")
    print("4. 正式 fetch_prices.py 暫時不要修改")

    return 1


if __name__ == "__main__":
    sys.exit(main())
